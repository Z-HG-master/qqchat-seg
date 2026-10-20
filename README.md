# qqchat-seg · 群聊事件切分流水线

把杂乱的群聊消息流（一人连发、多人交织、图文混杂、@ 与回复满天飞）清洗成
**事件级的纯文本对话片段**：每个片段是一个自然话题，图片被转成文字描述，
可以直接用于微调、检索或数据分析。

13 步流水线，配置驱动、模型中立、无数据依赖。
**本仓库从你已导出的 `messages.jsonl` 开始，不包含任何 QQ 客户端数据获取能力。**

> ⚠️ **伦理与法律警告（先读这一节）**
> - 本工具仅用于处理**你拥有合法访问权**的聊天记录；
> - 群聊记录含大量个人信息，**请勿上传未经脱敏的数据**；
> - 本项目**不提供数据库解密工具**，请自行合法导出数据；
> - 使用者对数据的合法性与用途负**全部责任**。

---

## 目录

1. [价值主张：它解决什么问题](#1-价值主张它解决什么问题)
2. [输出长什么样](#2-输出长什么样)
3. [流水线总览](#3-流水线总览)
4. [不包含什么](#4-不包含什么)
5. [快速开始](#5-快速开始)
6. [配置与模型中立](#6-配置与模型中立)
7. [数据格式](#7-数据格式)
8. [实测数据（含负面结论）](#8-实测数据含负面结论)
9. [成本口径](#9-成本口径)
10. [伦理与法律警告](#10-伦理与法律警告)
11. [已知缺口](#11-已知缺口)
12. [目录结构](#12-目录结构)
13. [离线自测（--mock）](#13-离线自测--mock)
14. [许可](#14-许可)

---

## 1. 价值主张：它解决什么问题

群聊是非结构化的：一人连发、多人交织、图文混杂、@ 与回复满天飞。
直接丢给大模型，8.5 万 tokens 的整群输入会让判断严重漂移。

本项目把整群任务**分解成 9,709 个「1,100 tokens 的小判断」**，
使 9B 级本地模型即可完成事件切分。

不是“省 API 费用”，而是**把一个大模型做不好的任务，拆成小模型能做好的任务**：

- 每一条消息只需要在“固定外延窗口”里判断一次“续 / 新”；
- 困难的边界（长事件、跨话题）再交给大模型做少量复核；
- 纯规则步骤（链接合并、质量闸门、导出）完全确定性，可复现。

---

## 2. 输出长什么样

最终产物 `<data_root>/groups/<群号>_<群名>/segments_ctx.jsonl`，每行一个事件：

```json
{"event_id": "L0123", "peer": "123456789",
 "ts_first": 1788190689, "ts_last": 1788190900,
 "participants": ["示例用户A", "示例用户B"], "n_units": 7, "n_msgs": 7, "chars": 640,
 "messages": [
   "示例用户A: 这耳机续航有点猛",
   "示例用户B: [图片：一张表情包，画面为戴眼镜男子开怀大笑并指向右侧，用于表达调侃或捧场]",
   "示例用户A: 上周三开始用 用到现在，平均每天10小时 还有电"
 ]}
```

---

## 3. 流水线总览

| 步骤 | 做什么 | 需要模型 | 产物 |
|---|---|---|---|
| 0.1 | 导入消息流（读用户已导出的 `messages.jsonl`）、补媒体占位、生成名字表 | — | `messages.jsonl` |
| 0.5 | 媒体转文本（图片约 50 字描述 / 表情 ≤15 字） | 小模型·视觉 | `messages_media.jsonl` |
| 0.6 | 同人连发 → 语义分块 → 合并成 unit | 小模型 | `units.jsonl` |
| 0.7 | @ / reply 语义初审（相关→建链接，不相关→删标记） | 小模型 | `units_at.jsonl` |
| 0.8 | 文本卫生、悬空链接修正、空正文丢弃 | — | `units_at.jsonl` + `units_stats.json` |
| 1.1 | **事件判别（逐条 + 外延 5 段）** | 小模型 | `s1_events_ev.jsonl` |
| 1.2 | 按已确认链接合并事件（非连续，≤20 条 / ≤2000 字） | — | `s1_events_linked.jsonl` |
| 1.3 | 长事件复核（>10 条，只切不合） | 大模型 | `s1_events_reviewed.jsonl` |
| 1.4 | 质量闸门（单条 + 文字含量 <30 丢弃，媒体按 15 字计） | — | `s1_events_gated.jsonl` |
| 1.5 | 事件级导出 + 规则分类 | — | `dataset_dataset.jsonl` |
| 2.1 | 图片结合上下文重述（每图一次，描述 + 关联两行） | 大模型·视觉 | `media_context_ctx.jsonl` |
| 2.2 | 回填描述 → 纯文本片段 | — | `segments_ctx.jsonl` |
| 2.3 | 导出图文交错格式（可选） | — | `segments_interleaved.jsonl` |

“小模型 / 大模型”只是**角色名**：具体是哪个模型完全由 `config.json` 决定。
详细设计理由、参数与参考耗时见 [`docs/pipeline-overview.md`](docs/pipeline-overview.md)。

---

## 4. 不包含什么

本项目**不包含** QQ 客户端数据库的读取、解密或协议解析功能。
你需要自己合法导出聊天记录，转换成 README 中约定的 `messages.jsonl` 格式后再使用本工具。
项目源码中不含任何数据库读取代码（可自行 grep 验证）。

具体地说：

- 0.1 步骤（`stages/s0_import.py`）只做「读取你提供的 JSONL → 规范化字段」；
- 仓库里没有任何 `sqlite3` / `pysqlcipher` / `PRAGMA key` / `decrypt` / `nt_msg` 相关代码；
- 也不解析 QQ 的 protobuf 消息体——导出的 JSONL 已经是你自己整理好的明文；
- 数据获取方式由你自行负责并承担合规责任（见 §10）。

---

## 5. 快速开始

需求：Python 3.9+；一个 OpenAI 兼容的推理服务（LM Studio / Ollama / vLLM / llama.cpp server / 云端 API 都行）。

```bash
# 0. 安装依赖（只有 Pillow 是必需的）
pip install -r requirements.txt

# 1. 启动任意 OpenAI 兼容推理服务，加载两个模型：
#    一个带视觉能力的小模型（阶段 0 / 1.1 用）+ 一个大模型（1.3 / 2.1 用）
#    不限定厂商，只要接口是 OpenAI 兼容的即可

# 2. 复制配置模板并填写
cp config.example.json config.json
#    至少填：data_root / group_id / base_url / model_small / model_big
#    视觉模型则 model_supports_vision 保持 true

# 3. 把你已合法导出的聊天记录转换成 messages.jsonl（格式见 §7），放到：
#    <data_root>/groups/<group_id>_<group_name>/input/messages.jsonl

# 4. 查看状态 / 检查模型是否就绪
python pipeline.py --status
python pipeline.py --check-llm

# 5. 跑完整流程
python pipeline.py --group 123456789
```

跑完后 `segments_ctx.jsonl` 就是成果；`python pipeline.py --status` 全 ✓ 即完成。
常用选项：

```bash
python pipeline.py --from 1.1 --to 1.5   # 断点续跑 / 只跑某几步
python pipeline.py --force               # 忽略已有产物重跑
python pipeline.py --list-prompts        # 打印全部提示词
```

### 三条便捷入口（都不需要额外依赖）

**① 交互式配置：`--init-config`**

不知道 config 怎么填？用它逐项问，回车取默认：

```bash
python pipeline.py --init-config
```

写完会直接提示下一步该干什么。最常填错的是 `media_root`——填错会导致图片全部
按“丢失”处理而且**不会报错**，这个向导会顺带解释每个字段。

**② 静态 HTML 报告：`--html`**

`segments_ctx.jsonl` 是机器格式，467 个事件用终端看不方便。生成一份单文件 HTML：

```bash
python pipeline.py --html                 # 引用本地图片，HTML 很小（467 事件约 0.9 MB）
python pipeline.py --html --html-embed    # 把缩略图内嵌进去，可整个拷走（文件大很多）
```

产物在 `data/groups/<群标签>/review.html`，**双击就能在浏览器打开**：

- 每个事件一张可折叠卡片：编号、时间范围、参与者、消息数、字数、类型
- 消息里的 `[图片：…]` 会直接显示出图，可以对着图检查描述准不准
- 顶部有汇总统计 + 一个即时过滤框（纯 JS，无依赖）

**③ 自定义提示词：`--prompts-override`**

默认提示词经过基准验证，不建议改。如果你确实要试自己的版本，**不要改代码**：

```bash
python pipeline.py --prompts-override my_prompts.json
```

```json
{ "segment.unit": "你在处理……{context}……{unit}……", "review.event": "下面是……{lines}……" }
```

可用的键与占位符见 [`core/prompts.py`](core/prompts.py) 顶部注释。运行时它会：

- 打印醒目警告（README 的基准数字不再适用）
- 把实际使用的提示词哈希写进 `pipeline_manifest.json`，便于追溯
- 键名写错会直接报错并列出可用键，不会被静默忽略

> **注意**：第 1 步不限定模型厂商，只要 OpenAI 兼容即可。默认配置里写的是我们验证过的
> Qwen 模型，仅为“可用的默认值”，不是硬性要求（见 §6）。

---

## 6. 配置与模型中立

`config.json` 全部字段（相对路径以 config.json 所在目录为基准）：

| 字段 | 说明 |
|---|---|
| `data_root` | 数据根目录；群目录、日志都在其下（默认 `data`） |
| `group_id` | 群号（**只作本地目录标签**，任何编号都可以） |
| `group_name` | 群名（可空，仅用于目录名和展示） |
| `input_messages` | 原始消息文件路径；空 = `<group_dir>/input/messages.jsonl` |
| `media_root` | 相对媒体路径的解析基准；空 = 用 `data_root` |
| `base_url` | OpenAI 兼容端点，通常以 `/v1` 结尾 |
| `api_key` | 云端 API 需要时填；本地服务留空 |
| `model_small` | “小模型”角色：阶段 0 与 1.1 用（建议带视觉能力） |
| `model_big` | “大模型”角色：1.3 复核与 2.1 图片重述用（建议带视觉能力） |
| `model_supports_vision` | **视觉能力开关**：false 时 0.5 / 2.1 自动降级跳过 |
| `temperature` | 默认 0.1（基准测试脚本内部用 0.0） |
| `reasoning_effort` | 默认 `"none"`（实验结论：验证用的模型开思考收益不足） |
| `request_timeout` | 单次请求超时秒数 |
| `mock` | true 时用确定性的假回复离线自测 |

### 模型可替换声明

> 本项目在 `qwen/qwen3.5-9b`（视觉）与 `qwen/qwen3.8-27b` 上验证。
> 由于只使用 OpenAI 兼容接口，**任何具备同等能力与视觉支持的本地模型都可以替换**，
> 只需修改 `config.json` 中的 `model_small` / `model_big` 与 `model_supports_vision`。
> 未在其他模型上做过基准测试。

工程上保证模型中立：

- 模型名**只出现在配置里**，任何 `.py` 业务逻辑中都没有硬编码模型名；
- 代码里没有按厂商名分支的判断；
- 视觉能力由 `model_supports_vision` 开关决定，**不靠模型名猜**；
- 连接失败 / 模型不存在 / 鉴权失败时给出**可读的报错**，而不是静默产出垃圾；
- `python pipeline.py --check-llm` 可以在开跑前检查端点与模型。

---

## 7. 数据格式

### 输入：`messages.jsonl`

用户自行合法导出的消息，每行一条。本仓库**不读取任何聊天数据库**：

```json
{"msg_id":"m0001","ts":1788190689,"sender_uid":"u1001","sender_name":"示例用户A",
 "text":"正文（已去掉图片占位）","at_uids":["u1002"],"reply_to_msg_id":"m0000",
 "media":[{"md5":"ab12cd34ef56ab12cd34ef56ab12cd34","kind":"Pic","path":"media/ab12cd34.jpg"}]}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `msg_id` | ✅ | 消息唯一 id（字符串） |
| `ts` | ✅ | Unix 时间戳（秒，整数） |
| `sender_uid` | ✅ | 发送者 id（任意字符串，不需要是 QQ 号；只要求同一人一致） |
| `sender_name` | 建议 | 显示名；缺省用 uid |
| `text` | ✅ | 正文，已去掉媒体占位 |
| `at_uids` | 可选 | 本条 @ 了谁的 uid 列表 |
| `reply_to_msg_id` | 可选 | 本条回复的消息 id |
| `media` | 可选 | 媒体列表：`md5`（32 位 hex，缺省时自动生成）、`kind`（`Pic`/`Emoji`/`Video`/`File`/`Ptt`）、`path`（绝对路径或相对 `media_root`） |

> 0.1 会按 `media` 顺序在正文末尾补回占位符，0.5 再替换成文字描述；
> 视频/文件/语音不做视觉处理，保留 `[视频]` / `[文件]` / `[语音]` 标签。

### 输出：`segments_ctx.jsonl`

```json
{"event_id":"L0123","peer":"123456789","ts_first":1788190689,"ts_last":1788190900,
 "participants":["示例用户A","示例用户B"],"n_units":7,"n_msgs":7,"chars":640,
 "messages":["示例用户A: 正文","示例用户B: [图片：一句话描述]"]}
```

**完整规范（含多媒体接入）见 [`docs/data-format.md`](docs/data-format.md)**，其中说明了 `media` 数组结构、`kind` 别名表、路径解析规则，以及占位符在「导入 → 0.5 无上下文转述 → 2.1 结合上下文重述」三阶段的完整形态。

自带两份编造样例：

| 文件 | 内容 |
|---|---|
| [`examples/sample_input.jsonl`](examples/sample_input.jsonl) | 20 条纯文本消息 |
| [`examples/sample_input_media.jsonl`](examples/sample_input_media.jsonl) | 12 条，含 **2 图 + 1 表情 + 1 视频 + 1 文件** |
| [`examples/media/`](examples/media/) | 3 张**程序合成**的占位图（无真实照片/个人信息） |

跑多媒体示例：把 `config.json` 的 `input_messages` 指向 `examples/sample_input_media.jsonl`、`media_root` 指向 `examples/media`，再 `python pipeline.py`。

---

## 8. 实测数据（含负面结论）

### Ubuntu IRC 基准（2,397 样本，金标“新”率 19.3%）

```
9B 单步      P=0.878 R=0.862 F1=0.870 准确率 95.0%  8.4 min
27B 单步     P=0.695 R=0.970 F1=0.809 准确率 91.2%  26.5 min
9B + 27B复核 P=0.802 R=0.867 F1=0.833              10.5 min
对照文献：TIAGE 上 ChatGPT 零样本 F1 0.362、CSM 0.427（我们是 27B 0.391）
```

**必须同时说明：1.3 复核在基础切分较好时会降低分数**（IRC F1 0.870 → 0.833；
TIAGE 0.249 → 0.239）——这不是“多级复核提升质量”的流水线，复核只适合处理长尾长事件。

其他重要结论：

- **规模上限不能去掉**：合并上限从“无上限”改为“≤20 条 / ≤2000 字”后，
  聚类 F1 从 0.232 升到 0.377；无上限时一条回复链会把整条通道焊成一坨。
- **0.7 @/reply 初审在 IRC 上几乎不过滤**（确认率 97.1%）——它在真实群聊场景的价值未经独立验证。
- **体裁不匹配的分数无意义**：中文会议数据（Alimeeting4MUG）上 F1 0.06 是任务定义差一个数量级，
  不是算法失效。
- 完整报告（含错误分析、图片转述实验、思考模式实验）见
  [`docs/validation-report.md`](docs/validation-report.md)。

> 以上数值来自作者在特定模型上的实测，换模型后请用 `bench/` 脚本自行复测。

---

## 9. 成本口径

单群（6,592 条消息 / 815 张图，本地 GPU，70 分钟量级）：

```
本地 GPU        约 ¥3–4（电费+折旧，70 分钟）
便宜档云 API    约 ¥9–17（等效全流程）
强模型云 API    约 ¥150–486（等效全流程）
```

成本主要由“每张图一次视觉调用 + 每条消息一次判别”的次数决定；
把视觉步骤或复核步骤关掉会显著降低成本（是否值得取决于数据形态）。

---

## 10. 伦理与法律警告

- 本工具仅用于处理**你拥有合法访问权**的聊天记录；
- 群聊记录含大量个人信息，**请勿上传未经脱敏的数据**；
- 本项目**不提供数据库解密工具**，请自行合法导出数据；
- 使用者对数据的合法性与用途负**全部责任**。

此外：图片描述由视觉模型转述，可能包含对图片内容的（错误）推断；
在把数据用于任何对外发布的用途前，请自行做隐私审查与人工抽检。

---

## 11. 已知缺口

1. **未经人工标注验证**——真实群聊的准确率未知，公开基准只验证了 1.1 单步；
2. **图文事件归属**（图片属于哪个事件）无外部基准可测；
3. **规模上限 20 条 / 2000 字未在 QQ 数据上扫描**（该值来自 IRC 扫描）；
4. **图片描述是 VLM 转述，可能有错字**（实测会把游戏任务名读错）；
5. **未做消息去重**（复读、转发同一内容会保留多份）；
6. **人物名未脱敏**——输出文本里是群昵称，如需通用角色名请在 2.2 之后加映射层。

---

## 12. 目录结构

```
qqchat-seg/
├── pipeline.py               # 编排器（13 步，--status / --mock / --check-llm）
├── config.py                 # 统一配置读取（路径 / 模型角色 / 视觉开关）
├── config.example.json       # 配置模板（复制为 config.json）
├── core/
│   ├── llm.py                # 模型中立的 OpenAI 兼容调用层（含 mock 后端）
│   └── ph_render.py          # 媒体占位符稳健解析 + 上下文渲染
├── stages/
│   ├── s0_import.py          # 0.1 导入消息流（只读用户导出的 JSONL，无数据获取）
│   ├── s0_media.py           # 0.5 媒体转文本
│   ├── s0_merge_runs.py      # 0.6 连发块合并
│   ├── s0_at_prep.py         # 0.7 @/reply 初审
│   ├── s0_finalize.py        # 0.8 阶段 0 收尾
│   ├── s1_segment_unit.py    # 1.1 事件判别（核心）
│   ├── s1_link_merge.py      # 1.2 按链接合并
│   ├── s1_review.py          # 1.3 长事件复核
│   ├── s1_quality_gate.py    # 1.4 质量闸门
│   ├── s1_finalize_dataset.py# 1.5 数据集导出
│   ├── s2_media_context.py   # 2.1 图片上下文重述
│   ├── s2_build_segments.py  # 2.2 纯文本片段（最终成果）
│   └── s2_export_interleaved.py  # 2.3 图文交错（可选）
├── prompts/PROMPTS.md        # 7 条提示词逐字存档
├── docs/
│   ├── data-format.md
│   ├── pipeline-overview.md  # 流程总览与设计决策
│   ├── validation-report.md  # 算法验证报告（含负面结论）
│   └── pitfalls.md           # 踩坑记录
├── bench/                    # 基准脚本（不含数据集）
│   ├── bench_ubuntu_irc.py
│   ├── bench_tiage.py
│   ├── bench_alimeeting.py
│   └── README.md
└── examples/sample_input.jsonl  # 编造的脱敏样例输入
```

数据与运行产物（`data/`、`logs/`、`config.json`、`*.jsonl`、`*.db` 等）全部在
[`.gitignore`](.gitignore) 中忽略，**不会进入版本库**。

---

## 13. 离线自测（--mock）

没有 GPU / 没有模型也能验证流水线是否跑通：`--mock` 用确定性的假回复代替真实模型。
仓库自带的脱敏样例正好可以跑一次最小闭环：

```bash
cp config.example.json config.json     # 默认 group_id=123456789, group_name=示例群
mkdir -p "data/groups/123456789_示例群/input"
cp examples/sample_input.jsonl "data/groups/123456789_示例群/input/messages.jsonl"
python pipeline.py --mock --group 123456789
# → data/groups/123456789_示例群/segments_ctx.jsonl
```

也可以直接跑单个阶段脚本（脚本都能独立运行）：

```bash
python stages/s0_import.py --group 123456789
python stages/s1_segment_unit.py --group 123456789 --tag ev --lookahead 5 --explain
```

---

## 14. 许可

[MIT](LICENSE)。

**再次强调**：请只处理你拥有合法访问权的数据；不要上传、分享或发布未经脱敏的聊天记录。
