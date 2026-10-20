#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阶段 0 · 步骤 0.5：媒体转文本（图片约 50 字描述 / 表情不超过 15 字）。

输入: ``messages.jsonl``（0.1 产物）
输出: ``media_caption.jsonl`` + ``messages_media.jsonl``

特性
  · 按 md5 去重：同一张图只调用一次模型
  · 断点续跑：已有描述的 md5 自动跳过
  · 单张图片不可读（文件缺失/损坏）只跳过该张，不影响整个流程
  · 视觉能力由 ``config.json`` 的 ``model_supports_vision`` 开关控制：
    关闭时不调用视觉模型，直接按「无描述」回填（占位符被移除）

用法::

    python stages/s0_media.py --group 123456789
    python stages/s0_media.py --group 123456789 --limit 20   # 只跑前 20 个媒体
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

# ---- 提示词（实验定稿，逐字保留，勿改） -----------------------------------
PIC_PROMPT = ('这是一张 QQ 群里的图片。判断它属于什么种类的图片，并用 50 字左右（45–55 字）客观描述画面内容。\n'
              '描述要写清：画面主体/人物/场景、图上出现的文字原文、关键数字与数值、'
              '界面或场景在做什么事、这张图在群里可能想表达什么。\n'
              '只输出一行')
EMO_PROMPT = ('这是一个 QQ 群里的表情包。用不超过 15 字，简要描述表情包的内容并说明它表达的情绪或语气。'
              '只输出这一行，不要解释。')

# --- 提示词覆盖点（--prompts-override，见 core/prompts.py） ---
from core.prompts import get as _prompt_get   # noqa: E402
PIC_PROMPT = _prompt_get('media.pic', PIC_PROMPT)
EMO_PROMPT = _prompt_get('media.emoji', EMO_PROMPT)
# --------------------------------------------------------------------------

VISION_KINDS = ('图片', '表情')
#: 宽松解析「某某：描述」的类别前缀，只用于统计，不参与约束
TYPE_RE = re.compile(r'^([^：:，。]{1,10}?(?:图片|截图|照片|画面|封面|插画|梗图|表情包))[：:]\s*(.+)$')


def load_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def placeholder_id(kind, md5):
    return '[%s#%s]' % (kind, md5[:8])


def load_captions(path):
    """读取已有标注，返回 {md5: rec}（只收成功拿到描述的）。"""
    caps = {}
    for rec in load_jsonl(path):
        if rec.get('md5') and rec.get('desc'):
            caps[rec['md5']] = rec
    return caps


def backfill(msgs, caps):
    """把 messages.jsonl 里的媒体占位替换成描述，返回 (messages_media, 统计)。"""
    out, n_repl, n_lost = [], 0, 0
    for m in msgs:
        body = m.get('body') or ''
        for x in (m.get('media') or []):
            kind = x.get('kind')
            if kind not in VISION_KINDS:
                continue
            old = placeholder_id(kind, x.get('md5') or '')
            if old not in body:
                continue
            c = caps.get(x.get('md5'))
            if c and c.get('desc'):
                new = '[图片：%s]' % c['desc'] if kind == '图片' else '[表情：%s]' % c['desc']
            else:
                new = ''      # 标注失败/视觉关闭 = 该媒体不可用，不留裸占位
                n_lost += 1
            body = body.replace(old, new, 1)
            n_repl += 1
        rec = dict(m)
        rec['body'] = body
        rec['text'] = '%s: %s' % (m.get('display', ''), body)
        out.append(rec)
    return out, n_repl, n_lost


def main(argv=None):
    ap = argparse.ArgumentParser(description='0.5 媒体转文本')
    add_common_args(ap)
    ap.add_argument('--limit', type=int, default=0, help='只处理前 N 个媒体（0=全部）')
    ap.add_argument('--model', default=None, help='覆盖 config 的 model_small（可选）')
    args = ap.parse_args(argv)

    cfg = load_config(args, require_group=True)
    if args.model:
        cfg.raw['model_small'] = args.model
    group_dir = cfg.group_dir
    msgs_path = os.path.join(group_dir, 'messages.jsonl')
    if not os.path.exists(msgs_path):
        print('错误：找不到 %s\n  请先运行 0.1：python stages/s0_import.py --group %s'
              % (msgs_path, cfg.group_id), file=sys.stderr)
        return 2

    msgs = load_jsonl(msgs_path)
    cap_path = os.path.join(group_dir, 'media_caption.jsonl')
    out_path = os.path.join(group_dir, 'messages_media.jsonl')

    # 视觉能力由开关决定，而不是靠模型名猜
    vision = cfg.model_supports_vision
    llm = LLM(cfg)
    if vision and not llm.mock:
        ok, msg = llm.check('small')
        if not ok:
            print('无法开始 0.5：%s' % msg, file=sys.stderr)
            return 2
        print(msg)
    elif not vision:
        print('model_supports_vision=false：跳过视觉标注，媒体占位将按「不可用」处理。')

    uniq = {}
    for m in msgs:
        for x in (m.get('media') or []):
            if x.get('kind') in VISION_KINDS and x.get('md5'):
                uniq.setdefault(x['md5'], x)
    done = load_captions(cap_path)
    todo = [v for k, v in uniq.items() if k not in done]
    if args.limit:
        todo = todo[:args.limit]
    print('媒体去重 %d 个 | 已有描述 %d 个 | 待处理 %d 个'
          % (len(uniq), len(done), len(todo)))

    t0 = time.time()
    ok_n = bad_n = 0
    if vision and todo:
        with open(cap_path, 'a', encoding='utf-8') as fo:
            for i, x in enumerate(todo, 1):
                rec = {'md5': x['md5'], 'kind': x['kind'], 'path': x.get('path', ''),
                       'type': '', 'desc': '', 'err': ''}
                path = cfg.resolve_media_path(x.get('path'))
                try:
                    if x['kind'] == '图片':
                        out = llm.ask_vision(PIC_PROMPT, path, role='small', max_tokens=200)
                        line = out.splitlines()[0].strip() if out else ''
                        rec['desc'] = line[:110]
                        m = TYPE_RE.match(line)
                        rec['type'] = m.group(1) if m else ''
                    else:
                        out = llm.ask_vision(EMO_PROMPT, path, role='small', max_tokens=80)
                        rec['desc'] = (out or '').splitlines()[0].strip()[:15]
                        rec['type'] = '表情'
                except MediaError as e:
                    rec['err'] = 'decode:%s' % (e,)
                if not rec['desc']:
                    rec['err'] = rec['err'] or 'empty'
                    bad_n += 1
                else:
                    ok_n += 1
                fo.write(json.dumps(rec, ensure_ascii=False) + '\n')
                fo.flush()
                if i % 50 == 0:
                    el = time.time() - t0
                    print('  %d/%d | %.1f min | %.2f 个/s | 失败 %d'
                          % (i, len(todo), el / 60, i / max(el, 1e-6), bad_n), flush=True)
    el = time.time() - t0
    print('0.5 标注完成：成功 %d | 失败/跳过 %d | 用时 %.1f 分钟'
          % (ok_n, bad_n, el / 60))

    caps = load_captions(cap_path)
    out_msgs, n_repl, n_lost = backfill(msgs, caps)
    with open(out_path, 'w', encoding='utf-8') as f:
        for m in out_msgs:
            f.write(json.dumps(m, ensure_ascii=False) + '\n')
    print('占位回填 %d 处（其中 %d 处因无描述被移除）' % (n_repl, n_lost))
    print('\n产物：\n  %s\n  %s' % (cap_path, out_path))
    print('\n下一步：python stages/s0_merge_runs.py --group %s' % cfg.group_id)
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
