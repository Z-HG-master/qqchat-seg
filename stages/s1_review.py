#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阶段 1 · 步骤 1.3：长事件复核（只切不合）。

9B 主切分故意放宽（宁合勿分），这一步让大模型对长事件补切：

  · 只处理 ``n_units > --min`` 的事件（默认 >10 条）；
  · 判据是「这段对话是否逻辑顺畅、有没有话题转折」；
  · 护栏：转折点 ≤1 或 ≥N 视为无效（按顺畅处理）；同一条只采纳最靠前的一处；
  · 递归：切开后某段若仍超阈值，再检一次（最多 3 层）。

⚠️ 实测结论：**基础切分已经较好时，这一步会降低分数**（详见 docs/validation-report.md）。
   默认配置保留它是因为它针对长事件、成本可控；如果你的主切分已经很好，可以跳过 1.3。

输入: ``units_at.jsonl`` + ``s1_events_<src>.jsonl``
输出: ``s1_events_<tag>.jsonl`` + ``s1_review_<tag>.jsonl``（判定日志）

用法::

    python stages/s1_review.py --group 123456789 \
        --src s1_events_linked.jsonl --tag reviewed --min 10
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
PROMPT = ('下面是社交群聊里一段连续的消息（按时间编号）：\n\n{lines}\n\n'
          '请判断：这段对话是不是一段**逻辑顺畅具有强关联性**的、'
          '适合作为 llm 训练输入数据源的对话？\n\n'
          '注意区分两种情况：\n'
          '- 顺畅：话题在自然推进，每一段前消息间具有内容主题关联性\n'
          '- 有转折：出现前后文间的话题切换，转移到一件**关联性弱**的新主题上，'
          '或前后文关注话题跨度过大\n\n'
          '如果你认为应该切分，请说明：\n'
          '- 从第几条开始是新话题？\n'
          '- 为什么这里算话题切换（前后文说的对象/事件有什么不同）？\n\n'
          '如果你认为不应该切分，请说明：\n'
          '- 这段里所有消息的通用主题是什么？\n'
          '- 如果存在话题重点的转移，那么为什么你认为这段消息仍然是同一件事的延续？\n\n'
          '只输出三行：\n'
          '结论: 顺畅 或 有转折\n'
          '转折: <编号，或"无">\n'
          '理由: <两句话说明>')

# --- 提示词覆盖点（--prompts-override，见 core/prompts.py） ---
from core.prompts import get as _prompt_get   # noqa: E402
PROMPT = _prompt_get('review.event', PROMPT)
MAX_TOKENS = 400
MAX_DEPTH = 3
# --------------------------------------------------------------------------


def load_jsonl(path):
    rows = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description='1.3 长事件复核（只切不合）')
    add_common_args(ap)
    ap.add_argument('--model', default=None, help='覆盖 config 的 model_big（可选）')
    ap.add_argument('--src', default='s1_events_linked.jsonl')
    ap.add_argument('--tag', default='reviewed')
    ap.add_argument('--min', type=int, default=10, help='超过多少条才复核（默认 >10）')
    args = ap.parse_args(argv)

    cfg = load_config(args, require_group=True)
    if args.model:
        cfg.raw['model_big'] = args.model
    group_dir = cfg.group_dir
    units_path = os.path.join(group_dir, 'units_at.jsonl')
    src_path = os.path.join(group_dir, args.src)
    for p, step in ((units_path, '0.8'), (src_path, '1.2')):
        if not os.path.exists(p):
            print('错误：找不到 %s（请先跑完 %s）' % (p, step), file=sys.stderr)
            return 2

    llm = LLM(cfg)
    if not llm.mock:
        ok, msg = llm.check('big')
        if not ok:
            print('无法开始 1.3：%s' % msg, file=sys.stderr)
            return 2
        print(msg)

    units = load_jsonl(units_path)
    units.sort(key=lambda u: (u['ts_first'], u['unit_id']))
    idx = {u['unit_id']: i for i, u in enumerate(units)}
    evs = load_jsonl(src_path)
    log_path = os.path.join(group_dir, 's1_review_%s.jsonl' % args.tag)
    logf = open(log_path, 'w', encoding='utf-8')

    def review(ks, depth=0):
        """返回切分后的 list[list[unit_id]]。"""
        n = len(ks)
        if n <= args.min or depth >= MAX_DEPTH:
            return [ks]
        lines = '\n'.join('%d. %s' % (j, units[k]['body'][:160])
                          for j, k in enumerate(ks, 1))
        out = llm.ask(PROMPT.format(lines=lines), role='big', max_tokens=MAX_TOKENS)
        m = re.search(r'转折\s*[:：]\s*([0-9,\s]+)', out)
        cuts = [int(x) for x in re.findall(r'\d+', m.group(1))] if m else []
        cuts = sorted({c for c in cuts if 1 < c < n})     # 护栏：1 和 n 视为无效
        why = re.search(r'理由\s*[:：]\s*([\s\S]*)', out)
        logf.write(json.dumps({'n': n, 'depth': depth, 'out': out[:60], 'cuts': cuts,
                               'why': (why.group(1).strip()[:300] if why else ''),
                               'first': ks[0]}, ensure_ascii=False) + '\n')
        logf.flush()
        if not cuts:
            return [ks]
        pieces, prev = [], 0
        for c in cuts:
            pieces.append(ks[prev:c - 1])
            prev = c - 1
        pieces.append(ks[prev:])
        result = []
        for p in pieces:
            result += review(p, depth + 1)
        return result

    t0 = time.time()
    result, n_split = [], 0
    for e in evs:
        ks = [idx[k] for k in e['unit_ids']]
        pieces = review(ks)
        if len(pieces) > 1:
            n_split += 1
        for p in pieces:
            us = [units[k] for k in p]
            result.append({
                'event_id': '', 'peer': us[0]['peer'],
                'ts_first': us[0]['ts_first'], 'ts_last': us[-1]['ts_last'],
                'n_units': len(us), 'chars': sum(len(u['body']) for u in us),
                'participants': sorted({u['display'] for u in us}),
                'unit_ids': [u['unit_id'] for u in us],
                'texts': [u['text'] for u in us],
            })
    for n, e in enumerate(result, 1):
        e['event_id'] = 'V%04d' % n

    out_path = os.path.join(group_dir, 's1_events_%s.jsonl' % args.tag)
    with open(out_path, 'w', encoding='utf-8') as f:
        for e in result:
            f.write(json.dumps(e, ensure_ascii=False) + '\n')
    logf.close()

    nu = sorted(e['n_units'] for e in result)
    sz = sorted(e['chars'] for e in result)
    q = lambda a, f: a[min(len(a) - 1, int(len(a) * f))]  # noqa: E731
    print('\n=== 1.3 长事件复核（%.1f 分钟）===' % ((time.time() - t0) / 60))
    print('  输入 %d 事件 → 输出 %d 事件（被切开的 %d 个）'
          % (len(evs), len(result), n_split))
    print('  unit/事件 p50=%d p90=%d max=%d | 单条 %d'
          % (q(nu, .5), q(nu, .9), nu[-1], sum(1 for x in nu if x == 1)))
    print('  字数 p50=%d p90=%d max=%d' % (q(sz, .5), q(sz, .9), sz[-1]))
    print('\n产物：\n  %s\n  %s' % (out_path, log_path))
    print('\n下一步：python stages/s1_quality_gate.py --group %s --src s1_events_%s.jsonl '
          '--tag gated --min-substance 30 --media-weight 15' % (cfg.group_id, args.tag))
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
