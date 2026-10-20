#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""QQ 群聊事件切分 · 全流程编排器。

一条命令跑完一个群（13 步）：

    0.1 导入已导出消息 → 0.5 媒体转文本 → 0.6 连发块合并 → 0.7 @/reply 初审
    → 0.8 收尾 → 1.1 事件判别（逐条 + 外延 5 段）→ 1.2 链接合并 → 1.3 长事件复核
    → 1.4 质量闸门 → 1.5 数据集导出 → 2.1 图片上下文重述 → 2.2 纯文本片段
    2.3 图文交错导出（可选，多模态微调用）

用法::

    python pipeline.py --status                     # 查看各步产物状态
    python pipeline.py --group 123456789            # 跑完整流程
    python pipeline.py --from 1.1 --to 1.5          # 只跑某几步（断点续跑）
    python pipeline.py --force                      # 忽略已有产物重跑
    python pipeline.py --list-prompts               # 打印全部提示词
    python pipeline.py --check-llm                  # 检查推理服务与模型是否就绪
    python pipeline.py --mock                       # 离线自测（不调用真实模型）
    python pipeline.py --html                       # 生成静态 HTML 报告
    python pipeline.py --export-chatlab             # 导出 ChatLab 标准格式（反向兼容）
    python pipeline.py --init-config                # 交互式写 config.json
    python pipeline.py --prompts-override p.json    # 用自定义提示词
"""
import argparse
import json
import os
import subprocess
import sys
import time

_REPO = os.path.dirname(os.path.abspath(__file__))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)
sys.dont_write_bytecode = True   # 不在仓库里留下 __pycache__

from config import ConfigError, add_common_args, load_config, setup_utf8_stdio  # noqa: E402
from core.llm import LLM, LLMError                           # noqa: E402
from core import prompts as prompt_overrides                 # noqa: E402
from core import report as html_report                       # noqa: E402
from core import wizard                                       # noqa: E402

STAGES_DIR = os.path.join(_REPO, 'stages')

# 步骤表：(key, 说明, 脚本, 额外参数, 需要的模型角色)
STEPS = [
    ('0.1', '导入消息流（读用户已导出的 messages.jsonl）',
     's0_import.py', [], None),
    ('0.5', '媒体转文本（图片约 50 字 / 表情 ≤15 字）',
     's0_media.py', [], 'small'),
    ('0.6', '连发块合并（同人连发 → 语义分块）',
     's0_merge_runs.py', [], 'small'),
    ('0.7', '@ / reply 语义初审（相关→reply，不相关→删@）',
     's0_at_prep.py', [], 'small'),
    ('0.8', '阶段 0 收尾（文本卫生、悬空链接修正）',
     's0_finalize.py', [], None),
    ('1.1', '事件判别（逐条 + 外延 5 段）',
     's1_segment_unit.py', ['--tag', 'ev', '--lookahead', '5', '--explain'], 'small'),
    ('1.2', '按确认链接合并事件（非连续，≤20 条 / ≤2000 字）',
     's1_link_merge.py',
     ['--src', 's1_events_ev.jsonl', '--tag', 'linked',
      '--max-units', '20', '--max-chars', '2000'], None),
    ('1.3', '长事件复核（>10 条）',
     's1_review.py',
     ['--src', 's1_events_linked.jsonl', '--tag', 'reviewed', '--min', '10'], 'big'),
    ('1.4', '质量闸门（单条 + 文字含量<30，媒体按 15 字计）',
     's1_quality_gate.py',
     ['--src', 's1_events_reviewed.jsonl', '--tag', 'gated',
      '--min-substance', '30', '--media-weight', '15'], None),
    ('1.5', '事件级分类与数据集导出',
     's1_finalize_dataset.py',
     ['--src', 's1_events_gated.jsonl', '--tag', 'dataset'], None),
    ('2.1', '图片结合上下文重述（每图一次）',
     's2_media_context.py', ['--tag', 'ctx'], 'big'),
    ('2.2', '回填描述 → 纯文本片段（最终成果）',
     's2_build_segments.py', ['--ctx-tag', 'ctx', '--tag', 'segments'], None),
    ('2.3', '导出图文交错格式（多模态微调用，可选）',
     's2_export_interleaved.py', ['--ctx-tag', 'ctx'], None),
]

#: 每步完成后必须存在的产物（用于校验，防止静默失败）
EXPECT = {
    '0.1': 'messages.jsonl',
    '0.5': 'messages_media.jsonl',
    '0.6': 'units.jsonl',
    '0.7': 'units_at.jsonl',
    '0.8': 'units_stats.json',
    '1.1': 's1_events_ev.jsonl',
    '1.2': 's1_events_linked.jsonl',
    '1.3': 's1_events_reviewed.jsonl',
    '1.4': 's1_events_gated.jsonl',
    '1.5': 'dataset_dataset.jsonl',
    '2.1': 'media_context_ctx.jsonl',
    '2.2': 'segments_ctx.jsonl',
    '2.3': 'segments_interleaved.jsonl',
}

FINAL_OUTPUT = 'segments_ctx.jsonl'


def _stage_cmd(script, extra, args):
    """构造阶段脚本的命令行（保持与父进程完全一致的配置解析结果）。"""
    cmd = [sys.executable, os.path.join(STAGES_DIR, script)] + list(extra)
    cmd += ['--config', os.path.abspath(args.config)]
    if args.group:
        cmd += ['--group', str(args.group)]
    if args.group_name is not None:
        cmd += ['--group-name', str(args.group_name)]
    if args.data_root:
        cmd += ['--data-root', str(args.data_root)]
    if args.mock:
        cmd += ['--mock']
    return cmd


def _run_process(cmd):
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'
    return subprocess.run(cmd, cwd=_REPO, capture_output=True, text=True,
                          encoding='utf-8', errors='replace', env=env)


def run_step(key, desc, script, extra, role, cfg, args, model_ready):
    out = os.path.join(cfg.group_dir, EXPECT[key])
    if os.path.exists(out) and not args.force:
        print('  ↷ 跳过 %s（产物已存在：%s）' % (key, EXPECT[key]))
        return True
    if role and not model_ready.get(role):
        llm = LLM(cfg)
        ok, msg = llm.check(role)
        print('  [模型] %s' % msg)
        model_ready[role] = ok
        if not ok:
            return False

    cmd = _stage_cmd(script, extra, args)
    print('  ▶ %s %s' % (key, desc), flush=True)
    t0 = time.time()
    r = _run_process(cmd)
    elapsed = time.time() - t0
    if r.returncode != 0:
        print('  ✗ %s 失败（%.0f 秒，退出码 %d）' % (key, elapsed, r.returncode))
        tail = (r.stderr or '').strip()
        if tail:
            print('    ' + '\n    '.join(tail.splitlines()[-12:]))
        return False
    if not os.path.exists(out):
        print('  ✗ %s 没有产出 %s' % (key, EXPECT[key]))
        tail = (r.stdout or '').strip()
        if tail:
            print('    ' + '\n    '.join(tail.splitlines()[-8:]))
        return False
    print('  ✓ %s 完成（%.0f 秒）' % (key, elapsed))
    for line in [x for x in (r.stdout or '').strip().splitlines() if x.strip()][-2:]:
        print('      %s' % line[:110])
    return True


def cmd_status(args):
    try:
        cfg = load_config(args, allow_missing=True)
    except ConfigError as e:
        print('配置错误：%s' % e)
        return 2

    print('=== 流水线步骤 ===')
    for key, desc, script, extra, role in STEPS:
        print('  %-3s %-16s %s' % (key, '[%s]' % role if role else '[规则]', desc))

    if not os.path.exists(cfg.config_path):
        print('\n配置文件不存在：%s' % cfg.config_path)
        print('  先复制模板：cp config.example.json config.json')
        print('  再填写 data_root / group_id / base_url / model_small / model_big。')
        return 0
    if not cfg.group_id:
        print('\n配置里没有 group_id（可用 --group 指定）。')
        return 0

    print('\n=== 产物状态（群 %s）===' % cfg.group_id)
    print('  目录：%s' % cfg.group_dir)
    if not os.path.isdir(cfg.group_dir):
        print('  （目录还不存在：跑 python pipeline.py --group %s 时会自动创建）' % cfg.group_id)
        return 0
    for key, desc, script, extra, role in STEPS:
        p = os.path.join(cfg.group_dir, EXPECT[key])
        print('  %-3s %s %s' % (key, '✓' if os.path.exists(p) else '✗', desc))
    final = os.path.join(cfg.group_dir, FINAL_OUTPUT)
    if os.path.exists(final):
        print('\n最终成果：%s' % final)
    return 0


def cmd_check_llm(cfg):
    llm = LLM(cfg)
    if cfg.mock:
        print('mock 模式：不会连接真实推理服务。')
        return 0
    print('推理服务：%s' % cfg.base_url)
    print('视觉能力开关 model_supports_vision = %s\n' % cfg.model_supports_vision)
    all_ok = True
    for role in ('small', 'big'):
        ok, msg = llm.check(role)
        print('  %s %s' % ('✓' if ok else '✗', msg))
        all_ok = all_ok and ok
    return 0 if all_ok else 1


def cmd_run(cfg, args):
    # ---- 分支：只影响 2.x 的哪些步骤要跑 ----
    BRANCH_STEPS = {
        'text': {'2.1', '2.2'},
        'interleaved': {'2.3'},
        'both': {'2.1', '2.2', '2.3'},
    }
    allowed = BRANCH_STEPS.get(args.branch, BRANCH_STEPS['both'])
    steps = [st for st in STEPS
             if not (st[0].startswith('2.') and st[0] not in allowed)]
    # --from/--to 只在过滤后的步骤表里定位
    key_list = [s[0] for s in steps]

    if args.frm and args.frm not in key_list:
        print('错误：--from=%s 在当前分支（--branch %s）下不会执行。' % (args.frm, args.branch))
        print('  该分支的步骤：%s' % ' / '.join(key_list))
        return 2
    if args.to and args.to not in key_list:
        print('错误：--to=%s 在当前分支（--branch %s）下不会执行。' % (args.to, args.branch))
        print('  该分支的步骤：%s' % ' / '.join(key_list))
        return 2
    lo = key_list.index(args.frm) if args.frm else 0
    hi = key_list.index(args.to) if args.to else len(key_list) - 1
    if lo > hi:
        print('错误：--from 在 --to 之后。')
        return 2

    os.makedirs(cfg.logs_dir, exist_ok=True)
    cfg.ensure_group_dir()

    print('群号：%s | 群名：%s' % (cfg.group_id, cfg.group_name or '(未设置)'))
    print('数据目录：%s' % cfg.data_root)
    print('群目录：%s' % cfg.group_dir)
    print('推理端点：%s（%s）' % (cfg.base_url, 'mock 模式' if cfg.mock else '真实调用'))
    print('产物分支：%s（%s）' % (args.branch, {
        'both': '2.1+2.2 纯文本 与 2.3 图文交错',
        'text': '仅 2.1+2.2 纯文本（跳过 2.3）',
        'interleaved': '仅 2.3 图文交错（跳过 2.1、2.2，省掉图片上下文重述）',
    }.get(args.branch, '')))
    if not cfg.model_supports_vision:
        print('注意：model_supports_vision=false，0.5 / 2.1 会跳过视觉标注。')
    if not args.mock:
        for role in ('small', 'big'):
            ok, _ = LLM(cfg).check(role)
            if not ok:
                print('\n模型不可用，先解决问题再跑：')
                cmd_check_llm(cfg)
                return 2
    print('')

    # 0.1 需要用户先放好输入文件
    if lo == 0 and not os.path.exists(cfg.input_messages):
        print('错误：找不到输入文件 %s' % cfg.input_messages, file=sys.stderr)
        print('  请把你已合法导出的聊天记录放到该路径（每行一条 JSON），', file=sys.stderr)
        print('  或把文件放到别处、在 config.json 里用 input_messages 指定/或加 --input。',
              file=sys.stderr)
        print('  字段约定见 README「数据格式」。', file=sys.stderr)
        return 2

    t_all = time.time()
    report = []
    model_ready = {}
    for key, desc, script, extra, role in steps[lo:hi + 1]:
        ok = run_step(key, desc, script, extra, role, cfg, args, model_ready)
        report.append((key, desc, ok))
        if not ok:
            print('\n流水线中断（%s 失败）。修复后可用 --from %s 续跑。' % (key, key))
            break

    print('\n=== 汇总（%.1f 分钟）===' % ((time.time() - t_all) / 60))
    for key, desc, ok in report:
        print('  %s %-3s %s' % ('✓' if ok else '✗', key, desc))

    manifest = {
        'group_id': cfg.group_id, 'group_name': cfg.group_name,
        'mock': cfg.mock,
        'branch': args.branch,
        'models': {'small': cfg.raw.get('model_small'), 'big': cfg.raw.get('model_big')},
        'steps': [{'key': k, 'desc': d, 'ok': o} for k, d, o in report],
        'finished_at': int(time.time()),
    }
    # 记录实际使用的自定义提示词（便于追溯；默认提示词不记录）
    _act = prompt_overrides.active()
    if _act:
        manifest['custom_prompts'] = {
            'file': prompt_overrides.override_path(),
            'keys': _act,
            'note': 'README 的基准数字是在默认提示词下测得的，自定义后不适用',
        }
    man_path = os.path.join(cfg.group_dir, 'pipeline_manifest.json')
    with open(man_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)

    all_ok = all(o for _, _, o in report)
    final = os.path.join(cfg.group_dir, FINAL_OUTPUT)
    if all_ok and os.path.exists(final):
        print('\n最终成果：%s' % final)
        print('  每行一个事件，messages 是纯文本消息列表。')
    print('清单：%s' % man_path)
    return 0 if all_ok else 1


def _apply_prompts_override(path):
    """设置提示词覆盖的环境变量（子进程会继承）。返回是否生效。"""
    if not path:
        return False
    full = os.path.abspath(path)
    if not os.path.isfile(full):
        print('错误：--prompts-override 指向的文件不存在：%s' % full, file=sys.stderr)
        return None
    os.environ[prompt_overrides.ENV_VAR] = full
    try:
        act = prompt_overrides.active()     # 触发解析与校验
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return None
    lines = prompt_overrides.warning_lines()
    if lines:
        print('')
        for ln in lines:
            print(ln)
    return bool(act)


def cmd_html(cfg):
    print('生成 HTML 报告…')
    print('  数据目录：%s' % cfg.group_dir)
    path = html_report.build(cfg.group_dir, cfg=cfg, embed=cfg.raw.get('_html_embed', False))
    if not path:
        print('  ✗ 找不到可渲染的 %s（先跑完 2.2）' % FINAL_OUTPUT, file=sys.stderr)
        return 2
    size = os.path.getsize(path)
    print('  ✓ 已生成：%s（%.1f MB）' % (path, size / 1024 / 1024))
    print('    双击打开即可；顶部搜索框可按关键词过滤事件。')
    if size > 60 * 1024 * 1024:
        print('    提示：文件较大（内嵌图片），可去掉 --html-embed 改用引用模式。')
    return 0


def cmd_export_chatlab(cfg, args):
    """把最终片段反向导出为 ChatLab 标准格式（复用 stage 脚本，不重复实现）。"""
    print('导出 ChatLab 标准格式（mode=%s）…' % args.chatlab_mode)
    extra = ['--mode', args.chatlab_mode]
    if args.chatlab_sort:
        extra.append('--sort-by-time')
    cmd = _stage_cmd('s2_export_chatlab.py', extra, args)
    r = _run_process(cmd)
    for line in (r.stdout or '').splitlines():
        print('  ' + line)
    if r.returncode != 0:
        for line in (r.stderr or '').splitlines():
            print('  ' + line, file=sys.stderr)
        return r.returncode
    return 0


def main(argv=None):
    setup_utf8_stdio()
    ap = argparse.ArgumentParser(description='QQ 群聊事件切分流水线',
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__)
    add_common_args(ap)
    ap.add_argument('--from', dest='frm', default=None, help='从哪一步开始（如 1.1）')
    ap.add_argument('--to', dest='to', default=None, help='跑到哪一步结束（如 1.5）')
    ap.add_argument('--force', action='store_true', help='忽略已有产物，强制重跑')
    ap.add_argument('--list-prompts', action='store_true', help='打印全部提示词')
    ap.add_argument('--status', action='store_true', help='只看各步产物状态，不执行')
    ap.add_argument('--check-llm', action='store_true', help='检查推理服务与模型是否就绪')
    ap.add_argument('--init-config', action='store_true',
                    help='交互式生成 config.json（逐项询问，回车取默认）')
    ap.add_argument('--html', action='store_true',
                    help='用最终产物生成静态 HTML 报告（拷到浏览器看事件与图片）')
    ap.add_argument('--html-embed', action='store_true',
                    help='配合 --html：把图片缩略图内嵌进 HTML（可整个拷走，文件较大）')
    ap.add_argument('--export-chatlab', action='store_true',
                    help='把最终片段反向导出为 ChatLab 标准格式（可用 ChatLab 浏览/SQL 查询）')
    ap.add_argument('--chatlab-sort', action='store_true',
                    help='配合 --export-chatlab：每个文件内按真实时间戳排序')
    ap.add_argument('--chatlab-mode', choices=('split', 'single'), default='split',
                    help='配合 --export-chatlab：split=一事件一文件（默认）；single=整群一个文件')
    ap.add_argument('--prompts-override', default=None, metavar='JSON',
                    help='用自定义提示词覆盖默认值（见 core/prompts.py 的键表）')
    ap.add_argument('--branch', choices=('both', 'text', 'interleaved'), default='both',
                    help='产物分支：text=纯文本（2.1+2.2），interleaved=图文交错（仅 2.3，'
                         '跳过 2.1/2.2），both=两路都出（默认）')
    args = ap.parse_args(argv)

    # ---- 提示词覆盖：先设置环境变量，子进程才能继承 ----
    ov = _apply_prompts_override(args.prompts_override)
    if ov is None:
        return 2

    if args.init_config:
        return wizard.run(os.path.abspath(os.path.join(_REPO, 'config.json')))

    if args.html_embed:
        args.html = True

    if args.list_prompts:
        path = os.path.join(_REPO, 'prompts', 'PROMPTS.md')
        if not os.path.exists(path):
            print('找不到提示词存档：%s' % path, file=sys.stderr)
            return 2
        with open(path, encoding='utf-8') as f:
            print(f.read())
        return 0

    if args.status:
        return cmd_status(args)

    try:
        cfg = load_config(args, allow_missing=False, require_group=True)
    except ConfigError as e:
        print('配置错误：%s' % e, file=sys.stderr)
        return 2

    if args.check_llm:
        return cmd_check_llm(cfg)

    if args.html:
        cfg.raw['_html_embed'] = bool(args.html_embed)
        return cmd_html(cfg)

    if args.export_chatlab:
        return cmd_export_chatlab(cfg, args)

    try:
        return cmd_run(cfg, args)
    except LLMError as e:
        print('模型调用失败：%s' % e, file=sys.stderr)
        return 2


if __name__ == '__main__':
    try:
        sys.exit(main())
    except BrokenPipeError:      # 例如 `python pipeline.py --list-prompts | head`
        try:
            sys.stdout.close()
        except OSError:
            pass
        sys.exit(0)
