#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""【基准复现脚本，非生产代码】在 Ubuntu IRC 上验证 0.7 语义初审 + 1.2 非连续合并

说明
  · 这是为 IRC 数据格式写的**兼容测试**，不修改生产脚本（s1_* / s2_* / pipeline.py）
  · 生产流程仍然面向 QQ 群：@ / reply 边来自 QQ 协议字段

流程
  1. 逐通道还原消息流 + 回复图（parents 即 reply 边）
  2. 阶段 0.7：对每条 reply 边跑我们的相关性初审 → 确认 / 驳回
  3. 阶段 1.1：复用已有的 9B 逐条判别结果 → 连续事件
  4. 阶段 1.2：按确认链接做并查集合并（非连续，≤20 条 / ≤2000 字）
  5. 评估：与"金标对话串"（回复图连通分量）的 pairwise F1 —— 合并前 vs 合并后

用法：python bench_irc_merge.py --model qwen/qwen3.5-9b [--channels 10]
"""
import pandas as pd, numpy as np, json, sys, io, os, re, time, argparse, urllib.request

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.dont_write_bytecode = True
BASE = 'http://127.0.0.1:1234/v1'
BENCH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')

ap = argparse.ArgumentParser()
ap.add_argument('--model', default='qwen/qwen3.5-9b')
ap.add_argument('--channels', type=int, default=0)
ap.add_argument('--max-units', type=int, default=20)
ap.add_argument('--max-chars', type=int, default=2000)
ap.add_argument('--preds', default='ubuntu_irc_preds_qwen3.5-9b.json')
ap.add_argument('--data-dir', default=None,
                help='数据集目录（默认 bench/data；需先下载 ubuntu_irc_dev.parquet / tiage_test.json）')
args = ap.parse_args()
if args.data_dir:
    BENCH = os.path.abspath(args.data_dir)

# ---- 生产流程里的 0.7 提示词（原样搬用，只把"QQ群"字样换成中性描述以适配英文数据）----
LINK_PROMPT = ('你在处理聊天记录相关性判别。下面是【被@/回复的人此前说的一条消息】和【现在这条 @ /回复了 TA 的消息】。\n'
               '判断【现在这条】是否在与 TA 此前的消息存在关联性。\n\n'
               '【TA 此前说】\n{a}\n\n'
               '【现在这条】\n{b}\n\n'
               '判「YES」的情况：\n'
               '- 在回应 / 追问 / 补充上面那条消息里的人、事、物\n'
               '- 短反应（"？""确实""草""哈哈哈"等等）且能看出是在回应上面那句，或是对其内容表示情绪\n'
               '- 由上一条延续出的相关话题\n\n'
               '判「NO」的情况：\n'
               '- 转向讨论了另一件相关性弱的事：换了话题、换了对象等等\n'
               '- 不满足任何「YES」条件时\n\n'
               '只输出 YES 或 NO，不要解释。')


def ask(p, mt=8, tries=2):
    body = {'model': args.model, 'messages': [{'role': 'user', 'content': p}],
            'temperature': 0.0, 'max_tokens': mt, 'stream': False, 'reasoning_effort': 'none'}
    data = json.dumps(body).encode('utf-8')
    for a in range(tries):
        try:
            req = urllib.request.Request(BASE + '/chat/completions', data=data,
                                         headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=120) as r:
                d = json.loads(r.read().decode('utf-8'))
            t = (d['choices'][0]['message'].get('content') or '').strip().upper()
            return t.startswith('Y') or t.startswith('是')
        except Exception:
            time.sleep(0.8 * (a + 1))
    return False


def as_list(v):
    return list(v) if isinstance(v, (np.ndarray, list, tuple)) else []


class UF:
    def __init__(self, n):
        self.p = list(range(n))

    def f(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def u(self, a, b):
        ra, rb = self.f(a), self.f(b)
        if ra != rb:
            self.p[rb] = ra


df = pd.read_parquet(os.path.join(BENCH, 'ubuntu_irc_dev.parquet'))
preds = json.load(open(os.path.join(BENCH, args.preds), encoding='utf-8'))
assert len(preds) == len(df)
df = df.copy()
df['pred_new'] = [bool(p['pred_new']) for p in preds]
df['last_msg'] = [as_list(r['messages'])[-1] for _, r in df.iterrows()]
CACHE_PATH = os.path.join(BENCH, 'irc_link_cache.json')
CACHE = json.load(open(CACHE_PATH, encoding='utf-8')) if os.path.exists(CACHE_PATH) else {}
channels = sorted(df['canon_name'].unique())
if args.channels:
    channels = channels[:args.channels]

print(f'模型 {args.model} | 通道 {len(channels)} | 合并上限 ≤{args.max_units} 条 / ≤{args.max_chars} 字')
t0 = time.time()
total_links = confirmed = 0
agg = {}

for ch in channels:
    sub = df[df['canon_name'] == ch].sort_values('id')
    ids = sub['id'].tolist()
    texts = sub['last_msg'].tolist()
    predn = sub['pred_new'].tolist()
    parent = {}
    for _, r in sub.iterrows():
        i = int(r['id']); ps = [int(x) for x in as_list(r['parents'])]
        parent[i] = i if (not ps or i in ps) else ps[0]
    pos_of = {i: k for k, i in enumerate(ids)}
    n = len(ids)

    # ---- 金标对话串：回复图连通分量 ----
    uf_gold = UF(n)
    for i in ids:
        p = parent[i]
        if p != i and p in pos_of:
            uf_gold.u(pos_of[i], pos_of[p])
    gold = [uf_gold.f(k) for k in range(n)]

    # ---- 0.7：逐条 reply 边做相关性初审 ----
    link_conf = []
    for i in ids:
        p = parent[i]
        if p == i or p not in pos_of:
            continue
        total_links += 1
        key = f'{ch}|{i}|{p}'
        if key in CACHE:
            ok = CACHE[key]
        else:
            a = texts[pos_of[p]][:300]
            b = texts[pos_of[i]][:300]
            ok = ask(LINK_PROMPT.format(a=a, b=b))
            CACHE[key] = ok
        if ok:
            confirmed += 1
            link_conf.append((pos_of[i], pos_of[p]))

    # ---- 1.1 事件（连续切分）----
    bounds = [k for k in range(1, n) if predn[k]]
    uf_ev = UF(n)
    prev = 0
    for b in bounds:
        for k in range(prev + 1, b):
            uf_ev.u(prev, k)
        prev = b
    for k in range(prev + 1, n):
        uf_ev.u(prev, k)
    ev_before = [uf_ev.f(k) for k in range(n)]

    # ---- 1.2 非连续合并（按确认链接，带规模上限）----
    size = {}
    chars = {}
    for k in range(n):
        r = uf_ev.f(k)
        size[r] = size.get(r, 0) + 1
        chars[r] = chars.get(r, 0) + len(texts[k])
    merged_cnt = 0
    for a_idx, b_idx in link_conf:
        ra, rb = uf_ev.f(a_idx), uf_ev.f(b_idx)
        if ra == rb:
            continue
        if size[ra] + size[rb] > args.max_units or chars[ra] + chars[rb] > args.max_chars:
            continue
        uf_ev.u(ra, rb)
        nr = uf_ev.f(ra)
        size[nr] = size.get(ra, 0) + size.get(rb, 0)
        chars[nr] = chars.get(ra, 0) + chars.get(rb, 0)
        merged_cnt += 1
    ev_after = [uf_ev.f(k) for k in range(n)]

    def pairwise_f1(pred, gold):
        tp = fp = fn = 0
        for x in range(n):
            for y in range(x + 1, n):
                s_pred = pred[x] == pred[y]
                s_gold = gold[x] == gold[y]
                if s_pred and s_gold:
                    tp += 1
                elif s_pred:
                    fp += 1
                elif s_gold:
                    fn += 1
        P = tp / max(1, tp + fp); R = tp / max(1, tp + fn)
        return P, R, 2 * P * R / max(1e-9, P + R)

    pb = pairwise_f1(ev_before, gold)
    pa = pairwise_f1(ev_after, gold)
    agg.setdefault('before', []).append(pb)
    agg.setdefault('after', []).append(pa)
    print(f'  {ch}: {n} 条 | reply边 {sum(1 for i in ids if parent[i]!=i)} | 确认 {len(link_conf)} | '
          f'事件 {len(set(ev_before))}→{len(set(ev_after))} | 合并 {merged_cnt} | '
          f'P-F1 {pb[2]:.3f} → {pa[2]:.3f} | {(time.time()-t0)/60:.1f} min', flush=True)


def mean3(rows):
    return tuple(sum(r[i] for r in rows) / len(rows) for i in range(3))


pb, pa = mean3(agg['before']), mean3(agg['after'])
json.dump(CACHE, open(CACHE_PATH, 'w', encoding='utf-8'), ensure_ascii=False)
print(f'\n=== Ubuntu IRC：1.2 非连续合并验证（上限 ≤{args.max_units} 条 / ≤{args.max_chars} 字）===')
print(f'  reply 边 {total_links:,} 条 | 0.7 初审确认 {confirmed:,}（{100*confirmed/max(1,total_links):.1f}%）')
print(f'  合并前（纯连续切分）: P={pb[0]:.3f} R={pb[1]:.3f} **pairwise F1={pb[2]:.3f}**')
print(f'  合并后（+1.2 非连续）: P={pa[0]:.3f} R={pa[1]:.3f} **pairwise F1={pa[2]:.3f}**')
print(f'  提升 {100*(pa[2]-pb[2]):+.1f} 个百分点 | 用时 {(time.time()-t0)/60:.1f} 分钟')
