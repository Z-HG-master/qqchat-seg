#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阶段 0 · 步骤 0.6：连发块合并。

同一个人连续发言、中间无他人插话 → 交给模型判断能切成几个语义块 →
把每个块合并成一条「伪消息」（unit）。这样后续事件判别看到的是完整语义，
而不是被拆碎的半句话。

输入: ``messages_media.jsonl``
输出: ``units.jsonl``（每个 unit = 语义单一的一条伪消息）+ ``run_split.jsonl``（分割决策日志）

用法::

    python stages/s0_merge_runs.py --group 123456789
    python stages/s0_merge_runs.py --group 123456789 --limit 10
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
SPLIT_PROMPT = ('下面是同一个人的连续发言（带序号）。'
                '判断这些发言能分成几个语义块（每块内部语义关联性强，主题统一；不得漏行、不得重叠）。\n'
                '只输出分割区间，格式如：1-2,3,4-5\n不要解释。\n\n{lines}')

# --- 提示词覆盖点（--prompts-override，见 core/prompts.py） ---
from core.prompts import get as _prompt_get   # noqa: E402
SPLIT_PROMPT = _prompt_get('merge.runs', SPLIT_PROMPT)
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


def parse_ranges(s, n):
    """解析 ``1-2,3,4-5`` 形式的区间；不合法返回 None（调用方回退成整块）。"""
    s = (s or '').replace('，', ',').replace('、', ',').replace(' ', '')
    out = []
    for tok in s.split(','):
        if not tok:
            continue
        m = re.fullmatch(r'(\d+)(?:-(\d+))?', tok)
        if not m:
            return None
        a = int(m.group(1))
        b = int(m.group(2) or a)
        if not (1 <= a <= b <= n):
            return None
        out.append((a, b))
    if not out:
        return None
    out.sort()
    if out[0][0] != 1 or out[-1][1] != n:
        return None
    for (a1, b1), (a2, b2) in zip(out, out[1:]):
        if a2 != b1 + 1:
            return None
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description='0.6 连发块合并')
    add_common_args(ap)
    ap.add_argument('--limit', type=int, default=0, help='只处理前 N 个多消息 run（0=全部）')
    ap.add_argument('--model', default=None, help='覆盖 config 的 model_small（可选）')
    args = ap.parse_args(argv)

    cfg = load_config(args, require_group=True)
    if args.model:
        cfg.raw['model_small'] = args.model
    group_dir = cfg.group_dir
    src_path = os.path.join(group_dir, 'messages_media.jsonl')
    if not os.path.exists(src_path):
        print('错误：找不到 %s\n  请先运行 0.5：python stages/s0_media.py --group %s'
              % (src_path, cfg.group_id), file=sys.stderr)
        return 2

    peer = cfg.group_id
    llm = LLM(cfg)
    if not llm.mock:
        ok, msg = llm.check('small')
        if not ok:
            print('无法开始 0.6：%s' % msg, file=sys.stderr)
            return 2
        print(msg)

    msgs = load_jsonl(src_path)
    msgs.sort(key=lambda m: (m.get('ts', 0), m.get('seq', 0)))
    print('输入消息 %d 条' % len(msgs))

    # ---- 切「同人连发」块
    runs = []
    i = 0
    while i < len(msgs):
        j = i
        while j + 1 < len(msgs) and msgs[j + 1].get('sender_qq') == msgs[i].get('sender_qq'):
            j += 1
        runs.append(msgs[i:j + 1])
        i = j + 1
    multi = [r for r in runs if len(r) > 1]
    print('run 总数 %d（其中 ≥2 条 %d 个）' % (len(runs), len(multi)))
    if args.limit:
        multi = multi[:args.limit]

    t0 = time.time()
    decisions = {}
    ok_n = fallback = 0
    with open(os.path.join(group_dir, 'run_split.jsonl'), 'w', encoding='utf-8') as split_log:
        for k, run in enumerate(multi, 1):
            lines = '\n'.join('%d. %s' % (n, m.get('body', '')) for n, m in enumerate(run, 1))
            out = llm.ask(SPLIT_PROMPT.format(lines=lines), role='small', max_tokens=120)
            rg = parse_ranges(out, len(run))
            if rg is None:
                rg = [(1, len(run))]
                fallback += 1
                tag = 'fallback'
            else:
                ok_n += 1
                tag = 'ok'
            key = run[0].get('msg_uid')
            decisions[key] = rg
            split_log.write(json.dumps({'first_uid': key, 'n': len(run), 'raw': out,
                                        'ranges': rg, 'tag': tag},
                                       ensure_ascii=False) + '\n')
            split_log.flush()
            if k % 100 == 0:
                el = time.time() - t0
                print('  %d/%d | %.1f min | %.2f run/s | fallback %d'
                      % (k, len(multi), el / 60, k / max(el, 1e-6), fallback), flush=True)
    print('\n0.6 分割完成：成功 %d | 回退单块 %d | 用时 %.1f 分钟'
          % (ok_n, fallback, (time.time() - t0) / 60))

    # ---- 组装 unit
    units = []
    for run in runs:
        rg = decisions.get(run[0].get('msg_uid')) or [(1, len(run))]
        for a, b in rg:
            chunk = run[a - 1:b]
            uid = '%s_%s_%s_%d' % (peer, chunk[0].get('ts'), chunk[0].get('sender_qq'),
                                   len(units) + 1)
            body = ' '.join(m.get('body', '') for m in chunk if (m.get('body') or '').strip())
            media, men, rep = [], [], None
            for m in chunk:
                media += (m.get('media') or [])
                men += (m.get('mentions') or [])
                if rep is None and m.get('reply_to'):
                    rep = m['reply_to']
            units.append({
                'unit_id': uid, 'peer': peer,
                'sender_qq': chunk[0].get('sender_qq'),
                'display': chunk[0].get('display', ''),
                'ts_first': chunk[0].get('ts'), 'ts_last': chunk[-1].get('ts'),
                'n_msgs': len(chunk), 'body': body,
                'text': '%s: %s' % (chunk[0].get('display', ''), body),
                'msg_uids': [m.get('msg_uid') for m in chunk], 'media': media,
                'mentions': men, 'reply_target_qq': (rep or {}).get('qq'),
                'reply_to_unit': None, 'at_action': None,
            })

    out_path = os.path.join(group_dir, 'units.jsonl')
    with open(out_path, 'w', encoding='utf-8') as f:
        for u in units:
            f.write(json.dumps(u, ensure_ascii=False) + '\n')

    st = collections.Counter()
    st['units'] = len(units)
    st['merged'] = sum(1 for u in units if u['n_msgs'] > 1)
    st['with_media'] = sum(1 for u in units if u['media'])
    st['with_mention'] = sum(1 for u in units if u['mentions'])
    st['with_reply'] = sum(1 for u in units if u['reply_target_qq'])
    print('\n=== 0.6 结果 ===')
    print('  unit 总数 %d（其中合并块 %d）' % (st['units'], st['merged']))
    print('  含媒体 unit %d | 含@ %d | 含真 reply %d'
          % (st['with_media'], st['with_mention'], st['with_reply']))
    lengths = sorted(len(u['body']) for u in units)
    q = lambda f: lengths[min(len(lengths) - 1, int(len(lengths) * f))]  # noqa: E731
    print('  正文长度 p50=%d p90=%d p99=%d max=%d'
          % (q(.5), q(.9), q(.99), lengths[-1]))
    print('\n产物：%s' % out_path)
    print('\n下一步：python stages/s0_at_prep.py --group %s' % peer)
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
