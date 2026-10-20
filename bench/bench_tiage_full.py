#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""在 TIAGE 上走完整流水线：9B 逐条判 → 建事件 → 27B 复核(>10轮) → 算边界 F1

对比三档：
  A) 9B 单轮判别（基线）
  B) A + 27B 复核（我们的完整方案）
  C) 27B 单轮判别（上界参考）

用法：python bench_tiage_full.py [--limit N] [--review-min 10]
"""
import json, sys, io, os, re, time, argparse, urllib.request

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.dont_write_bytecode = True
BASE = 'http://127.0.0.1:1234/v1'
BENCH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
M_SMALL, M_BIG = 'qwen/qwen3.5-9b', 'qwen/qwen3.8-27b'

ap = argparse.ArgumentParser()
ap.add_argument('--limit', type=int, default=0)
ap.add_argument('--review-min', type=int, default=10)
ap.add_argument('--ctx-cap', type=int, default=20)
ap.add_argument('--phase', default='a', choices=['a','b'])
args = ap.parse_args()
if args.data_dir:
    BENCH = os.path.abspath(args.data_dir)

SEG_PROMPT = ('你在处理社交群聊记录的事件归类工作。\n\n'
              '【当前对话】下面是这个事件相关的消息记录（按时间先后）：\n'
              '{context}\n\n'
              '【新的一条消息】\n'
              '{unit}\n\n'
              '请判断：这条新消息是不是与【当前话题】里所写对话中谈论的事件/主题有关联？\n\n'
              '判「续」的情况：\n'
              '- 新消息在回应 / 追问 / 补充前文某条消息里的人、事、物，图时\n'
              '- 新消息为短反应（"？""确实""草""哈哈哈"等等）且能看出是在回应上面某句、'
              '或是对前消息内容的吐槽（包括反讽、比喻和阴阳怪气等等）或表达情绪时（感慨，赞叹，附和等等）\n'
              '- 新消息是由当前话题中延续出的具有相关性的话题\n\n'
              '判「新」的情况：\n'
              '- 新消息内容（图、文）转向讨论了另一件相关性弱的事时：换了话题、换了对象等等\n'
              '- 新消息内容不满足判「续」情况中任何一条的要求时\n\n'
              '只输出一个字：续 或 新')

REV_PROMPT = ('下面是社交群聊里一段连续的消息（按时间编号）：\n\n{lines}\n\n'
              '请判断：这段对话是不是一段**逻辑顺畅具有强关联性**的、适合作为 llm 训练输入数据源的对话？\n\n'
              '注意区分两种情况：\n- 顺畅：话题在自然推进，每一段前消息间具有内容主题关联性\n'
              '- 有转折：出现前后文间的话题切换，转移到一件**关联性弱**的新主题上，或前后文关注话题跨度过大\n\n'
              '如果你认为应该切分，请说明：\n- 从第几条开始是新话题？\n'
              '- 为什么这里算话题切换（前后文说的对象/事件有什么不同）？\n\n'
              '如果你认为不应该切分，请说明：\n- 这段里所有消息的通用主题是什么？\n'
              '- 如果存在话题重点的转移，那么为什么你认为这段消息仍然是同一件事的延续？\n\n'
              '只输出三行：\n结论: 顺畅 或 有转折\n转折: <编号，或"无">\n理由: <两句话说明>')


def ask(model, p, mt=16, tries=2):
    body = {'model': model, 'messages': [{'role': 'user', 'content': p}],
            'temperature': 0.0, 'max_tokens': mt, 'stream': False, 'reasoning_effort': 'none'}
    data = json.dumps(body).encode('utf-8')
    for a in range(tries):
        try:
            req = urllib.request.Request(BASE + '/chat/completions', data=data,
                                         headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=180) as r:
                d = json.loads(r.read().decode('utf-8'))
            return (d['choices'][0]['message'].get('content') or '').strip()
        except Exception:
            time.sleep(0.8 * (a + 1))
    return ''


def verdict(txt):
    t = (txt or '').strip()
    m = re.search(r'判定\s*[:：]\s*(续|新)', t)
    if m:
        return m.group(1)
    head = t[:12]
    if '新' in head:
        return '新'
    if '续' in head:
        return '续'
    return '续'


def parse_cuts(out, n):
    c = re.search(r'结论\s*[:：]\s*(\S+)', out)
    cu = re.search(r'转折\s*[:：]\s*([^\n]*)', out)
    if not c or '转折' not in c.group(1):
        return []
    if not cu:
        return []
    nums = [int(x) for x in re.findall(r'\d+', cu.group(1))]
    return sorted({x for x in nums if 1 < x <= n})


REVIEW_CALLS = [0]


def review_split(turns, depth=0, max_depth=3):
    """turns: [(idx_in_dial, role, utt)]；返回切点（全局位置列表）"""
    n = len(turns)
    if n <= args.review_min or depth >= max_depth:
        return []
    lines = '\n'.join(f'{j}. {t[1]}: {t[2]}' for j, t in enumerate(turns, 1))
    out = ask(M_BIG, REV_PROMPT.format(lines=lines), mt=400)
    REVIEW_CALLS[0] += 1
    cuts = parse_cuts(out, n)
    if not cuts:
        return []
    if len(cuts) >= n - 1:      # 退化：切得太碎
        return []
    pos = [turns[c - 1][0] + 1 for c in cuts]   # 切点 = 第 c 条之后
    prev = 0
    for c in cuts:
        seg = turns[prev:c]
        pos += review_split(seg, depth + 1, max_depth)
        prev = c
    pos += review_split(turns[prev:], depth + 1, max_depth)
    return pos


dials = json.load(open(os.path.join(BENCH, 'tiage_test.json'), encoding='utf-8'))['dial_data']['tiage']
if args.limit:
    dials = dials[:args.limit]
print(f'对话 {len(dials)} | 复核门槛 >{args.review_min} 轮 | 上下文上限 {args.ctx_cap}\n')

t0 = time.time()
CACHE = os.path.join(BENCH, f'tiage_predA_{args.limit or "all"}.json')

if args.phase == 'a':
    predA_all = {}
    for di, dl in enumerate(dials, 1):
        turns = dl['turns']
        utts = [(i, t.get('role', '?'), t.get('utterance', '')) for i, t in enumerate(turns)]
        predA = []
        ctx = []
        for i, (idx, role, utt) in enumerate(utts):
            if i == 0:
                ctx.append((role, utt)); continue
            ctx_txt = '\n'.join(f'{r}: {u}' for r, u in ctx[-args.ctx_cap:])
            out = ask(M_SMALL, SEG_PROMPT.format(context=ctx_txt, unit=f'{role}: {utt}'))
            if verdict(out) == '新':
                predA.append(i)
            ctx.append((role, utt))
        predA_all[dl['dial_id']] = predA
        if di % 10 == 0:
            print(f'  [A] {di}/{len(dials)} | {(time.time()-t0)/60:.1f} min', flush=True)
    json.dump(predA_all, open(CACHE, 'w', encoding='utf-8'), ensure_ascii=False)
    print(f'\n阶段A完成：9B 预测已存 {CACHE} | {(time.time()-t0)/60:.1f} 分钟')
    sys.exit(0)

# ---- 阶段 B：读 9B 结果，用 27B 复核 ----
predA_all = json.load(open(CACHE, encoding='utf-8'))
gold_total = 0
stats = {'A': [0, 0], 'B': [0, 0]}
for di, dl in enumerate(dials, 1):
    turns = dl['turns']
    utts = [(i, t.get('role', '?'), t.get('utterance', '')) for i, t in enumerate(turns)]
    gold = {i for i in range(1, len(turns)) if turns[i - 1].get('segmentation_label') == 1}
    gold_total += len(gold)
    predA = set(predA_all.get(dl['dial_id'], []))
    segs, prev = [], 0
    for i in sorted(predA):
        segs.append(utts[prev:i]); prev = i
    segs.append(utts[prev:])
    extra = []
    for s in segs:
        if len(s) > args.review_min:
            extra += review_split(s)
    predB = set(predA) | set(extra)
    for key, pred in (('A', predA), ('B', predB)):
        tp = len(pred & gold)
        stats[key][0] += tp
        stats[key][1] += len(pred)
    if di % 10 == 0:
        print(f'  [B] {di}/{len(dials)} | {(time.time()-t0)/60:.1f} min | 复核调用 {REVIEW_CALLS[0]}', flush=True)

print(f'\n=== TIAGE-test 完整流水线（{len(dials)} 段对话）===')
print(f'  金标边界总数 {gold_total}')
for key, name in (('A', '9B 逐条'), ('B', '9B 逐条 + 27B 复核')):
    tp, npred = stats[key]
    P = tp / max(1, npred)
    R = tp / max(1, gold_total)
    F1 = 2 * P * R / max(1e-9, P + R)
    print(f'  {name:<20} 预测边界 {npred:>4} | P={P:.3f} R={R:.3f} **F1={F1:.3f}**')
print(f'  27B 复核调用 {REVIEW_CALLS[0]} 次 | 总用时 {(time.time()-t0)/60:.1f} 分钟')
print(f'\n  参考：27B 单轮判别 F1=0.391 · ChatGPT 0.362 · CSM 0.427')

json.dump({'stats': stats, 'gold_total': gold_total, 'review_calls': REVIEW_CALLS[0]},
          open(os.path.join(BENCH, 'tiage_full_result.json'), 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1)
