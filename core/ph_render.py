# -*- coding: utf-8 -*-
"""媒体占位符的稳健解析与上下文渲染（被 s2_media_context / s2_build_segments 引用）。

修过的两个坑
  Bug1: 媒体描述里可能含 ``]``（例如 "哇我上电视了[天哪]"），简单正则会把占位符截断、留残渣。
        解法：括号深度解析，见 :func:`parse_placeholders`。
  Bug2: 一条消息可以带多张媒体，必须只把「目标那一张」标成 ``【本次图片】``，
        其余保留阶段 0 的描述，见 :func:`render_context`。

占位符形态::

    [图片#ab12cd34]       阶段 0 导入后、媒体转文本前的原始占位
    [图片：一句话描述]      阶段 0 转文本后的占位
    [表情：情绪]           表情包占位
    [视频] / [文件] / [语音]  非图片媒体（不做视觉描述）
"""
import re

OPEN_KINDS = ('图片', '表情', '视频', '文件', '语音')


def parse_placeholders(body):
    """返回 ``[(kind, raw, start, end)]``，按出现顺序；支持描述里嵌套 ``[]``。"""
    out = []
    i = 0
    n = len(body or '')
    while i < n:
        if body[i] != '[':
            i += 1
            continue
        kind = None
        for k in OPEN_KINDS:
            if body.startswith('[' + k, i):
                kind = k
                break
        if kind is None:
            i += 1
            continue
        j = i + 1
        depth = 0
        while j < n:
            if body[j] == '[':
                depth += 1
            elif body[j] == ']':
                if depth == 0:
                    out.append((kind, body[i:j + 1], i, j + 1))
                    i = j + 1
                    break
                depth -= 1
            j += 1
        else:
            break   # 没找到闭合括号，放弃
    return out


def render_context(unit_bodies, target_unit_idx, target_occ_in_unit):
    """渲染「某事件全文 + 目标图位置」的上下文。

    unit_bodies: ``[(display, body)]``，按时间顺序；
    target_unit_idx: 目标图所在消息在列表中的下标（0-based）；
    target_occ_in_unit: 该消息里第几个图片占位符是本次要识别的那张（0-based）。

    目标图 → ``【本次图片】``；其余媒体 → 保留阶段 0 的描述。
    """
    lines = []
    for i, (disp, body) in enumerate(unit_bodies):
        phs = parse_placeholders(body)
        if not phs:
            lines.append('%d. %s: %s' % (i + 1, disp, body))
            continue
        pieces, last = [], 0
        for k, (kind, raw, a, b) in enumerate(phs):
            pieces.append(body[last:a])
            if kind == '图片' and i == target_unit_idx and k == target_occ_in_unit:
                pieces.append('【本次图片】')
            else:
                pieces.append(raw)
            last = b
        pieces.append(body[last:])
        lines.append('%d. %s: %s' % (i + 1, disp, ''.join(pieces)))
    return '\n'.join(lines)


#: 质量闸门里用来统计「文字含量」的媒体占位正则（图片/表情带描述，其它媒体不带）。
MEDIA_TOKEN_RE = re.compile(r'\[(?:图片|表情)[：:][^\]]*\]|\[(?:视频|文件|语音)[^\]]*\]')
