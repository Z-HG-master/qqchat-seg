# -*- coding: utf-8 -*-
"""静态 HTML 报告（--html）。

把最终产物 `segments_ctx.jsonl` 渲染成一个**单文件 HTML**，双击就能看：

  · 每个事件一张卡片：编号、时间、参与者、消息数、字数
  · 消息里的 `[图片：…]` / `[表情：…]` 会显示出对应的图片
  · 顶部有汇总统计 + 一个即时过滤框（纯 JS，无依赖）

默认**引用**原始图片文件（HTML 很小）；加 `embed=True` 则把缩略图内嵌成 base64
（HTML 变大但可以整个拷走）。

不依赖任何第三方包（图片处理可选 Pillow，缺失时自动退化为只显示路径）。
"""
import base64
import io
import json
import os
import re
import time

PLACEHOLDER = re.compile(r'\[(图片|表情)：([^\]]*)\]')
MEDIA_CACHE = {}


def _read_jsonl(path):
    rows = []
    if not os.path.isfile(path):
        return rows
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _read_json(path):
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _desc_to_path(group_dir):
    """描述文本 → 图片路径。

    同一张图会经历两轮描述：
      · media_caption.jsonl      阶段 0.5（无上下文）
      · media_context_ctx.jsonl  阶段 2.1（带上下文）
    最终文本里用的是 2.1 的描述，表情则仍是 0.5 的，所以两张表都要建。
    """
    mapping = {}
    for name in ('media_caption.jsonl', 'media_context_ctx.jsonl'):
        for rec in _read_jsonl(os.path.join(group_dir, name)):
            desc = (rec.get('desc') or '').strip()
            path = (rec.get('path') or '').strip()
            if desc and path:
                mapping[desc] = path
    return mapping


def _media_root(cfg):
    """媒体根目录：优先 cfg.media_root，其次 data_root。"""
    for attr in ('media_root', 'data_root'):
        try:
            v = getattr(cfg, attr, None)
            if v:
                return v
        except Exception:
            pass
    return ''


def _resolve(path, media_root, group_dir):
    """把媒体路径解析成真实文件路径。"""
    if not path:
        return None
    if os.path.isabs(path):
        cand = path
    else:
        cand = os.path.normpath(os.path.join(media_root or group_dir, path))
    return cand if os.path.isfile(cand) else None


def _src_url(real_path, html_dir, embed):
    """生成 <img src>：默认用相对/绝对文件路径，embed=True 用 base64。"""
    if embed:
        key = ('embed', real_path)
        if key in MEDIA_CACHE:
            return MEDIA_CACHE[key]
        try:
            from PIL import Image
            im = Image.open(real_path)
            im.thumbnail((480, 480))
            if im.mode not in ('RGB', 'L'):
                im = im.convert('RGB')
            buf = io.BytesIO()
            im.save(buf, 'JPEG', quality=72)
            url = 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode('ascii')
        except Exception:
            url = ''
        MEDIA_CACHE[key] = url
        return url
    try:
        rel = os.path.relpath(real_path, html_dir)
        if not rel.startswith('..'):
            return rel.replace(os.sep, '/')
    except ValueError:
        pass
    return 'file:///' + real_path.replace('\\', '/').lstrip('/')


def _esc(s):
    return (str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def _render_message(text, mapping, media_root, group_dir, html_dir, embed):
    """把一条消息渲染成 HTML 片段。"""
    parts = []
    pos = 0
    for m in PLACEHOLDER.finditer(text):
        parts.append(_esc(text[pos:m.start()]))
        kind, desc = m.group(1), m.group(2).strip()
        real = _resolve(mapping.get(desc, ''), media_root, group_dir)
        if real:
            src = _src_url(real, html_dir, embed)
            if src:
                parts.append(
                    '<figure class="media"><img loading="lazy" src="%s" alt="%s">'
                    '<figcaption>%s：%s</figcaption></figure>'
                    % (_esc(src), _esc(desc[:80]), kind, _esc(desc)))
            else:
                parts.append('<span class="ph">[%s：%s]</span>' % (kind, _esc(desc)))
        else:
            parts.append('<span class="ph">[%s：%s]</span>' % (kind, _esc(desc)))
        pos = m.end()
    parts.append(_esc(text[pos:]))
    return ''.join(parts)


CSS = """
:root{--bg:#0f1218;--card:#171c25;--line:#252c38;--fg:#e6e9ef;--dim:#8b95a7;--acc:#5b9dff;--warn:#e0b34a}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.65 "Segoe UI","Microsoft YaHei",system-ui,sans-serif}
header{position:sticky;top:0;z-index:9;background:rgba(15,18,24,.96);border-bottom:1px solid var(--line);padding:14px 20px}
h1{margin:0 0 6px;font-size:17px;font-weight:600}
.meta{color:var(--dim);font-size:12.5px}
.stats{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:6px 12px;font-size:12.5px}
.stat b{color:var(--acc);font-size:15px;margin-right:5px}
#q{margin-top:10px;width:100%;max-width:520px;background:var(--card);border:1px solid var(--line);color:var(--fg);
   border-radius:8px;padding:8px 12px;font-size:13px}
#q:focus{outline:1px solid var(--acc)}
main{padding:16px 20px 60px}
.ev{background:var(--card);border:1px solid var(--line);border-radius:10px;margin:0 0 14px;overflow:hidden}
.ev>summary{cursor:pointer;padding:11px 14px;display:flex;gap:12px;align-items:baseline;flex-wrap:wrap;list-style:none}
.ev>summary::-webkit-details-marker{display:none}
.ev>summary:before{content:"▸";color:var(--dim);transition:transform .15s}
.ev[open]>summary:before{transform:rotate(90deg)}
.eid{color:var(--acc);font-weight:600;font-family:ui-monospace,Consolas,monospace}
.tags{color:var(--dim);font-size:12px}
.body{padding:4px 14px 14px;border-top:1px solid var(--line)}
.msg{padding:6px 0;border-bottom:1px dashed rgba(255,255,255,.05)}
.msg:last-child{border-bottom:0}
.who{color:var(--warn);font-weight:600;margin-right:8px}
figure.media{margin:8px 0;display:inline-block;max-width:100%}
figure.media img{max-width:340px;max-height:340px;border-radius:8px;border:1px solid var(--line);display:block}
figure.media figcaption{color:var(--dim);font-size:12px;margin-top:4px;max-width:340px}
.ph{color:var(--dim);font-style:italic}
.empty{color:var(--dim);padding:30px;text-align:center}
footer{color:var(--dim);font-size:12px;padding:0 20px 30px}
"""

JS = """
const q=document.getElementById('q');
const evs=[...document.querySelectorAll('.ev')];
q.addEventListener('input',()=>{
  const v=q.value.trim().toLowerCase();
  for(const e of evs){
    e.style.display = !v || e.textContent.toLowerCase().includes(v) ? '' : 'none';
  }
  if(v) evs.forEach(e=>{if(e.style.display!=='none') e.open=true;});
});
"""


def build(group_dir, cfg=None, out_name='review.html', embed=False):
    """生成 HTML 报告，返回文件路径（失败返回 None）。"""
    seg_path = os.path.join(group_dir, 'segments_ctx.jsonl')
    events = _read_jsonl(seg_path)
    if not events:
        return None

    mapping = _desc_to_path(group_dir)
    media_root = _media_root(cfg) if cfg is not None else ''
    summary = _read_json(os.path.join(group_dir, 'summary_dataset.json'))
    catalog = _read_jsonl(os.path.join(group_dir, 'catalog_dataset.jsonl'))
    type_of = {}
    for rec in catalog:
        eid = rec.get('event_id') or rec.get('id')
        if eid:
            type_of[eid] = rec.get('type') or rec.get('category') or ''

    out_path = os.path.join(group_dir, out_name)
    html_dir = os.path.dirname(out_path)

    total_msgs = sum(len(e.get('messages') or []) for e in events)
    total_chars = sum(len(m) for e in events for m in (e.get('messages') or []))
    n_img = sum(len(PLACEHOLDER.findall(m)) for e in events for m in (e.get('messages') or []))
    types = {}
    for e in events:
        t = type_of.get(e.get('event_id'))
        if t:
            types[t] = types.get(t, 0) + 1

    buf = []
    buf.append('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">')
    buf.append('<meta name="viewport" content="width=device-width,initial-scale=1">')
    buf.append('<title>事件切分报告 · %s</title>' % _esc(os.path.basename(group_dir)))
    buf.append('<style>%s</style></head><body>' % CSS)

    buf.append('<header>')
    buf.append('<h1>事件切分报告</h1>')
    buf.append('<div class="meta">群目录：%s ｜ 生成于 %s ｜ %s</div>'
               % (_esc(group_dir),
                  time.strftime('%Y-%m-%d %H:%M:%S'),
                  '图片已内嵌' if embed else '图片引用本地文件'))
    buf.append('<div class="stats">')
    buf.append('<div class="stat"><b>%d</b>事件</div>' % len(events))
    buf.append('<div class="stat"><b>%d</b>消息</div>' % total_msgs)
    buf.append('<div class="stat"><b>%s</b>总字数</div>' % format(total_chars, ','))
    buf.append('<div class="stat"><b>%d</b>处媒体</div>' % n_img)
    if types:
        buf.append('<div class="stat">类型：%s</div>'
                   % _esc(' · '.join('%s %d' % (k, v) for k, v in sorted(types.items(), key=lambda x: -x[1]))))
    if summary.get('total_events'):
        buf.append('<div class="stat">数据集摘要：%s 事件 / %s 字</div>'
                   % (summary.get('total_events'), summary.get('total_chars', '?')))
    buf.append('</div>')
    buf.append('<input id="q" placeholder="过滤：输入关键词，匹配事件编号、参与者或任意消息内容">')
    buf.append('</header><main>')

    for e in events:
        eid = e.get('event_id', '?')
        parts = e.get('participants') or []
        t0 = e.get('ts_first')
        t1 = e.get('ts_last')
        def ts(v):
            try:
                return time.strftime('%m-%d %H:%M', time.localtime(int(v)))
            except (TypeError, ValueError, OSError):
                return '?'
        tags = ['%d 条' % len(e.get('messages') or []),
                '%d 字' % (e.get('chars') or sum(len(m) for m in (e.get('messages') or [])))]
        if t0 and t1:
            tags.append('%s → %s' % (ts(t0), ts(t1)))
        if parts:
            tags.append(' · '.join(parts[:6]))
        if type_of.get(eid):
            tags.append(type_of[eid])

        buf.append('<details class="ev"><summary>')
        buf.append('<span class="eid">%s</span>' % _esc(eid))
        buf.append('<span class="tags">%s</span>' % _esc(' ｜ '.join(tags)))
        buf.append('</summary><div class="body">')
        for msg in e.get('messages') or []:
            who, sep, rest = msg.partition(': ')
            if not sep:
                who, rest = '', msg
            buf.append('<div class="msg">')
            if who:
                buf.append('<span class="who">%s</span>' % _esc(who))
            buf.append(_render_message(rest, mapping, media_root, group_dir, html_dir, embed))
            buf.append('</div>')
        buf.append('</div></details>')

    if not events:
        buf.append('<div class="empty">没有事件</div>')
    buf.append('</main>')
    buf.append('<footer>由 <code>pipeline.py --html</code> 生成。'
               '图上描述来自两轮识别：阶段 0.5（无上下文）与阶段 2.1（带事件上下文）。</footer>')
    buf.append('<script>%s</script>' % JS)
    buf.append('</body></html>')

    with open(out_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(buf))
    return out_path
