# -*- coding: utf-8 -*-
"""模型中立的 OpenAI 兼容调用层。

原则
  · 只认识配置里的 ``base_url`` / ``model_small`` / ``model_big`` /
    ``model_supports_vision``，不硬编码任何模型名，也不按厂商名分支；
  · 视觉能力由 ``model_supports_vision`` 开关控制，而不是猜模型名；
  · 传输失败会重试，鉴权/模型名/端点错误会立刻抛出**可读的** :class:`LLMError`；
  · 单张媒体文件不可读抛 :class:`MediaError`，调用方可以跳过这一条继续跑。

``--mock`` 模式使用确定性的假回复，便于在没有 GPU / 没有模型时自测流水线。
"""
import base64
import io
import json
import re
import time
import urllib.error
import urllib.request

DEFAULT_MAX_TOKENS = 128

#: 重试次数（仅针对连接失败 / 超时 / 5xx / 429 这类可恢复错误）
MAX_TRIES = 3


class LLMError(RuntimeError):
    """调用推理服务失败（配置、连接、模型不存在、鉴权……）。消息可直接展示。"""


class MediaError(RuntimeError):
    """单个媒体文件不可读（不存在 / 损坏 / 非图片）。调用方可跳过该条继续。"""


# --------------------------------------------------------------------------- #
# 图片预处理：长边短于 900 放大、长于 1400 缩小，统一转 JPEG base64 data URL
# --------------------------------------------------------------------------- #
def image_to_data_url(path, min_side=900, max_side=1400, quality=88):
    try:
        from PIL import Image
    except ImportError:
        raise LLMError('处理图片需要 Pillow，请先安装依赖：pip install -r requirements.txt')
    import os
    if not path or not os.path.isfile(path):
        raise MediaError('媒体文件不存在：%s' % (path,))
    try:
        im = Image.open(path)
        im = im.convert('RGB')
        w, h = im.size
        long_side = max(w, h)
        if long_side < min_side:
            scale = float(min_side) / long_side
            im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        elif long_side > max_side:
            scale = float(max_side) / long_side
            im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, 'JPEG', quality=quality)
    except (OSError, ValueError) as e:
        raise MediaError('图片读取/解码失败（%s）：%s' % (type(e).__name__, path))
    b64 = base64.b64encode(buf.getvalue()).decode('ascii')
    return 'data:image/jpeg;base64,' + b64


# --------------------------------------------------------------------------- #
# mock 后端：按任务类型返回确定性的假回复
# --------------------------------------------------------------------------- #
def _mock_reply(prompt):
    p = prompt or ''
    # 图片结合上下文重述（两行输出）
    if '关联' in p and '描述' in p:
        return ('描述: 示例图片，画面为聊天窗口截图与一张表格，图上有示例文字和数字。\n'
                '关联: 这张图补充了上文提到的数据。')
    # 连发分块：把 N 行合成一个块
    if '分割区间' in p or '分成几个语义块' in p:
        n = len(re.findall(r'(?m)^\s*\d+[.、]\s*', p))
        return '1-%d' % n if n > 1 else '1'
    # @ / reply 相关性判定
    if 'YES' in p.upper() and 'NO' in p.upper() and ('相关' in p or '关联' in p):
        return 'YES'
    # 事件判别（带解释版）
    if '判定' in p and '理由' in p:
        return '判定: 续\n理由: 与上文属于同一话题。'
    # 事件判别（单字版）
    if '续' in p and '新' in p and '只输出一个字' in p:
        return '续'
    # 长事件复核
    if '转折' in p or '逻辑顺畅' in p:
        return '结论: 顺畅\n转折: 无\n理由: 示例数据中未发现话题转折。'
    # 阶段 0 图片标注
    if '50 字左右' in p or ('图片' in p and '客观描述' in p):
        return '示例图片，画面为聊天界面与表格，图上有示例文字和数字。'
    # 阶段 0 表情标注
    if '表情包' in p:
        return '示例表情，表示赞同'
    return ''


# --------------------------------------------------------------------------- #
# 主客户端
# --------------------------------------------------------------------------- #
class LLM(object):
    """OpenAI 兼容聊天接口的薄封装。业务代码只通过它调用模型。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self.base_url = cfg.base_url
        self.mock = bool(cfg.mock)
        self._checked = {}

    # ------------------------------------------------------------------ 外部 API
    def ask(self, prompt, role='small', max_tokens=DEFAULT_MAX_TOKENS,
            temperature=None, reasoning_effort=None):
        """单条 user 消息调用，返回纯文本（可能为空字符串）。

        role: 'small' / 'big'，具体模型名从 config 读取。
        """
        if self.mock:
            return _mock_reply(prompt)
        model = self.cfg.model_for(role)
        payload = self._payload(model, [{'role': 'user', 'content': prompt}],
                                max_tokens, temperature, reasoning_effort)
        d = self._request_chat(payload, model)
        return self._response_text(d)

    def ask_vision(self, prompt, media_path, role='small', max_tokens=200,
                   temperature=None, reasoning_effort=None):
        """带一张图片的调用。图片由本函数做缩放/编码（媒体文件不可读抛 MediaError）。"""
        if not self.cfg.model_supports_vision:
            raise LLMError(
                'config.json 里 model_supports_vision=false，但当前步骤需要视觉能力。\n'
                '  如果要处理图片，请换一个具备视觉能力的模型并把 model_supports_vision 设为 true；\n'
                '  否则请跳过依赖视觉的步骤。')
        if self.mock:
            return _mock_reply(prompt)
        url = image_to_data_url(media_path)
        model = self.cfg.model_for(role)
        content = [{'type': 'text', 'text': prompt},
                   {'type': 'image_url', 'image_url': {'url': url}}]
        payload = self._payload(model, [{'role': 'user', 'content': content}],
                                max_tokens, temperature, reasoning_effort)
        d = self._request_chat(payload, model)
        return self._response_text(d)

    def list_models(self):
        """GET {base_url}/models。端点未实现时返回 None；连接失败抛 LLMError。"""
        if self.mock:
            return []
        req = urllib.request.Request(self.base_url + '/models', headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                d = json.loads(r.read().decode('utf-8', 'replace'))
        except urllib.error.HTTPError as e:
            if e.code in (404, 405):
                return None
            raise self._http_error(e, model=None)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise LLMError(self._conn_error(e))
        data = d.get('data') if isinstance(d, dict) else None
        if not isinstance(data, list):
            return None
        return [str(m.get('id')) for m in data if isinstance(m, dict) and m.get('id')]

    def check(self, role):
        """校验某个角色模型是否可用。返回 (ok: bool, message: str)，不抛异常。"""
        try:
            model = self.cfg.model_for(role)
        except Exception as e:  # ConfigError
            return False, str(e)
        if self.mock:
            return True, 'mock 模式，不校验模型（%s）' % model
        try:
            ids = self.list_models()
        except LLMError as e:
            return False, str(e)
        if ids is None:
            return True, '推理服务未提供 /models 列表，跳过模型存在性校验（%s）' % model
        if model not in ids:
            avail = '、'.join(ids) if ids else '（空）'
            return False, (
                '推理服务没有提供该模型：%s（%s 角色模型）\n'
                '  端点：%s\n'
                '  当前端点可用：%s\n'
                '  → 请在推理服务里加载/下载该模型，或修改 config.json 里的 model_small / model_big。'
                % (model, role, self.base_url, avail))
        return True, '模型就绪：%s（%s 角色，端点 %s）' % (model, role, self.base_url)

    def require(self, role):
        """check() 的抛异常版本，给各阶段脚本开头用。"""
        ok, msg = self.check(role)
        if not ok:
            raise LLMError(msg)
        return msg

    # ------------------------------------------------------------------ 内部
    def _headers(self):
        headers = {'Content-Type': 'application/json'}
        if self.cfg.api_key:
            headers['Authorization'] = 'Bearer ' + self.cfg.api_key
        return headers

    def _payload(self, model, messages, max_tokens, temperature, reasoning_effort):
        payload = {
            'model': model,
            'messages': messages,
            'temperature': self.cfg.temperature if temperature is None else float(temperature),
            'max_tokens': int(max_tokens),
            'stream': False,
        }
        effort = self.cfg.reasoning_effort if reasoning_effort is None else reasoning_effort
        if effort not in (None, ''):
            payload['reasoning_effort'] = effort
        return payload

    def _request_chat(self, payload, model):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        last = ''
        for attempt in range(MAX_TRIES):
            req = urllib.request.Request(self.base_url + '/chat/completions',
                                         data=body, headers=self._headers())
            try:
                with urllib.request.urlopen(req, timeout=self.cfg.request_timeout) as r:
                    return json.loads(r.read().decode('utf-8', 'replace'))
            except urllib.error.HTTPError as e:
                # 鉴权 / 端点 / 模型名错误：立刻给出可读报错，不重试
                if e.code in (401, 403) or e.code in (400, 404, 422):
                    raise self._http_error(e, model=model)
                last = 'HTTP %d' % e.code
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = '%s: %s' % (type(e).__name__, getattr(e, 'reason', e))
            except json.JSONDecodeError as e:
                raise LLMError('推理服务返回的不是 JSON（端点 %s）：%s' % (self.base_url, e))
            if attempt < MAX_TRIES - 1:
                time.sleep(1.5 * (attempt + 1))
        raise LLMError(self._conn_error(last))

    def _conn_error(self, detail):
        return (
            '无法连接推理服务：%s\n'
            '  端点：%s\n'
            '  原因：%s\n'
            '  → 请确认推理服务已启动（LM Studio / Ollama / vLLM / llama.cpp 等均可），\n'
            '    并检查 config.json 里的 base_url（OpenAI 兼容前缀，通常以 /v1 结尾）。'
            % (self.base_url, self.base_url, detail))

    def _http_error(self, e, model=None):
        try:
            body = e.read().decode('utf-8', 'replace')[:600]
        except Exception:
            body = ''
        if e.code in (401, 403):
            return LLMError(
                '推理服务鉴权失败（HTTP %d）。\n  端点：%s\n  请检查 config.json 里的 api_key。\n  服务返回：%s'
                % (e.code, self.base_url, body))
        if e.code in (400, 404, 422):
            return LLMError(
                '推理服务拒绝了这次请求（HTTP %d）。\n'
                '  端点：%s\n  请求的模型：%s\n'
                '  常见原因：模型名不在端点里（请检查 config.json 的 model_small / model_big），'
                '或该端点不支持当前参数。\n  服务返回：%s'
                % (e.code, self.base_url, model or '(未指定)', body))
        return LLMError('推理服务返回 HTTP %d：%s\n  端点：%s\n  服务返回：%s'
                        % (e.code, e.reason, self.base_url, body))

    @staticmethod
    def _response_text(d):
        try:
            choice = (d.get('choices') or [{}])[0]
            msg = choice.get('message') or {}
            content = msg.get('content')
            if isinstance(content, list):  # 少数端点返回分段 content
                content = ''.join(str(x.get('text', '')) if isinstance(x, dict) else str(x)
                                  for x in content)
            return (content or '').strip()
        except (AttributeError, IndexError, TypeError):
            return ''
