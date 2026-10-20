#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阶段 2 · 步骤 2.1：图片结合上下文重述（第二遍，每张图一次调用）。

为什么要有第二遍
  · 第一遍（0.5）**无上下文**，只看图本身 → 用于阶段 0/1 的文本替换与事件判别；
  · 第二遍（2.1）**带事件上下文**，能说出「这张图在回应什么」→ 用于最终数据集。
  两遍互补：第一遍敢写专有名词，第二遍擅长语境。

输出两行
  描述: <不超过 80 字，只讲图本身>      ← 回填进最终数据集
  关联: <一句话，说明图片与对话的关系>   ← 只作元数据，不进训练文本

视觉能力由 ``model_supports_vision`` 开关控制：关闭时本步骤直接跳过，
2.2 会保留阶段 0 的图片描述。

输入: ``s1_events_gated.jsonl`` + ``units_at.jsonl``
输出: ``media_context_<tag>.jsonl``（断点续跑：已处理的 (event_id, unit_id, occ) 会跳过）

用法::

    python stages/s2_media_context.py --group 123456789 --tag ctx
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

from config import ConfigError, add_common_args, load_config        # noqa: E402
from core.llm import LLM, LLMError, MediaError                     # noqa: E402
from core.ph_render import parse_placeholders, render_context      # noqa: E402

# ---- 提示词（实验定稿，逐字保留，勿改） -----------------------------------
PROMPT = ('下面是社交群聊里的一段对话（按时间先后编号）。其中第 {k} 条消息带了一张图片（随本消息发送）。\n\n'
          '【对话上下文】\n{ctx}\n\n'
          '请结合上下文描述这张图片。\n\n'
          '要求：\n'
          '- 「描述」以一句话连贯描述，输出内容不超过80字\n'
          '- 「描述」需要尽可能包含以下三类信息：'
          '① 图片的类型与场景（结合上下文判断）'
          '② 发言人想通过它表达的信息（观点、吐槽或情绪）或发送这张图片的用意 '
          '③ 图片画面上出现的、与这段对话内容有关的文字与数字\n'
          '- 「描述」输出内容仅涉及图片文件内相关推理内容，'
          '禁止涉及发言人、回复人及具体的上下文信息等相关信息\n'
          '- 「关联」另起一句，说明这张图片与上面这段对话之间的联系\n'
          '- 合计只输出两行，不要其他内容：\n'
          '描述: <不超过 80 字>\n'
          '关联: <一句话>')

# --- 提示词覆盖点（--prompts-override，见 core/prompts.py） ---
from core.prompts import get as _prompt_get   # noqa: E402
PROMPT = _prompt_get('media.context', PROMPT)
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


def main(argv=None):
    ap = argparse.ArgumentParser(description='2.1 图片结合上下文重述')
    add_common_args(ap)
    ap.add_argument('--model', default=None, help='覆盖 config 的 model_big（可选）')
    ap.add_argument('--limit', type=int, default=0, help='只处理前 N 张图')
    ap.add_argument('--tag', default='ctx')
    ap.add_argument('--src', default='s1_events_gated.jsonl')
    args = ap.parse_args(argv)

    cfg = load_config(args, require_group=True)
    if args.model:
        cfg.raw['model_big'] = args.model
    group_dir = cfg.group_dir
    units_path = os.path.join(group_dir, 'units_at.jsonl')
    evs_path = os.path.join(group_dir, args.src)
    for p, step in ((units_path, '0.8'), (evs_path, '1.4')):
        if not os.path.exists(p):
            print('错误：找不到 %s（请先跑完 %s）' % (p, step), file=sys.stderr)
            return 2

    out_path = os.path.join(group_dir, 'media_context_%s.jsonl' % args.tag)

    if not cfg.model_supports_vision:
        # 视觉关闭：产出一个空产物占位，2.2 会沿用阶段 0 的描述
        open(out_path, 'a', encoding='utf-8').close()
        print('model_supports_vision=false：跳过 2.1 图片上下文重述。')
        print('  最终片段将使用阶段 0 的图片描述。产物占位：%s' % out_path)
        print('\n下一步：python stages/s2_build_segments.py --group %s --ctx-tag %s'
              % (cfg.group_id, args.tag))
        return 0

    llm = LLM(cfg)
    if not llm.mock:
        ok, msg = llm.check('big')
        if not ok:
            print('无法开始 2.1：%s' % msg, file=sys.stderr)
            return 2
        print(msg)

    units = load_jsonl(units_path)
    uid = {u['unit_id']: u for u in units}
    evs = load_jsonl(evs_path)

    done = set()
    for rec in load_jsonl(out_path):
        if rec.get('desc'):
            done.add((rec.get('event_id'), rec.get('unit_id'), rec.get('occ')))

    def pictures_of(u):
        pics = [m for m in (u.get('media') or []) if m.get('kind') == '图片']
        if not llm.mock:
            pics = [m for m in pics
                    if os.path.exists(cfg.resolve_media_path(m.get('path')))]
        return pics

    todo = []
    for e in evs:
        for i, k in enumerate(e['unit_ids']):
            pics = pictures_of(uid[k])
            occ = 0
            for kind, raw, a, b in parse_placeholders(uid[k].get('body') or ''):
                if kind != '图片':
                    continue
                if occ >= len(pics):
                    occ += 1
                    continue
                key = (e['event_id'], k, occ)
                if key not in done:
                    todo.append((e, i, occ, pics[occ], raw))
                occ += 1
    if args.limit:
        todo = todo[:args.limit]
    print('待处理图片 %d 张（已完成 %d 张）' % (len(todo), len(done)))

    t0 = time.time()
    ok_n = bad_n = 0
    with open(out_path, 'a', encoding='utf-8') as fo:
        for n, (e, i, occ, m, raw) in enumerate(todo, 1):
            body = [(uid[x]['display'], uid[x].get('body') or '') for x in e['unit_ids']]
            ctx = render_context(body, i, occ)
            try:
                out = llm.ask_vision(PROMPT.format(k=i + 1, ctx=ctx),
                                     cfg.resolve_media_path(m.get('path')),
                                     role='big', max_tokens=300)
            except MediaError as ex:
                fo.write(json.dumps({'event_id': e['event_id'],
                                     'unit_id': e['unit_ids'][i], 'occ': occ,
                                     'md5': (m.get('md5') or '')[:8],
                                     'err': 'decode:%s' % (ex,)},
                                    ensure_ascii=False) + '\n')
                fo.flush()
                bad_n += 1
                continue
            d = re.search(r'描述\s*[:：]\s*(.+)', out)
            r2 = re.search(r'关联\s*[:：]\s*(.+)', out)
            desc = d.group(1).strip() if d else ''
            rel = r2.group(1).strip() if r2 else ''
            if not desc:      # 没按格式输出，退化为取第一行
                desc = out.splitlines()[0].strip() if out else ''
                bad_n += 1
            else:
                ok_n += 1
            fo.write(json.dumps({
                'event_id': e['event_id'], 'unit_id': e['unit_ids'][i],
                'k': i + 1, 'occ': occ, 'md5': (m.get('md5') or '')[:8],
                'path': m.get('path', ''), 'desc': desc[:200], 'rel': rel[:300],
                'caption_old': raw[:260]}, ensure_ascii=False) + '\n')
            fo.flush()
            if n % 50 == 0:
                el = time.time() - t0
                print('  %d/%d | %.1f min | %.2f 张/s | 失败 %d'
                      % (n, len(todo), el / 60, n / max(el, 1e-6), bad_n), flush=True)

    print('\n2.1 完成：成功 %d | 退化/失败 %d | 用时 %.1f 分钟'
          % (ok_n, bad_n, (time.time() - t0) / 60))
    print('\n产物：%s' % out_path)
    print('\n下一步：python stages/s2_build_segments.py --group %s --ctx-tag %s'
          % (cfg.group_id, args.tag))
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
