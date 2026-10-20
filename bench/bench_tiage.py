#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""基准：TIAGE（双人闲聊话题分段）—— 体裁不匹配的对照实验。

数据
    需要自行下载 TIAGE 数据集，把 ``tiage_test.json`` 放到
    ``bench/data/tiage_test.json``。结构：``dial_data.tiage[*].turns[*]``，
    每轮含 ``role`` / ``utterance`` / ``segmentation_label``；
    ``segmentation_label == 1`` 表示该轮结束一个话题段（下一轮开始新话题）。

用法::

    python bench/bench_tiage.py --model <模型ID> [--limit 10]
"""
import argparse
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
          '- 新消息是由当前话题中延续出的具有相关性的话题\n'
          '- 新消息发的图或表情，能看出是接着上面的话题或对当前话题表示情感吐槽和回应的'
          '（例如回应前面的截图，补充同一件事的画面，用图片表达自己对当前话题的情绪感慨、认同、附和、吐槽等）\n\n'
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
    tu = t.upper()
    if 'NEW' in tu or 'OUT' in tu:
        return '新'
    return '续'


def main(argv=None):
    ap = argparse.ArgumentParser(description='TIAGE 基准（双人闲聊分段）')
    add_common_args(ap)
    ap.add_argument('--model', required=True, help='被测模型 ID')
    ap.add_argument('--base-url', default=None, help='覆盖 config 的 base_url')
    ap.add_argument('--split', default='test', help='数据集划分（默认 test）')
    ap.add_argument('--data', default=None, help='数据路径（默认 bench/data/tiage_<split>.json）')
    ap.add_argument('--out', default=None, help='结果目录（默认 bench/results）')
    ap.add_argument('--limit', type=int, default=0, help='只跑前 N 个对话')
    ap.add_argument('--ctx-cap', type=int, default=20, help='上下文最多保留多少轮')
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
        _REPO, 'bench', 'data', 'tiage_%s.json' % args.split)
    out_dir = os.path.abspath(args.out) if args.out else os.path.join(_REPO, 'bench', 'results')
    if not os.path.exists(data_path):
        print('找不到数据：%s' % data_path, file=sys.stderr)
        print('  请先把 TIAGE 数据集的 %s 划分放到 bench/data/；数据不是本仓库的一部分。'
              % args.split, file=sys.stderr)
        return 2

    llm = LLM(cfg)
    ok, msg = llm.check('small')
    if not ok:
        print('模型不可用：%s' % msg, file=sys.stderr)
        return 2
    print(msg)

    with open(data_path, encoding='utf-8') as f:
        dials = json.load(f)['dial_data']['tiage']
    if args.limit:
        dials = dials[:args.limit]
    print('数据集 TIAGE-%s | 对话 %d | 上下文上限 %d 轮 | 模型 %s'
          % (args.split, len(dials), args.ctx_cap, args.model))

    t0 = time.time()
    tp = fp = fn = tn = 0
    calls = 0
    by_dial = []
    for di, dl in enumerate(dials, 1):
        turns = dl['turns']
        ctx = []
        d_ok = d_n = 0
        for i, t in enumerate(turns):
            if i == 0:
                ctx.append(t)
                continue
            gold_shift = (turns[i - 1].get('segmentation_label') == 1)
            ctx_txt = '\n'.join('%s: %s' % (c.get('role', '?'), c.get('utterance', ''))
                                for c in ctx[-args.ctx_cap:])
            out = llm.ask(PROMPT.format(context=ctx_txt,
                                        unit='%s: %s' % (t.get('role', '?'),
                                                         t.get('utterance', ''))),
                          role='small', max_tokens=16, temperature=0.0)
            calls += 1
            pred_shift = verdict(out) == '新'
            if pred_shift and gold_shift:
                tp += 1
                d_ok += 1
            elif pred_shift and not gold_shift:
                fp += 1
            elif not pred_shift and gold_shift:
                fn += 1
            else:
                tn += 1
                d_ok += 1
            d_n += 1
            ctx.append(t)
        by_dial.append([dl.get('dial_id'), d_ok, d_n])
        if di % 10 == 0:
            el = time.time() - t0
            print('  %d/%d 对话 | %d 次调用 | %.1f min | ETA %.1f min'
                  % (di, len(dials), calls, el / 60,
                     (len(dials) - di) * el / di / 60), flush=True)

    p = tp / max(1, tp + fp)
    r = tp / max(1, tp + fn)
    f1 = 2 * p * r / max(1e-9, p + r)
    acc = (tp + tn) / max(1, tp + tn + fp + fn)
    n = tp + tn + fp + fn
    print('\n=== TIAGE-%s 结果（%s）===' % (args.split, args.model.split('/')[-1]))
    print('  判定 %d 次 | 模型调用 %d | 用时 %.1f 分钟'
          % (n, calls, (time.time() - t0) / 60))
    print('  混淆 TP=%d FP=%d FN=%d TN=%d' % (tp, fp, fn, tn))
    print('  **P=%.3f  R=%.3f  F1=%.3f** | 准确率 %.3f' % (p, r, f1, acc))
    print('\n  文献基线（TIAGE 边界 F1）：')
    print('    ChatGPT 零样本 0.362 · CSM 0.427 · BayesSeg 0.366 · TextTiling 0.204 · Random 0.237')

    os.makedirs(out_dir, exist_ok=True)
    slug = re.sub(r'[^0-9A-Za-z._-]+', '_', args.model)
    json.dump({'model': args.model, 'split': args.split, 'n': n,
               'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn,
               'P': round(p, 4), 'R': round(r, 4), 'F1': round(f1, 4),
               'acc': round(acc, 4), 'calls': calls,
               'minutes': round((time.time() - t0) / 60, 1), 'by_dial': by_dial},
              open(os.path.join(out_dir, 'tiage_result_%s%s.json' % (slug, args.tag)),
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
