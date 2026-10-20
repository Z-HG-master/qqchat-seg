# -*- coding: utf-8 -*-
"""提示词覆盖（可选功能）。

默认提示词内联在各阶段脚本里，与 `prompts/PROMPTS.md` 逐字一致，且经过基准测试。
如果确实想改，**不要改代码**，用覆盖文件：

    python pipeline.py --prompts-override my_prompts.json

`my_prompts.json` 是一个扁平 JSON，键是提示词 ID，值是完整提示词文本：

    {
      "segment.unit": "你在处理……{context}……{unit}……",
      "review.event": "下面是……{lines}……"
    }

未提供的键保持默认。可用的键与占位符：

| 键 | 阶段 | 占位符 |
|---|---|---|
| `media.pic` | 0.5 图片标注 | 无 |
| `media.emoji` | 0.5 表情标注 | 无 |
| `merge.runs` | 0.6 连发块合并 | `{lines}` |
| `link.at` | 0.7 @/reply 初审 | `{a}` `{b}` |
| `segment.unit` | 1.1 事件判别 | `{context}` `{unit}` |
| `review.event` | 1.3 长事件复核 | `{lines}` |
| `media.context` | 2.1 图片上下文重述 | `{k}` `{ctx}` |

> ⚠ 覆盖提示词后，README 里的基准数字（Ubuntu IRC F1 0.862 等）不再适用。
> 每次运行会把实际使用的提示词哈希写进 `pipeline_manifest.json`，便于追溯。
"""
import hashlib
import json
import os

ENV_VAR = 'SOCIALCHAT_PROMPTS_OVERRIDE'
#: 旧变量名（重命名前），仍兼容
LEGACY_ENV_VAR = 'QQSEG_PROMPTS_OVERRIDE'

#: 全部可覆盖的键（顺序即文档顺序）
KEYS = (
    'media.pic', 'media.emoji', 'merge.runs', 'link.at',
    'segment.unit', 'review.event', 'media.context',
)

_cache = {'loaded': False, 'data': {}, 'path': None}


def _load():
    if _cache['loaded']:
        return _cache['data']
    _cache['loaded'] = True
    path = (os.environ.get(ENV_VAR) or os.environ.get(LEGACY_ENV_VAR) or '').strip()
    if not path:
        return _cache['data']
    _cache['path'] = path
    if not os.path.isfile(path):
        raise SystemExit('错误：提示词覆盖文件不存在：%s' % path)
    try:
        with open(path, encoding='utf-8-sig') as f:
            raw = json.load(f)
    except json.JSONDecodeError as e:
        raise SystemExit('错误：提示词覆盖文件不是合法 JSON（%s）：%s' % (path, e))
    if not isinstance(raw, dict):
        raise SystemExit('错误：提示词覆盖文件顶层必须是对象（键→提示词文本）')
    unknown = [k for k in raw if k not in KEYS]
    if unknown:
        raise SystemExit(
            '错误：未知的提示词键 %s\n可用键：%s' % (unknown, ', '.join(KEYS)))
    bad = [k for k, v in raw.items() if not isinstance(v, str) or not v.strip()]
    if bad:
        raise SystemExit('错误：以下键的值必须是非空字符串：%s' % bad)
    _cache['data'] = {k: v for k, v in raw.items()}
    return _cache['data']


def get(key, default):
    """取提示词：有覆盖则用覆盖，否则用代码里的默认值。"""
    data = _load()
    return data.get(key, default)


def override_path():
    """当前生效的覆盖文件路径（没有则为 None）。"""
    _load()
    return _cache['path']


def active():
    """返回 {key: sha256前12位}，只包含被覆盖的键。"""
    data = _load()
    return {k: hashlib.sha256(v.encode('utf-8')).hexdigest()[:12] for k, v in data.items()}


def warning_lines():
    """给 CLI 打印的警告（没有覆盖时返回空列表）。"""
    act = active()
    if not act:
        return []
    p = override_path()
    return [
        '⚠ 正在使用自定义提示词（%s）' % p,
        '  覆盖了 %d 项：%s' % (len(act), ', '.join('%s=%s' % (k, v) for k, v in sorted(act.items()))),
        '  README 中的基准数字（Ubuntu IRC F1 0.862 等）是在默认提示词下测得的，自定义后不再适用。',
    ]
