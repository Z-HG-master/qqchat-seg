#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阶段 1 · 步骤 1.2：按「已确认的 @ / reply 链接」做非连续事件合并（纯规则）。

规则（重要）
  · 只认 ``link_confirmed == True`` 的链接（0.7 初审通过的）；
  · 链接两端若已在同一事件 → 无事发生；
  · 若在**不同事件** → 只合并这两个事件，中间的事件原样不动
    （因此合并结果的事件内部 unit 可以不连续，这是允许的）；
  · 合并规模上限 **≤20 条 / ≤2000 字**：超限的链接跳过而不是截断
    ——并查集的传递闭包会把整条通道焊成一个巨块，实测无上限时精确率会崩。

输入: ``units_at.jsonl`` + ``s1_events_<src>.jsonl``
输出: ``s1_events_<tag>.jsonl``

用法::

    python stages/s1_link_merge.py --group 123456789 \
        --src s1_events_ev.jsonl --tag linked --max-units 20 --max-chars 2000
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


def load_jsonl(path):
    rows = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description='1.2 按确认链接非连续合并事件')
    add_common_args(ap)
    ap.add_argument('--src', default='s1_events_ev.jsonl')
    ap.add_argument('--tag', default='linked')
    ap.add_argument('--max-units', type=int, default=20, help='合并后事件条数上限')
    ap.add_argument('--max-chars', type=int, default=2000, help='合并后事件字数上限')
    args = ap.parse_args(argv)

    cfg = load_config(args, require_group=True)
    group_dir = cfg.group_dir
    units_path = os.path.join(group_dir, 'units_at.jsonl')
    src_path = os.path.join(group_dir, args.src)
    for p, step in ((units_path, '0.8'), (src_path, '1.1')):
        if not os.path.exists(p):
            print('错误：找不到 %s（请先跑完 %s）' % (p, step), file=sys.stderr)
            return 2

    units = load_jsonl(units_path)
    units.sort(key=lambda u: (u['ts_first'], u['unit_id']))
    idx = {u['unit_id']: i for i, u in enumerate(units)}
    evs = load_jsonl(src_path)
    for e in evs:
        e['ks'] = [idx[k] for k in e['unit_ids']]
    evs.sort(key=lambda e: min(e['ks']))

    pos = {}
    for p, e in enumerate(evs):
        for k in e['ks']:
            pos[k] = p

    # ---- 并查集：只 union 链接两端事件，不碰中间事件
    parent = list(range(len(evs)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    confirmed = [u for u in units
                 if u.get('reply_to_unit') and u.get('link_confirmed')]
    cand = []
    for u in confirmed:
        t = u.get('reply_to_unit')
        if t not in idx:
            continue
        i, j = idx[t], idx[u['unit_id']]
        if j <= i:
            continue
        pa, pb = pos.get(i), pos.get(j)
        if pa is None or pb is None:
            continue
        cand.append((abs(j - i), pa, pb))
    n_link = len(cand)
    cand.sort()                     # 先合距离近的，避免远处的链接把块撑大
    grp_units = {p: len(evs[p]['ks']) for p in range(len(evs))}
    grp_chars = {p: sum(len(units[k]['body']) for k in evs[p]['ks'])
                 for p in range(len(evs))}
    n_same = n_cross = n_block = 0
    for _, pa, pb in cand:
        ra, rb = find(pa), find(pb)
        if ra == rb:
            n_same += 1
            continue
        merged_units = grp_units[ra] + grp_units[rb]
        merged_chars = grp_chars[ra] + grp_chars[rb]
        if merged_units > args.max_units or merged_chars > args.max_chars:
            n_block += 1
            continue
        parent[rb] = ra
        grp_units[ra] = merged_units
        grp_chars[ra] = merged_chars
        n_cross += 1

    groups = collections.defaultdict(list)
    for p in range(len(evs)):
        groups[find(p)].append(p)

    result = []
    n_multi = 0
    for root, ps in groups.items():
        if len(ps) > 1:
            n_multi += 1
        ks = sorted(k for p in ps for k in evs[p]['ks'])
        us = [units[k] for k in ks]
        result.append({
            'event_id': '', 'peer': us[0]['peer'],
            'ts_first': us[0]['ts_first'], 'ts_last': us[-1]['ts_last'],
            'n_units': len(us), 'chars': sum(len(x['body']) for x in us),
            'contiguous': (ks == list(range(ks[0], ks[0] + len(ks)))),
            'merged_events': len(ps),
            'participants': sorted({x['display'] for x in us}),
            'unit_ids': [x['unit_id'] for x in us],
            'texts': [x['text'] for x in us],
        })
    result.sort(key=lambda e: idx[e['unit_ids'][0]])
    for n, e in enumerate(result, 1):
        e['event_id'] = 'L%04d' % n

    out_path = os.path.join(group_dir, 's1_events_%s.jsonl' % args.tag)
    with open(out_path, 'w', encoding='utf-8') as f:
        for e in result:
            f.write(json.dumps(e, ensure_ascii=False) + '\n')

    nu = sorted(e['n_units'] for e in result)
    sz = sorted(e['chars'] for e in result)
    q = lambda a, f: a[min(len(a) - 1, int(len(a) * f))]  # noqa: E731
    print('候选链接 %d 条 | 实际合并 %d 条（同事件 %d / 超限跳过 %d）'
          % (n_link, n_cross, n_same, n_block))
    print('  合并后事件 %d → %d（其中由多个事件合并而成 %d 个）'
          % (len(evs), len(result), n_multi))
    print('  条数 p50=%d p90=%d max=%d | 字数 p50=%d p90=%d max=%d'
          % (q(nu, .5), q(nu, .9), nu[-1], q(sz, .5), q(sz, .9), sz[-1]))
    print('  非连续事件（内部 unit 有跳跃）%d 个'
          % sum(1 for e in result if not e['contiguous']))
    print('\n产物：%s' % out_path)
    print('\n下一步：python stages/s1_review.py --group %s --src s1_events_%s.jsonl '
          '--tag reviewed --min 10' % (cfg.group_id, args.tag))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except ConfigError as e:
        print('配置错误：%s' % e, file=sys.stderr)
        sys.exit(2)
