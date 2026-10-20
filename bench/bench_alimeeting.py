#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""基准：Alimeeting4MUG（中文会议话题分段）—— 体裁严重不匹配的对照实验。

数据
    需要自行下载 Alimeeting4MUG 开发集，放到 ``bench/data/alimeeting_dev.csv``
    （TSV 格式、第二列内嵌 JSON）：
        sentences[]            逐句，含 speaker / s / id
        topic_segment_ids[]    每段末句的句子 id（下一句 = 新话题）

    说明：会议数据每 62 句才有一个话题边界（边界密度 1.6%），QQ 群是 19–25%，
    任务定义差一个数量级，分数低**不代表**算法失效。

用法::

    python bench/bench_alimeeting.py --model <模型ID> --meetings 3
"""
import argparse
import csv
import json
import os
import re
import sys
import time

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)
sys.dont_write_bytecode = True   # 不在仓库里留下 __pycache__

from config import ConfigError, add_common_args, load_config  # noqa: E402
from core.llm import LLM, LLMError                           # noqa: E402

csv.field_size_limit(1024 * 1024 * 50)

# ---- 提示词（与 1.1 判别一致） --------------------------------------------
PROMPT = ('你在处理社交群聊记录的事件归类工作。\n\n'
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
# --------------------------------------------------------------------------


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


def main(argv=None):
    ap = argparse.ArgumentParser(description='Alimeeting4MUG 基准（中文会议分段）')
    add_common_args(ap)
    ap.add_argument('--model', required=True, help='被测模型 ID')
    ap.add_argument('--base-url', default=None, help='覆盖 config 的 base_url')
    ap.add_argument('--data', default=None,
                    help='TSV 路径（默认 bench/data/alimeeting_dev.csv）')
    ap.add_argument('--out', default=None, help='结果目录（默认 bench/results）')
    ap.add_argument('--meetings', type=int, default=3, help='评测前 N 个会议')
    ap.add_argument('--ctx-cap', type=int, default=20, help='上下文最多保留多少句')
    ap.add_argument('--tag', default='', help='结果文件名后缀')
    args = ap.parse_args(argv)

    cfg = load_config(args, allow_missing=True)
    if args.base_url:
        cfg.raw['base_url'] = args.base_url
    cfg.raw['model_small'] = args.model
    # Qwen 系：不传该字段会默认开启思考模式，导致输出被推理占满、正文为空。
    # 这里显式关闭，保证与报告里的数字同一套调用参数。
    cfg.raw['reasoning_effort'] = 'none'
    data_path = os.path.abspath(args.data) if args.data else os.path.join(
        _REPO, 'bench', 'data', 'alimeeting_dev.csv')
    out_dir = os.path.abspath(args.out) if args.out else os.path.join(_REPO, 'bench', 'results')
    if not os.path.exists(data_path):
        print('找不到数据：%s' % data_path, file=sys.stderr)
        print('  请先把 Alimeeting4MUG 开发集放到 bench/data/；数据不是本仓库的一部分。',
              file=sys.stderr)
        return 2

    llm = LLM(cfg)
    ok, msg = llm.check('small')
    if not ok:
        print('模型不可用：%s' % msg, file=sys.stderr)
        return 2
    print(msg)

    recs = []
    with open(data_path, encoding='utf-8', newline='') as f:
        reader = csv.reader(f, delimiter='\t')
        next(reader, None)
        for row in reader:
            if len(row) >= 2 and row[1].strip():
                recs.append(json.loads(row[1]))
    recs = recs[:args.meetings]
    print('会议 %d | 上下文上限 %d 句 | 模型 %s' % (len(recs), args.ctx_cap, args.model))

    t0 = time.time()
    tp = fp = fn = 0
    total_sent = 0
    for mi, d in enumerate(recs, 1):
        ss = d['sentences']
        total_sent += len(ss)
        ends = {x['id'] for x in d['topic_segment_ids']}
        gold = {i for i in range(1, len(ss)) if ss[i - 1]['id'] in ends}
        ctx = []
        d_tp = d_fp = d_fn = 0
        for i, s in enumerate(ss):
            if i == 0:
                ctx.append((s['speaker'], s['s']))
                continue
            ctx_txt = '\n'.join('%s: %s' % (sp, tx) for sp, tx in ctx[-args.ctx_cap:])
            out = llm.ask(PROMPT.format(context=ctx_txt,
                                        unit='%s: %s' % (s['speaker'], s['s'])),
                          role='small', max_tokens=16, temperature=0.0)
            pred = verdict(out) == '新'
            g = i in gold
            if pred and g:
                tp += 1
                d_tp += 1
            elif pred:
                fp += 1
                d_fp += 1
            elif g:
                fn += 1
                d_fn += 1
            ctx.append((s['speaker'], s['s']))
        p = d_tp / max(1, d_tp + d_fp)
        r = d_tp / max(1, d_tp + d_fn)
        f1 = 2 * p * r / max(1e-9, p + r)
        print('  [%d/%d] %s: %d 句 / %d 边界 | TP=%d FP=%d FN=%d | P=%.3f R=%.3f F1=%.3f | %.0fs'
              % (mi, len(recs), d.get('meeting_key'), len(ss), len(gold),
                 d_tp, d_fp, d_fn, p, r, f1, time.time() - t0), flush=True)

    p = tp / max(1, tp + fp)
    r = tp / max(1, tp + fn)
    f1 = 2 * p * r / max(1e-9, p + r)
    print('\n=== Alimeeting 结果（%s，%d 会议）===' % (args.model.split('/')[-1], len(recs)))
    print('  句子 %d | 判定 %d | 用时 %.1f 分钟'
          % (total_sent, total_sent - len(recs), (time.time() - t0) / 60))
    print('  混淆 TP=%d FP=%d FN=%d | 金标边界率 %.2f%%'
          % (tp, fp, fn, 100.0 * (tp + fn) / max(1, total_sent)))
    print('  **P=%.3f  R=%.3f  F1=%.3f**' % (p, r, f1))

    os.makedirs(out_dir, exist_ok=True)
    slug = re.sub(r'[^0-9A-Za-z._-]+', '_', args.model)
    json.dump({'model': args.model,
               'meetings': [d.get('meeting_key') for d in recs],
               'tp': tp, 'fp': fp, 'fn': fn,
               'P': round(p, 4), 'R': round(r, 4), 'F1': round(f1, 4)},
              open(os.path.join(out_dir, 'alimeeting_result_%s%s.json' % (slug, args.tag)),
                   'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('\n结果已存：%s' % out_dir)
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
