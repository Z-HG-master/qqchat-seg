#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阶段 0 · 步骤 0.8：阶段 0 收尾（纯规则，不调用模型）。

做的事
  · 文本卫生：合并多余空格、去掉删 @ 后的行首残留 @、去掉行尾空白；
  · 丢弃正文为空且没有媒体的 unit；
  · 修复悬空的 reply 链接（目标 unit 已被丢弃时置空）。

输入/输出: ``units_at.jsonl``（原地重写）+ ``units_stats.json``

用法::

    python stages/s0_finalize.py --group 123456789
"""
import argparse
import collections
import json
import os
import re
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
    ap = argparse.ArgumentParser(description='0.8 阶段 0 收尾')
    add_common_args(ap)
    args = ap.parse_args(argv)

    cfg = load_config(args, require_group=True)
    path = cfg.group_file('units_at.jsonl')
    if not os.path.exists(path):
        print('错误：找不到 %s\n  请先运行 0.7：python stages/s0_at_prep.py --group %s'
              % (path, cfg.group_id), file=sys.stderr)
        return 2

    units = load_jsonl(path)
    print('输入 %d 个 unit' % len(units))

    kept, dropped = [], []
    for u in units:
        body = re.sub(r'[ \t]{2,}', ' ', u.get('body') or '').strip()
        body = re.sub(r'^\s*@\s*', '', body)          # 删 @ 后的行首残留
        body = re.sub(r'\s+$', '', body)
        u['body'] = body
        u['text'] = '%s: %s' % (u.get('display', ''), body) if body else '%s:' % u.get('display', '')
        if not body and not u.get('media'):
            dropped.append(u)
            continue
        kept.append(u)

    alive = {u['unit_id'] for u in kept}
    fixed = 0
    for u in kept:
        if u.get('reply_to_unit') and u['reply_to_unit'] not in alive:
            u['reply_to_unit'] = None
            fixed += 1

    with open(path, 'w', encoding='utf-8') as f:
        for u in kept:
            f.write(json.dumps(u, ensure_ascii=False) + '\n')

    st = collections.Counter()
    for u in kept:
        body = u.get('body') or ''
        if not body.strip():
            st['空正文'] += 1
        if u.get('media'):
            st['含媒体'] += 1
        if u.get('reply_to_unit'):
            st['有 reply 链接'] += 1
        if u.get('mentions'):
            st['有 @'] += 1
    print('保留 %d | 丢弃空 unit %d | 悬空链接修正 %d'
          % (len(kept), len(dropped), fixed))
    print('清理后异常：%s' % (dict(st) if st else '无'))

    stats = {
        'units': len(kept),
        'merged': sum(1 for u in kept if u.get('n_msgs', 1) > 1),
        'with_media': sum(1 for u in kept if u.get('media')),
        'with_mention': sum(1 for u in kept if u.get('mentions')),
        'with_reply_link': sum(1 for u in kept if u.get('reply_to_unit')),
        'senders': len({u.get('sender_qq') for u in kept}),
        'ts_first': min(u['ts_first'] for u in kept),
        'ts_last': max(u['ts_last'] for u in kept),
    }
    with open(cfg.group_file('units_stats.json'), 'w', encoding='utf-8') as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)

    print('\n=== 阶段 0 最终统计 ===')
    for k, v in stats.items():
        print('  %-16s%d' % (k, v) if isinstance(v, int) else '  %-16s%s' % (k, v))
    print('\n下一步：python stages/s1_segment_unit.py --group %s --tag ev --lookahead 5 --explain'
          % cfg.group_id)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except ConfigError as e:
        print('配置错误：%s' % e, file=sys.stderr)
        sys.exit(2)
