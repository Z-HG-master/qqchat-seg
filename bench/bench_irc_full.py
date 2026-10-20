#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""在 Ubuntu IRC 上验证：27B 复核长事件能否改善漏切

流程：
  1. 用滑动窗口还原每个通道的连续消息流（窗口步长 1 条）
  2. 读入 9B 逐条判别结果 → 建事件
  3. 对 >review-min 条的事件做 27B 复核（我们的生产提示词）→ 追加切点
  4. 按消息级边界算 P/R/F1，并单独统计复核"新增加的切点"里有多少是对的

用法：python bench_irc_full.py --review-min 10 [--limit-channels N]
"""
import pandas as pd, numpy as np, json, sys, io, os, re, time, argparse, urllib.request

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.dont_write_bytecode = True
BASE = 'http://127.0.0.1:1234/v1'
BENCH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
M_BIG = 'qwen/qwen3.8-27b'

ap = argparse.ArgumentParser()
ap.add_argument('--review-min', type=int, default=10)
ap.add_argument('--limit-channels', type=int, default=0)
ap.add_argument('--preds', default='ubuntu_irc_preds_qwen3.5-9b.json')
ap.add_argument('--data-dir', default=None,
                help='数据集目录（默认 bench/data；需先下载 ubuntu_irc_dev.parquet / tiage_test.json）')
args = ap.parse_args()
if args.data_dir:
    BENCH = os.path.abspath(args.data_dir)

REV_PROMPT = ('下面是 QQ 群里一段连续的消息（按时间编号）：\n\n{lines}\n\n'
              '请判断：这段对话是不是一段**逻辑顺畅具有强关联性**的、适合作为 llm 训练输入数据源的对话？\n\n'
              '注意区分两种情况：\n- 顺畅：话题在自然推进，每一段前消息间具有内容主题关联性\n'
              '- 有转折：出现前后文间的话题切换，转移到一件**关联性弱**的新主题上，或前后文关注话题跨度过大\n\n'
              '如果你认为应该切分，请说明：\n- 从第几条开始是新话题？\n'
              '- 为什么这里算话题切换（前后文说的对象/事件有什么不同）？\n\n'
              '如果你不应该切分，请说明：\n- 这段里所有消息的通用主题是什么？\n'
              '- 如果存在话题重点的转移，那么为什么你认为这段消息仍然是同一件事的延续？\n\n'
              '只输出三行：\n结论: 顺畅 或 有转折\n转折: <编号，或"无">\n理由: <两句话说明>')


def ask(p, mt=400, tries=2):
    body = {'model': M_BIG, 'messages': [{'role': 'user', 'content': p}],
            'temperature': 0.0, 'max_tokens': mt, 'stream': False, 'reasoning_effort': 'none'}
    data = json.dumps(body).encode('utf-8')
    for a in range(tries):
        try:
            req = urllib.request.Request(BASE + '/chat/completions', data=data,
                                         headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=300) as r:
                d = json.loads(r.read().decode('utf-8'))
            return (d['choices'][0]['message'].get('content') or '').strip()
        except Exception:
            time.sleep(1.0 * (a + 1))
    return ''


def parse_cuts(out, n):
    c = re.search(r'结论\s*[:：]\s*(\S+)', out)
    cu = re.search(r'转折\s*[:：]\s*([^\n]*)', out)
    if not c or '转折' not in c.group(1) or not cu:
        return []
    nums = [int(x) for x in re.findall(r'\d+', cu.group(1))]
    return sorted({x for x in nums if 1 < x <= n})


CALLS = [0]
MIN = None

def review_abs(msgs, lo, hi, depth=0, max_depth=3):
    """在 msgs[lo:hi) 上复核，返回需要追加的**绝对**切点列表"""
    n = hi - lo
    if n <= MIN or depth >= max_depth:
        return []
    lines = '\n'.join(f'{j}. {msgs[lo + j - 1][:160]}' for j in range(1, n + 1))
    out = ask(REV_PROMPT.format(lines=lines))
    CALLS[0] += 1
    cuts = parse_cuts(out, n)                 # 1..n（相对段首）
    if not cuts or len(cuts) >= n - 1:
        return []
    bounds = [lo] + [lo + c for c in cuts] + [hi]
    res = []
    for i in range(len(bounds) - 1):
        s0, e0 = bounds[i], bounds[i + 1]
        if i > 0:
            res.append(s0)                    # 该切点本身
        res += review_abs(msgs, s0, e0, depth + 1, max_depth)
    return res


# ---------- 还原消息流 ----------
df = pd.read_parquet(os.path.join(BENCH, 'ubuntu_irc_dev.parquet'))


def as_list(v):
    return list(v) if isinstance(v, (np.ndarray, list, tuple)) else []


preds = json.load(open(os.path.join(BENCH, args.preds), encoding='utf-8'))
assert len(preds) == len(df)
df = df.copy()
df['pred_new'] = [bool(p['pred_new']) for p in preds]
df['gold_new'] = [bool(p['gold_new']) for p in preds]
df['last_msg'] = [as_list(r['messages'])[-1] for _, r in df.iterrows()]

channels = sorted(df['canon_name'].unique())
if args.limit_channels:
    channels = channels[:args.limit_channels]
MIN = args.review_min
print(f'通道 {len(channels)} | 复核门槛 >{args.review_min} 条 | 模型 {M_BIG}')

t0 = time.time()
tp9 = fp9 = fn9 = 0
tpB = fpB = fnB = 0
added = added_ok = 0
for ch in channels:
    sub = df[df['canon_name'] == ch].sort_values('id')
    ids = sub['id'].tolist()
    msgs = sub['last_msg'].tolist()
    pred = sub['pred_new'].tolist()
    gold = sub['gold_new'].tolist()
    n = len(msgs)
    # 9B 边界（位置 i 表示第 i 条消息开始新事件，i>=1）
    b9 = [i for i in range(1, n) if pred[i]]
    g = [i for i in range(1, n) if gold[i]]
    tp9 += len(set(b9) & set(g)); fp9 += len(set(b9) - set(g)); fn9 += len(set(g) - set(b9))

    # 建事件
    segs, prev = [], 0
    for i in b9:
        segs.append((prev, i)); prev = i
    segs.append((prev, n))
    bB = set(b9)
    for a, b in segs:
        for pos in review_abs(msgs, a, b):
            if 0 < pos < n:
                if pos not in bB:
                    added += 1
                    if pos in g:
                        added_ok += 1
                bB.add(pos)
    tpb = len(bB & set(g)); fpb = len(bB - set(g)); fnb = len(set(g) - bB)
    tpB += tpb; fpB += fpb; fnB += fnb
    print(f'  {ch}: {n} 条 | 金标 {len(g)} | 9B 边界 {len(b9)} | 复核后 {len(bB)} | '
          f'累计 {(time.time()-t0)/60:.1f} min', flush=True)


def pr(k_tp, k_fp, k_fn):
    P = k_tp / max(1, k_tp + k_fp); R = k_tp / max(1, k_tp + k_fn)
    return P, R, 2 * P * R / max(1e-9, P + R)


print(f'\n=== Ubuntu IRC 完整流程（{len(channels)} 通道）===')
P, R, F = pr(tp9, fp9, fn9)
print(f'  9B 逐条            : P={P:.3f} R={R:.3f} **F1={F:.3f}**  (TP={tp9} FP={fp9} FN={fn9})')
P, R, F = pr(tpB, fpB, fnB)
print(f'  9B + 27B 复核       : P={P:.3f} R={R:.3f} **F1={F:.3f}**  (TP={tpB} FP={fpB} FN={fnB})')
print(f'  复核新增切点 {added} 个，其中正确的 {added_ok} 个（精确率 {100*added_ok/max(1,added):.1f}%）')
print(f'  27B 调用 {CALLS[0]} 次 | 用时 {(time.time()-t0)/60:.1f} 分钟')
