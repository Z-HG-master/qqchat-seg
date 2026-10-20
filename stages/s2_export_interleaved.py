#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阶段 2 · 步骤 2.3：导出**图文交错**格式（供多模态模型微调）。

与 2.2 的关系
  同一次事件切分，导出两种形态：
    · 2.2 → ``segments_ctx.jsonl``         纯文本（图片转成一句话描述）
    · 2.3 → ``segments_interleaved.jsonl`` 图文交错（保留真实图片引用）
  切分结果完全复用，不需要重跑 1.x。

输出格式（每行一个事件）
  {
    "event_id": "V0001", "peer": "123456789",
    "ts_first": 1788190689, "ts_last": 1788190719,
    "participants": ["示例用户A", "示例用户B"],
    "n_msgs": 3,
    "messages": [
      {"sender": "示例用户A",
       "content": [
         {"type": "text", "text": "刚打完一把，这把评分居然给了 S"},
         {"type": "image", "image": "<路径>", "kind": "图片",
          "desc": "游戏结算界面…", "md5": "…"}
       ]}
    ]
  }

``desc`` 是流水线已经算好的两轮描述之一（2.1 优先），可作为**辅助监督信号**
一起喂给模型，而不必二选一。

图片寻址两种模式
  · ``--media-mode path``（默认）：写文件路径，训练时按同一路径读取
  · ``--media-mode base64``：把图片内联成 data URL，整个 jsonl 自包含（文件很大）
  另可用 ``--relative-to`` 把绝对路径改写成相对某目录（便于搬移数据集）

用法::

    python stages/s2_export_interleaved.py --group 123456789
    python stages/s2_export_interleaved.py --group 123456789 --media-mode base64
    python stages/s2_export_interleaved.py --group 123456789 --relative-to ./dataset
"""
import argparse
import base64
import json
import shutil
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)
sys.dont_write_bytecode = True   # 不在仓库里留下 __pycache__

from config import ConfigError, add_common_args, load_config  # noqa: E402
from core.ph_render import parse_placeholders                 # noqa: E402

#: 视觉类型（其余按文本标签保留）
VISION_KINDS = ('图片', '表情')

#: 输出目录（jsonl 所在目录），供 copy-media 计算相对路径
GROUP_DIR = [None]

#: 兼容旧产物：把原始 kind 归一化（与 s0_import.py 的 KIND_TAG 保持一致）
KIND_ALIAS = {
    'pic': '图片', 'image': '图片', '图片': '图片', 'photo': '图片', 'image_url': '图片',
    'emoji': '表情', '表情': '表情', 'sticker': '表情', 'mface': '表情',
    'video': '视频', '视频': '视频',
    'file': '文件', '文件': '文件',
    'ptt': '语音', 'voice': '语音', 'audio': '语音', '语音': '语音',
}


def norm_kind(kind):
    return KIND_ALIAS.get(str(kind or '').strip().lower(), '文件')


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


def build_desc_map(group_dir, ctx_tag):
    """md5 → 描述。

    优先级：2.1 的上下文描述 > 0.5 的无上下文描述。
    """
    m = {}
    for name in ('media_caption.jsonl', 'media_context_%s.jsonl' % ctx_tag):
        for rec in load_jsonl(os.path.join(group_dir, name)):
            md5 = (rec.get('md5') or '').strip()
            desc = (rec.get('desc') or '').strip()
            if md5 and desc:
                m[md5] = desc          # 后读的覆盖先读的（2.1 在后）
    return m


def build_ctx_desc_map(group_dir, ctx_tag):
    """(event_id, unit_id, occ) → 2.1 上下文描述（逐事件精确匹配）。"""
    m = {}
    for rec in load_jsonl(os.path.join(group_dir, 'media_context_%s.jsonl' % ctx_tag)):
        if rec.get('desc'):
            m[(rec.get('event_id'), rec.get('unit_id'), rec.get('occ'))] = rec['desc']
    return m


def to_data_url(path, max_px=768, quality=82):
    """把图片转成 data URL；max_px>0 时等比缩放到最长边不超过它。"""
    if max_px and max_px > 0:
        try:
            import io as _io
            from PIL import Image
            im = Image.open(path)
            if max(im.size) > max_px:
                im.thumbnail((max_px, max_px))
            if im.mode not in ('RGB', 'L'):
                im = im.convert('RGB')
            buf = _io.BytesIO()
            im.save(buf, 'JPEG', quality=quality, optimize=True)
            return 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode('ascii')
        except Exception:
            pass          # Pillow 缺失或图片损坏 → 退回原图
    ext = os.path.splitext(path)[1].lower().lstrip('.') or 'png'
    mime = {'jpg': 'jpeg', 'jpeg': 'jpeg', 'png': 'png', 'gif': 'gif', 'webp': 'webp'}.get(ext, 'png')
    try:
        with open(path, 'rb') as f:
            return 'data:image/%s;base64,%s' % (mime, base64.b64encode(f.read()).decode('ascii'))
    except OSError:
        return ''


def main(argv=None):
    ap = argparse.ArgumentParser(description='2.3 导出图文交错格式（多模态微调用）')
    add_common_args(ap)
    ap.add_argument('--src', default='s1_events_gated.jsonl')
    ap.add_argument('--ctx-tag', default='ctx')
    ap.add_argument('--tag', default='segments_interleaved')
    ap.add_argument('--media-mode', choices=('path', 'base64'), default='path',
                    help='path=写文件路径（默认）；base64=内联成 data URL')
    ap.add_argument('--relative-to', default=None,
                    help='把图片路径改写成相对该目录（仅 media-mode=path 有效）')
    ap.add_argument('--no-desc', action='store_true',
                    help='不输出 desc 字段（只要纯图 + 文本，不要辅助描述）')
    ap.add_argument('--copy-media', default=None, metavar='DIR',
                    help='把用到的图片复制到 DIR（按 md5 命名去重），路径改写为相对 jsonl 所在目录')
    ap.add_argument('--base64-max-px', type=int, default=768,
                    help='base64 模式下图片最长边（默认 768；0=不缩放，文件会非常大）')
    ap.add_argument('--base64-quality', type=int, default=82,
                    help='base64 模式 JPEG 质量（默认 82）')
    args = ap.parse_args(argv)

    cfg = load_config(args, require_group=True)
    group_dir = cfg.group_dir
    units_path = os.path.join(group_dir, 'units_at.jsonl')
    src_path = os.path.join(group_dir, args.src)
    for p, step in ((units_path, '0.8'), (src_path, '1.4')):
        if not os.path.exists(p):
            print('错误：找不到 %s（请先跑完 %s）' % (p, step), file=sys.stderr)
            return 2

    GROUP_DIR[0] = group_dir
    units = load_jsonl(units_path)
    uid = {u['unit_id']: u for u in units}
    evs = load_jsonl(src_path)
    desc_by_md5 = {} if args.no_desc else build_desc_map(group_dir, args.ctx_tag)
    desc_by_occ = {} if args.no_desc else build_ctx_desc_map(group_dir, args.ctx_tag)

    media_root = getattr(cfg, 'media_root', '') or ''
    n_img = n_missing = n_msgs = 0
    out_rows = []

    def media_path(p):
        """解析媒体真实路径：绝对路径原样，相对路径按 media_root。"""
        if not p:
            return ''
        if os.path.isabs(p):
            return p
        return os.path.normpath(os.path.join(media_root or group_dir, p))

    for e in evs:
        msgs = []
        for k in e['unit_ids']:
            u = uid[k]
            body = u.get('body') or ''
            media_list = u.get('media') or []
            pics = [m for m in media_list if norm_kind(m.get('kind')) in VISION_KINDS]
            phs = parse_placeholders(body)

            content = []
            if not phs:
                if body.strip():
                    content.append({'type': 'text', 'text': body.strip()})
                for m in pics:      # 理论上不会有（占位符已在 0.1 补过）
                    content.append(_media_block(m, media_path, desc_by_md5, args, GROUP_DIR[0]))
            else:
                pieces, last, occ = [], 0, 0
                for kind, raw, a, b in phs:
                    text = body[last:a].strip()
                    if text:
                        content.append({'type': 'text', 'text': text})
                    if kind in VISION_KINDS:
                        m = pics[occ] if occ < len(pics) else None
                        if m:
                            blk = _media_block(m, media_path, desc_by_md5, args, GROUP_DIR[0])
                            # 2.1 的逐事件描述优先级最高
                            d = desc_by_occ.get((e['event_id'], k, occ))
                            if d and not args.no_desc:
                                blk['desc'] = d
                            content.append(blk)
                        else:
                            content.append({'type': 'text', 'text': raw.strip()})
                            n_missing += 1
                        occ += 1
                    else:
                        # 视频/文件/语音：保留文本标签
                        content.append({'type': 'text', 'text': raw.strip()})
                    last = b
                tail = body[last:].strip()
                if tail:
                    content.append({'type': 'text', 'text': tail})

            for c in content:
                if c['type'] == 'image':
                    n_img += 1
            if not content:
                continue
            msgs.append({'sender': u.get('display', ''), 'content': content})
            n_msgs += 1

        out_rows.append({
            'event_id': e['event_id'], 'peer': e['peer'],
            'ts_first': e['ts_first'], 'ts_last': e['ts_last'],
            'participants': e['participants'],
            'n_msgs': len(msgs),
            'messages': msgs,
        })

    out_path = os.path.join(group_dir, '%s.jsonl' % args.tag)
    with open(out_path, 'w', encoding='utf-8') as f:
        for r in out_rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')

    size = os.path.getsize(out_path)
    print('格式：图文交错（media-mode=%s%s）'
          % (args.media_mode, '，相对路径到 %s' % args.relative_to if args.relative_to else ''))
    print('事件 %d | 消息 %d | 图片块 %d'
          % (len(out_rows), n_msgs, n_img))
    if n_missing:
        print('保留为文本的媒体占位符 %d 处（通常是 QQ 内置表情：源码里就是文本，没有图片文件）'
              % n_missing)
    print('描述辅助字段：%s' % ('关闭' if args.no_desc else '已附带（%d 条 md5 映射）' % len(desc_by_md5)))
    print('\n产物：%s（%.2f MB）' % (out_path, size / 1024 / 1024))
    print('  这是给多模态模型用的：messages[].content 是 text / image 混合块。')
    print('  切分结果与 2.2 的纯文本版完全一致，只是媒体的呈现方式不同。')
    return 0


def _media_block(m, media_path, desc_by_md5, args, out_dir=None):
    """构造一个 image 内容块。"""
    real = media_path(m.get('path') or '')
    kind = norm_kind(m.get('kind'))
    md5 = (m.get('md5') or '')[:32]

    if args.media_mode == 'base64':
        src = (to_data_url(real, args.base64_max_px, args.base64_quality)
               if real and os.path.isfile(real) else '')
    elif args.copy_media and real and os.path.isfile(real):
        # 复制到指定目录（按 md5 命名去重），路径相对 jsonl 所在目录
        ext = os.path.splitext(real)[1].lower() or '.bin'
        name = (md5 or os.path.basename(real).split('.')[0]) + ext
        dst = os.path.join(args.copy_media, name)
        if not os.path.isfile(dst):
            os.makedirs(args.copy_media, exist_ok=True)
            try:
                shutil.copy2(real, dst)
            except OSError:
                dst = real
        try:
            src = os.path.relpath(dst, out_dir or os.getcwd())
        except ValueError:
            src = dst
        src = src.replace('\\', '/')
    else:
        src = real
        if args.relative_to and src:
            try:
                src = os.path.relpath(src, os.path.abspath(args.relative_to))
            except ValueError:
                pass
        src = src.replace('\\', '/') if src else ''

    blk = {'type': 'image', 'image': src, 'kind': kind}
    if not args.no_desc:
        d = desc_by_md5.get(md5)
        if d:
            blk['desc'] = d
    if md5:
        blk['md5'] = md5
    return blk


if __name__ == '__main__':
    try:
        sys.exit(main())
    except ConfigError as e:
        print('配置错误：%s' % e, file=sys.stderr)
        sys.exit(2)
