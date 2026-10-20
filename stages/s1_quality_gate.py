#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阶段 1 · 步骤 1.4：质量闸门（纯规则，统一丢弃，放在复核之后只执行一次）。

规则
  · 丢弃 ``n_units == 1`` 的事件；
  · 计算事件「文字含量」：
        文字含量 = Σ(正文去掉媒体占位后的实义字数 + 媒体权重 × 媒体占位个数)
    媒体默认按 15 字计（一张图承载的信息量近似，来自实验设定）；
  · 文字含量 < ``--min-substance``（默认 30）的事件丢弃。

为什么要放在复核之后：早前把「剔单条」放在复核前，复核把长事件切开又产生了
新的单条碎片；统一放到最后只丢一次。

输入: ``units_at.jsonl`` + ``s1_events_<src>.jsonl``
输出: ``s1_events_<tag>.jsonl``（保留）+ ``s1_events_<tag>_dropped.jsonl``（丢弃明细）

用法::

    python stages/s1_quality_gate.py --group 123456789 \
        --src s1_events_reviewed.jsonl --tag gated --min-substance 30 --media-weight 15
"""
import argparse
import collections
import json
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)
sys.dont_write_bytecode = True   # 不在仓库里留下 __pycache__

from config import ConfigError, add_common_args, load_config  # noqa: E402
from core.ph_render import MEDIA_TOKEN_RE                     # noqa: E402


def load_jsonl(path):
    rows = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description='1.4 质量闸门（单条 + 文字含量）')
    add_common_args(ap)
    ap.add_argument('--src', default='s1_events_reviewed.jsonl')
    ap.add_argument('--tag', default='gated')
    ap.add_argument('--min-substance', type=int, default=30, help='事件文字含量下限')
    ap.add_argument('--media-weight', type=int, default=15, help='每个媒体占位折算的字数')
    args = ap.parse_args(argv)

    cfg = load_config(args, require_group=True)
    group_dir = cfg.group_dir
    units_path = os.path.join(group_dir, 'units_at.jsonl')
    src_path = os.path.join(group_dir, args.src)
    for p, step in ((units_path, '0.8'), (src_path, '1.3')):
        if not os.path.exists(p):
            print('错误：找不到 %s（请先跑完 %s）' % (p, step), file=sys.stderr)
            return 2

    units = load_jsonl(units_path)
    uid = {u['unit_id']: u for u in units}
    evs = load_jsonl(src_path)

    def ev_score(e):
        text = media = 0
        for k in e['unit_ids']:
            body = uid[k]['body']
            m = len(MEDIA_TOKEN_RE.findall(body))
            t = len(MEDIA_TOKEN_RE.sub('', body).strip())
            text += t
            media += m
        return text + args.media_weight * media, text, media

    keep, dropped = [], []
    for e in evs:
        score, txt, media = ev_score(e)
        e['_score'] = score
        if e['n_units'] == 1:
            e['_drop'] = '单条'
            dropped.append(e)
        elif score < args.min_substance:
            e['_drop'] = '文字含量%d<%d' % (score, args.min_substance)
            dropped.append(e)
        else:
            keep.append(e)

    out_path = os.path.join(group_dir, 's1_events_%s.jsonl' % args.tag)
    with open(out_path, 'w', encoding='utf-8') as f:
        for e in keep:
            e.pop('_score', None)
            e.pop('_drop', None)
            f.write(json.dumps(e, ensure_ascii=False) + '\n')
    drop_path = os.path.join(group_dir, 's1_events_%s_dropped.jsonl' % args.tag)
    with open(drop_path, 'w', encoding='utf-8') as f:
        for e in dropped:
            f.write(json.dumps({'event_id': e['event_id'], 'n_units': e['n_units'],
                                'score': e['_score'], 'reason': e['_drop'],
                                'texts': e['texts']}, ensure_ascii=False) + '\n')

    reasons = collections.Counter(e['_drop'].split('文字含量')[0] or '文字含量不足'
                                  for e in dropped)
    nu = sorted(e['n_units'] for e in keep)
    q = lambda a, f: a[min(len(a) - 1, int(len(a) * f))]  # noqa: E731
    print('质量闸门（文字含量 ≥ %d 字，媒体按 %d 字计）'
          % (args.min_substance, args.media_weight))
    print('  输入 %d → 保留 %d | 丢弃 %d（%s）'
          % (len(evs), len(keep), len(dropped), dict(reasons)))
    print('  条数分布 p50=%d p90=%d max=%d' % (q(nu, .5), q(nu, .9), nu[-1]))
    print('\n产物：\n  %s\n  %s' % (out_path, drop_path))
    print('\n下一步：python stages/s1_finalize_dataset.py --group %s '
          '--src s1_events_%s.jsonl --tag dataset' % (cfg.group_id, args.tag))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except ConfigError as e:
        print('配置错误：%s' % e, file=sys.stderr)
        sys.exit(2)
