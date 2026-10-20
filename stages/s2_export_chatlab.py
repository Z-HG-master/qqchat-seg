#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阶段 2 · 步骤 2.4：把最终片段**反向导出为 ChatLab 标准格式**（可选）。

用途
    把 ``segments_ctx.jsonl``（我们切好的事件）转成
    `ChatLab <https://github.com/ChatLab/ChatLab>`_ 的标准格式，
    这样可以直接用 ChatLab 的界面 / SQL 引擎浏览、检索这些事件。

    与 0.x 的「输入兼容」正好相反：那边是 ChatLab → 我们，这里是 我们 → ChatLab。

两种形态
    ``--mode split``（默认）
        一个事件一个文件：``chatlab/<event_id>.jsonl``
        ChatLab 是「一个文件一个会话」，拆开后在 ChatLab 里表现为多个聊天，
        适合按事件逐个查看。同时写 ``chatlab/index.json`` 做索引。

    ``--mode single``
        整个群一个文件：``chatlab/<群标签>.jsonl``
        事件之间用系统消息（``type=80``）分隔，适合整体导入后一次查询。

已知取舍
    · ChatLab 格式没有媒体字段，所以图片只剩正文里的 ``[图片：…]`` 文字；
    · 我们的 ``segments_ctx.jsonl`` 不保存每条消息的时间戳，导出时用
      ``ts_first + 序号`` 近似（同一事件内相对顺序正确，绝对时间会偏移若干秒）。
      需要精确时间戳的话，改用 ``s1_events_gated.jsonl`` + ``units_at.jsonl`` 自行组装。

用法::

    python stages/s2_export_chatlab.py --group 123456789
    python stages/s2_export_chatlab.py --group 123456789 --mode single
"""
import argparse
import hashlib
import json
import os
import sys
import time

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)
sys.dont_write_bytecode = True   # 不在仓库里留下 __pycache__

from config import ConfigError, add_common_args, load_config  # noqa: E402

FORMAT_VERSION = '0.0.2'
PLATFORM = 'qq'
TYPE_TEXT = 0
TYPE_SYSTEM = 80


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


def split_sender(text):
    """把 '示例用户A: 正文' 拆成 (显示名, 正文)。没有前缀时返回 ('', text)。"""
    who, sep, rest = text.partition(': ')
    if not sep:
        return '', text
    return who, rest


def sender_id(name):
    """ChatLab 的 sender 是 platformId；我们没有真实 id，用显示名派生一个稳定值。"""
    if not name:
        return 'unknown'
    return 'u_' + hashlib.md5(name.encode('utf-8')).hexdigest()[:12]


def build_member(platform_id, name, nick=None):
    m = {'platformId': platform_id, 'accountName': name or platform_id}
    if nick and nick != name:
        m['groupNickname'] = nick
    return m


def event_to_messages(row):
    """一个事件 → ChatLab message 列表（含每个成员的 platformId 映射）。"""
    ts0 = row.get('ts_first') or 0
    out, members = [], {}
    for i, raw in enumerate(row.get('messages') or []):
        who, body = split_sender(raw)
        pid = sender_id(who)
        if who:
            members[pid] = build_member(pid, who)
        out.append({
            'sender': pid if who else 'unknown',
            'accountName': who or 'unknown',
            'timestamp': int(ts0) + i,          # 近似：事件内相对顺序正确
            'type': TYPE_TEXT,
            'content': body if who else raw,
        })
    return out, list(members.values())


def _line(kind, obj):
    """构造一行：_type 放最前面，读起来更像规范里的示例。"""
    out = {'_type': kind}
    out.update(obj)
    return out


def write_jsonl(path, lines):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        for obj in lines:
            f.write(json.dumps(obj, ensure_ascii=False) + '\n')


def main(argv=None):
    ap = argparse.ArgumentParser(description='2.4 导出 ChatLab 标准格式')
    add_common_args(ap)
    ap.add_argument('--src', default='segments_ctx.jsonl', help='源片段文件')
    ap.add_argument('--out-dir', default='chatlab', help='输出子目录（相对群目录）')
    ap.add_argument('--mode', choices=('split', 'single'), default='split',
                    help='split=一个事件一个文件（默认）；single=整个群一个文件')
    ap.add_argument('--limit', type=int, default=0, help='只导出前 N 个事件（0=全部）')
    ap.add_argument('--no-separators', action='store_true',
                    help='single 模式下不插入事件分隔的系统消息')
    args = ap.parse_args(argv)

    cfg = load_config(args, require_group=True)
    group_dir = cfg.group_dir
    src_path = os.path.join(group_dir, args.src)
    if not os.path.isfile(src_path):
        print('错误：找不到 %s（请先跑完 2.2）' % src_path, file=sys.stderr)
        return 2

    rows = load_jsonl(src_path)
    if args.limit:
        rows = rows[:args.limit]
    if not rows:
        print('错误：%s 为空，没有可导出的事件。' % src_path, file=sys.stderr)
        return 2

    out_dir = os.path.join(group_dir, args.out_dir)
    os.makedirs(out_dir, exist_ok=True)

    header_common = {
        'version': FORMAT_VERSION,
        'exportedAt': int(time.time()),
        'generator': 'qqchat-seg 2.4',
        'description': '由 qqchat-seg 事件切分结果导出',
    }
    index_entries = []
    n_msgs = 0

    if args.mode == 'split':
        for row in rows:
            eid = str(row.get('event_id') or 'event')
            msgs, members = event_to_messages(row)
            n_msgs += len(msgs)
            name = '%s · %s' % (cfg.group_name or cfg.group_id, eid)
            fname = '%s.jsonl' % eid
            lines = [{
                '_type': 'header',
                'chatlab': dict(header_common),
                'meta': {'name': name, 'platform': PLATFORM, 'type': 'group',
                         'groupId': cfg.group_id},
            }]
            lines += [_line('member', m) for m in members]
            lines += [_line('message', m) for m in msgs]
            write_jsonl(os.path.join(out_dir, fname), lines)
            index_entries.append({'event_id': eid, 'file': fname,
                                  'n_msgs': len(msgs),
                                  'ts_first': row.get('ts_first'),
                                  'ts_last': row.get('ts_last'),
                                  'participants': row.get('participants') or []})
        with open(os.path.join(out_dir, 'index.json'), 'w', encoding='utf-8') as f:
            json.dump({'chatlab': dict(header_common),
                       'group_id': cfg.group_id, 'group_name': cfg.group_name,
                       'n_events': len(rows), 'events': index_entries},
                      f, ensure_ascii=False, indent=1)
        printed = os.path.join(out_dir, '<event_id>.jsonl')
    else:
        lines = [{
            '_type': 'header',
            'chatlab': dict(header_common),
            'meta': {'name': cfg.group_name or str(cfg.group_id),
                     'platform': PLATFORM, 'type': 'group', 'groupId': cfg.group_id},
        }]
        all_members = {}
        tail_ts = 0
        for row in rows:
            eid = str(row.get('event_id') or 'event')
            msgs, members = event_to_messages(row)
            for m in members:
                all_members[m['platformId']] = m
            n_msgs += len(msgs)
            if not args.no_separators:
                ts = int(row.get('ts_first') or tail_ts)
                lines.append({'_type': 'message', 'sender': 'system',
                              'accountName': '系统',
                              'timestamp': ts, 'type': TYPE_SYSTEM,
                              'content': '[事件 %s]' % eid})
            lines += [_line('message', m) for m in msgs]
            tail_ts = int(row.get('ts_last') or tail_ts)
        # 成员行必须排在消息行之前
        body = lines[1:]
        members_lines = [_line('member', m) for m in all_members.values()]
        lines = lines[:1] + members_lines + body
        fname = '%s.jsonl' % (cfg.group_name or cfg.group_id)
        write_jsonl(os.path.join(out_dir, fname), lines)
        printed = os.path.join(out_dir, fname)

    total = sum(os.path.getsize(os.path.join(out_dir, n))
                for n in os.listdir(out_dir) if n.endswith(('.jsonl', '.json')))
    print('模式：%s | 事件 %d | 消息 %d' % (args.mode, len(rows), n_msgs))
    print('输出目录：%s' % out_dir)
    print('  产物：%s' % printed)
    if args.mode == 'split':
        print('  索引：%s' % os.path.join(out_dir, 'index.json'))
    print('  总体积：%.1f KB' % (total / 1024))
    print('')
    print('校验（可选，需要 Node.js）：')
    print('  npx chatlab-cli validate "%s" --json' % printed)
    print('导入 ChatLab 后即可用它的界面 / SQL 引擎浏览这些事件。')
    print('注意：ChatLab 格式没有媒体字段，图片只保留为正文里的 [图片：…] 文字。')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except ConfigError as e:
        print('配置错误：%s' % e, file=sys.stderr)
        sys.exit(2)
