# -*- coding: utf-8 -*-
"""ChatLab 标准格式兼容（输入适配器）。

ChatLab（https://github.com/ChatLab/ChatLab）是一个本地优先的聊天记录分析工具，
定义了跨平台的标准交换格式 `chatlab` v0.0.1 / v0.0.2（JSON 或 JSONL）。

它支持的平台包括 QQ / WhatsApp / LINE / Discord / Telegram / iMessage 等，
所以「已经用 ChatLab 整理过数据」的用户可以直接把文件交给本流水线。

⚠️ 一个重要限制
    ChatLab 标准格式**不携带聊天媒体的文件路径**：图片只有 ``type=1`` 和一个
    字符串字段 ``content``（例如 ``"[Image]"``），没有 md5 / 本地路径 / URL 约定。
    因此本适配器把图片/表情降级为**文本标签**（``[图片]`` / ``[表情]``），
    阶段 0.5 与 2.1 不会对它们做视觉识别——这是格式本身的能力边界，不是 bug。

    如果需要视觉识别（本项目的亮点功能），请使用原生格式
    （``msg_id/ts/sender_uid/text/media[{md5,kind,path}]``），见 docs/data-format.md。

支持的输入
    · JSONL：首行 ``{"_type":"header", ...}``，其后 ``_type`` 为 member / message
    · JSON ：``{"chatlab": {...}, "meta": {...}, "members": [...], "messages": [...]}``
"""
import json
import re

#: ChatLab 消息类型 → 我们内部的媒体种类（None = 纯文本类）
TYPE_TEXT = 0
TYPE_IMAGE = 1
TYPE_VOICE = 2
TYPE_VIDEO = 3
TYPE_FILE = 4
TYPE_EMOJI = 5

#: 视觉类：本格式拿不到文件 → 降级为文本标签
VISION_LABEL = {TYPE_IMAGE: '图片', TYPE_EMOJI: '表情'}
#: 非视觉媒体：直接落成裸标签文本
PLAIN_LABEL = {TYPE_VOICE: '语音', TYPE_VIDEO: '视频', TYPE_FILE: '文件'}

#: 互动/系统类 → 文本标签（保持可读性，不参与对话语义）
EXTRA_LABEL = {
    7: '链接', 8: '位置', 20: '红包', 21: '转账', 22: '戳一戳',
    23: '通话', 24: '分享', 26: '转发', 27: '名片',
}
#: 默认丢弃的消息类型（非对话内容）
SYSTEM_TYPES = {80, 81, 99}

#: 视为「回复」的类型：正文是引用内容，关系字段在 replyToMessageId
TYPE_REPLY = 25


def looks_like_chatlab(path, head_bytes=262144):
    """快速判断一个文件是不是 ChatLab 格式（只看文件头，不整体解析）。"""
    try:
        with open(path, 'rb') as f:
            head = f.read(head_bytes)
    except OSError:
        return False
    text = head.decode('utf-8', 'replace').lstrip('\ufeff')
    if '"chatlab"' in text:
        return True
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith('#'):
            continue
        return '"_type"' in s and '"header"' in s
    return False


PLACEHOLDER_RE = re.compile(r'^\[[^\[\]]{1,24}\]$')


def _is_placeholder(text):
    """content 是否只是 [Image] / [Sticker] 这类占位串。"""
    return bool(text) and bool(PLACEHOLDER_RE.match(text.strip()))


def _new_row(idx, msg):
    """把一条 ChatLab message 转成我们原生的输入行。"""
    mtype = msg.get('type')
    try:
        mtype = int(mtype)
    except (TypeError, ValueError):
        mtype = TYPE_TEXT

    display = (msg.get('groupNickname') or msg.get('accountName') or '').strip()
    uid = str(msg.get('sender') or '').strip()
    content = msg.get('content')
    text = '' if content is None else str(content).strip()

    row = {
        'msg_id': str(msg.get('platformMessageId') or 'cl%06d' % idx),
        'ts': msg.get('timestamp'),
        'sender_uid': uid,
        'sender_name': display or uid,
        'text': text,
        'at_uids': [],                      # ChatLab 格式不表达 @
        'reply_to_msg_id': msg.get('replyToMessageId') or None,
        'media_unavailable': [],            # 视觉类媒体（本格式无文件）
    }

    if mtype in VISION_LABEL:
        # 图片/表情：保留文本标签，交给 0.ROW 之后的文本流程
        row['media_unavailable'].append(VISION_LABEL[mtype])
        if _is_placeholder(text) or not text:
            row['text'] = ''
    elif mtype in PLAIN_LABEL:
        label = '[%s]' % PLAIN_LABEL[mtype]
        row['text'] = label if (_is_placeholder(text) or not text) else (text + ' ' + label).strip()
    elif mtype in EXTRA_LABEL:
        label = '[%s]' % EXTRA_LABEL[mtype]
        row['text'] = label if (_is_placeholder(text) or not text) else (text + ' ' + label).strip()
    # TYPE_TEXT / TYPE_REPLY 直通
    return row, mtype


def load(path, keep_system=False):
    """读取 ChatLab 文件，返回原生格式行列表。

    返回 ``(rows, meta, stats)``：
        rows   —— 每行一个 dict，字段与原生 messages.jsonl 对齐
        meta   —— 文件头里的 meta（JSONL 的 header 行 / JSON 的 meta 字段）
        stats  —— 统计信息（各种类型数量、跳过的系统消息数）
    """
    with open(path, encoding='utf-8-sig') as f:
        raw = f.read()

    rows, meta = [], {}
    stats = {'system_skipped': 0, 'by_type': {}}
    idx = 0

    stripped = raw.lstrip()
    is_jsonl = stripped.startswith('{') and '"_type"' in stripped[:4096]
    if not is_jsonl and stripped.startswith('{'):
        # 可能是单个 JSON 对象
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError:
            doc = None
        if isinstance(doc, dict) and 'chatlab' in doc:
            meta = doc.get('meta') or {}
            for msg in (doc.get('messages') or []):
                idx += 1
                row, mtype = _new_row(idx, msg)
                stats['by_type'][mtype] = stats['by_type'].get(mtype, 0) + 1
                if mtype in SYSTEM_TYPES and not keep_system:
                    stats['system_skipped'] += 1
                    continue
                rows.append(row)
            return rows, meta, stats

    # JSONL 流式读取
    for line_no, line in enumerate(raw.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith('#'):
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            stats['bad_lines'] = stats.get('bad_lines', 0) + 1
            continue
        if not isinstance(obj, dict):
            stats['bad_lines'] = stats.get('bad_lines', 0) + 1
            continue
        t = obj.get('_type')
        if t == 'header':
            meta = obj.get('meta') or {}
            continue
        if t == 'member':
            continue                    # 成员信息在消息里自带，无需单独处理
        if t != 'message':
            stats['unknown_lines'] = stats.get('unknown_lines', 0) + 1
            continue
        idx += 1
        row, mtype = _new_row(idx, obj)
        stats['by_type'][mtype] = stats['by_type'].get(mtype, 0) + 1
        if mtype in SYSTEM_TYPES and not keep_system:
            stats['system_skipped'] += 1
            continue
        rows.append(row)
    return rows, meta, stats


def meta_text(meta, stats):
    """给命令行打印的一行说明。"""
    bits = []
    if meta.get('name'):
        bits.append('群名=%s' % meta['name'])
    if meta.get('platform'):
        bits.append('平台=%s' % meta['platform'])
    if meta.get('type'):
        bits.append('类型=%s' % meta['type'])
    if stats.get('system_skipped'):
        bits.append('跳过系统消息 %d 条' % stats['system_skipped'])
    return ' | '.join(bits)
