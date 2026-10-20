#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阶段 0 · 步骤 0.1：导入用户已导出的消息流（**不做任何数据获取/数据库读取/解密**）。

输入
    ``<data_root>/groups/<会话ID>_<名称>/input/messages.jsonl``
    每行一条消息，字段约定见 README「数据格式」：
        msg_id, ts, sender_uid, sender_name, text, at_uids, reply_to_msg_id, media[]

输出（都在同一个群目录下）
    messages.jsonl     规范化后的消息流（补上 seq / msg_uid / display / media 占位符）
    name_table.jsonl   发送者名单（供后续 @ 名字匹配使用）
    dropped.jsonl      被丢弃的条目及原因
    stats.json         统计摘要

兼容输入
    也可以直接喂 **ChatLab 标准格式**（chatlab v0.0.1/0.0.2，JSON 或 JSONL）：
    用 ``--input-format chatlab`` 显式指定，或让它自动识别。
    ⚠️ 该格式不携带聊天媒体的文件路径，图片/表情会降级成 ``[图片]`` / ``[表情]``
    文本标签，0.5 与 2.1 不会对它们做视觉识别。

本脚本**不含任何数据获取、数据库读取、解密或 QQ 协议解析逻辑**：数据由使用者
自行合法导出，导出格式只要符合上面的 JSONL 约定即可。

用法::

    python stages/s0_import.py --group 123456789
    python stages/s0_import.py --group 123456789 --input my_export.jsonl
"""
import argparse
import collections
import hashlib
import json
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)
sys.dont_write_bytecode = True   # 不在仓库里留下 __pycache__

from config import ConfigError, add_common_args, load_config  # noqa: E402
from core import chatlab as chatlab_import                              # noqa: E402

#: 媒体类型 → 正文里的占位标签。图片/表情会在 0.5 被替换成描述；
#: 视频/文件/语音不做视觉处理，保留裸标签。
KIND_TAG = {
    'pic': '图片', 'image': '图片', '图片': '图片', 'photo': '图片', 'image_url': '图片',
    'emoji': '表情', '表情': '表情', 'sticker': '表情', 'mface': '表情',
    'video': '视频', '视频': '视频',
    'file': '文件', '文件': '文件',
    'ptt': '语音', 'voice': '语音', 'audio': '语音', '语音': '语音',
}
#: 哪些类型在阶段 0.5 需要视觉模型转文本
VISION_KINDS = ('图片', '表情')


def normalize_kind(kind):
    return KIND_TAG.get(str(kind or '').strip().lower(), '文件')


def read_input(path):
    rows, bad = [], 0
    with open(path, encoding='utf-8-sig') as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            if not isinstance(d, dict):
                bad += 1
                continue
            rows.append((line_no, d))
    return rows, bad


def main(argv=None):
    ap = argparse.ArgumentParser(description='0.1 导入用户已导出的 messages.jsonl')
    add_common_args(ap)
    ap.add_argument('--input', default=None,
                    help='输入 JSONL 路径（默认 <group_dir>/input/messages.jsonl）')
    ap.add_argument('--input-format', choices=('auto', 'native', 'chatlab'), default='auto',
                    help='输入格式：native=本项目的 messages.jsonl；'
                         'chatlab=ChatLab 标准格式（JSON/JSONL）；auto=自动识别（默认）')
    ap.add_argument('--keep-system', action='store_true',
                    help='ChatLab 格式下保留系统消息（入群/撤回等，默认丢弃）')
    args = ap.parse_args(argv)

    cfg = load_config(args, require_group=True)
    group_dir = cfg.ensure_group_dir()
    in_path = os.path.abspath(args.input) if args.input else cfg.input_messages

    if not os.path.isfile(in_path):
        print('错误：找不到输入文件 %s' % in_path, file=sys.stderr)
        print('  请把已合法导出的消息放进该路径（每行一条 JSON），或加 --input 指定文件。',
              file=sys.stderr)
        print('  字段约定见 README「数据格式」一节。', file=sys.stderr)
        return 2

    fmt = args.input_format
    if fmt == 'auto':
        fmt = 'chatlab' if chatlab_import.looks_like_chatlab(in_path) else 'native'

    cl_meta, cl_stats = {}, {}
    if fmt == 'chatlab':
        cl_rows, cl_meta, cl_stats = chatlab_import.load(in_path, keep_system=args.keep_system)
        rows = [(i + 1, r) for i, r in enumerate(cl_rows)]
        bad = cl_stats.get('bad_lines', 0)
        print('会话 %s | 输入 %s' % (cfg.group_id, in_path))
        print('格式：ChatLab 标准格式（%s）' % (chatlab_import.meta_text(cl_meta, cl_stats) or '无 header 信息'))
        print('读到 %d 条消息（跳过无法解析的行 %d 条）' % (len(rows), bad))
        if cl_meta.get('name') and not cfg.group_name:
            print('  提示：文件头群名为「%s」，可把它填进 config 的 group_name。' % cl_meta['name'])
        print('  ⚠ 该格式不含媒体文件路径：图片/表情降级为文本标签，0.5 与 2.1 不做视觉识别。')
    else:
        rows, bad = read_input(in_path)
        print('会话 %s | 输入 %s' % (cfg.group_id, in_path))
        print('读到 %d 条消息（跳过无法解析的行 %d 条）' % (len(rows), bad))

    # ---- 第一遍：建立 msg_id → 发送者 的索引（reply 关系需要向后查找目标）
    sender_of = {}
    for _, d in rows:
        mid = str(d.get('msg_id') or '').strip()
        uid = str(d.get('sender_uid') or '').strip()
        if mid and uid:
            sender_of[mid] = uid

    peer = cfg.group_id
    out_msgs, dropped = [], []
    for order, (line_no, d) in enumerate(rows):
        ts = d.get('ts')
        try:
            ts = int(ts)
        except (TypeError, ValueError):
            dropped.append({'line': line_no, 'msg_id': d.get('msg_id'),
                            'reason': 'ts 不是整数'})
            continue
        uid = str(d.get('sender_uid') or '').strip()
        if not uid:
            dropped.append({'line': line_no, 'msg_id': d.get('msg_id'),
                            'reason': '缺少 sender_uid'})
            continue
        display = str(d.get('sender_name') or uid).strip()
        body = str(d.get('text') or '')

        media = []
        for item in (d.get('media') or []):
            if not isinstance(item, dict):
                continue
            kind = normalize_kind(item.get('kind'))
            md5 = str(item.get('md5') or '').strip().lower()
            if not md5:
                # 没有 md5 时用 内容指纹 生成一个稳定 id（只影响占位符 id，不影响路径）
                seed = '%s|%s|%s' % (d.get('msg_id'), item.get('path'), kind)
                md5 = hashlib.md5(seed.encode('utf-8')).hexdigest()
            media.append({'md5': md5, 'kind': kind,
                          'path': str(item.get('path') or ''),
                          'id': '[%s#%s]' % (kind, md5[:8])})

        cleanup = {'removed': [], 'lost': []}
        # 正文里补回媒体占位符：图片/表情带 md5 前缀，供 0.5 精确替换；
        # 视频/文件/语音保留裸标签（不做视觉处理）。
        for m in media:
            if m['kind'] in VISION_KINDS:
                body += ' ' + m['id']
            else:
                body += ' [%s]' % m['kind']
        # 正文里直接出现的裸 [图片]/[表情]（没有对应媒体）视为丢失
        for label in ('图片', '表情'):
            if ('[%s]' % label) in body:
                body = body.replace('[%s]' % label, '')
                cleanup['lost'].append(label)
        body = body.strip()

        # ChatLab 等格式可能声明"这里有图但没有文件"：在 cleanup 之后补成文本标签，
        # 这样既不会触发视觉调用，也不会像裸 [图片] 那样被当作丢失标签删掉。
        for label in (d.get('media_unavailable') or []):
            label = '[%s]' % label
            if label not in body:
                body = (body + ' ' + label).strip()

        if not body and not media:
            dropped.append({'line': line_no, 'msg_id': d.get('msg_id'),
                            'sender_uid': uid, 'reason': '无可用内容（空正文且无媒体）'})
            continue

        reply = None
        reply_id = str(d.get('reply_to_msg_id') or '').strip()
        if reply_id:
            reply = {'qq': sender_of.get(reply_id), 'msg_id': reply_id}

        mentions = []
        for target in (d.get('at_uids') or []):
            target = str(target or '').strip()
            if target:
                mentions.append({'qq': target})

        cat = ['text'] if body else []
        cat += sorted({m['kind'] for m in media})
        out_msgs.append({'peer': peer, 'sender_qq': uid, 'sender_uid': uid,
                         'display': display, 'ts': ts, 'body': body,
                         'media': media, 'mentions': mentions, 'reply_to': reply,
                         'cleanup': cleanup, 'cat': cat})

    if not out_msgs:
        print('错误：没有任何可用消息，无法继续。', file=sys.stderr)
        return 2

    # 稳定排序：同一毫秒内保持导出文件里的原始顺序
    out_msgs.sort(key=lambda m: m['ts'])

    mf_path = os.path.join(group_dir, 'messages.jsonl')
    ef_path = os.path.join(group_dir, 'dropped.jsonl')
    nf_path = os.path.join(group_dir, 'name_table.jsonl')
    with open(mf_path, 'w', encoding='utf-8') as mf, \
            open(ef_path, 'w', encoding='utf-8') as df:
        for i, m in enumerate(out_msgs, 1):
            msg_uid = '%s_%s_%s_%d' % (peer, m['ts'], m['sender_uid'], i)
            rec = {'seq': i, 'peer': peer, 'msg_uid': msg_uid, 'ts': m['ts'],
                   'sender_qq': m['sender_qq'], 'sender_uid': m['sender_uid'],
                   'display': m['display'], 'body': m['body'],
                   'text': '%s: %s' % (m['display'], m['body']),
                   'media': m['media'], 'mentions': m['mentions'],
                   'reply_to': m['reply_to'],
                   'cleanup': m['cleanup'], 'cat': m['cat'],
                   'media_unavailable': m.get('media_unavailable') or []}
            mf.write(json.dumps(rec, ensure_ascii=False) + '\n')
        for d in dropped:
            df.write(json.dumps(d, ensure_ascii=False) + '\n')

    senders = collections.OrderedDict()
    for m in out_msgs:
        senders.setdefault(m['sender_qq'], m['display'])
    with open(nf_path, 'w', encoding='utf-8') as nf:
        for qq, name in senders.items():
            nf.write(json.dumps({'peer': peer, 'qq': qq, 'card': name,
                                 'qq_nick': '', 'remark': '', 'display': name},
                                ensure_ascii=False) + '\n')

    st = collections.Counter()
    st['raw'] = len(rows)
    st['kept'] = len(out_msgs)
    st['dropped'] = len(dropped)
    st['unparsable_lines'] = bad
    st['senders'] = len(senders)
    st['with_media'] = sum(1 for m in out_msgs if m['media'])
    st['images'] = sum(1 for m in out_msgs for x in m['media'] if x['kind'] == '图片')
    st['emojis'] = sum(1 for m in out_msgs for x in m['media'] if x['kind'] == '表情')
    st['mentions'] = sum(len(m['mentions']) for m in out_msgs)
    st['media_unavailable'] = sum(len(m.get('media_unavailable') or []) for m in out_msgs)
    st['replies'] = sum(1 for m in out_msgs if m['reply_to'])
    st['ts_first'] = min(m['ts'] for m in out_msgs)
    st['ts_last'] = max(m['ts'] for m in out_msgs)
    with open(os.path.join(group_dir, 'stats.json'), 'w', encoding='utf-8') as f:
        json.dump(dict(st), f, ensure_ascii=False, indent=1)

    print('\n=== 0.1 导入结果 ===')
    print('  保留 %d 条 | 丢弃 %d 条 | 发言人 %d 人' % (st['kept'], st['dropped'], st['senders']))
    print('  图片 %d 张 | 表情 %d 个 | @ %d 处 | reply %d 条'
          % (st['images'], st['emojis'], st['mentions'], st['replies']))
    if st['media_unavailable']:
        print('  无文件媒体（已降级为文本标签）%d 处' % st['media_unavailable'])
    print('  时间范围 %s → %s' % (st['ts_first'], st['ts_last']))
    print('\n产物：')
    for p in (mf_path, nf_path, ef_path):
        print('  %s' % p)
    print('\n下一步：python stages/s0_media.py --group %s' % peer)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except ConfigError as e:
        print('配置错误：%s' % e, file=sys.stderr)
        sys.exit(2)
