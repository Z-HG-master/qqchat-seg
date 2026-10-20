# -*- coding: utf-8 -*-
"""统一配置层：路径、推理端点、模型名、开关全部来自 ``config.json``。

设计约定
  · 业务代码里不出现任何绝对路径、群号、模型名；
  · ``config.json`` 中的相对路径以**配置文件所在目录**为基准解析；
  · 运行产物目录（``<data_root>/groups/<群>/``、``<data_root>/logs/``）首次运行时自动创建；
  · 模型名只存在于配置里，任何 OpenAI 兼容推理服务（LM Studio / Ollama / vLLM /
    llama.cpp server / 云端 API）都可以通过修改 ``base_url`` 接入。

命令行覆盖优先级：命令行 > config.json > 内置默认值。
"""
import json
import os
import re

#: 内置默认值。注意：这里**故意不写任何模型名**——模型名只出现在 config 里。
DEFAULTS = {
    'data_root': 'data',
    'group_id': '',
    'group_name': '',
    'input_messages': '',      # 空 = <group_dir>/input/messages.jsonl
    'media_root': '',          # 空 = 用 data_root 解析相对媒体路径
    'base_url': 'http://127.0.0.1:1234/v1',
    'api_key': '',
    'model_small': '',
    'model_big': '',
    'model_supports_vision': True,
    'temperature': 0.1,
    # 可选：仅部分模型家族支持的「思考模式强度」透传参数（如 Qwen）。
    # 留空 = 不发送该字段，交给模型/服务端的默认行为。本项目不依赖它。
    'reasoning_effort': '',
    'request_timeout': 180,
    'mock': False,
}

ROLE_KEYS = {'small': 'model_small', 'big': 'model_big'}


class ConfigError(RuntimeError):
    """配置缺失/不合法。消息可直接展示给最终用户。"""


def repo_root():
    """本仓库根目录（即 config.py 所在目录）。"""
    return os.path.dirname(os.path.abspath(__file__))


def setup_utf8_stdio():
    """让中文输出在 Windows 控制台/重定向管道下不乱码。"""
    import sys
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass


def _sanitize_dirname(name):
    """把群名里不能出现在目录名中的字符替换掉。"""
    name = str(name or '').strip()
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', '_', name)
    return name.strip(' .')


def add_common_args(parser):
    """给任意脚本挂上通用的配置参数。"""
    parser.add_argument(
        '--config',
        default=os.environ.get('QQCHAT_CONFIG', os.path.join(repo_root(), 'config.json')),
        help='配置文件路径（默认仓库根目录下的 config.json，可用环境变量 QQCHAT_CONFIG 指定）')
    parser.add_argument('--group', default=None, help='群号，覆盖 config 里的 group_id')
    parser.add_argument('--group-name', default=None,
                        help='群名（仅作展示/目录标签），覆盖 config 里的 group_name')
    parser.add_argument('--data-root', default=None, help='数据根目录，覆盖 config 里的 data_root')
    parser.add_argument('--mock', action='store_true',
                        help='离线 mock 模式：用确定性的假回复代替真实模型，用于自测流水线')
    return parser


class Config(object):
    """一份解析好的配置。属性访问力求直白：cfg.group_id / cfg.data_root / ..."""

    def __init__(self, raw, config_path):
        self.raw = dict(DEFAULTS)
        self.raw.update(raw or {})
        self.config_path = os.path.abspath(config_path)
        self.config_dir = os.path.dirname(self.config_path)
        self._data_root_override = None

    # ------------------------------------------------------------------ 基础
    def get(self, key, default=None):
        return self.raw.get(key, default)

    def _resolve(self, path):
        """相对路径 → 绝对路径（基准 = 配置文件所在目录）。"""
        if not path:
            return ''
        path = os.path.expanduser(str(path))
        if os.path.isabs(path):
            return os.path.normpath(path)
        return os.path.normpath(os.path.join(self.config_dir, path))

    # ------------------------------------------------------------------ 路径
    @property
    def data_root(self):
        if self._data_root_override:
            return self._data_root_override
        return self._resolve(self.raw.get('data_root') or 'data')

    @property
    def media_root(self):
        return self._resolve(self.raw.get('media_root')) or self.data_root

    @property
    def logs_dir(self):
        return os.path.join(self.data_root, 'logs')

    @property
    def group_id(self):
        return str(self.raw.get('group_id') or '').strip()

    @property
    def group_name(self):
        return str(self.raw.get('group_name') or '').strip()

    def group_dirname(self):
        gid = self.group_id
        if not gid:
            raise ConfigError(
                '未配置群号：请在 config.json 里填 group_id，或运行时加 --group <群号>。')
        name = _sanitize_dirname(self.group_name)
        return '%s_%s' % (gid, name) if name else gid

    @property
    def group_dir(self):
        return os.path.join(self.data_root, 'groups', self.group_dirname())

    def ensure_group_dir(self):
        path = self.group_dir
        os.makedirs(path, exist_ok=True)
        return path

    def group_file(self, name):
        return os.path.join(self.group_dir, name)

    @property
    def input_messages(self):
        """用户已导出的原始消息文件（每行一条，格式见 README）。"""
        raw = str(self.raw.get('input_messages') or '').strip()
        if raw:
            return self._resolve(raw)
        return os.path.join(self.group_dir, 'input', 'messages.jsonl')

    def resolve_media_path(self, path):
        """媒体路径：绝对路径原样返回；相对路径按 media_root 解析。"""
        if not path:
            return ''
        path = os.path.expanduser(str(path))
        if os.path.isabs(path):
            return os.path.normpath(path)
        return os.path.normpath(os.path.join(self.media_root, path))

    @property
    def prompts_file(self):
        return os.path.join(repo_root(), 'prompts', 'PROMPTS.md')

    # ------------------------------------------------------------------ 模型
    def model_for(self, role):
        """按角色取模型名。角色只有 small / big，模型名完全来自配置。"""
        key = ROLE_KEYS.get(role)
        if key is None:
            raise ConfigError('未知的模型角色 %r（只支持 small / big）' % (role,))
        name = str(self.raw.get(key) or '').strip()
        if not name:
            raise ConfigError(
                'config.json 未配置 %s（%s 角色模型）。\n'
                '  任何 OpenAI 兼容模型都可以，把它填进 config.json 后重试；\n'
                '  可参考 config.example.json 的默认值。' % (key, role))
        return name

    @property
    def mock(self):
        return bool(self.raw.get('mock'))

    @property
    def model_supports_vision(self):
        """视觉能力开关：代码据此决定是否调用视觉模型，而不是模型名。"""
        return bool(self.raw.get('model_supports_vision'))

    @property
    def base_url(self):
        return str(self.raw.get('base_url') or DEFAULTS['base_url']).rstrip('/')

    @property
    def api_key(self):
        return str(self.raw.get('api_key') or '')

    @property
    def temperature(self):
        try:
            return float(self.raw.get('temperature', 0.1))
        except (TypeError, ValueError):
            return 0.1

    @property
    def reasoning_effort(self):
        # 默认空字符串 = 不发送该字段；它不是本项目的必要参数
        return self.raw.get('reasoning_effort', '')

    @property
    def request_timeout(self):
        try:
            return float(self.raw.get('request_timeout', 180))
        except (TypeError, ValueError):
            return 180.0

    # ------------------------------------------------------------------ 描述
    def describe(self):
        return {
            'config_path': self.config_path,
            'data_root': self.data_root,
            'group_id': self.group_id,
            'group_name': self.group_name,
            'group_dir': self.group_dir,
            'base_url': self.base_url,
            'model_small': self.raw.get('model_small', ''),
            'model_big': self.raw.get('model_big', ''),
            'model_supports_vision': self.model_supports_vision,
            'mock': self.mock,
        }


def load_config(args, allow_missing=False, require_group=False):
    """从 ``args.config`` 读取配置并叠加命令行覆盖。

    allow_missing=True 时允许配置文件不存在（用于 --status / bench 这类
    可以完全靠命令行参数跑的场景）。
    """
    setup_utf8_stdio()
    path = getattr(args, 'config', None) or os.path.join(repo_root(), 'config.json')
    raw = {}
    if os.path.exists(path):
        try:
            with open(path, encoding='utf-8-sig') as f:
                raw = json.load(f)
        except json.JSONDecodeError as e:
            raise ConfigError('配置文件不是合法 JSON：%s（%s）' % (path, e))
        if not isinstance(raw, dict):
            raise ConfigError('配置文件顶层必须是 JSON 对象：%s' % path)
    elif not allow_missing:
        raise ConfigError(
            '找不到配置文件：%s\n'
            '  请先复制模板并填写必要字段：\n'
            '      cp config.example.json config.json\n'
            '  至少填写 data_root / group_id / base_url / model_small / model_big。' % path)

    cfg = Config(raw, path)

    # ---- 命令行覆盖（--data-root 的相对路径按当前工作目录解析）
    if getattr(args, 'data_root', None):
        cfg._data_root_override = os.path.abspath(
            os.path.expanduser(str(args.data_root)))
    if getattr(args, 'group', None):
        cfg.raw['group_id'] = str(args.group)
    if getattr(args, 'group_name', None) is not None:
        cfg.raw['group_name'] = args.group_name
    if getattr(args, 'mock', False):
        cfg.raw['mock'] = True

    if require_group and not cfg.group_id:
        raise ConfigError('未指定群号：请在 config.json 填 group_id，或运行时加 --group <群号>。')
    return cfg
