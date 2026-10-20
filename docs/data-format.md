# 数据格式规范

> 本项目**不读取任何聊天数据库**。你自行合法导出的聊天记录，只要能转换成下面的
> `messages.jsonl`，就可以接入这条流水线。

---

## 0. 与平台无关

本流水线**不含任何平台专属逻辑**：只关心「时间戳 / 发送者 / 正文 / 回复 / @ / 媒体」。

- `sender_uid`（内部字段名历史上叫 `sender_qq`）可以是**任何平台**的用户 ID：
  QQ 号、Discord user id、Telegram user id、WhatsApp JID 都行，只要求「同一人一致」。
- `peer` / `group_id` 是**会话 ID**（群号 / 频道 ID / 会话标识），只用作本地目录标签。
- 媒体 `kind` 用通用名（`Pic`/`Emoji`/`Video`/`File`/`Ptt` 及中文别名），不绑定 QQ 类型码。

QQ 是验证最充分的场景；其他平台只要导出成本格式就能跑，
或直接使用 [ChatLab 标准格式](https://docs.chatlab.fun/standard/chatlab-format)（见 §5）。

---

## 1. 输入：`messages.jsonl`

每行一个 JSON 对象，代表**一条消息**。字段如下：

| 字段 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `msg_id` | ✅ | string | 消息唯一 id。用于 `reply_to_msg_id` 指向 |
| `ts` | ✅ | int | Unix 时间戳（**秒**）。流水线按它排序 |
| `sender_uid` | ✅ | string | 发送者 id。**不需要是 QQ 号**，只要求同一个人始终一致 |
| `sender_name` | 建议 | string | 显示名。缺省时用 `sender_uid` 代替 |
| `text` | ✅ | string | 正文，**已去掉媒体占位**（媒体写在 `media` 里） |
| `at_uids` | 可选 | string[] | 本条 @ 了谁的 uid 列表 |
| `reply_to_msg_id` | 可选 | string \| null | 本条回复的消息 id |
| `media` | 可选 | object[] | 媒体列表，见 §2 |

### 最小示例

```json
{"msg_id":"m0001","ts":1788190689,"sender_uid":"u1001","sender_name":"示例用户A","text":"周末去爬山吗","at_uids":[],"reply_to_msg_id":null,"media":[]}
```

---

## 2. `media` 数组：多媒体的接入方式

**这是本项目与纯文本清洗工具的主要区别**：媒体不是被丢掉，而是先转成文字描述、
第二轮再结合上下文重述，最后进入训练文本。

每个元素的结构：

| 字段 | 必填 | 说明 |
|---|---|---|
| `kind` | ✅ | 媒体类型，见下方的别名表 |
| `path` | 视类型 | 文件路径。**绝对路径**原样使用；**相对路径**按 `config.json` 的 `media_root` 解析（`media_root` 为空时按 `data_root`） |
| `md5` | 可选 | 32 位十六进制。缺省时按 `msg_id + path + kind` 自动生成稳定 id |

### `kind` 支持的写法（大小写不敏感）

| 归一化结果 | 可接受的写法 | 是否做视觉识别 |
|---|---|---|
| **图片** | `Pic` / `Image` / `Photo` / `图片` | ✅ |
| **表情** | `Emoji` / `Sticker` / `MFace` / `表情` | ✅ |
| **视频** | `Video` / `视频` | ❌ 只保留 `[视频]` 标签 |
| **文件** | `File` / `文件` | ❌ 只保留 `[文件]` 标签 |
| **语音** | `Ptt` / `Voice` / `Audio` / `语音` | ❌ 只保留 `[语音]` 标签 |

无法识别的 `kind` 一律按 **文件** 处理（不会中断流程）。

### 路径解析规则

```
绝对路径（D:\pics\a.jpg、/home/u/a.jpg）  → 原样使用
相对路径（media/a.jpg、demo_meme.png）    → os.path.join(media_root, path)
```

`media_root` 取值顺序：`config.json` 的 `media_root` → 为空则用 `data_root`。

---

## 3. 占位符的生命周期（理解这一段就理解了流水线）

同一条带图消息，在三个阶段的样子：

### 阶段 0.1 · 导入

代码把 `media` **按数组顺序**在正文末尾补回占位符，带 md5 前缀以便精确定位：

```
示例用户A: 刚打完一把，这把评分居然给了 S [图片#96a5413d]
```

### 阶段 0.5 · 媒体转文本（第一轮，**无上下文**）

视觉模型只看图本身就写描述，替换掉占位符：

```
示例用户A: 刚打完一把，这把评分居然给了 S [图片：游戏结算界面，显示 LEVEL 42、SCORE 12345、RATING S…]
```

> 为什么第一轮不看上下文？因为这一轮的产物要同时服务于**事件切分**——
> 切分时如果把"这张图在回应什么"写进去，等于把答案泄漏进题目。

### 阶段 2.1 · 结合上下文重述（第二轮，**带事件上下文**）

对整个事件重看每张图，输出「描述 + 关联」两行，只把「描述」回填：

```
示例用户A: 刚打完一把，这把评分居然给了 S [图片：游戏结算界面截图，显示 RATING S、SCORE 12345，用于佐证自己拿到了 S 评级]
```

### 阶段 2.2 · 输出纯文本片段

最终 `segments_ctx.jsonl` 里，媒体已经变成一句话，整条消息是纯文本。

### 阶段 2.3 · 输出图文交错格式（分支 B，多模态微调用）

**这一分支不需要 2.1，也不需要文字描述**——图本身就进训练，占位符直接还原成图片引用：

```bash
python pipeline.py --branch interleaved    # 只跑 0.1~1.5 + 2.3，跳过 2.1/2.2
```

同一次切分导出**保留真实图片**的版本：

```json
{"event_id":"V0001","peer":"123456789","ts_first":1788190689,"ts_last":1788190719,
 "participants":["示例用户A","示例用户B"],"n_msgs":2,
 "messages":[
   {"sender":"示例用户A","content":[
      {"type":"text","text":"刚打完一把，这把评分居然给了 S"},
      {"type":"image","image":"examples/media/demo_screenshot.png","kind":"图片",
       "desc":"游戏结算界面截图，显示 RATING S、SCORE 12345","md5":"96a5413d…"}]},
   {"sender":"示例用户B","content":[{"type":"text","text":"真的假的，我最高才 A"}]}
 ]}
```

要点：

- **切分结果与 2.2 完全一致**，只是媒体呈现方式不同（这里是真图片，不是描述）
- `desc` **只在 2.1 跑过时才出现**（`--desc auto`）。分支 B 跳过 2.1，所以默认**没有 desc**——
  这是刻意的：图已经是完整信号，不需要先转成一句话再看
- 如果确实想让描述作为辅助监督，可以 `--desc on`（用 0.5 的描述）或先跑 2.1 再 `--desc auto`
- 视频/文件/语音保留为 `{"type":"text","text":"[视频]"}` 这类标签
- QQ 内置表情（源码里就是文本、没有图片文件）同样保留为文本标签

三种寻址方式：

| 参数 | 产物 | 适用 |
|---|---|---|
| 默认 | 写文件路径 | 训练机和数据在同一台机器 |
| `--copy-media DIR` | 图片复制到 DIR（按 md5 去重），路径相对 jsonl | 数据集要整体打包搬走 |
| `--media-mode base64` | 内联 data URL（默认缩放到 768px） | 完全不依赖外部文件 |

实测（467 事件 / 938 个图片块）：

| 模式 | 大小 | 备注 |
|---|---|---|
| 默认 path | 0.72 MB | 推荐 |
| `--copy-media` | 0.68 MB + 图片 500 MB（706 张去重后） | 可搬移 |
| `base64`（768px） | **47 MB** | 自包含；若不缩放会到 **837 MB** |

---

## 4. 输出：`segments_ctx.jsonl`

每行一个**事件**（一段连续的、话题内聚的对话）：

```json
{"event_id":"E0001","peer":"123456789","ts_first":1788190689,"ts_last":1788190719,
 "participants":["示例用户A","示例用户B","示例用户C"],"n_units":5,"n_msgs":5,"chars":186,
 "messages":["示例用户A: 刚打完一把，这把评分居然给了 S [图片：…]",
             "示例用户B: 真的假的，我最高才 A",
             "示例用户A: 你试试把连招少放两段…"]}
```

| 字段 | 说明 |
|---|---|
| `event_id` | 事件编号 |
| `peer` | 群标识（来自 `config.json` 的 `group_id`） |
| `ts_first` / `ts_last` | 事件首尾消息时间戳 |
| `participants` | 参与者的显示名列表 |
| `n_units` | 事件包含的 unit 数（unit = 同一人的连发合并后的语义块） |
| `n_msgs` | 事件包含的原始消息条数 |
| `chars` | 事件总字符数 |
| `messages` | **纯文本消息列表**，格式 `显示名: 正文`，媒体已转成 `[图片：…]` |

> 中间产物（`units_at.jsonl`、`s1_events_*.jsonl`、`media_context_ctx.jsonl` 等）
> 都写在同一群目录下，便于逐阶段排查，格式见各脚本 docstring。

---

## 5. 可直接运行的完整示例（含多媒体）

仓库自带一份**带多媒体的编造数据**：

```
examples/
├── sample_input.jsonl          20 条纯文本消息（最小闭环用）
├── sample_input_media.jsonl    12 条含媒体：2 张图 + 1 张表情 + 1 个视频 + 1 个文件
└── media/
    ├── demo_screenshot.png     合成的"游戏结算界面"
    ├── demo_meme.png           合成的"表情包"
    └── demo_photo.jpg          合成的"实物照片"
```

三张图都是**程序生成的占位图**（纯色块 + 英文/数字），不含任何真实照片或个人信息，
用来演示多媒体通路。

### 跑起来

```bash
# 1) 复制配置，指向媒体示例
cp config.example.json config.json
#    编辑 config.json，把这两行改成：
#      "input_messages": "examples/sample_input_media.jsonl"
#      "media_root":     "examples/media"

# 2) 不接模型先看流程是否通（媒体会被跳过）
python pipeline.py --status
python pipeline.py --mock

# 3) 接了视觉模型后，跑真实识别
python pipeline.py --check-llm
python pipeline.py
```

跑完看 `data/groups/<群标签>/segments_ctx.jsonl`，带图的消息应该长这样：
`示例用户A: … [图片：合成的游戏结算界面截图，显示 RATING S…]`

### 本仓库用这份样例实际跑出来的结果（`--mock` 模式）

`--mock` 用固定假回复替代模型，用来验证**流程连通性**而不消耗算力。
它会走完整 13 步，媒体占位符一样会被正确替换：

```
【V0001】12 条 / 326 字
   示例用户A: 刚打完一把，这把评分居然给了 S [图片：示例图片，画面为聊天窗口截图与一张表格，图上有示例文字和数字。]
   示例用户B: 真的假的，我最高才 A
   示例用户A: 你试试把连招少放两段，评分看的是连击质量不是数量
   示例用户B: 懂了，晚上回去试试 [表情：示例表情，表示赞同]
   示例用户C: 你们两个又在卷这个
   示例用户A: 卷不动了，一天就玩两把
   示例用户D: 说到评分，我把结算界面截下来了 [图片：示例图片，画面为聊天窗口截图与一张表格，图上有示例文字和数字。]
   示例用户C: 这界面不错啊，什么设备
   示例用户D: 老机器，开了最低画质
   示例用户B: 分享个录像，最后那段连招 [视频]
   示例用户D: 收了，回头参考
   示例用户E: 传了个按键配置表 [文件]
```

注意两点：

1. **`[视频]` / `[文件]` 保持裸标签**——只有图片和表情走视觉识别，其他类型不做处理
2. **两轮描述不同**：0.5 写出的是"画面为聊天界面与表格"，2.1 重述为"聊天窗口截图与一张表格"。
   接真实模型时差异会更明显，因为 2.1 能看到这段对话在聊什么。

---

## 6. 常见问题

**Q：媒体文件丢了怎么办？**
`media[].path` 指向的文件不存在时，该条媒体按"丢失"处理并记录到 `dropped.jsonl`，
**不会中断流程**。0 字节文件同样视为丢失。

**Q：一条消息能带多个媒体吗？**
可以。`media` 是数组，占位符按数组顺序补在正文末尾；阶段的 2.1 会为每张图单独做一次上下文重述。

**Q：`text` 里能不能直接写 `[图片]`？**
不建议。正文里的 `[图片]` / `[表情]` 裸标签会在导入时被清理掉（实测真实数据里这类脏数据很常见），
媒体请走 `media` 字段。

**Q：md5 必须给吗？**
不必。不给会自动生成稳定 id。但如果你有原始文件的 md5，建议填上——它能让同一张图在不同事件里
被识别为同一份文件，便于后续统计与去重。

**Q：支持微信群、Discord、Telegram 吗？**
只要你能把消息导出成上面的 `messages.jsonl`，流水线不关心来源。字段语义是通用的。

---

## 5. 兼容输入：ChatLab 标准格式

[ChatLab](https://github.com/ChatLab/ChatLab) 是一个本地优先的聊天记录分析工具，
支持 QQ / WhatsApp / LINE / Discord / Telegram / iMessage / Google Chat，
并定义了跨平台交换格式 `chatlab` **v0.0.1 / v0.0.2**
（规范：<https://docs.chatlab.fun/standard/chatlab-format>）。

如果数据已经是这个格式，不需要自己写转换脚本：

```bash
python pipeline.py --input-format chatlab     # 显式指定
python pipeline.py                            # 默认 auto：按文件头自动识别
```

支持两种文件形态：

| 形态 | 识别特征 | 说明 |
|---|---|---|
| JSONL | 首行 `{"_type":"header", ...}` | 大文件推荐，流式读取 |
| JSON | 顶层含 `"chatlab": {...}` | 小文件 |

### 5.1 字段映射

| ChatLab 字段 | 本项目字段 | 说明 |
|---|---|---|
| `platformMessageId` | `msg_id` | 缺失时合成 `cl000123` |
| `sender` | `sender_uid` | |
| `groupNickname` / `accountName` | `sender_name` | 优先群昵称 |
| `timestamp` | `ts` | 秒级时间戳 |
| `replyToMessageId` | `reply_to_msg_id` | 指向对方的 `platformMessageId` |
| `type` + `content` | `text` / 媒体标签 | 见下表 |
| `meta.name` / `meta.groupId` | 提示填入 `group_name` / `group_id` | 不自动覆盖配置 |

### 5.2 消息类型映射

| `type` | 含义 | 本项目处理 |
|---|---|---|
| 0 | TEXT | 正文 |
| 1 / 5 | IMAGE / EMOJI | **`[图片]` / `[表情]` 文本标签**（无文件路径） |
| 2 / 3 / 4 | VOICE / VIDEO / FILE | `[语音]` / `[视频]` / `[文件]` 文本标签 |
| 7 / 8 / 20–24 / 26 / 27 | 链接 / 位置 / 红包 / 转发 / 名片等 | 转成对应 `[...]` 标签 |
| 25 | REPLY | 正文 + `replyToMessageId` 关系 |
| 80 / 81 / 99 | SYSTEM / RECALL / OTHER | 默认丢弃；`--keep-system` 保留 |

### 5.3 能力边界（重要）

ChatLab 标准格式**只承载文本**：图片只有一个 `type=1` 和字符串 `content`
（如 `"[Image]"`），没有 md5、本地路径或 URL 约定；也**没有 @ 提及字段**。

因此从该格式导入时：

- 阶段 **0.5（媒体转文本）与 2.1（图片上下文重述）不会做视觉识别**，
  因为拿不到文件。图片/表情只保留为 `[图片]` / `[表情]` 标签。
- 阶段 **0.7（@ 语义初审）没有 @ 关系可用**，只会处理 reply 关系。
  这意味着「reply 边里约 48% 是假关联」那套纠错在这条路径上**只覆盖一半**，
  切分质量会低于原生输入——两者不等价。

**要保留视觉能力，请使用第 1 节的原生格式**（`media[{md5,kind,path}]`）——
多媒体识别是本项目的差异化功能。

样例：[`examples/sample_input_chatlab.jsonl`](../examples/sample_input_chatlab.jsonl)。

### 5.4 反向导出：把事件交给 ChatLab 浏览

```bash
python pipeline.py --export-chatlab                        # split：一事件一文件
python pipeline.py --export-chatlab --chatlab-mode single  # single：整群一个文件
```

| 模式 | 产物 | 在 ChatLab 里看到什么 |
|---|---|---|
| `split`（默认） | `chatlab/<event_id>.jsonl` × N + `index.json` | 多个会话，每个会话 = 一个我们切好的事件 |
| `single` | `chatlab/<群标签>.jsonl` | 一个会话，事件之间用系统消息 `[事件 V0001]` 分隔 |

字段生成规则：

| ChatLab 字段 | 来源 |
|---|---|
| `meta.name` | split：`群名 · 事件ID`；single：`群名` |
| `meta.groupId` | `config.group_id` |
| `sender` / `accountName` | 把 `"显示名: 正文"` 拆开；`sender` 用显示名的 md5 派生稳定 id |
| `timestamp` | **真实值**：join `s1_events_gated.jsonl` + `units_at.jsonl`，取每个 unit 的 `ts_first` |
| `type` | 统一 `0`（TEXT）；single 模式的分隔行用 `80`（SYSTEM） |
| `content` | 正文（图片已是 `[图片：…]` 文字） |

时间戳粒度是 **unit**，不是原始单条消息：0.6 会把同一人的连发合并成一个 unit，
最终文本本来就是按 unit 生成的，所以导出也只能到这个粒度。

旁表缺失或长度对不上时，会自动退回 `ts_first + 序号` 近似并**告警**——
近似时间戳会破坏 ChatLab 的时间轴/热力图，不建议在这种情况下做时间维度分析。

发送者用 `units_at.jsonl` 里的 **`sender_qq`（真实账号 id）** 作为 `platformId`，
因此**重名用户不会合并**；导出时会打印重名组数。

### 5.5 时间序与 ChatLab 的时间轴

两条需要知道的事实：

1. **事件可能跨时段。** 1.2 的「非连续合并」按 reply/@ 链接把不同时间的 unit 并进
   同一事件（实测有事件跨度达 14 天）。所以**相邻事件的时间范围可以重叠**、
   事件内部也可能不是严格时间序——这是数据性质，不是导出错误。
2. **默认保持对话顺序**（0.6→1.2 的语义顺序），而 ChatLab 的时间轴/热力图
   假设消息按时间排好。

需要严格时间序时加 `--sort-by-time`：

```bash
python pipeline.py --export-chatlab --chatlab-sort
```

它会在**每个输出文件内**按 `timestamp` 排序；`single` 模式下会同时省略事件分隔行
（因为全局排序后分隔行已经没有意义），并打印提示。

导出后可用 ChatLab 官方 CLI 校验（需要 Node.js）：

```bash
npx chatlab-cli validate "<某个导出文件>.jsonl" --json
```
