#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""冒烟测试（不依赖 pytest，也不需要模型）。

设计目标：**改一行代码，5 秒内知道有没有改坏东西**。

覆盖四件事
  1. requirements.txt 必须是纯 ASCII
     —— 中文 Windows 上 pip 会用 gbk 解码这个文件，非 ASCII 会导致
        `pip install -r` 直接崩溃（我们踩过这个坑，见 docs/pitfalls.md）
  2. prompts/PROMPTS.md 必须与代码里的提示词逐字一致
     —— 存档由 AST 从代码生成，改动后忘记重生成会被这里挡住
  3. `pipeline.py --status` 在没有 config.json 时也能正常退出
  4. mock 全流程能跑通并产出 segments_ctx.jsonl（不调用任何模型）

运行
  python tests/test_smoke.py          # 直接跑
  pytest tests/test_smoke.py          # 装了 pytest 也能跑
"""
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

# Windows 上 stdout 默认是 cp1252，打印 ✓ / ✗ 会 UnicodeEncodeError。
# 这里强制 utf-8（CI 的 windows-latest 就是因此挂掉的）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable

#: 1.1 的基础尾：`PROMPT` 以它结尾，enable-explain 时会被 EXPLAIN_TAIL 替换。
#: 如果 s1_segment_unit.py 里这句改字了，下面的断言会**明确报出是哪一边变了**，
#: 而不是让 CI 把错误归因到 PROMPTS.md。
BASE_TAIL = '只输出一个字：续 或 新'

#: PROMPTS.md 里 7 个代码块的顺序 → (脚本, 用于定位的标记, 优先选的分支)
PROMPT_SPEC = [
    ('media.pic',      'stages/s0_media.py',          '这是一张 QQ 群里的图片',        None),
    ('media.emoji',    'stages/s0_media.py',          '这是一个 QQ 群里的表情包',      None),
    ('merge.runs',     'stages/s0_merge_runs.py',     '下面是同一个人的连续发言',      None),
    ('link.at',        'stages/s0_at_prep.py',        '你在处理 QQ 群聊天记录相关性判别', None),
    ('segment.unit',   'stages/s1_segment_unit.py',   '你在处理 QQ 群聊天记录的事件归类工作', '只输出两行'),
    ('review.event',   'stages/s1_review.py',         '下面是 QQ 群里一段连续的消息',  None),
    ('media.context',  'stages/s2_media_context.py',  '下面是 QQ 群里的一段对话',      None),
]


# ---------------------------------------------------------------- helpers
def _run(args, cwd=REPO, timeout=120):
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    return subprocess.run(args, cwd=cwd, env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, timeout=timeout)


def _extract_prompt(rel_path, marker, prefer=None):
    """用 AST 从脚本里取出提示词常量（支持 'A' + ('B' if cond else 'C') 形式）。"""
    src = open(os.path.join(REPO, rel_path), encoding='utf-8').read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        val = node.value
        consts = [n.value for n in ast.walk(val)
                  if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        if not any(marker in c for c in consts):
            continue
        if isinstance(val, ast.Constant):
            return val.value
        if isinstance(val, ast.BinOp):
            left = val.left.value if isinstance(val.left, ast.Constant) else ''
            right = val.right
            if isinstance(right, ast.IfExp):
                body = right.body.value if isinstance(right.body, ast.Constant) else ''
                orel = right.orelse.value if isinstance(right.orelse, ast.Constant) else ''
                return left + (body if (prefer and prefer in body) else orel)
            if isinstance(right, ast.Constant):
                return left + right.value
        return ''.join(consts)
    return None


def _compose_explain(rel_path):
    """1.1 的 explain 版是运行时拼出来的：PROMPT.replace(基础尾, EXPLAIN_TAIL)。"""
    src = open(os.path.join(REPO, rel_path), encoding='utf-8').read()
    tree = ast.parse(src)
    vals = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id in ('PROMPT', 'EXPLAIN_TAIL'):
                if t.id in vals:
                    continue        # 只取第一个可字面量化的赋值（后面是 --prompts-override 的运行时覆盖）
                try:
                    vals[t.id] = ast.literal_eval(node.value)
                except (ValueError, TypeError):
                    continue        # 覆盖用的 _prompt_get(...) 调用，跳过
    base, tail = vals.get('PROMPT'), vals.get('EXPLAIN_TAIL')
    assert base and tail, '无法从 %s 取到 PROMPT / EXPLAIN_TAIL' % rel_path
    assert BASE_TAIL in base, (
        '%s 的 PROMPT 里找不到基础尾 %r。\n'
        '原因可能是：源码里的这句被改写了 → 请同步更新 tests/test_smoke.py 的 BASE_TAIL，'
        '并重新生成 prompts/PROMPTS.md' % (rel_path, BASE_TAIL))
    return base.replace(BASE_TAIL, tail)


# ---------------------------------------------------------------- tests
def test_requirements_is_pure_ascii():
    """requirements.txt 必须能按 ascii/gbk/utf-8 三种方式解码（防 locale 坑）。"""
    path = os.path.join(REPO, 'requirements.txt')
    assert os.path.isfile(path), '缺少 requirements.txt'
    raw = open(path, 'rb').read()
    assert not raw.startswith(b'\xef\xbb\xbf'), 'requirements.txt 不应带 BOM'
    for enc in ('ascii', 'gbk', 'utf-8'):
        raw.decode(enc)      # 解码失败会抛异常 → 测试失败


def test_prompts_md_matches_code():
    """PROMPTS.md 的 7 个代码块必须与代码里的提示词逐字一致。"""
    md = open(os.path.join(REPO, 'prompts', 'PROMPTS.md'), encoding='utf-8').read()
    blocks = re.findall(r'```\n(.*?)```', md, re.S)
    assert len(blocks) == 7, 'PROMPTS.md 应有 7 个代码块，实际 %d' % len(blocks)

    for i, (key, rel, marker, prefer) in enumerate(PROMPT_SPEC):
        code = (_compose_explain(rel) if key == 'segment.unit'
                else _extract_prompt(rel, marker, prefer))
        assert code, '无法从 %s 提取提示词 %s' % (rel, key)
        assert blocks[i].strip() == code.strip(), (
            'PROMPTS.md 第 %d 个代码块（%s）与 %s 不一致——'
            '改完提示词请重新生成 PROMPTS.md' % (i + 1, key, rel))


def test_status_without_config():
    """没有 config.json 时 --status 应当给出提示并正常退出（不是 traceback）。"""
    with tempfile.TemporaryDirectory() as tmp:
        r = _run([PY, os.path.join(REPO, 'pipeline.py'), '--status'],
                 cwd=tmp)
    out = r.stdout.decode('utf-8', 'replace')
    assert r.returncode == 0, '--status 退出码应为 0，实际 %d\n%s' % (r.returncode, out)
    assert 'Traceback' not in out, '--status 不应抛 traceback\n%s' % out


def test_mock_pipeline_end_to_end():
    """mock 模式跑完整流程，产出 segments_ctx.jsonl 与图文交错文件。"""
    with tempfile.TemporaryDirectory() as tmp:
        cfg = {
            'data_root': os.path.join(tmp, 'data'),
            'group_id': '123456789',
            'group_name': '冒烟测试',
            'input_messages': os.path.join(REPO, 'examples', 'sample_input_media.jsonl'),
            'media_root': os.path.join(REPO, 'examples', 'media'),
            'base_url': 'http://127.0.0.1:9/v1',      # 不会被访问
            'api_key': '',
            'model_small': 'mock/small',
            'model_big': 'mock/big',
            'model_supports_vision': True,
            'temperature': 0.1,
            'reasoning_effort': 'none',
            'request_timeout': 10,
            'mock': True,
        }
        cfg_path = os.path.join(tmp, 'config.json')
        with open(cfg_path, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False)

        r = _run([PY, os.path.join(REPO, 'pipeline.py'),
                  '--config', cfg_path, '--mock'])
        out = r.stdout.decode('utf-8', 'replace')
        assert r.returncode == 0, 'mock 流程失败（exit %d）\n%s' % (r.returncode, out)

        group_dir = os.path.join(cfg['data_root'], 'groups', '123456789_冒烟测试')
        seg = os.path.join(group_dir, 'segments_ctx.jsonl')
        inter = os.path.join(group_dir, 'segments_interleaved.jsonl')
        assert os.path.isfile(seg), '未产出 segments_ctx.jsonl\n%s' % out
        assert os.path.isfile(inter), '未产出 segments_interleaved.jsonl\n%s' % out

        rows = [json.loads(l) for l in open(seg, encoding='utf-8') if l.strip()]
        assert rows, 'segments_ctx.jsonl 为空'
        assert rows[0]['messages'], '事件里没有消息'
        # 媒体占位符应已被替换成 [图片：…] / [表情：…]
        joined = '\n'.join(m for r_ in rows for m in r_['messages'])
        assert '[图片：' in joined or '[表情：' in joined, '媒体占位符未被替换'

        seg_rows = [json.loads(l) for l in open(inter, encoding='utf-8') if l.strip()]
        assert seg_rows, 'segments_interleaved.jsonl 为空'
        blocks = [b for m in seg_rows[0]['messages'] for b in m['content']]
        assert any(b['type'] == 'image' for b in blocks), '图文交错里没有 image 块'

        # 分支 B 不应带 desc（因为 mock 跑的是 both 分支 → 2.1 跑过，所以这里允许有；
        # 真正要保证的是结构正确，字段有无由 --desc 控制）

    # 跑完不应在仓库里留下 __pycache__
    stray = []
    for root, dirs, files in os.walk(REPO):
        if '.git' in root:
            continue
        if '__pycache__' in dirs:
            stray.append(os.path.join(root, '__pycache__'))
    assert not stray, '运行后留下 __pycache__：%s' % stray


def test_chatlab_format_import():
    """ChatLab 标准格式（JSONL）能自动识别并跑通；媒体降级为文本标签。"""
    with tempfile.TemporaryDirectory() as tmp:
        cfg = {
            'data_root': os.path.join(tmp, 'data'),
            'group_id': '123456789',
            'group_name': 'ChatLab测试',
            'input_messages': os.path.join(REPO, 'examples', 'sample_input_chatlab.jsonl'),
            'media_root': '',
            'base_url': 'http://127.0.0.1:9/v1',
            'api_key': '',
            'model_small': 'mock/small',
            'model_big': 'mock/big',
            'model_supports_vision': True,
            'temperature': 0.1,
            'reasoning_effort': 'none',
            'request_timeout': 10,
            'mock': True,
        }
        cfg_path = os.path.join(tmp, 'config.json')
        with open(cfg_path, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False)

        # 先单独跑 0.1，验证格式自动识别（pipeline 会缓冲阶段输出，这里直连更要紧）
        r0 = _run([PY, os.path.join(REPO, 'stages', 's0_import.py'),
                   '--config', cfg_path, '--mock'])
        out0 = r0.stdout.decode('utf-8', 'replace')
        assert r0.returncode == 0, '0.1 导入失败\n%s' % out0
        assert 'ChatLab 标准格式' in out0, '未自动识别出 ChatLab 格式\n%s' % out0

        msgs_path = os.path.join(cfg['data_root'], 'groups', '123456789_ChatLab测试',
                                 'messages.jsonl')
        bodies = [json.loads(l)['body'] for l in open(msgs_path, encoding='utf-8') if l.strip()]
        joined = '\n'.join(bodies)
        assert '[图片]' in joined, '图片未降级为 [图片] 标签\n%s' % joined
        assert '[Image]' not in joined, '原始 [Image] 占位未被替换\n%s' % joined

        # 再跑完整流程，确认下游能吃这份数据
        r = _run([PY, os.path.join(REPO, 'pipeline.py'), '--config', cfg_path, '--mock'])
        out = r.stdout.decode('utf-8', 'replace')
        assert r.returncode == 0, 'ChatLab 输入流程失败（exit %d）\n%s' % (r.returncode, out)

        seg = os.path.join(cfg['data_root'], 'groups', '123456789_ChatLab测试',
                           'segments_ctx.jsonl')
        assert os.path.isfile(seg), '未产出 segments_ctx.jsonl\n%s' % out
        rows = [json.loads(l) for l in open(seg, encoding='utf-8') if l.strip()]
        body = '\n'.join(m for r_ in rows for m in r_['messages'])
        # 图片/表情应保留为中文标签，且原始 [Image]/[Sticker] 占位被替换掉
        assert '[图片]' in body, '图片未降级为 [图片] 标签：\n%s' % body
        assert '[表情]' in body, '表情未降级为 [表情] 标签：\n%s' % body
        assert '[Image]' not in body and '[Sticker]' not in body, \
            '原始英文占位串未被替换：\n%s' % body


# ---------------------------------------------------------------- runner
def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description='冒烟测试')
    ap.add_argument('--only', default='', help='只跑名字包含该子串的测试（如 prompts）')
    args = ap.parse_args(argv)

    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith('test_') and callable(f)]
    if args.only:
        tests = [(n, f) for n, f in tests if args.only in n]
        if not tests:
            print('没有匹配 --only %r 的测试' % args.only)
            return 2
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print('  ✓ %s' % name)
        except AssertionError as e:
            failed += 1
            print('  ✗ %s\n    %s' % (name, str(e).replace('\n', '\n    ')))
        except Exception as e:                       # noqa: BLE001
            failed += 1
            print('  ✗ %s\n    %s: %s' % (name, type(e).__name__, e))
    print('\n%d 项通过，%d 项失败' % (len(tests) - failed, failed))
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.dont_write_bytecode = True
    sys.exit(main())
