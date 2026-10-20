# -*- coding: utf-8 -*-
"""交互式配置向导（--init-config）。

不需要任何第三方依赖：只用标准库 input()。
所有项都给了默认值，直接回车即采用默认。
"""
import json
import os

#: (键, 提示语, 默认值, 类型说明)
FIELDS = [
    ('data_root', '数据目录（产物会写到这里）', 'data', 'str'),
    ('group_id', '群标识（只用作本地目录名，随便填）', '123456789', 'str'),
    ('group_name', '群名标签（可留空）', '示例群', 'str'),
    ('input_messages', '输入文件（相对 data_root 或绝对路径；留空=自动找）', '', 'str'),
    ('media_root', '媒体根目录（图片/表情所在目录；留空=data_root）', '', 'str'),
    ('base_url', '推理服务地址（OpenAI 兼容）', 'http://127.0.0.1:1234/v1', 'str'),
    ('api_key', 'API Key（本地服务通常留空）', '', 'str'),
    ('model_small', '小模型（用于逐条判别、媒体标注）', 'qwen/qwen3.5-9b', 'str'),
    ('model_big', '大模型（用于长事件复核、图片重述）', 'qwen/qwen3.8-27b', 'str'),
    ('model_supports_vision', '小模型支持视觉？(y/N)', 'y', 'bool'),
]


def _ask(label, default, kind):
    suffix = ' [%s]' % default if default != '' else '（可留空）'
    while True:
        try:
            raw = input('  %s%s: ' % (label, suffix)).strip()
        except EOFError:            # 非交互环境（管道输入用尽）→ 取默认
            print()
            return default if kind != 'bool' else (default.lower().startswith('y'))
        if not raw:
            return default if kind != 'bool' else (default.lower().startswith('y'))
        if kind == 'bool':
            return raw.lower() in ('y', 'yes', '1', 'true', '是')
        return raw


def run(config_path):
    """在终端里逐项询问，写出 config.json。返回 0=成功 2=放弃。"""
    if os.path.exists(config_path):
        print('配置文件已存在：%s' % config_path)
        try:
            ans = input('  覆盖它？(y/N): ').strip().lower()
        except EOFError:
            ans = ''
        if ans not in ('y', 'yes', '是'):
            print('已取消，未改动任何文件。')
            return 2

    print('')
    print('=== 配置向导 ===')
    print('  直接回车 = 采用方括号里的默认值。共 %d 项。' % len(FIELDS))
    print('')

    cfg = {}
    for key, label, default, kind in FIELDS:
        cfg[key] = _ask(label, default, kind)

    cfg['temperature'] = 0.1
    cfg['reasoning_effort'] = 'none'
    cfg['request_timeout'] = 180
    cfg['mock'] = False

    # 基本校验：地址必须像 URL
    if not (cfg['base_url'].startswith('http://') or cfg['base_url'].startswith('https://')):
        print('\n✗ base_url 必须以 http:// 或 https:// 开头：%s' % cfg['base_url'])
        return 2
    if not cfg['group_id']:
        print('\n✗ group_id 不能为空（它只是本地目录名）')
        return 2

    d = os.path.dirname(os.path.abspath(config_path))
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write('\n')

    print('\n✓ 已写入 %s' % config_path)
    print('')
    print('下一步：')
    print('  1) python pipeline.py --status        # 看看各步状态')
    print('  2) python pipeline.py --check-llm     # 确认模型就绪（会列出可用模型名）')
    print('  3) 把聊天记录放到 input_messages 指向的文件（格式见 docs/data-format.md）')
    print('  4) python pipeline.py                 # 跑完整流程')
    print('')
    print('提示：想先不接模型看流程是否通，用 python pipeline.py --mock')
    return 0
