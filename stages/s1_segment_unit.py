#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阶段 1 · 步骤 1.1：逐条事件判别（核心步骤）。

每条未认领的消息都要对「当前事件」判一次「续（接着说）」或「新（另起一事）」：

  · 上下文 = 当前事件已认领消息的**全部原文**（带固定外延窗口，不做摘要）；
  · reply 目标落在当前事件内 → 硬捷径直接认领，不调用模型；
  · 判「新」后向后看 N 条（--lookahead），其中若有「续」就认领该条；
  · 事件达到字数/条数上限即封口，避免上下文越长越容易判「续」。

关键参数（除非有实验依据，不要改）
  --lookahead 5   判「新」后向后看 5 条
  --chars 4000    单事件字数字上限
  --max-units 60  单事件条数上限（安全阀）
  --grace 0       新事件开头自动认领条数（0=关）

输入: ``units_at.jsonl``
输出: ``s1_events_<tag>.jsonl`` + ``s1_decisions_<tag>.jsonl``

用法::

    python stages/s1_segment_unit.py --group 123456789 --tag ev --lookahead 5 --explain
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
PROMPT = ('你在处理社交群聊记录的事件归类工作。\n\n'
          '【当前对话】下面是这个事件相关的消息记录（按时间先后）：\n'
          '{context}\n\n'
          '【新的一条消息】\n'
          '{unit}\n\n'
          '请判断：这条新消息是不是与【当前话题】里所写对话中谈论的事件/主题有关联？\n\n'
          '判「续」的情况：\n'
          '- 新消息在回应 / 追问 / 补充前文某条消息里的人、事、物，图时\n'
          '- 新消息为短反应（"？""确实""草""哈哈哈"等等）且能看出是在回应上面某句、'
          '或是对前消息内容的吐槽（包括反讽、比喻和阴阳怪气等等）或表达情绪时（感慨，赞叹，附和等等）\n'
          '- 新消息是由当前话题中延续出的具有相关性的话题\n'
          '- 新消息发的图或表情，能看出是接着上面的话题或对当前话题表示情感吐槽和回应的'
          '（例如回应前面的截图，补充同一件事的画面，用图片表达自己对当前话题的情绪感慨、认同、附和、吐槽等）\n\n'
          '判「新」的情况：\n'
          '- 新消息内容（图、文）转向讨论了另一件相关性弱的事时：换了话题、换了对象等等\n'
          '- 新消息内容不满足判「续」情况中任何一条的要求时\n\n'
          '只输出一个字：续 或 新')

# --- 提示词覆盖点（--prompts-override，见 core/prompts.py） ---
from core.prompts import get as _prompt_get   # noqa: E402
PROMPT = _prompt_get('segment.unit', PROMPT)
EXPLAIN_TAIL = ('只输出两行，不要输出其他内容：\n判定: 续 或 新\n理由: <一句话说明为什么>')
# --------------------------------------------------------------------------


def load_jsonl(path):
    rows = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def verdict(txt):
    """解析「续 / 新」，兼容 判定:/IN/OUT/接着/不接着 等表述。"""
    raw = (txt or '').strip()
    m = re.search(r'判定\s*[:：]\s*(续|新)', raw)
    if m:
        return m.group(1)
    t = raw.upper()
    if re.search(r'续|IN|接着|属于', t) and not re.search(r'新|OUT|另起', t):
        return '续'
    if re.search(r'新|OUT|另起', t):
        return '新'
    return '续' if t.startswith(('续', 'IN')) else '新'


def main(argv=None):
    ap = argparse.ArgumentParser(description='1.1 逐条事件判别（续/新）')
    add_common_args(ap)
    ap.add_argument('--model', default=None, help='覆盖 config 的 model_small（可选）')
    ap.add_argument('--limit', type=int, default=0, help='只处理前 N 个 unit（调试用）')
    ap.add_argument('--chars', type=int, default=4000, help='单事件字数上限')
    ap.add_argument('--max-units', type=int, default=60, help='单事件条数上限（安全阀）')
    ap.add_argument('--grace', type=int, default=0, help='新事件开头自动认领的条数（0=关）')
    ap.add_argument('--explain', action='store_true', help='要求模型额外输出一句理由')
    ap.add_argument('--min-ctx', type=int, default=0,
                    help='事件头实义字数不足时补足到该字数（0=关）')
    ap.add_argument('--ctx-tail', type=int, default=0,
                    help='判定时只带最近 N 条上下文（0=全部）')
    ap.add_argument('--lookahead', type=int, default=5, help='判「新」后向后看几条（0=关）')
    ap.add_argument('--tag', default='ev', help='产物标签（默认 ev）')
    args = ap.parse_args(argv)

    cfg = load_config(args, require_group=True)
    if args.model:
        cfg.raw['model_small'] = args.model
    group_dir = cfg.group_dir
    units_path = os.path.join(group_dir, 'units_at.jsonl')
    if not os.path.exists(units_path):
        print('错误：找不到 %s\n  请先运行 0.8：python stages/s0_finalize.py --group %s'
              % (units_path, cfg.group_id), file=sys.stderr)
        return 2

    llm = LLM(cfg)
    if not llm.mock:
        ok, msg = llm.check('small')
        if not ok:
            print('无法开始 1.1：%s' % msg, file=sys.stderr)
            return 2
        print(msg)

    units = load_jsonl(units_path)
    units.sort(key=lambda u: (u['ts_first'], u['unit_id']))
    if args.limit:
        units = units[:args.limit]
    n_units = len(units)
    idx = {u['unit_id']: i for i, u in enumerate(units)}
    prompt = PROMPT if not args.explain else PROMPT.replace(
        '只输出一个字：续 或 新', EXPLAIN_TAIL)
    print('unit=%d | chars=%d max_units=%d grace=%d lookahead=%d min_ctx=%d | tag=%s'
          % (n_units, args.chars, args.max_units, args.grace, args.lookahead,
             args.min_ctx, args.tag))

    ev_path = os.path.join(group_dir, 's1_events_%s.jsonl' % args.tag)
    dec_path = os.path.join(group_dir, 's1_decisions_%s.jsonl' % args.tag)

    remaining = set(range(n_units))
    events = []
    ev = None
    n_call = hits = 0
    t0 = time.time()

    def ctx_of(event):
        ks = event['units'] if args.ctx_tail <= 0 else event['units'][-args.ctx_tail:]
        return '\n'.join(units[k]['text'] for k in ks)

    def seal(reason):
        nonlocal ev
        if ev is None:
            return
        ev['sealed_by'] = reason
        events.append(ev)
        ev = None

    with open(dec_path, 'w', encoding='utf-8') as df:
        while remaining or ev is not None:
            if ev is None:
                i = min(remaining)
                ev = {'units': [i], 'rejected': set(),
                      'chars': len(units[i]['body']),
                      'grace_left': args.grace}
                remaining.discard(i)
                continue

            cand = [k for k in sorted(remaining) if k not in ev['rejected']]
            if not cand:
                seal('exhausted')
                continue
            i = cand[0]
            u = units[i]

            if ev['chars'] + len(u['body']) > args.chars or len(ev['units']) >= args.max_units:
                seal('length')
                continue

            # 冷启动宽限：新事件开头 N 条直接认领，先把上下文攒起来
            if ev.get('grace_left', 0) > 0:
                ev['units'].append(i)
                ev['chars'] += len(u['body'])
                ev['grace_left'] -= 1
                remaining.discard(i)
                df.write(json.dumps({'kind': 'grace', 'unit': i}, ensure_ascii=False) + '\n')
                continue

            # 内容感知补足：事件头实义文字太少时，继续补到 --min-ctx 再开始判定
            if args.min_ctx > 0 and len(ev['units']) < 3:
                substance = sum(len(re.sub(r'\[[^\]]*\]', '', units[k]['body']))
                                for k in ev['units'])
                if substance < args.min_ctx:
                    ev['units'].append(i)
                    ev['chars'] += len(u['body'])
                    remaining.discard(i)
                    df.write(json.dumps({'kind': 'fill', 'unit': i,
                                         'substance': substance},
                                        ensure_ascii=False) + '\n')
                    continue

            # reply 硬捷径：目标落在当前事件内 → 直接认领
            rt = u.get('reply_to_unit')
            if rt and rt in idx and idx[rt] in ev['units']:
                ev['units'].append(i)
                ev['chars'] += len(u['body'])
                remaining.discard(i)
                hits += 1
                df.write(json.dumps({'kind': 'struct', 'unit': i},
                                    ensure_ascii=False) + '\n')
                continue

            n_call += 1
            v = verdict(llm.ask(prompt.format(context=ctx_of(ev), unit=u['text']),
                                role='small', max_tokens=8))
            df.write(json.dumps({'kind': 'judge', 'unit': i, 'v': v,
                                 'ctx': len(ctx_of(ev))}, ensure_ascii=False) + '\n')
            df.flush()
            if v == '续':
                ev['units'].append(i)
                ev['chars'] += len(u['body'])
                remaining.discard(i)
                continue

            ev['rejected'].add(i)
            claimed = None
            ahead = [k for k in sorted(remaining)
                     if k > i and k not in ev['rejected']][:max(0, args.lookahead)]
            for j in ahead:
                if ev['chars'] + len(units[j]['body']) > args.chars:
                    break
                n_call += 1
                v2 = verdict(llm.ask(prompt.format(context=ctx_of(ev),
                                                   unit=units[j]['text']),
                                     role='small', max_tokens=8))
                if v2 == '续':
                    claimed = j
                    break
                ev['rejected'].add(j)
            if claimed is not None:
                ev['units'].append(claimed)
                ev['chars'] += len(units[claimed]['body'])
                remaining.discard(claimed)
                continue
            seal('new')

    with open(ev_path, 'w', encoding='utf-8') as f:
        for n, e in enumerate(events, 1):
            us = [units[k] for k in e['units']]
            f.write(json.dumps({
                'event_id': 'U%04d' % n, 'peer': us[0]['peer'],
                'ts_first': us[0]['ts_first'], 'ts_last': us[-1]['ts_last'],
                'n_units': len(us), 'chars': sum(len(x['body']) for x in us),
                'sealed_by': e['sealed_by'],
                'participants': sorted({x['display'] for x in us}),
                'unit_ids': [x['unit_id'] for x in us],
                'texts': [x['text'] for x in us],
            }, ensure_ascii=False) + '\n')

    covered = len({k for e in events for k in e['units']})
    nu = sorted(len(e['units']) for e in events)
    sz = sorted(sum(len(units[k]['body']) for k in e['units']) for e in events)
    q = lambda a, f: a[min(len(a) - 1, int(len(a) * f))]  # noqa: E731
    print('\n=== 1.1 逐条判别结果（%.1f 分钟）===' % ((time.time() - t0) / 60))
    print('  事件 %d | 覆盖 %d/%d | 模型调用 %d（reply 捷径 %d）'
          % (len(events), covered, n_units, n_call, hits))
    print('  unit/事件 p50=%d p90=%d max=%d | 单条 %d'
          % (q(nu, .5), q(nu, .9), nu[-1], sum(1 for x in nu if x == 1)))
    print('  字数 p50=%d p90=%d max=%d' % (q(sz, .5), q(sz, .9), sz[-1]))
    print('  封口原因 %s' % dict(collections.Counter(e['sealed_by'] for e in events)))
    print('\n产物：\n  %s\n  %s' % (ev_path, dec_path))
    print('\n下一步：python stages/s1_link_merge.py --group %s --src s1_events_%s.jsonl '
          '--tag linked --max-units 20 --max-chars 2000' % (cfg.group_id, args.tag))
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
