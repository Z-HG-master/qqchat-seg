#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阶段 1 · 步骤 1.5：事件级数据集导出 + 规则分类（纯规则）。

产物（都在群目录下）
  dataset_<tag>.jsonl   数据集：每行一个事件（messages 为纯文本消息列表）
  catalog_<tag>.jsonl   分类清单（类型、规模、参与者、时间跨度）
  summary_<tag>.json     汇总统计

规则分类（不调用模型）按以下优先级判定：图集型 / 多人讨论 / 双人对话 /
单人长述 / 单人零散。

用法::

    python stages/s1_finalize_dataset.py --group 123456789 \
        --src s1_events_gated.jsonl --tag dataset
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
    ap = argparse.ArgumentParser(description='1.5 事件级数据集导出与分类')
    add_common_args(ap)
    ap.add_argument('--src', default='s1_events_gated.jsonl')
    ap.add_argument('--tag', default='dataset')
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
    idx = {u['unit_id']: u for u in units}
    evs = load_jsonl(src_path)

    def classify(e):
        """规则分类（不调用模型）。"""
        ks = [idx[k] for k in e['unit_ids']]
        media = sum(1 for u in ks if any(x['kind'] == 'Pic' for x in (u.get('media') or [])))
        n = len(ks)
        text_units = sum(1 for u in ks
                         if (u.get('body') or '').strip()
                         and not (u.get('body') or '').startswith('['))
        if n and media / float(n) >= 0.5:
            return '图集型'
        if len(e['participants']) >= 3:
            return '多人讨论'
        if len(e['participants']) == 2:
            return '双人对话'
        if text_units >= max(2, n // 2):
            return '单人长述'
        return '单人零散'

    catalog, buckets = [], collections.Counter()
    for e in evs:
        t = classify(e)
        buckets[t] += 1
        catalog.append({'event_id': e['event_id'], 'type': t, 'n_units': e['n_units'],
                        'chars': e['chars'], 'n_participants': len(e['participants']),
                        'participants': e['participants'],
                        'ts_first': e['ts_first'], 'ts_last': e['ts_last'],
                        'span_min': (e['ts_last'] - e['ts_first']) // 60})

    cat_path = os.path.join(group_dir, 'catalog_%s.jsonl' % args.tag)
    with open(cat_path, 'w', encoding='utf-8') as f:
        for r in catalog:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')

    ds_path = os.path.join(group_dir, 'dataset_%s.jsonl' % args.tag)
    with open(ds_path, 'w', encoding='utf-8') as f:
        for e in evs:
            f.write(json.dumps({'event_id': e['event_id'], 'peer': e['peer'],
                                'ts_first': e['ts_first'], 'ts_last': e['ts_last'],
                                'participants': e['participants'],
                                'n_units': e['n_units'], 'chars': e['chars'],
                                'messages': e['texts']}, ensure_ascii=False) + '\n')

    nu = sorted(e['n_units'] for e in evs)
    sz = sorted(e['chars'] for e in evs)
    q = lambda a, f: a[min(len(a) - 1, int(len(a) * f))]  # noqa: E731
    total_chars = sum(e['chars'] for e in evs)
    summary = {
        'source': args.src, 'events': len(evs),
        'units_total': len(units),
        'units_covered': sum(e['n_units'] for e in evs),
        'chars_total': total_chars,
        'tokens_est': int(total_chars / 1.4),
        'n_units_p50': q(nu, .5), 'n_units_p90': q(nu, .9), 'n_units_max': nu[-1],
        'chars_p50': q(sz, .5), 'chars_p90': q(sz, .9), 'chars_max': sz[-1],
        'singletons': sum(1 for x in nu if x == 1),
        'type_buckets': dict(buckets),
    }
    summary_path = os.path.join(group_dir, 'summary_%s.json' % args.tag)
    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)

    print('=== 1.5 最终数据集 ===')
    print('  事件       %d' % summary['events'])
    covered = summary['units_covered']
    print('  覆盖 unit  %d / %d（%.1f%%）'
          % (covered, summary['units_total'],
             100.0 * covered / max(1, summary['units_total'])))
    print('  总字数     %d（≈%.1f 万 tokens）'
          % (total_chars, summary['tokens_est'] / 10000.0))
    print('  条数分布   p50=%d p90=%d max=%d'
          % (summary['n_units_p50'], summary['n_units_p90'], summary['n_units_max']))
    print('  字数分布   p50=%d p90=%d max=%d'
          % (summary['chars_p50'], summary['chars_p90'], summary['chars_max']))
    print('  类型分布   %s' % dict(buckets))
    print('\n产物：\n  %s\n  %s\n  %s' % (ds_path, cat_path, summary_path))
    print('\n下一步：python stages/s2_media_context.py --group %s --tag ctx'
          % cfg.group_id)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except ConfigError as e:
        print('配置错误：%s' % e, file=sys.stderr)
        sys.exit(2)
