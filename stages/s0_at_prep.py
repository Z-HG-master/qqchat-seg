#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阶段 0 · 步骤 0.7：@ 与 reply 的语义初审。

规则
  · 对每条 mention：找被 @ 的人此前最近的 unit
      - 没有 → 不相关 → 从正文里删掉 @；
      - 有   → 让模型判「是不是接着 TA 那条在聊」：
                 相关   → 改写成 reply 结构（reply_to_unit），并标记 link_confirmed；
                 不相关 → 删掉 @。
  · 真 reply 关系同样走一次相关性判定，避免「回复了你但聊别的」被错误合并。
    实测 541 条 reply 里约 48% 说的不是同一件事。

输入: ``units.jsonl`` + ``name_table.jsonl``
输出: ``units_at.jsonl``（reply_to_unit / at_action 已填）+ ``at_links.jsonl``（判定日志）

用法::

    python stages/s0_at_prep.py --group 123456789
"""
import argparse
import collections
import json
import os
import re
import sys
import time

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)
sys.dont_write_bytecode = True   # 不在仓库里留下 __pycache__

from config import ConfigError, add_common_args, load_config        # noqa: E402
from core.llm import LLM, LLMError                                 # noqa: E402

# ---- 提示词（实验定稿，逐字保留，勿改） -----------------------------------
REL_PROMPT = ('你在处理社交群聊记录相关性判别。'
              '下面是【被@/回复的人此前说的一条消息】和【现在这条 @ /回复了 TA 的消息】。\n'
              '判断【现在这条】是否在与 TA 此前的消息存在关联性。\n\n'
              '【TA 此前说】\n{a}\n\n'
              '【现在这条】\n{b}\n\n'
              '判「YES」的情况：\n'
              '- 【现在这条】消息在回应 / 追问 / 补充【TA 此前说】消息里的人、事、物，图时\n'
              '- 【现在这条】消息为短反应（"？""确实""草""哈哈哈"等等）且能看出是在回应上面【TA 此前说】的消息、'
              '或是对【TA 此前说】的消息内容的吐槽（包括反讽、比喻和阴阳怪气等等）或表达情绪时（感慨，赞叹，附和等等）\n'
              '- 【现在这条】消息是由【TA 此前说】的消息中延续出的具有相关性的话题\n'
              '- 【现在这条】消息发的图或表情，能看出是接着【TA 此前说】的消息或对【TA 此前说】的消息表示情感吐槽和回应的'
              '（例如回应前面的截图，补充同一件事的画面，用图片表达自己对前消息的情绪感慨、认同、附和、吐槽等）\n\n'
              '判「NO」的情况：\n'
              '- 【现在这条】消息内容（图、文）转向讨论了另一件相关性弱的事时：换了话题、换了对象等等\n'
              '- 【现在这条】消息内容不满足判「YES」情况中任何一条的要求时\n\n'
              '只输出 YES 或 NO，不要解释。')

# --- 提示词覆盖点（--prompts-override，见 core/prompts.py） ---
from core.prompts import get as _prompt_get   # noqa: E402
REL_PROMPT = _prompt_get('link.at', REL_PROMPT)
# --------------------------------------------------------------------------


def load_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description='0.7 @ / reply 语义初审')
    add_common_args(ap)
    ap.add_argument('--model', default=None, help='覆盖 config 的 model_small（可选）')
    args = ap.parse_args(argv)

    cfg = load_config(args, require_group=True)
    if args.model:
        cfg.raw['model_small'] = args.model
    group_dir = cfg.group_dir
    units_path = os.path.join(group_dir, 'units.jsonl')
    names_path = os.path.join(group_dir, 'name_table.jsonl')
    if not os.path.exists(units_path):
        print('错误：找不到 %s\n  请先运行 0.6：python stages/s0_merge_runs.py --group %s'
              % (units_path, cfg.group_id), file=sys.stderr)
        return 2

    units = load_jsonl(units_path)
    names = load_jsonl(names_path)
    alias = collections.defaultdict(set)
    for r in names:
        for k in ('display', 'card', 'qq_nick', 'remark'):
            if r.get(k):
                alias[str(r.get('qq'))].add(r[k])
    print('unit %d 个 | 有别名的人 %d 个' % (len(units), len(alias)))

    llm = LLM(cfg)
    if not llm.mock:
        ok, msg = llm.check('small')
        if not ok:
            print('无法开始 0.7：%s' % msg, file=sys.stderr)
            return 2
        print(msg)

    by_sender = collections.defaultdict(list)
    for i, u in enumerate(units):
        by_sender[u.get('sender_qq')].append(i)

    def last_before(qq, idx):
        """返回 qq 在 idx 之前最近的一个 unit 下标；没有则 None。"""
        r = None
        for j in by_sender.get(qq, []):
            if j >= idx:
                break
            r = j
        return r

    def strip_at(text, qq):
        """按别名（长的优先）删掉正文里指向 qq 的 @名字。"""
        for c in sorted(alias.get(qq, ()), key=len, reverse=True):
            pat = re.compile(r'@' + re.escape(c))
            if pat.search(text):
                return pat.sub('', text, count=1), c
        m = re.search(r'@\S{1,14}', text)
        if m:
            return text.replace(m.group(0), '', 1), m.group(0)
        return text, None

    t0 = time.time()
    st = collections.Counter()
    with open(os.path.join(group_dir, 'at_links.jsonl'), 'w', encoding='utf-8') as log:
        for i, u in enumerate(units):
            changed = False
            # ---- 真 reply 关系：先找目标最近的 unit，再确认相关性
            target_qq = u.get('reply_target_qq')
            if target_qq:
                j = last_before(target_qq, i)
                if j is not None:
                    out = llm.ask(REL_PROMPT.format(a=units[j]['text'][:300],
                                                    b=u['text'][:300]),
                                  role='small', max_tokens=10)
                    yes = bool(re.match(r'^(YES|是)', out.strip().upper()))
                    st['reply_related' if yes else 'reply_unrelated'] += 1
                    if yes:
                        u['reply_to_unit'] = units[j]['unit_id']
                        u['link_confirmed'] = True
                        u['link_via'] = 'reply'
                    else:
                        u['reply_to_unit'] = None
                else:
                    st['reply_no_prior'] += 1
                log.write(json.dumps({'unit': u['unit_id'], 'kind': 'reply',
                                      'target_qq': target_qq,
                                      'linked_to': u.get('reply_to_unit'),
                                      'verdict': 'direct'}, ensure_ascii=False) + '\n')

            # ---- @ 预处理
            for mn in (u.get('mentions') or []):
                q = str(mn.get('qq') or '')
                if not q:
                    continue
                st['mention'] += 1
                j = last_before(q, i)
                if j is None:
                    body, hit = strip_at(u['body'], q)
                    u['body'] = body
                    changed = True
                    st['at_no_prior_stripped'] += 1
                    log.write(json.dumps({'unit': u['unit_id'], 'kind': 'mention',
                                          'target_qq': q, 'linked_to': None,
                                          'verdict': 'no_prior', 'stripped': hit},
                                         ensure_ascii=False) + '\n')
                    continue
                out = llm.ask(REL_PROMPT.format(a=units[j]['text'][:300],
                                                b=u['text'][:300]),
                              role='small', max_tokens=10)
                yes = bool(re.match(r'^(YES|是|yes|Yes)', out.strip()))
                st['at_related' if yes else 'at_unrelated'] += 1
                if yes:
                    if not u.get('reply_to_unit'):
                        u['reply_to_unit'] = units[j]['unit_id']
                    u['at_action'] = 'rewritten'
                    u['link_confirmed'] = True
                    u['link_via'] = 'at'
                else:
                    body, hit = strip_at(u['body'], q)
                    u['body'] = body
                    changed = True
                    u['at_action'] = 'stripped'
                log.write(json.dumps({'unit': u['unit_id'], 'kind': 'mention',
                                      'target_qq': q,
                                      'linked_to': units[j]['unit_id'] if yes else None,
                                      'verdict': 'related' if yes else 'unrelated',
                                      'raw': out[:20]}, ensure_ascii=False) + '\n')
                log.flush()
            if changed:
                u['text'] = ('%s: %s' % (u['display'], u['body'])
                             if (u.get('body') or '').strip() else '%s:' % u['display'])
            if (i + 1) % 100 == 0:
                el = time.time() - t0
                print('  %d/%d | %.1f min | 已判定 @ %d 个'
                      % (i + 1, len(units), el / 60, st['mention']), flush=True)

    out_path = os.path.join(group_dir, 'units_at.jsonl')
    with open(out_path, 'w', encoding='utf-8') as f:
        for u in units:
            f.write(json.dumps(u, ensure_ascii=False) + '\n')

    print('\n=== 0.7 结果（用时 %.1f 分钟）===' % ((time.time() - t0) / 60))
    for k, v in sorted(st.items()):
        print('  %-24s%7d' % (k, v))
    print('\n产物：%s' % out_path)
    print('\n下一步：python stages/s0_finalize.py --group %s' % cfg.group_id)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except ConfigError as e:
        print('配置错误：%s' % e, file=sys.stderr)
        sys.exit(2)
    except LLMError as e:
        print('模型调用失败：%s' % e, file=sys.stderr)
        sys.exit(2)
