# qqchat-seg · 群聊事件切分流水线

[![CI](https://github.com/Z-HG-master/qqchat-seg/actions/workflows/ci.yml/badge.svg)](https://github.com/Z-HG-master/qqchat-seg/actions/workflows/ci.yml)

把开放域的 QQ 群聊天记录，清洗成**事件级对话片段**（一段连续的、话题内聚的对话）。

配置驱动 · 模型中立 · 全流程本地 · 不含任何数据获取功能。

| | |
|---|---|
| **实测**（Ubuntu IRC，2,397 样本） | `qwen3.5-9b` 单步 **F1 0.870** / 准确率 **95.0%**<br>⚠️ 但 1.3 复核会让分数**下降**到 0.833，见 [§9](#9-实测数据含负面结论) |
| **隐私** | 全流程本地，聊天记录不出本机 |
| **费用** | 无 API 账单，只有电费与折旧 |
| **依赖** | 只有 Pillow（`--html` 内嵌图片时才需要） |

---

## 1. 它解决什么问题

群聊是非结构化的：一人连发、多人交织、图文混杂、@ 与回复满天飞。

直接把整群（约 8.5 万 tokens）丢给大模型做切分，判断会严重漂移——我们实测过：
上下文越长，模型越倾向判"同一话题"，续接率从 23% 涨到 84%。

**本项目的做法是把整群任务分解成数千个"上下文约 1.1k tokens 的小判断"**，
让 9B 级本地模型就能完成事件切分，而不必依赖更大的模型或更长的上下文。

具体做了四件事：

| 分解动作 | 解决的原始问题 |
|---|---|
| 连发合并成 unit | 一人连发 5 条，单条没有完整语义 |
| @/reply 语义初审 | "谁在跟谁说话"从自然语言推断变成可验证的结构边 |
| 媒体转文本 | 消除多模态异构，让所有消息同构 |
| 逐条判定 + 结构后处理 | 把整群任务切成小判断，绕开长上下文 |

---

## 2. 输出长什么样

```json
{"event_id":"V0001","peer":"123456789","ts_first":1788190689,"ts_last":1788190719,
 "participants":["示例用户A","示例用户B"],"n_units":2,"n_msgs":3,"chars":96,
 "messages":[
   "示例用户A: 刚打完一把，这把评分居然给了 S [图片：游戏结算界面，显示 RATING S、SCORE 12345]",
   "示例用户B: 真的假的，我最高才 A",
   "示例用户A: 你试试把连招少放两段，评分看的是连击质量不是数量"]}
```

每行一个事件，`messages` 是纯文本消息列表，可直接用于训练或检索。
图片已转成一句话描述；如果下游是多模态模型，另有[图文交错分支](#5-两条产物分支)。

---

## 3. 流水线

13 步，前 10 步两条产物分支共用：

| 步骤 | 做什么 | 需要模型 | 产物 |
|---|---|---|---|
| 0.1 | 导入已导出消息、补媒体占位、生成名字表 | — | `messages.jsonl` |
| 0.5 | 媒体转文本（图片约 50 字 / 表情 ≤15 字） | 小模型·视觉 | `messages_media.jsonl` |
| 0.6 | 同人连发 → 语义分块 → 合并成 unit | 小模型 | `units.jsonl` |
| 0.7 | @ / reply 语义初审（相关→建链接，不相关→删标记） | 小模型 | `units_at.jsonl` |
| 0.8 | 文本卫生、悬空链接修正 | — | `units_stats.json` |
| **1.1** | **事件判别（逐条 + 外延 5 段）** | 小模型 | `s1_events_ev.jsonl` |
| 1.2 | 按已确认链接合并（非连续，≤20 条 / ≤2000 字） | — | `s1_events_linked.jsonl` |
| 1.3 | 长事件复核（>10 条，只切不合） | 大模型 | `s1_events_reviewed.jsonl` |
| 1.4 | 质量闸门（单条 + 文字含量 <30 丢弃） | — | `s1_events_gated.jsonl` |
| 1.5 | 事件级导出 + 规则分类 | — | `dataset_dataset.jsonl` |
| 2.1 | 图片结合上下文重述（每图一次） | 大模型·视觉 | `media_context_ctx.jsonl` |
| 2.2 | 回填描述 → 纯文本片段 | — | `segments_ctx.jsonl` |
| 2.3 | 导出图文交错格式 | — | `segments_interleaved.jsonl` |

"小模型 / 大模型"只是**角色名**，具体是哪个模型完全由 `config.json` 决定。
设计理由、参数与参考耗时见 [`docs/pipeline-overview.md`](docs/pipeline-overview.md)。

---

## 4. 关键设计决策

| 决策 | 理由 |
|---|---|
| unit = 同人连发合并 | 群聊常连发 3–5 条说完一件事，按单条处理会切碎句子 |
| @ 与 reply 都要"初审" | 实测 reply 边里 **48%** 说的不是同一件事（"回复了你但聊别的"） |
| 逐条判别 + 外延 5 段 | 比窗口切分更贴近人读对话的方式；窗口版边界僵硬 |
| 事件判别**必须带解释** | 只输出一个字的准确率显著低于输出"判定+理由"（p90 事件长度 48 → 14） |
| 长事件 27B 复核 | 9B 判完仍有长事件混着跨话题，复核带解释后会切开约 1/3 |
| 质量闸门放最后 | 早放会导致复核切出的碎片漏网；统一只丢一次 |
| 媒体两轮识别 | 第一轮无上下文（服务切分），第二轮带上下文（服务最终文本） |
| 全部 `reasoning_effort: "none"` | 9B 不响应此参数；27B 响应但收益不抵成本（详见验证报告） |

---

## 5. 两条产物分支

前 10 步共用，切分只做一次；2.x 分岔成两条路：

```
0.1 → 0.5 → 0.6 → 0.7 → 0.8 → 1.1 → 1.2 → 1.3 → 1.4 → 1.5   （共用）
                                                        │
                            ┌───────────────────────────┴───────────────────────────┐
                     A：纯文本分支                                        B：图文交错分支
                     2.1 图片上下文重述（大模型·视觉，慢）                  2.3 导出（纯规则，秒级）
                     2.2 回填 → segments_ctx.jsonl                       segments_interleaved.jsonl
                     给文本模型                                             给多模态模型
```

```bash
python pipeline.py --branch text          # 只要纯文本（跑 2.1 + 2.2）
python pipeline.py --branch interleaved   # 只要图文交错（跳过 2.1，2.x 约 1 秒）
python pipeline.py --branch both          # 两份都出（默认）
```

| | `text` | `interleaved` | `both` |
|---|---|---|---|
| 2.1 图片上下文重述 | ✅ | **❌ 跳过** | ✅ |
| 产物 | `segments_ctx.jsonl` | `segments_interleaved.jsonl` | 两份 |
| 2.x 参考耗时 | 约 21 分钟 | **约 1 秒** | 约 21 分钟 |

**分支 B 不需要文本描述**：图本身就是信号，占位符直接还原成真实图片引用。

```json
{"sender":"示例用户A","content":[
   {"type":"text","text":"刚打完一把，这把评分居然给了 S"},
   {"type":"image","image":"examples/media/demo_screenshot.png","kind":"图片"}]}
```

图片寻址三选一：默认写路径 / `--copy-media DIR` 复制并去重 / `--media-mode base64` 自包含。
实测（467 事件 / 938 个图片块）：path 0.72 MB · copy-media 0.68 MB + 图 500 MB · base64（768px）47 MB。

> **注意**：`0.5 媒体转文本` 在两条分支里都必须跑——1.1 事件判别要靠文本判断
> "这几条在聊同一件事"。省掉的是 2.1（为重述服务的第二轮识别），不是 0.5。

---

## 6. 快速开始

需求：Python 3.9+；一个 OpenAI 兼容的推理服务（LM Studio / Ollama / vLLM / llama.cpp / 云端都行）。

```bash
# 1. 安装依赖（只有 Pillow）
pip install -r requirements.txt

# 2. 启动推理服务，加载两个模型：
#    一个带视觉能力的小模型（阶段 0 / 1.1）+ 一个大模型（1.3 / 2.1）

# 3. 生成配置（逐项询问，回车取默认）
python pipeline.py --init-config

# 4. 把你已合法导出的聊天记录转成 messages.jsonl（格式见 §7）
#    放到 <data_root>/groups/<group_id>_<group_name>/input/messages.jsonl

# 5. 检查模型是否就绪
python pipeline.py --check-llm

# 6. 跑完整流程
python pipeline.py --group 123456789
```

跑完看 `data/groups/<群标签>/segments_ctx.jsonl`。

常用开关：

```bash
python pipeline.py --status                    # 各步产物状态
python pipeline.py --mock                      # 离线自测（不调用真实模型）
python pipeline.py --from 1.1 --to 1.5         # 断点续跑
python pipeline.py --force                     # 忽略已有产物重跑
python pipeline.py --html                      # 生成静态 HTML 报告（双击可看）
python pipeline.py --list-prompts              # 打印各步提示词
python pipeline.py --prompts-override p.json   # 用自定义提示词（会警告基准数字失效）
```

**HTML 报告**：`--html` 生成单文件 `review.html`，每个事件一张卡片，
消息里的图片直接渲染，顶部有即时过滤框（纯 JS，无依赖）。

---

## 7. 配置与模型中立

`config.json` 全部字段（相对路径以该文件所在目录为基准）：

| 字段 | 说明 |
|---|---|
| `data_root` / `group_id` / `group_name` | 产物目录；群标识只作本地目录名 |
| `input_messages` | 输入 JSONL 路径（留空则在群目录 `input/` 下找） |
| `media_root` | 媒体根目录（留空= `data_root`）；**填错会导致图片全部按丢失处理且不报错** |
| `base_url` / `api_key` | OpenAI 兼容端点 |
| `model_small` / `model_big` / `model_supports_vision` | 模型角色名与视觉开关 |
| `temperature` / `reasoning_effort` / `request_timeout` | 调用参数 |

### 模型可替换声明

本项目在 `qwen/qwen3.5-9b`（视觉）与 `qwen/qwen3.8-27b` 上验证。
由于只使用 OpenAI 兼容接口，**任何具备同等能力与视觉支持的本地模型都可以替换**，
只需修改 `config.json` 中的 `model_small` / `model_big` 与 `model_supports_vision`。
**未在其他模型上做过基准测试。**

代码中没有任何按厂商名分支的逻辑，模型名也只出现在配置文件里。

---

## 8. 数据格式

输入 `messages.jsonl` 每行一条消息：

```json
{"msg_id":"m0001","ts":1788190689,"sender_uid":"u1001","sender_name":"示例用户A",
 "text":"周末去爬山吗","at_uids":["u1002"],"reply_to_msg_id":null,
 "media":[{"md5":"96a5413d…","kind":"Pic","path":"demo_screenshot.png"}]}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `msg_id` | ✅ | 消息唯一 id（供 `reply_to_msg_id` 指向） |
| `ts` | ✅ | Unix 时间戳（秒） |
| `sender_uid` | ✅ | 发送者 id，不需要是 QQ 号，只要求同一人一致 |
| `sender_name` | 建议 | 显示名，缺省用 uid |
| `text` | ✅ | 正文，媒体请放 `media` 而不是写 `[图片]` |
| `at_uids` / `reply_to_msg_id` | 可选 | @ 谁 / 回复哪条 |
| `media` | 可选 | `kind` 支持 `Pic`/`Image`/`Emoji`/`Sticker`/`Video`/`File`/`Ptt`（大小写不敏感，也接受中文） |

**完整规范**（`kind` 别名表、路径解析规则、占位符三阶段形态、两轮描述的区别）
见 [`docs/data-format.md`](docs/data-format.md)。

自带两份编造样例：

| 文件 | 内容 |
|---|---|
| [`examples/sample_input.jsonl`](examples/sample_input.jsonl) | 20 条纯文本消息（最小闭环用） |
| [`examples/sample_input_media.jsonl`](examples/sample_input_media.jsonl) | 12 条，含 2 图 + 1 表情 + 1 视频 + 1 文件 |
| [`examples/media/`](examples/media/) | 3 张**程序合成**的占位图（无真实照片/个人信息） |

跑多媒体示例：把 `input_messages` 指向 `sample_input_media.jsonl`、`media_root` 指向
`examples/media`，再 `python pipeline.py --mock`。

---

## 9. 实测数据（含负面结论）

### Ubuntu IRC 基准（多人群聊，2,397 样本，金标"新"率 19.3%）

| 配置 | P | R | F1 | 准确率 | 耗时 |
|---|---|---|---|---|---|
| **`qwen/qwen3.5-9b@q4_k_m` 单步** | 0.878 | 0.862 | **0.870** | **95.0%** | 8.4 min |
| `qwen/qwen3.8-27b@q4_k_m` 单步 | 0.695 | **0.970** | 0.809 | 91.2% | 26.5 min |
| `qwen3.5-9b` + `qwen3.8-27b` 复核 | 0.802 | 0.867 | 0.833 | — | 10.5 min |
| 全判"续"（什么都不切） | — | 0 | 0 | 80.7% | — |

> 设备与推理配置见 [§10 测试环境](#我们的测试环境真实配置供对照)：
> RTX 5090 + LM Studio，两个模型均为 Q4_K_M 量化、均支持视觉。
> 换量化档位或换模型后，以上数字不保证仍然成立。
> 下文为简洁起见，把 `qwen/qwen3.5-9b` 简称为 **9B**、`qwen/qwen3.8-27b` 简称为 **27B**。

> ⚠️ **负面结论：1.3 的 27B 复核会让分数变低（0.870 → 0.833）。**
> 复核是为"事件被过度合并"设计的；当基础切分已经比较准时，它只是在加噪声。
> 在 9B 单步已经 0.870 的场景下，复核新增 45 个切点只有 2 个是对的。
> 保留这一步是因为真实群聊里 @/reply 合并会产生超长事件，但**不要期待它总是加分**。

### 与文献对照

| 方法 | 数据集 | F1 |
|---|---|---|
| ChatGPT 零样本 | TIAGE | 0.362 |
| CSM（专门训练） | TIAGE | 0.427 |
| **我们的 27B** | TIAGE | **0.391** |
| ChatGPT 零样本 | SuperDialseg | 0.658 |

### 其他结果

- **非连续合并（1.2）**：交织场景下聚类 pairwise F1 **0.345 → 0.377**，召回 0.309 → 0.413（+34%）
- **体裁不匹配的分数无意义**：同一个 9B 在正式会议数据（Alimeeting）上只有 F1 0.060——
  因为会议的话题段平均 62 句才切一次，而群聊每 4 条就可能换话题

完整的对比、错误分析与未验证项见 [`docs/validation-report.md`](docs/validation-report.md)。

---

## 10. 为什么强调本地跑

**整个流程在你的机器上完成，聊天记录不外溢。**

模型通过 OpenAI 兼容接口调用（LM Studio / Ollama / vLLM / llama.cpp 都行），
不需要把任何数据发给第三方。这意味着：

- 群里的真名、手机号、地址、生活照片都不会离开本机
- 没有按次计费，也没有 QPS / TPM 限制——一个群上万次调用可以连续跑完
- 任何一步失败都能原地重跑，不产生额外费用
- 完全离线可用

**成本**：只有电费与硬件折旧，没有 API 账单。具体金额取决于你的机器、群规模和模型大小，
我们不做精确估算——真正的收益是**数据不出本机**，而不是省下多少钱。

### 硬件要求：消费级显卡够用

流水线是**逐条小判断**，所以显存需求由"单个模型"而不是"整个群"决定——
几万条消息和几百条消息，占的显存是一样的，只是跑得久一点。

#### 我们的测试环境（真实配置，供对照）

| 项 | 配置 |
|---|---|
| **GPU** | **NVIDIA GeForce RTX 5090**（32,607 MiB ≈ 32 GB），驱动 595.97 / CUDA 13.2 |
| **CPU / 内存** | Intel i7-13700KF / 64 GB |
| **系统** | Windows 10 专业版 |
| **推理服务** | LM Studio（OpenAI 兼容端点 `http://127.0.0.1:1234/v1`） |
| **小模型** | `qwen/qwen3.5-9b@q4_k_m`（9B，arch `qwen35`，视觉 VLM，6.55 GB） |
| **大模型** | `qwen/qwen3.8-27b@q4_k_m`（27B，arch `qwen35`，17.74 GB） |
| **加载参数** | 小模型 `-c 16384 --parallel 4`；大模型 `-c 32768 --parallel 2`（两者不同时加载） |

> 模型名就是 LM Studio 里的标识符。`q4_k_m` 是量化版本——
> 如果你下载的是 Q5/Q8，显存占用会更大，判断质量可能略有提升。
> 这两个都是视觉模型（VLM），因为 0.5 媒体标注和 2.1 图片重述需要看图。

#### 显存对照

| 角色 | 我们实测 | 16GB 卡能不能跑 |
|---|---|---|
| **小模型**（0.5 / 0.6 / 0.7 / 1.1 判别） | 9B @Q4，16k 上下文，**6.6 GB** | ✅ 很宽裕，还能开多并行槽 |
| **大模型**（1.3 复核 / 2.1 图片重述） | 27B @Q4，32k 上下文，**17.7 GB** | ⚠️ 放不下，换 7–14B 即可 |

**16GB 显存的实际配置建议**：

```
小模型：9B 视觉模型（Q4/Q5）        → 约占 6–7 GB（实测 6.55）
大模型：7–14B 视觉模型（Q4）        → 约占 8–12 GB（估算，未实测）
两者不同时加载，串行跑即可
```

> 27B 那一档不是必需的。它只在 1.3 复核和 2.1 图片重述里担当"更强的判官"，
> 换成 14B 会损失一些判断质量，但流程和产物完全一致——
> 只需改 `config.json` 里的 `model_big`。

**数据量**：实测 6,592 条消息 / 815 张图约 **70 分钟**（RTX 5090 单卡）。
耗时随数据量线性增长，显存占用不变——这是我们和"整群塞进一次请求"最大的区别。

> 如果显存更小：优先降低 `model_supports_vision` 相关的图片尺寸、或改用更小的模型；
> 上下文长度（`max_tokens` / 并行槽数）都在配置与启动参数里，不需要改代码。

> 如果你想接云端 API，改 `base_url` 和 `api_key` 即可，代码不需要动。
> 但那就不再满足"数据不外溢"这个前提了，需要你自己评估合规性。

---

## 11. 不包含什么

本项目**不包含** QQ 客户端数据库的读取、解密或协议解析功能。

你需要自己合法导出聊天记录，转换成本文档约定的 `messages.jsonl` 格式后再使用本工具。
源码中不含任何数据库读取代码（可自行 grep 验证）：

```bash
grep -rn "sqlite3\|pysqlcipher\|PRAGMA key\|nt_msg\|decrypt" --include=*.py .
```

---

## 12. 伦理与法律警告

- 本工具仅用于处理**你拥有合法访问权**的聊天记录
- 群聊记录含大量个人信息（真名、手机号、住址），**请勿上传未经脱敏的数据**
- 本项目不提供数据获取工具，数据来源的合法性由使用者负责
- 用他人的聊天记录训练模型可能涉及隐私与授权问题，请自行确认合规

---

## 13. 已知缺口

1. **未经人工标注验证**：公开基准只验证了 1.1 单步；真实群聊的准确率未知
2. **图片描述是转述不是原文**：VLM 可能读错字（实测游戏任务名会被读错）
3. **跨事件引用会丢**：合并只对"已确认链接且规模受限"的生效
4. **规模上限（20 条 / 2000 字）未在真实数据上扫描**：是 IRC 基准上的最优值
5. **未做消息去重**：群聊复读、转发同一内容会保留多份

---

## 14. 目录结构

```
qqchat-seg/
├── pipeline.py            编排器（13 步 + 全部命令行开关）
├── config.py              配置读取（路径 / 模型角色 / 视觉开关）
├── config.example.json    配置模板
├── core/                  模型中立调用层、媒体占位符解析、提示词覆盖、HTML 报告、配置向导
├── stages/                13 个阶段脚本，文件名即步骤号（s0_* / s1_* / s2_*）
├── prompts/PROMPTS.md     7 条提示词逐字存档（由代码自动生成）
├── docs/                  设计文档与验证报告
├── bench/                 基准脚本（不含数据集）
└── examples/              编造的脱敏样例（含 3 张合成图）
```

数据与运行产物（`data/`、`logs/`、`config.json`、`*.jsonl`、`*.db` 等）全部在
[`.gitignore`](.gitignore) 中忽略，不会进入版本库。

文档索引：

| 文档 | 内容 |
|---|---|
| [`docs/data-format.md`](docs/data-format.md) | 输入格式规范、多媒体接入、两种输出形态 |
| [`docs/pipeline-overview.md`](docs/pipeline-overview.md) | 每个步骤的设计理由与参数 |
| [`docs/validation-report.md`](docs/validation-report.md) | 三个数据集上的验证结果与错误分析 |
| [`docs/pitfalls.md`](docs/pitfalls.md) | 实现时踩过的坑 |

---

## 15. 许可

[MIT](LICENSE)
