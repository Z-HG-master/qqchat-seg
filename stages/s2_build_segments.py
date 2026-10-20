#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阶段 2 · 步骤 2.2：回填上下文图片描述 → 纯文本连续片段（纯规则）。

回填规则
  · 每张图片的占位符替换为 ``[图片：<2.1 的新描述>]``；
  · 没拿到新描述的图片保留阶段 0 的旧描述；
  · 表情包保持阶段 0 的 ``[表情：<情绪>]``。

输入: ``s1_events_gated.jsonl`` + ``media_context_<ctx-tag>.jsonl`` + ``units_at.jsonl``
输出: ``segments_<ctx-tag>.jsonl`` —— 每个事件一条，messages 为纯文本消息列表（最终成果）

用法::

    python stages/s2_build_segments.py --group 123456789 --ctx-tag ctx --tag segments
"""
import argparse
import json
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)
sys.dont_write_bytecode = True   # 不在仓库里留下 __pycache__

from config import ConfigError, add_common_args, load_config  # noqa: E402
from core.ph_render import parse_placeholders                 # noqa: E402


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
    ap = argparse.ArgumentParser(description='2.2 回填描述生成纯文本片段')
    add_common_args(ap)
    ap.add_argument('--src', default='s1_events_gated.jsonl')
    ap.add_argument('--ctx-tag', default='ctx')
    ap.add_argument('--tag', default='segments')
    args = ap.parse_args(argv)

    cfg = load_config(args, require_group=True)
    group_dir = cfg.group_dir
    units_path = os.path.join(group_dir, 'units_at.jsonl')
    src_path = os.path.join(group_dir, args.src)
    for p, step in ((units_path, '0.8'), (src_path, '1.4')):
        if not os.path.exists(p):
            print('错误：找不到 %s（请先跑完 %s）' % (p, step), file=sys.stderr)
            return 2

    units = load_jsonl(units_path)
    uid = {u['unit_id']: u for u in units}
    evs = load_jsonl(src_path)

    caps = {}
    ctx_path = os.path.join(group_dir, 'media_context_%s.jsonl' % args.ctx_tag)
    for d in load_jsonl(ctx_path):
        if d.get('desc'):
            caps[(d.get('event_id'), d.get('unit_id'), d.get('occ'))] = d
    print('上下文描述 %d 条' % len(caps))

    n_repl = n_keep = 0
    out_rows = []
    for e in evs:
        msgs = []
        for i, k in enumerate(e['unit_ids']):
            u = uid[k]
            body = u.get('body') or ''
            phs = parse_placeholders(body)
            if not phs:
                msgs.append('%s: %s' % (u.get('display', ''), body))
                continue
            pieces, last, occ = [], 0, 0
            for kind, raw, a, b in phs:
                pieces.append(body[last:a])
                if kind == '图片':
                    c = caps.get((e['event_id'], k, occ))
                    if c:
                        pieces.append('[图片：%s]' % c['desc'])
                        n_repl += 1
                    else:
                        pieces.append(raw)
                        n_keep += 1
                    occ += 1
                else:
                    pieces.append(raw)
                last = b
            pieces.append(body[last:])
            msgs.append(('%s: %s' % (u.get('display', ''), ''.join(pieces))).strip())
        out_rows.append({
            'event_id': e['event_id'], 'peer': e['peer'],
            'ts_first': e['ts_first'], 'ts_last': e['ts_last'],
            'participants': e['participants'], 'n_units': e['n_units'],
            'n_msgs': len(msgs),
            'chars': sum(len(m) for m in msgs),
            'messages': msgs,
        })

    out_path = os.path.join(group_dir, '%s_%s.jsonl' % (args.tag, args.ctx_tag))
    with open(out_path, 'w', encoding='utf-8') as f:
        for r in out_rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')

    sz = sorted(r['chars'] for r in out_rows)
    q = lambda a, fr: a[min(len(a) - 1, int(len(a) * fr))]  # noqa: E731
    n_desc = sum(1 for r in out_rows for m in r['messages'] if '[图片：' in m)
    print('回填：新描述 %d 处 | 保留旧占位 %d 处' % (n_repl, n_keep))
    print('事件 %d | 消息 %d' % (len(out_rows), sum(r['n_msgs'] for r in out_rows)))
    print('事件字数 p50=%d p90=%d p99=%d max=%d'
          % (q(sz, .5), q(sz, .9), q(sz, .99), sz[-1]))
    print('含图片描述的消息 %d 条' % n_desc)
    print('\n=== 最终成果 ===\n  %s' % out_path)
    print('  每行一个事件，messages 为纯文本消息列表，可直接用于后续训练/检索。')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except ConfigError as e:
        print('配置错误：%s' % e, file=sys.stderr)
        sys.exit(2)
