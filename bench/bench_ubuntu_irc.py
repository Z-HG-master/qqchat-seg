#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""基准：Ubuntu IRC（会话解缠）—— 评测 1.1「续 / 新」判别的边界能力。

数据
    需要自行下载 Ubuntu IRC / Conversation Disentanglement 数据集，转成 parquet 后
    放到 ``bench/data/ubuntu_irc_dev.parquet``（每行一个 20 条消息的窗口）：
        messages  list[str]  窗口内消息（最后一条为目标）
        parents   list[int]  每条消息回复的 id 列表（空 = 开新串）
        id        int        最后一条消息的 id

    金标「新」 = 目标消息的 id 出现在 parents 里（= 它开启了新对话串）/ 19.3% 左右。
    数据不是本仓库的一部分。

用法::

    python bench/bench_ubuntu_irc.py --model <模型ID> [--limit 200]
    python bench/bench_ubuntu_irc.py --model <模型ID> --config config.json
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

# ---- 提示词（与 1.1 判别一致，实验定稿） ----------------------------------
PROMPT = ('你在处理 QQ 群聊天记录的事件归类工作。\n\n'
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


def as_list(v):
    try:
        import numpy as np
        if isinstance(v, np.ndarray):
            return list(v)
    except ImportError:
        pass
    if isinstance(v, (list, tuple)):
        return list(v)
    return []


def main(argv=None):
    ap = argparse.ArgumentParser(description='Ubuntu IRC 基准（1.1 边界判别）')
    add_common_args(ap)
    ap.add_argument('--model', required=True, help='被测模型 ID（任意 OpenAI 兼容端点上的模型）')
    ap.add_argument('--base-url', default=None, help='覆盖 config 的 base_url')
    ap.add_argument('--data', default=None,
                    help='parquet 路径（默认 bench/data/ubuntu_irc_dev.parquet）')
    ap.add_argument('--out', default=None, help='结果目录（默认 bench/results）')
    ap.add_argument('--limit', type=int, default=0, help='只跑前 N 个样本')
    ap.add_argument('--tag', default='', help='结果文件名后缀')
    args = ap.parse_args(argv)

    cfg = load_config(args, allow_missing=True)
    if args.base_url:
        cfg.raw['base_url'] = args.base_url
    cfg.raw['model_small'] = args.model
    data_path = os.path.abspath(args.data) if args.data else os.path.join(
        _REPO, 'bench', 'data', 'ubuntu_irc_dev.parquet')
    out_dir = os.path.abspath(args.out) if args.out else os.path.join(_REPO, 'bench', 'results')
    if not os.path.exists(data_path):
        print('找不到数据：%s' % data_path, file=sys.stderr)
        print('  请先把 Ubuntu IRC（Conversation Disentanglement）数据集放到该路径；'
              '数据不是本仓库的一部分。', file=sys.stderr)
        return 2
    try:
        import pandas as pd
    except ImportError:
        print('需要 pandas / pyarrow：pip install pandas pyarrow', file=sys.stderr)
        return 2

    llm = LLM(cfg)
    ok, msg = llm.check('small')
    if not ok:
        print('模型不可用：%s' % msg, file=sys.stderr)
        return 2
    print(msg)

    df = pd.read_parquet(data_path)
    if args.limit:
        df = df.head(args.limit)
    print('样本 %d | 20 条窗口 | 模型 %s' % (len(df), args.model))

    t0 = time.time()
    tp = fp = fn = tn = 0
    records = []
    for n, (_, r) in enumerate(df.iterrows(), 1):
        msgs = [str(x) for x in as_list(r['messages'])]
        parents = [int(x) for x in as_list(r['parents'])]
        gold_new = int(r['id']) in parents
        ctx = '\n'.join('%d. %s' % (j, m) for j, m in enumerate(msgs[:-1], 1))
        out = llm.ask(PROMPT.format(context=ctx, unit=msgs[-1]),
                      role='small', max_tokens=16, temperature=0.0)
        pred_new = verdict(out) == '新'
        if pred_new and gold_new:
            tp += 1
        elif pred_new:
            fp += 1
        elif gold_new:
            fn += 1
        else:
            tn += 1
        records.append({'n': n, 'gold_new': gold_new, 'pred_new': pred_new,
                        'unit': msgs[-1][:300], 'ctx': ctx[-1500:], 'out': out[:40]})
        if n % 200 == 0:
            el = time.time() - t0
            print('  %d/%d | %.1f min | ETA %.1f min'
                  % (n, len(df), el / 60, (len(df) - n) * el / n / 60), flush=True)

    p = tp / max(1, tp + fp)
    r = tp / max(1, tp + fn)
    f1 = 2 * p * r / max(1e-9, p + r)
    acc = (tp + tn) / max(1, tp + tn + fp + fn)
    print('\n=== Ubuntu IRC 结果（%s，%d 样本）===' % (args.model.split('/')[-1], len(df)))
    print('  混淆 TP=%d FP=%d FN=%d TN=%d | 金标"新"率 %.1f%%'
          % (tp, fp, fn, tn, 100.0 * (tp + fn) / max(1, tp + tn + fp + fn)))
    print('  **P=%.3f  R=%.3f  F1=%.3f** | 准确率 %.3f' % (p, r, f1, acc))
    print('  参考：全判"续" → 准确率 %.1f%%（F1=0）'
          % (100.0 * (tn + fp) / max(1, tp + tn + fp + fn)))
    print('  用时 %.1f 分钟 | 平均 %.0f ms/样本'
          % ((time.time() - t0) / 60, 1000 * (time.time() - t0) / max(1, len(df))))

    os.makedirs(out_dir, exist_ok=True)
    slug = re.sub(r'[^0-9A-Za-z._-]+', '_', args.model)
    json.dump(records, open(os.path.join(out_dir, 'ubuntu_irc_preds_%s%s.json' % (slug, args.tag)),
                            'w', encoding='utf-8'), ensure_ascii=False)
    json.dump({'model': args.model, 'n': len(df), 'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn,
               'P': round(p, 4), 'R': round(r, 4), 'F1': round(f1, 4), 'acc': round(acc, 4)},
              open(os.path.join(out_dir, 'ubuntu_irc_result_%s%s.json' % (slug, args.tag)),
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
