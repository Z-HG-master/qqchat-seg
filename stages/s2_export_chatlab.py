#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阶段 2 · 步骤 2.4：把最终片段**反向导出为 ChatLab 标准格式**（可选）。

用途
    把 ``segments_ctx.jsonl``（我们切好的事件）转成
    `ChatLab <https://github.com/ChatLab/ChatLab>`_ 的标准格式，
    这样可以直接用 ChatLab 的界面 / SQL 引擎浏览、检索这些事件。
    与 0.x 的「输入兼容」正好相反：那边是 ChatLab → 我们，这里是 我们 → ChatLab。

时间戳：用真实值，不用近似
    ``segments_ctx.jsonl`` 本身不保存时间戳，所以这里会 join 两张旁表还原：

        ``s1_events_gated.jsonl``（event_id → unit_ids）
        ``units_at.jsonl``        （unit_id → ts_first / sender_qq / display）

    导出的每条消息取所属 unit 的 **ts_first（真实时间）**，发送者取 ``sender_qq``
    （真实账号 id，重名用户不会被合并）。粒度是 **unit** 而不是原始单条消息——
    因为 0.6 会把同一人的连发合并成一个 unit，最终文本本来就是按 unit 生成的。

    若旁表缺失或长度对不上，自动退回 ``ts_first + i`` 近似，并在输出里明确告警
    （近似时间戳会让 ChatLab 的时间轴可视化失真）。

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
    · 没有 @ 提及字段（规范不表达 @），导出时无 @ 关系可写。

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
PLATFORM = 'qq'          # ChatLab 规范必填；用 --platform 覆盖
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


def split_sender(text, known_names=None):
    """把 '昵称: 正文' 拆成 (昵称, 正文)。

    只在没有旁表、只能靠字符串猜的时候用（近似路径）。``known_names`` 给定时
    会校验候选昵称是否真的出现过，避免正文里的冒号被误判成分隔符。
    """
    who, sep, rest = text.partition(': ')
    if not sep:
        return '', text
    if known_names is not None and who not in known_names:
        return '', text
    return who, rest


def sender_id(qq, name):
    """ChatLab 的 sender 是 platformId。优先用真实账号 id，其次由显示名派生。"""
    qq = str(qq or '').strip()
    if qq:
        return 'u_%s' % qq
    if not name:
        return 'unknown'
    return 'u_n_' + hashlib.md5(name.encode('utf-8')).hexdigest()[:12]


def build_member(platform_id, name, nick=None):
    m = {'platformId': platform_id, 'accountName': name or platform_id}
    if nick and nick != name:
        m['groupNickname'] = nick
    return m


def load_context(group_dir, events_name, units_name):
    """读旁表，返回 {event_id: [{ts, qq, display}, ...]}（按 unit 顺序）。"""
    units = {}
    for u in load_jsonl(os.path.join(group_dir, units_name)):
        uid = u.get('unit_id')
        if uid:
            units[uid] = u
    ctx = {}
    for e in load_jsonl(os.path.join(group_dir, events_name)):
        eid = e.get('event_id')
        ids = e.get('unit_ids') or []
        if not eid or not ids:
            continue
        seq = []
        for uid in ids:
            u = units.get(uid) or {}
            ts = u.get('ts_first')
            try:
                ts = int(ts)
            except (TypeError, ValueError):
                ts = None
            seq.append({'ts': ts,
                        'qq': str(u.get('sender_qq') or '').strip(),
                        'display': str(u.get('display') or '').strip()})
        ctx[eid] = seq
    return ctx


def event_messages(row, ctx_seq, known_names=None):
    """一个事件 → (ChatLab message 列表, 成员 dict, 统计)。"""
    try:
        ts0 = int(row.get('ts_first') or 0)
    except (TypeError, ValueError):
        ts0 = 0
    msgs, members = [], {}
    n_real_ts = n_guess_ts = 0
    known = known_names if not ctx_seq else None

    for i, raw in enumerate(row.get('messages') or []):
        u = ctx_seq[i] if (ctx_seq and i < len(ctx_seq)) else None
        if u:
            who = u.get('display') or ''
            qq = u.get('qq') or ''
            ts = u.get('ts')
            content = raw
            # segments 消息形如 '显示名: 正文'；用显示名做精确前缀剥离，
            # 昵称里带冒号也不会拆错。
            prefix = who + ': '
            if who and content.startswith(prefix):
                content = content[len(prefix):]
            if ts is not None:
                n_real_ts += 1
            else:
                ts = ts0 + i
                n_guess_ts += 1
        else:
            who, content = split_sender(raw, known)
            qq, ts = '', ts0 + i
            n_guess_ts += 1

        pid = sender_id(qq, who)
        if who:
            members[pid] = build_member(pid, who)
        msgs.append({
            'sender': pid if who else 'unknown',
            'accountName': who or 'unknown',
            'timestamp': int(ts),
            'type': TYPE_TEXT,
            'content': content,
        })
    return msgs, members, {'real_ts': n_real_ts, 'guess_ts': n_guess_ts}


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
    ap.add_argument('--events', default='s1_events_gated.jsonl',
                    help='事件表（event_id → unit_ids，用于还原真实时间戳）')
    ap.add_argument('--units', default='units_at.jsonl',
                    help='unit 表（unit_id → ts_first / sender_qq）')
    ap.add_argument('--platform', default=PLATFORM,
                    help='写入 meta.platform 的平台标识（默认 qq；ChatLab 规范必填字段）')
    ap.add_argument('--out-dir', default='chatlab', help='输出子目录（相对群目录）')
    ap.add_argument('--mode', choices=('split', 'single'), default='split',
                    help='split=一个事件一个文件（默认）；single=整个群一个文件')
    ap.add_argument('--limit', type=int, default=0, help='只导出前 N 个事件（0=全部）')
    ap.add_argument('--no-separators', action='store_true',
                    help='single 模式下不插入事件分隔的系统消息')
    ap.add_argument('--sort-by-time', action='store_true',
                    help='每个文件内按时间戳排序（ChatLab 推荐；'
                         '事件默认保持对话顺序，1.2 非连续合并会让时间序与对话序不同）')
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

    ctx = load_context(group_dir, args.events, args.units)
    known_names = {u['display'] for seq in ctx.values() for u in seq if u.get('display')}

    # 重名检测：同一显示名对应多个真实账号（QQ 群里很常见）。
    # 用 sender_qq 做 platformId 就不会把两个人合并成一个成员。
    name_to_qq = {}
    for seq in ctx.values():
        for u in seq:
            if u.get('display') and u.get('qq'):
                name_to_qq.setdefault(u['display'], set()).add(u['qq'])
    dup_names = {n: qs for n, qs in name_to_qq.items() if len(qs) > 1}

    # 旁表必须与片段一一对应，否则退回近似
    matched = sum(1 for r in rows
                  if ctx.get(r.get('event_id'))
                  and len(ctx[r['event_id']]) == len(r.get('messages') or []))
    use_ctx = matched == len(rows)
    if not use_ctx:
        print('⚠ 旁表与片段对不上（%d/%d 个事件可精确还原），本次退回近似时间戳。'
              % (matched, len(rows)), file=sys.stderr)
        print('  精确导出需要 %s + %s 与 %s 同时存在且事件数一致。'
              % (args.events, args.units, args.src), file=sys.stderr)

    out_dir = os.path.join(group_dir, args.out_dir)
    os.makedirs(out_dir, exist_ok=True)

    header_common = {
        'version': FORMAT_VERSION,
        'exportedAt': int(time.time()),
        'generator': 'socialchat-seg 2.4',
        'description': '由 socialchat-seg 事件切分结果导出',
    }
    index_entries = []
    n_msgs = n_real = n_guess = 0

    def emit(row):
        nonlocal n_msgs, n_real, n_guess
        eid = str(row.get('event_id') or 'event')
        seq = ctx.get(eid) if use_ctx else None
        msgs, members, st = event_messages(row, seq, known_names)
        n_msgs += len(msgs)
        n_real += st['real_ts']
        n_guess += st['guess_ts']
        return eid, msgs, members

    if args.mode == 'split':
        for row in rows:
            eid, msgs, members = emit(row)
            if args.sort_by_time:
                msgs.sort(key=lambda m: m['timestamp'])
            name = '%s · %s' % (cfg.group_name or cfg.group_id, eid)
            fname = '%s.jsonl' % eid
            lines = [{
                '_type': 'header',
                'chatlab': dict(header_common),
                'meta': {'name': name, 'platform': args.platform, 'type': 'group',
                         'groupId': cfg.group_id},
            }]
            lines += [_line('member', m) for m in members.values()]
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
                     'platform': args.platform, 'type': 'group', 'groupId': cfg.group_id},
        }]
        all_members = {}
        tail_ts = 0
        for row in rows:
            eid, msgs, members = emit(row)
            all_members.update(members)
            if not args.no_separators:
                try:
                    ts = int(row.get('ts_first') or tail_ts)
                except (TypeError, ValueError):
                    ts = tail_ts
                lines.append({'_type': 'message', 'sender': 'system',
                              'accountName': '系统',
                              'timestamp': ts, 'type': TYPE_SYSTEM,
                              'content': '[事件 %s]' % eid})
            lines += [_line('message', m) for m in msgs]
            try:
                tail_ts = int(row.get('ts_last') or tail_ts)
            except (TypeError, ValueError):
                pass
        body = lines[1:]
        if args.sort_by_time:
            body = [x for x in body if not (x.get('_type') == 'message'
                                            and x.get('type') == TYPE_SYSTEM)]
            body.sort(key=lambda x: x.get('timestamp') or 0)
            print('提示：--sort-by-time 已按时间全局排序，事件分隔行被自动省略。')
        members_lines = [_line('member', m) for m in all_members.values()]
        lines = lines[:1] + members_lines + body
        fname = '%s.jsonl' % (cfg.group_name or cfg.group_id)
        write_jsonl(os.path.join(out_dir, fname), lines)
        printed = os.path.join(out_dir, fname)

    total = sum(os.path.getsize(os.path.join(out_dir, n))
                for n in os.listdir(out_dir) if n.endswith(('.jsonl', '.json')))
    print('模式：%s | 事件 %d | 消息 %d' % (args.mode, len(rows), n_msgs))
    if dup_names:
        print('重名：%d 组显示名对应多个账号，已用真实账号 id 区分（互不合并）'
              % len(dup_names))
    if n_msgs:
        print('时间戳：真实 %d 条（unit.ts_first）| 近似 %d 条' % (n_real, n_guess))
    if n_guess:
        print('  ⚠ 有 %d 条用了近似时间戳，ChatLab 的时间轴可视化会失真；'
              '补齐旁表后重跑即可。' % n_guess)
    print('输出目录：%s' % out_dir)
    print('  产物：%s' % printed)
    if args.mode == 'split':
        print('  索引：%s' % os.path.join(out_dir, 'index.json'))
    print('  总体积：%.1f KB' % (total / 1024))
    print('')
    print('校验（可选，需要 Node.js）：')
    print('  npx chatlab-cli validate "%s" --json' % printed)
    print('注意：ChatLab 格式没有媒体与 @ 字段，图片只保留为正文里的 [图片：…] 文字。')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except ConfigError as e:
        print('配置错误：%s' % e, file=sys.stderr)
        sys.exit(2)
