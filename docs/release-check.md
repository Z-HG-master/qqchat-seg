# 发布前验收报告（release-check）

- **验收对象**：`qqchat-seg` 仓库（32 个仓库文件 + 本报告 = 33 个文件）
- **验收时间**：2026-10-20（第 1 轮验收）；**复验（第 2 轮）**：2026-10-20，P1/P2 修复后重测
- **验收方式**：独立第三方验收（只做检查、不修改任何源码/文档）。静态项（A/B/C/F/G）直接扫描仓库；
  运行项（D/B 的运行验证）在**临时目录复制仓库**后执行，不在仓库原目录里跑，避免污染。
- **执行环境**：Windows + Python 3.11.15（干净 venv）；中文 Windows 默认 locale = `cp936`（GBK）。
- **规格依据**：交接规格 §6/§7/§8/§9，任务板 task-2 检查清单。
- **复验说明**：首轮发现的 P1（`requirements.txt` 编码缺陷，D 组阻断）与 P2（`docs/pitfalls.md` 陈旧）
  已修复；本轮重新执行 D 组全流程 + A/F 组复扫，结论已更新为**可以发布**（详见 §D-复测、§A8、§F4、§11）。

> ## ⚠️ 脱敏声明（先读）
> 本报告会进入仓库，因此**报告本身也不能包含任何真实数据字面量**——否则 A 组 grep 复查时会出现假阳性，
> 等于验收报告自己制造了一次泄露。为此本报告统一使用下列占位符，实际执行命令时仍使用真实字面量：
>
> | 占位符 | 指代 |
> |---|---|
> | `<个人绝对路径前缀>` | 作者本地工作区根目录的盘符+目录名（A1 扫描目标） |
> | `<真实群号>` / `<真实群名>` | 源项目里出现的真实 QQ 群标识 |
> | `<真实昵称/媒体路径>` | 从源数据提取的 40 个高频昵称与媒体绝对路径（A3 黑名单） |
> | `<QQ 客户端数据目录名>` | A1 中三个 QQ 客户端数据目录关键词 |
> | `<仓库根目录>` / `<临时 clone>` | 本机上的本仓库路径 / 复制出来的临时副本路径 |
>
> 报告写入后已重跑 A 组全部扫描（见 §A8），仓库内命中数仍为 **0**。

---

## 0. 结论总表

| 组 | 检查内容 | 结论 |
|---|---|---|
| **A** | 零数据泄露（路径 / 群号群名 / 昵称 / 数据文件 / .gitignore / 样例 / 缓存） | ✅ **通过** |
| **B** | 模型中立（无 qwen 硬编码 / 无厂商分支 / 视觉开关 / 假模型名清晰报错） | ✅ **通过** |
| **C** | 完全不含数据获取/解密代码（s0_import 声明 / 无 extract 脚本 / README 不包含什么） | ✅ **通过** |
| **D** | 干净环境可运行（`pip install -r requirements.txt` → `--status` → mock 最小闭环） | ✅ **通过（修复后复测）** |
| **E** | README 完整性（价值主张 / 实测数据 / 负面结论 / 成本 / 伦理 / 快速开始 / 已知缺口） | ✅ **通过** |
| **F** | 提示词一致性（PROMPTS.md 与源逐字 diff + 与代码内联提示词一致） | ✅ **通过** |
| **G** | 关键参数一致性（外延 5 / 合并 20·2000 / 闸门 30·15 / effort none / temp 0.1） | ✅ **通过** |

**发布结论：✅ 可以发布。** 第 1 轮发现的 2 个问题已全部修复并复测通过：
P1 = `requirements.txt` 编码缺陷（阻断项，已改纯 ASCII，复测安装/运行全部通过）；
P2 = `docs/pitfalls.md` 提示词版本差异说明陈旧（已改写，与当前实现一致）。
复测结果详见 §D-复测、§A8、§F4、§11。

---

## A. 零数据泄露（最高优先级）

在仓库根目录执行；A1–A3 的 grep 均为**全仓库递归**（含 `.md` 文档、`.gitignore`、配置）。

### A1 个人绝对路径 / QQ 客户端数据目录

| # | 检查项（字面量已脱敏） | 命令 | 结果 |
|---|---|---|---|
| A1-1 | 个人绝对路径 | `grep -rn "F:<个人绝对路径前缀>" .` | **0 命中**（exit 1） |
| A1-2 | 宽泛残留（工作区目录名单独出现） | `grep -rn "<工作区目录关键字>" .` | **0 命中**（exit 1） |
| A1-3 | 源项目名 | `grep -rni "<源项目目录名>" .` | **0 命中**（exit 1） |
| A1-4 | 数据目录关键词 1 | `grep -rn "<QQ 客户端数据目录名#1>" .` | **0 命中**（exit 1） |
| A1-5 | 数据目录关键词 2 | `grep -rn "<QQ 客户端数据目录名#2>" .` | **0 命中**（exit 1） |
| A1-6 | 数据目录关键词 3 | `grep -rn "<QQ 客户端数据目录名#3>" .` | **0 命中**（exit 1） |
| A1-7 | 任意 Windows 盘符绝对路径（盘符+反斜杠开头） | `grep -rn "<盘符>:<反斜杠>" .` | **0 命中**（exit 1） |
| A1-8 | 用户目录路径（盘符+用户目录） | `grep -rn "<盘符>:<反斜杠>Users" .` | **0 命中**（exit 1） |

**结论：通过。** 源码、文档（README/docs/bench/README）、配置模板中均无任何本机绝对路径。

### A2 真实群号 / 群名

| # | 命令 | 结果 |
|---|---|---|
| A2-1 | `grep -rn "<真实群号>" .` | **0 命中**（exit 1） |
| A2-2 | `grep -rn "<真实群名>" .` | **0 命中**（exit 1） |

**附加（超出任务要求的强化）**：从源项目（`groups.json` + 源 `groups/` 目录名）反推出 **4 个真实群标识**
（2 个真实群号 + 真实群名 + 真实群目录名，其中第 2 个群号为复验时补充纳入的黑名单项），
逐个扫描仓库全部文件（33 个，含本报告）：**命中 0**。

### A3 真实昵称 / 媒体路径

| # | 命令 | 结果 |
|---|---|---|
| A3-1 | `grep -rn "<5 个已知真实昵称>" .` | **0 命中**（exit 1） |
| A3-2 | 从源 `name_table.jsonl` + `messages.jsonl` 提取 **40 个高频昵称/媒体绝对路径**做黑名单扫描 | **0 命中** |
| A3-3 | 从源 `messages.jsonl` 取 **5,097 条正文（≥10 字）**，逐条检查是否原样出现在仓库任意文件中 | **0 条命中** |

**结论：通过。** 真实昵称、真实消息正文、真实媒体路径均无残留。

### A4 数据文件 / 缓存

| # | 命令 | 输出 | 结论 |
|---|---|---|---|
| A4-1 | `find . -type f \( -name "*.db" -o -name "*.sqlite" -o -name "*.jsonl" -o -name "*.parquet" -o -name "*.csv" -o -name "*.xlsx" -o -name "*.jpg" -o -name "*.jpeg" -o -name "*.png" -o -name "*.webp" -o -name "*.gif" -o -name "*.bmp" -o -name "*.mp4" -o -name "*.zip" -o -name "*.gz" \)` | 见下方 A4-1 修订说明 | ✅ |
| A4-2 | `find . \( -name "__pycache__" -o -name "*.py[co]" -o -name ".git" -o -name ".pytest_cache" \)` | 空 | ✅ |
| A4-3 | 隐藏条目 `find . -name ".*" -maxdepth 2` | 仅 `./.gitignore` | ✅ |

#### A4-1 修订说明（第 3 轮：新增多媒体样例后）

第 1 轮验收时 A4-1 的允许清单只有 `./examples/sample_input.jsonl`。此后仓库**新增了多媒体示例**
（多媒体识别是本项目的核心亮点，只给纯文本样例等于没有展示该能力），允许清单相应放宽为：

| 允许的文件 | 说明 | 安全性依据 |
|---|---|---|
| `examples/sample_input.jsonl` | 20 条纯文本编造消息 | 示例用户A/B、群号 123456789 |
| `examples/sample_input_media.jsonl` | 12 条含媒体：2 图 + 1 表情 + 1 视频 + 1 文件 | 同样为编造内容 |
| `examples/media/demo_screenshot.png` | 合成的"游戏结算界面"（色块 + 英文数字） | **PIL 程序生成**；EXIF=0、无 ICC/无任何元数据 |
| `examples/media/demo_meme.png` | 合成的表情包 | 同上 |
| `examples/media/demo_photo.jpg` | 合成的"实物照片" | 同上（仅 JFIF 头，无 EXIF） |

**复验命令与结果**（第 3 轮）：

```bash
$ find . -type f \( -name "*.jsonl" -o -name "*.png" -o -name "*.jpg" \) | sort
./examples/media/demo_meme.png
./examples/media/demo_photo.jpg
./examples/media/demo_screenshot.png
./examples/sample_input.jsonl
./examples/sample_input_media.jsonl
# → 5 个，全部在允许清单内；无 .db/.sqlite/.parquet/.csv

$ python -c "from PIL import Image; ..."   # 检查三张图的元数据
demo_screenshot.png  (720,420) EXIF=0 info=[]
demo_meme.png        (420,420) EXIF=0 info=[]
demo_photo.jpg       (640,400) EXIF=0 info=['jfif','jfif_version','jfif_unit','jfif_density']
# → 无 EXIF、无相机/软件/作者等可追溯信息，仅 JPEG 必备的 JFIF 头

$ 八类敏感串复扫（个人路径/两个真实群号/群名/客户端目录/项目名）
全部 0 命中
```

**结论：通过。** 新增文件均为程序合成的展示材料，不含任何真实照片、聊天内容或可追溯元数据；
数据文件仍无一例来自真实数据。

**注意**：这几张图是**二进制文件**，`grep` 无法像文本那样扫描，所以安全性靠两点保证：
① 生成脚本可复现（纯 PIL 绘图，无外部素材）；② EXIF/元数据实测为空。


### A5 `.gitignore` 覆盖

`cat .gitignore` 关键条目（行号为实际行号）：

| 必须项 | 行号 | 结果 |
|---|---|---|
| `data/` | 5 | ✅ |
| `logs/` | 6 | ✅ |
| `config.json` | 33 | ✅ |
| `*.db`（含 `*.db-wal` / `*.db-shm`） | 9–11 | ✅ |
| `*.jsonl` | 16 | ✅ |
| 样例例外 `!examples/sample_input.jsonl` | 18 | ✅ |
| 媒体/表格/parquet/csv、`bench/data/`、`bench/results/`、`__pycache__/`、`.venv/` 等 | 7–8, 14–15, 20–30, 38–46 | ✅ |

**用 `git` 实测生效**（在临时 clone 里 `git init && git add -A`，此时 clone 已含运行产物 `config.json` 与 `data/`）：

```
git status --porcelain  → 只有 32 个仓库文件被加入，无 config.json / data/ / logs
git check-ignore -v config.json                                       → IGNORED (.gitignore:33)
git check-ignore -v data/groups/123456789_示例群/segments_ctx.jsonl   → IGNORED (.gitignore:5 data/)
git check-ignore -v logs/                                             → IGNORED (.gitignore:6 logs/)
git ls-files 中 .jsonl 只有 examples/sample_input.jsonl；tracked files = 32
```

**结论：通过。**

### A6 编造样例内容

`examples/sample_input.jsonl`：20 行、20 条合法 JSON（逐行 `json.loads` 通过）。

- 昵称集合 = `示例用户A/B/C/D`（全部编造，与源数据 5,097 条正文/40 个昵称无交集）；
- 无 9–11 位真实群号（样例行本身不含群号字段；`123456789` 只出现在 `config.example.json` 与 README 示例中，为规格指定的编造值）；
- 媒体路径为相对路径 `media/ab12cd34....jpg`，与 README §7 的约定一致；**无任何 Windows / 绝对路径**；
- 无真实文件名、真实 QQ 号、真实对话内容。

**结论：通过。**

### A7 文档专项

A1–A3 是全仓库扫描（已覆盖全部 `.md`）。额外确认：

- `grep -rn "F:\\\\\\\\" --include=*.md .` → **0 命中**；
- 文档中出现的数字仅为规格允许的汇总统计（如 6,592 条消息 / 815 张图等）与编造时间戳，无个人身份信息；
- README/docs 中的 `qwen/qwen3.5-9b`、`qwen/qwen3.8-27b` 属于规格要求的“已验证默认配置”，不是泄露。

**结论：通过。**

### A8 报告自身复查（写完本报告后重跑）

**第 1 轮（报告写入后）**：重新执行 A1–A3 全部扫描（真实字面量），仓库内命中数 **0**；
文件总数 33（32 个原文件 + 本报告）。即：验收报告没有引入新的泄露。

**第 2 轮（P1/P2 修复后复扫，2026-10-20）**：逐条重跑 A1–A7（含真实字面量）→ 全部 **0 命中**；
并用源数据重新派生黑名单做强化扫描 → 也全部 0（详见下表）：

| 复扫项 | 方法 | 结果 |
|---|---|---|
| A1 个人路径 / QQ 客户端目录关键词 | 全仓库 grep（7 组模式） | 0 命中 |
| A2 真实群标识（4 个：2 群号 + 群名 + 群目录名） | 全仓库 grep | 0 命中 |
| A3 真实昵称 / 媒体路径（从源 `name_table.jsonl` + `messages.jsonl` 取 40 个长度≥2 的高频项） | 全仓库 grep（掩码比对，不打印真实值） | 0 命中 |
| A3++ 源消息正文（5,097 条，≥10 字） | 逐条查是否原样出现在仓库任意文件 | 0 条命中 |
| A4 数据文件 / `__pycache__` / `.git` | `find` | 仅 `examples/sample_input.jsonl`；无缓存 |
| A6 样例 | JSON 解析 | 20/20 合法，无绝对路径 |
| 文件数 | `find . -type f \| wc -l` | 33（32 仓库文件 + 本报告） |

**修复后仓库变更范围（供追溯）**：按修改时间检查，第 1 轮报告生成后只有 2 个文件被修改：
`requirements.txt`（P1 修复，0:51:20）与 `docs/pitfalls.md`（P2 修复，0:51:29）；
`prompts/PROMPTS.md` **未被改动**（SHA-256 与第 1 轮相同、mtime 未变），其余 30 个文件亦未改动。

---

## B. 模型中立（不要绑定 Qwen）

### B1 `*.py` 中 qwen 字样

| # | 命令 | 结果 |
|---|---|---|
| B1-1 | `grep -rin "qwen" --include=*.py .` | **0 命中**（exit 1）✅ |
| B1-2 | `grep -rin "qwen" .`（含 md/json） | 仅 `config.example.json:11-12`（默认值）、`README.md:145,172`（可替换声明）✅ |
| B1-3 | `grep -rin "通义\|tongyi" --include=*.py .` | 0 命中 ✅ |

`config.py` 的 `DEFAULTS` **故意不含任何模型名**（`model_small` / `model_big` 默认空串，`config.py:26-27`），
缺模型时报可读配置错误（B9 实测）。

### B2 是否按模型名/厂商分支

| # | 命令 | 结果 |
|---|---|---|
| B2-1 | `grep -rn "if .*model.*==\|if .*model.*in " --include=*.py .` | 仅 `core/llm.py:178` `if model not in ids:` |
| B2-2 | `grep -rnE "model[^a-z_].*(==\| in \|startswith\|endswith\|find\()" --include=*.py .` | 同上一条 |
| B2-3 | `grep -rn "startswith(\|endswith(" --include=*.py .` | 3 处：解析占位符/判定文本，与模型名无关 |

`core/llm.py:178` 是**模型存在性校验**（`--check-llm` 用 `/models` 列表核对配置里的模型是否存在），
不是按厂商名分支；它比较的是“配置里写的名字”与“端点声明的名字”，任何模型名一视同仁。

**结论：通过。**

### B3 模型名只从 config 读取

`grep -rn "model_small\|model_big" --include=*.py .` 共 27 处，全部为：
`config.py` 的 DEFAULTS/属性、`core/llm.py` 的 `model_for(role)` 与报错文案、`pipeline.py` 的展示、
各 stage 的 `--model` 覆盖参数（`cfg.raw['model_small'] = args.model`）。**没有任何内联模型名字面量。**
`config.py:35` `ROLE_KEYS = {'small': 'model_small', 'big': 'model_big'}` → 角色→配置键映射。

### B4 视觉能力开关（而非模型名）

| 使用位置 | 代码 |
|---|---|
| 开关定义 | `config.py:28` 默认值 `True`；`config.py:185-187` `model_supports_vision` 属性 |
| 调用层 | `core/llm.py:129` `if not self.cfg.model_supports_vision:` → 抛可读错误（视觉步骤不可用却调用时） |
| 0.5 媒体转文本 | `s0_media.py:127` `vision = cfg.model_supports_vision`，135-136 关闭时跳过并打印说明 |
| 2.1 图片重述 | `s2_media_context.py:96-103` 关闭时直接产出空占位并跳过 |
| 编排层 | `pipeline.py:190` 展示、`pipeline.py:220-221` 提示 |
| 配置模板 | `config.example.json:13` `"model_supports_vision": true` |

**实测**（截断 `model_supports_vision=false` 后跑 0.5）：
```
model_supports_vision=false：跳过视觉标注，媒体占位将按「不可用」处理。
媒体去重 2 个 | 已有描述 0 个 | 待处理 2 个
0.5 标注完成：成功 0 | 失败/跳过 0 | 用时 0.0 分钟
```
→ 视觉能力由开关控制，**不靠模型名猜**。**结论：通过。**

### B5 假模型名行为（本地桩端点，未调用任何真实模型）

验证方法：起一个只提供 `real-model-7b` 的本地 OpenAI 兼容桩服务（`GET /v1/models` + 固定回复），
把 config 指向它，模型名填 `foo/bar-7b` / `foo/baz-3b`。

| # | 命令 | 实际输出摘要 | 结论 |
|---|---|---|---|
| B2 | `python pipeline.py --status`（假模型名） | 正常打印 12 步与产物状态，**exit 0，无崩溃** | ✅ |
| B3 | `python pipeline.py --check-llm` | `✗ 推理服务没有提供该模型：foo/bar-7b（small 角色模型）` / `当前端点可用：real-model-7b` / `→ 请…修改 config.json 里的 model_small / model_big。`；**exit 1，无 traceback** | ✅ 清晰报错 |
| B4 | `python pipeline.py --group 123456789`（无 mock） | 启动前拦住：`模型不可用，先解决问题再跑：` + 两条清晰原因；**exit 2，无 traceback** | ✅ |
| B6 | 端点不可达（空端口）`--check-llm` | `✗ 无法连接推理服务：…` `原因：<urlopen error [WinError 10061] …>` `→ 请确认推理服务已启动…查 base_url`；**exit 1，无 traceback** | ✅ 清晰报错 |
| B7 | `python pipeline.py --list-prompts` | exit 0，输出 197 行（即 PROMPTS.md） | ✅ README 承诺命令可用 |
| B8 | 视觉开关关闭后跑 0.5 | 自动跳过视觉标注（见 B4 实测） | ✅ |
| B9 | config 里 `model_small` 为空 → `--check-llm` | `✗ config.json 未配置 model_small（small 角色模型）。任何 OpenAI 兼容模型都可以，把它填进 config.json 后重试；` exit 1 | ✅ 可读错误 |

说明：`--status` 本身只报产物状态、不连接推理服务，因此“假模型名”在 `--status` 下表现为
「正常运行、不崩溃」；真正的模型名校验入口是 `--check-llm` 与真实运行前的预检（B3/B4），二者均给出清晰报错。
这与验收标准“不存在的模型名不会导致崩溃（应给出清晰报错）”一致，**不判为不符合**。

### B10 README 模型可替换声明

`README.md:170-175`：
> 本项目在 `qwen/qwen3.5-9b`（视觉）与 `qwen/qwen3.8-27b` 上验证。
> 由于只使用 OpenAI 兼容接口，**任何具备同等能力与视觉支持的本地模型都可以替换**，
> 只需修改 `config.json` 中的 `model_small` / `model_big` 与 `model_supports_vision`。
> 未在其他模型上做过基准测试。

并补充工程保证（`README.md:177-183`）：模型名只在配置里、无厂商分支、视觉用开关、连接/模型不存在/鉴权失败给出可读报错。

**结论：通过。**

---

## C. 完全不含数据获取/解密代码（硬性要求）

### C1 源码级关键词禁令

| # | 命令 | 结果 |
|---|---|---|
| C1-1 | `grep -rn "sqlite3\|pysqlcipher\|PRAGMA key\|nt_msg\|decrypt\|cipher" --include=*.py .` | **0 命中**（exit 1）✅ |
| C1-2 | `grep -rni "sqlite\|sqlcipher\|PRAGMA\|Msg\.db\|protobuf\|nt_msg" --include=*.py .` | **0 命中** ✅ |
| C1-3 | `grep -rn "\.db" --include=*.py .`（规格 §8.3 单列项） | **0 命中** ✅ |
| C1-4 | `grep -rni "decrypt\|解密\|cipher\|密钥" .`（全仓库） | 仅出现在**说明文字**：`stages/s0_import.py:3,16`（“不做任何数据库读取/解密”）、`README.md:94,101,272`、`prompts/PROMPTS.md:180`、`docs/pitfalls.md:4`、`README.md:13` ✅ 均是否定式声明 |
| C1-5 | `grep -rni "数据库读取\|读数据库\|数据库文件\|db_path" --include=*.py .` | 仅 `s0_import.py:3,16` 的否定式声明 ✅ |

### C2 脚本命名

```
$ find . \( -name "*extract*" -o -name "*decrypt*" \)
（无输出，exit 0）
```
`find . -type f -name "*.py" | sort` = 21 个 .py：pipeline/config/core×3/stages×13/bench×3，**无任何 extract/decrypt 命名**。
`s0_extract_stream.py` 已按要求更名为 `stages/s0_import.py`。**结论：通过。**

### C3 导入脚本存在且干净

```
$ ls -l stages/s0_import.py
-rwxr-xr-x … 10184 … stages/s0_import.py
$ head -5 stages/s0_import.py
"""阶段 0 · 步骤 0.1：导入用户已导出的消息流（**不做任何数据获取/数据库读取/解密**）。
```
`stages/s0_import.py:3` 标题声明、`:16-17` 正文声明：
> 本脚本**不含任何数据获取、数据库读取、解密或 QQ 协议解析逻辑**：数据由使用者自行合法导出，
> 导出格式只要符合上面的 JSONL 约定即可。

代码实际行为：只读用户提供的 `messages.jsonl`（`utf-8-sig`），规范化字段 → 写 JSONL；
无任何数据库连接、无协议解析、无网络请求。**结论：通过。**

### C4 README 独立「不包含什么」小节

```
$ grep -n "不包含什么" README.md
23:4. [不包含什么](#4-不包含什么)
92:## 4. 不包含什么
```
小节正文（`README.md:92-103`）明确写出：
> 本项目**不包含** QQ 客户端数据库的读取、解密或协议解析功能。
> …仓库里没有任何 `sqlite3` / `pysqlcipher` / `PRAGMA key` / `decrypt` / `nt_msg` 相关代码；
> 也不解析 QQ 的 protobuf 消息体…
> 数据获取方式由你自行负责并承担合规责任（见 §10）。

**结论：通过。**

### C5 网络访问面（只允许 LLM 端点）

`grep -rn "requests\.\|urllib\|http" --include=*.py .`：仅 `core/llm.py` 用标准库 `urllib` 访问
`{base_url}/models` 与 `{base_url}/chat/completions`；`config.py:24` 默认 `http://127.0.0.1:1234/v1`。
**没有任何 QQ/微信/协议相关端点，也没有数据库文件读取。结论：通过。**

---

## D. 干净环境运行

**方法**：把仓库复制到临时目录（模拟 `git clone`，初始无 `config.json` / `data/` / `logs/`；当前仓库 33 个文件
= 32 个仓库文件 + 本报告），用 `python -m venv` 建全新虚拟环境，按 README 顺序执行。**不在仓库原目录跑，避免污染。**

### D1–D2 安装依赖（第 1 轮：**不通过** → P1 已修复，见 §D-复测）

| 步骤 | 命令 | 结果 |
|---|---|---|
| D1 | `python -m venv <临时目录>/venv` | ✅ venv 创建成功（Python 3.11.15） |
| D2 | `<venv>/python -m pip install -r requirements.txt` | ❌ **exit 2，直接崩溃** |

原始输出（末尾）：
```
ERROR: Exception:
Traceback (most recent call last):
  ...
  File ".../pip/_internal/utils/encoding.py", line 34, in auto_decode
    return data.decode(
UnicodeDecodeError: 'gbk' codec can't decode byte 0xaa in position 28: illegal multibyte sequence
```

**根因**：`requirements.txt:1-2` 是 UTF-8 中文注释，但文件没有 BOM / PEP 263 coding cookie；
pip 的 `auto_decode` 在两者都不存在时回退到 `locale.getpreferredencoding(False)`。
本机（中文 Windows）该值为 `cp936`，用 GBK 解码 UTF-8 中文注释 → 直接崩溃（解析阶段就失败，还没开始下载包）。
这与 README §5 的“pip install -r requirements.txt”第一步冲突，属于**发布阻断问题**。

**修复建议（已实测有效，见 §11 P1）**：
- 方案 A（最小改动）：在 `requirements.txt` **第 1 行**加 `# -*- coding: utf-8 -*-`（pip 会走 PEP 263 cookie 分支按 UTF-8 解码）；
- 方案 B：把 `requirements.txt` 的注释全部改成 ASCII。

**验证记录**（同一 venv、`--no-index` 离线，避免网络干扰）：
```
原始 requirements.txt     → exit 2, UnicodeDecodeError ('gbk' … position 28)     ← 复现缺陷
加 coding cookie 的版本    → exit 1, "No matching distribution found for Pillow>=9.0" ← 解析通过，仅缺离线包
纯 ASCII 注释的版本        → exit 1, "No matching distribution found for Pillow>=9.0" ← 解析通过，仅缺离线包
```
另单独确认网络与 PyPI 可达（`https://pypi.org/simple/` HTTP 200），因此该失败**不是网络原因**；
本环境到 PyPI 下载单包很慢，故完整在线安装未跑完，但“编码缺陷 + 两种修复”的因果关系已可确定性复现。

### D-复测（P1 修复后，2026-10-20 第 2 轮）

**修复内容**：`requirements.txt` 改为**纯 ASCII**（英文注释，并写明“必须保持纯 ASCII”的原因；
726 字节、非 ASCII 字节 = 0、无 BOM、依赖仍为 `Pillow>=9.0`）。未使用 PEP 263 cookie——
纯 ASCII 在任何 locale 下都安全，不依赖 pip 是否尊重 cookie。

**复测环境**：重新复制仓库到临时目录（模拟 clone，初始无 config/data/logs）→ 新建干净 venv（Python 3.11.15）。

| 步骤 | 命令 | 实际结果 | 结论 |
|---|---|---|---|
| 编码确认 | 字节级读取 `requirements.txt` | 726 字节 / 非 ASCII=0 / BOM=False；ascii、gbk、utf-8 三种解码均 OK；依赖行 `['Pillow>=9.0']` | ✅ |
| D1 | `python -m venv <临时目录>/venv` | ✅ Python 3.11.15 | ✅ |
| D2 | `<venv>/python -m pip install -r requirements.txt` | ✅ **exit 0**（默认 PyPI 源：`Downloading pillow-13.0.0-cp311-cp311-win_amd64.whl (7.3 MB)` → `Successfully installed Pillow-13.0.0`，约 57s） | ✅ |
| D2' | `import PIL` | `PIL import OK, version 13.0.0` | ✅ |
| D3 | `python pipeline.py --status`（无 config.json） | exit 0，正常打印 12 步 + “配置文件不存在…先复制模板”指引 | ✅ |
| D4 | `cp config.example.json config.json` → `--status` | exit 0，正常打印产物状态 | ✅ |
| D5 | `examples/sample_input.jsonl` → `python pipeline.py --mock --group 123456789` | exit 0，**12 步全部 ✓**；`segments_ctx.jsonl` 1 行/1 事件/20 条消息 | ✅ |
| D6 | mock 后 `find . \( -name "__pycache__" -o -name "*.py[co]" \)` | **空**（`sys.dont_write_bytecode=True` 生效） | ✅ |
| D7 | 运行产物位置 | 全部在 `data/` 下（`.gitignore` 已覆盖）；仓库根目录仅新增被忽略的 `config.json` | ✅ |

**复测同时确认**：
- 原崩溃（`UnicodeDecodeError: 'gbk' codec can't decode byte 0xaa in position 28`）**不再出现**；
- pip 输出明确显示 “from -r requirements.txt (line 8)”，说明文件被正确解析到第 8 行依赖行。

> 环境备注（与仓库无关）：本机首次在线安装时 pip 曾卡在索引读取阶段（疑似本机 pip 缓存/网络环境问题，
> 与 requirements 内容无关）。改用 `pip install --no-cache-dir -r requirements.txt` 后正常并成功安装。
> 这属于本机 pip 环境细节，不影响仓库：requirements.txt 现在在任何 locale 下都能被正确解析。

### D3–D6（第 1 轮原始记录，复测已再次通过）

### D3 `python pipeline.py --status`（无 config.json）

```
python pipeline.py --status  → exit 0
=== 流水线步骤 ===
  0.1 [规则]  导入消息流（读用户已导出的 messages.jsonl）
  0.5 [small] 媒体转文本（图片约 50 字 / 表情 ≤15 字）
  ...（共 12 步，全部列出）...
配置文件不存在：<临时 clone>/config.json
  先复制模板：cp config.example.json config.json
  再填写 data_root / group_id / base_url / model_small / model_big。
```
**结论：✅ 可运行、无报错、给出下一步指引。**

### D4 复制 config 后 `--status`

```
cp config.example.json config.json
python pipeline.py --status  → exit 0
=== 产物状态（群 123456789）===
  目录：<临时 clone>/data/groups/123456789_示例群
  （目录还不存在：跑 python pipeline.py --group 123456789 时会自动创建）
```
**结论：✅。**

### D5 mock 最小闭环（用 examples/sample_input.jsonl 跑完 12 步）

准备工作（与 README §13 一致）：
```
mkdir -p "data/groups/123456789_示例群/input"
cp examples/sample_input.jsonl "data/groups/123456789_示例群/input/messages.jsonl"
python pipeline.py --mock --group 123456789
```

结果：**exit 0，12 步全部 ✓（0.0 分钟量级，纯 mock）**：

```
=== 汇总（0.0 分钟）===
  ✓ 0.1 导入消息流 …          ✓ 1.1 事件判别（逐条 + 外延 5 段）
  ✓ 0.5 媒体转文本 …          ✓ 1.2 按确认链接合并事件 …
  ✓ 0.6 连发块合并 …          ✓ 1.3 长事件复核（>10 条）
  ✓ 0.7 @ / reply 语义初审 …  ✓ 1.4 质量闸门 …
  ✓ 0.8 阶段 0 收尾 …         ✓ 1.5 事件级分类与数据集导出
                              ✓ 2.1 图片结合上下文重述
                              ✓ 2.2 回填描述 → 纯文本片段（最终成果）

最终成果：<临时 clone>/data/groups/123456789_示例群/segments_ctx.jsonl
```

产物 24 个（`messages/units/units_at/s1_events_*/segments_ctx.jsonl` 等齐全，每步 EXPECT 校验均通过）。
`segments_ctx.jsonl` 1 行、1 个事件、20 条消息、participants=`示例用户A/B/C/D`，图片/表情占位已替换为
`[图片：…]` / `[表情：…]`（mock 固定回复）。**闭环跑通。✅**

### D6 运行后污染检查

| # | 检查 | 结果 |
|---|---|---|
| D6 | `find . \( -name "__pycache__" -o -name "*.py[co]" \)`（在跑完 mock 的 clone 里） | **空** ✅（`sys.dont_write_bytecode=True` 生效） |
| D7 | `find data -type f` | 全部落在 `data/`（被 `.gitignore` 覆盖）✅ |
| D8 | 仓库原目录（非 clone）复检 | 仍为 **33 个文件**（32 仓库文件 + 本报告），无 `data/`、`logs/`、`config.json`、`__pycache__` ✅ |

**D 组结论：✅ 通过（第 2 轮复测）。** 第 1 轮唯一阻断项 P1（`requirements.txt` 编码缺陷）已修复：
纯 ASCII 后的 `pip install -r requirements.txt` 在干净 venv 中 **exit 0 并成功安装 Pillow**，
`--status`（D3/D4）与 mock 最小闭环（D5，12 步全部 ✓）再次全部通过（见 §D-复测）。

---

## E. README 完整性（对照规格 §6）

| # | 要求 | README 位置 | 实际内容 | 结论 |
|---|---|---|---|---|
| E1 | 价值主张 = **“分解使小模型可用”**，不是“省 API 费用” | `README.md:37-49` | 原文：“本项目把整群任务**分解成 9,709 个「1,100 tokens 的小判断」**，使 9B 级本地模型即可完成事件切分。不是“省 API 费用”，而是**把一个大模型做不好的任务，拆成小模型能做好的任务**” | ✅ |
| E2 | 实测数据：Ubuntu IRC F1 0.870 / 准确率 95.0% | `README.md:225-234` | `9B 单步 P=0.878 R=0.862 F1=0.870 准确率 95.0% 8.4 min`；27B 与 27B 复核行；对照文献 TIAGE ChatGPT 0.362 / CSM 0.427（27B 0.391） | ✅ |
| E3 | **必须包含负面结论**：1.3 复核会降分（0.870→0.833） | `README.md:236-237` | “**必须同时说明：1.3 复核在基础切分较好时会降低分数**（IRC F1 0.870 → 0.833；TIAGE 0.249 → 0.239）——这不是“多级复核提升质量”的流水线…” | ✅ |

> **后续修订（第 5 轮）**：提示词措辞由「QQ 群」改为「社交群聊」并重测，当前 README 数字为 9B F1 **0.862** / 准确率 **94.9%**、复核 **0.810**。本表 E2/E3 记录的是修订前的数字（0.870 / 0.833），差异与原因见验证报告 §2.7。

| E4 | 成本口径改为**定性表述**（第 3 轮修订）：不再列具体金额，改为强调「本地跑 + 数据不外溢 + 无 API 账单」 | `README.md` §10 | 仍满足「成本可解释」要求，且更贴合项目定位（价值在隐私而非省钱）；原文的具体数字已移除 |单群 6,592 条消息 / 815 张图口径 | ✅ |
| E5 | 伦理与法律警告（显著位置） | **顶部** `README.md:10-14` + `README.md:268-276` | 4 条：合法访问权 / 勿上传未脱敏 / 不提供解密工具自行合法导出 / 使用者负全责；顶部即警告框 | ✅ |
| E6 | 独立「不包含什么」小节（数据获取/解密） | `README.md:92-103` | 明确“不包含 QQ 客户端数据库的读取、解密或协议解析功能”+ 具体说明 | ✅（同时满足 C4） |
| E7 | 快速开始（含模型中立说明） | `README.md:107-145` | 4 步：任意 OpenAI 兼容服务 → cp config → 放 messages.jsonl → `--status`/`--check-llm`/`--group`；并注明“第 1 步不限定模型厂商…默认配置写的是我们验证过的 Qwen 模型，仅为可用的默认值”；另 `README.md:170-175` 模型可替换声明 | ✅ |
| E8 | 已知缺口（规格 §9 的 5 条） | `README.md:280-287` | 5 条全部包含（未经人工标注 / 图文归属 / 20·2000 未在 QQ 数据扫描 / VLM 错字 / 未去重），另加第 6 条“人物名未脱敏” | ✅（超出要求） |
| E9 | 五节齐全 + 结构 | 目录 `README.md:18-33` | 价值主张 / 输出样例 / 流水线总览 / 不包含什么 / 快速开始 / 配置与模型中立 / 数据格式 / 实测数据 / 成本口径 / 伦理警告 / 已知缺口 / 目录结构 / 离线自测 / 许可 | ✅ |

补充确认：

- `README.md:87` 明确“小模型 / 大模型”只是**角色名**，具体模型由 `config.json` 决定 → 与 B 组一致；
- `README.md:332-343` 的离线自测命令与 D5 实测一致，可直接复现；
- `LICENSE` 为 MIT（21 行标准文本），`requirements.txt`、`config.example.json` 均存在且格式正确。✅
- **E 组复验说明**：P1/P2 修复未改动 README（mtime 未变），E 组结论继续有效。

---

## F. 提示词一致性

### F1 `prompts/PROMPTS.md` 与源 PROMPTS.md 逐字 diff

| 项 | 结果 |
|---|---|
| 文件 SHA-256 | 两份**完全相同**：`9C91028615158515DBF77849DE80C6C9AFF67DD249232E5E81729A134C752EBC` |
| 大小/行数 | 8,629 bytes / 196 行 |
| fenced 代码块数 | repo = **7**，source = **7**；逐块字符串比较 **7/7 全部相等** |

**结论：✅ 逐字节一致。**

### F2 7 个代码块 vs 代码内联提示词（逐字）

用 AST 从脚本里提取字符串常量，与 PROMPTS.md 的代码块做**逐字**比较（`block.strip('\n') == const.strip('\n')`，
先比首尾空白、再比精确）：

| 提示词 | PROMPTS.md 块 | 仓库代码位置 | 结果 |
|---|---|---|---|
| 0.5 图片标注 | ① | `stages/s0_media.py:36-39`（`PIC_PROMPT`） | ✅ EXACT |
| 0.5 表情标注 | ② | `stages/s0_media.py:40-41`（`EMO_PROMPT`） | ✅ EXACT |
| 0.6 连发块合并 | ③ | `stages/s0_merge_runs.py:34-36`（`SPLIT_PROMPT`，含 `{lines}`） | ✅ EXACT |
| 0.7 @/reply 初审 | ④ | `stages/s0_at_prep.py:38-53`（`REL_PROMPT`，含“相关性判别”） | ✅ EXACT |
| 1.1 事件判别 | ⑤ | `stages/s1_segment_unit.py:42-58`（`PROMPT`）+ `:59`（`EXPLAIN_TAIL`） | ✅ EXACT（按 `--explain` 分支重建后逐字一致，481 字符） |
| 1.3 长事件复核 | ⑥ | `stages/s1_review.py:40-56`（含“逻辑顺畅具有强关联性”，末尾三行 结论/转折/理由） | ✅ EXACT |
| 2.1 图片上下文重述 | ⑦ | `stages/s2_media_context.py:41-55` | ✅ EXACT |

**1.1 的 `--explain` 重建说明**：仓库实现是 `PROMPT.replace('只输出一个字：续 或 新', EXPLAIN_TAIL)`
（`s1_segment_unit.py:128-129`）。重建结果是 **481 字符**，与 PROMPTS.md 块⑤**逐字一致**；
非 explain 分支末行为“只输出一个字：续 或 新”，与 PROMPTS.md 文件的说明文字（第 85 行）一致。

**源脚本 vs 仓库脚本**：7 条提示词的源码级对比全部一致（0.5×2 / 0.6 / 0.7 / 1.3 / 2.1 为 EXACT；
1.1 的基础 `PROMPT` 与条件尾部经结构化重建后，源/仓库两种写法产出的 explain 提示词**逐字相同**，均为 481 字符）。

### F3 关于“源码内联提示词与 PROMPTS.md 原本有版本差异（0.7 / 1.3）”的独立确认

按任务要求单独核查结论：

1. **该差异在源项目里曾经存在**：交接说明/原调查指出源 `PROMPTS.md` 中有 4 条（0.6 / 0.7 / 1.1 / 1.3）是较早版本的压缩稿，
   而 `scripts/` 里内联的是后续迭代版。这属于**源项目文档陈旧缺陷**。
2. **当前状态下差异已不存在**：本次验收时，源 `PROMPTS.md` 已按代码 AST 重新生成；
   实测源文件与仓库文件 SHA-256 相同、7 个代码块 7/7 逐字相等，且与仓库代码内联提示词 7/7 一致（见 F1/F2）。
3. **以哪个为准的判断**：**以仓库代码内联提示词为准**。理由：
   (a) PROMPTS.md 文件头（第 3-4 行）已声明“本文件与代码逐字一致，由 AST 从各脚本中直接提取生成…修改提示词时先改代码，再重新生成本文件”；
   (b) 代码是实际运行时使用的版本，存档必须服从代码；
   (c) 7 条提示词是实验定稿，**不应为对齐文档而改代码**。
4. **遗留文档问题（已修复）**：`docs/pitfalls.md:48-56` 的《关于 prompts/PROMPTS.md 与代码内联提示词的版本差异》一节
   原先仍在描述“存档中 0.7 与 1.3 是较早的压缩版”“本仓库迁移时刻意没有改动任何一条提示词（存档与代码都保持原样）”，
   与当前实现不符；复验前已改写为「PROMPTS.md 由 AST 从代码生成、与代码逐字一致（7/7）、不一致以代码为准；
   0.7/1.3 历史差异已消除」（见 §F4、§11 P2）。

### F4 复验（P2 修复后，第 2 轮）

| 项 | 结果 |
|---|---|
| `prompts/PROMPTS.md` 是否变动 | **未变动**：SHA-256 仍为 `9C9102…52EBC`（与源文件相同）、mtime 未变 → 第 1 轮 F1/F2 结论继续有效 |
| 源/仓库逐字一致（复跑确认） | ✅ SHA-256 相同；fenced 代码块 repo=7 / source=7，逐块 **7/7 相等** |
| 7 块 vs 代码内联（复跑确认） | ✅ 0.5×2 / 0.6 / 0.7 / 1.3 / 2.1 为 EXACT；1.1 `--explain` 重建 481 字符，逐字相等 |
| `docs/pitfalls.md` P2 修复核验 | ✅ 该节已改写，明确“从代码自动生成、逐字一致（7/7）、以代码为准、历史差异已消除”，与 PROMPTS.md 头注释的维护规则一致 |

**F 组结论：✅ 通过（含复验）。**

---

## G. 参数一致性（对照规格 §7）

| # | 参数 | 规格值 | 实际值（文件:行） | 结论 |
|---|---|---|---|---|
| G1 | 事件判别**外延 5 段** | 5 | `pipeline.py:51` 传 `--lookahead 5`；`s1_segment_unit.py:100` `default=5` | ✅ |
| G2 | 判别上下文 **≤4,000 字** | 4000 | `s1_segment_unit.py:92` `--chars default=4000`；事件超限即封口（`:172`），上下文因此有界 | ✅ |
| G3 | 合并上限 **≤20 条 / ≤2000 字** | 20 / 2000 | `pipeline.py:55` 传 `--max-units 20 --max-chars 2000`；`s1_link_merge.py:50-51` 默认同值；`:112-114` 超限**跳过而非截断** | ✅ |
| G4 | 质量闸门 **文字含量 ≥30 / 媒体按 15 字计** | 30 / 15 | `pipeline.py:62` 传 `--min-substance 30 --media-weight 15`；`s1_quality_gate.py:53-54` 默认同值；`:70-78` 计分逻辑 `text + 15*media`，`:84-89` 单条/阈值丢弃 | ✅ |
| G5 | 复核触发 **事件 >10 条** | >10 | `pipeline.py:58` 传 `--min 10`；`s1_review.py:78` `default=10`；`:110` `if n <= args.min` 跳过 | ✅ |
| G6 | 全部 `reasoning_effort: "none"` | none | `config.py:30` 默认 `'none'`；`config.example.json:15`；`core/llm.py:210-212` 所有调用统一取配置值、无任何 stage 覆盖；B5 实测请求体 `"reasoning_effort": "none"` | ✅ |
| G7 | `temperature` 判别 **0.1** / 基准 **0.0** | 0.1 / 0.0 | `config.py:29` `0.1`；`config.example.json:14` `0.1`；`core/llm.py:206` 取配置；bench 三脚本请求显式 `temperature=0.0`（`bench_ubuntu_irc.py:130`、`bench_tiage.py:125`、`bench_alimeeting.py:128`）。B5 实测请求体 `"temperature": 0.1` | ✅ |
| G8 | 图片重述 **≤80 字** | ≤80 | `s2_media_context.py:45,54` 提示词约束（“输出内容不超过80字”/“描述: <不超过 80 字>”），与 PROMPTS.md 块⑦一致 | ✅ |
| G9 | 统一调用参数（补充） | temperature/effort/stream | B5 实测请求体：`temperature 0.1`、`reasoning_effort "none"`、`stream false`、单条 user 消息；与 PROMPTS.md 末尾“统一调用参数”表一致 | ✅ |


> **后续修订（第 4 轮）**：默认配置已**不再发送** `reasoning_effort`——它是部分模型家族（如 Qwen）的私有旋钮，不是本项目的前提。此表记录的是当时（默认传 `none`）的验收行为；现在的默认值见 `config.py`。
---

## H. 附加检查（不在 A–G 要求内，供参考）

| 检查 | 方法 | 结果 |
|---|---|---|
| Python 版本兼容（README 声称 3.9+） | 用 AST `feature_version=(3,9)` 解析全部 21 个 .py | ✅ 无 3.9+ 不兼容语法；粗查无 zip(strict=)/tomllib/pairwise 等 3.10+ API |
| 文件编码 | 逐文件读字节 | ✅ 全部 UTF-8、**无 BOM** |
| 样例 JSON 合法性 | 逐行 `json.loads` | ✅ 20/20 |
| 仓库文件盘点 | `find . -type f` | ✅ 32 个仓库文件 + 本报告 = 33 个；与规格 §4 目标结构一致（pipeline/config/core×3/stages×13/prompts/docs×3/bench×4/examples/根文件×6） |
| 无 `_HANDOFF_SPEC.md` / `_TASK.md` / 交接文档残留 | 文件盘点 | ✅ 交接文档在仓库外，未带入 |
| 源项目目录只读 | 全程只读源目录，未修改 | ✅ 源目录未改动 |
| README 命令可复现 | `pip install -r` / `--status` / `--list-prompts` / mock 全流程实测 | ✅ 全部实测通过（pip 安装见 §D-复测） |

---

## I. 同类问题排查（Windows locale / 文件编码）

P1 的根因是“pip 直接按系统 locale 解码 requirements 文件”。为排除同类坑，复验时把所有涉及**文本读写**的
位置都核对了一遍（含源码行号）：

| # | 排查点 | 方法 | 结论 |
|---|---|---|---|
| I1 | `requirements.txt` | 字节级检查 | ✅ 726 字节，**非 ASCII 字节 = 0**，无 BOM；ascii / gbk / utf-8 三种解码均 OK |
| I2 | `config.example.json` 含中文（306 个非 ASCII 字节） | 检查读取方 | ✅ `config.py:242` 显式 `open(path, encoding='utf-8-sig')`，**不受 locale 影响** |
| I3 | 全部 `open()` 调用 | AST 扫描全部 21 个 `.py`：47 个文本模式 `open()` | ✅ 46 个显式指定 `encoding='utf-8'` 或 `'utf-8-sig'`；唯一“未指定”的是 `core/llm.py:47` `Image.open(path)`（PIL 读图像文件，非文本，属误报）；二进制模式 0 个 |
| I4 | bench 数据加载 | 逐个检查 | ✅ `bench_tiage.py:100` 与 `bench_alimeeting.py:102` 显式 `encoding='utf-8'`（后者另加 `newline=''`）；`bench_ubuntu_irc.py:116` 为 `pd.read_parquet`（二进制，无 locale 依赖） |
| I5 | 子进程输出解码 | 检查 `pipeline.py` | ✅ `subprocess.run(..., text=True, encoding='utf-8', errors='replace')`（`pipeline.py:110-111`），并给子进程设 `PYTHONIOENCODING=utf-8`（`:108`） |
| I6 | 控制台输出 | 检查 `config.py` | ✅ `setup_utf8_stdio()` 对 stdout/stderr `reconfigure(encoding='utf-8')`（`config.py:47-54`），pipeline 启动即调用 |
| I7 | 其他文本 API | 递归 grep 全部 `.py` | ✅ 无 `read_text` / `write_text` / `read_csv` / `read_json` 等隐式编码调用；`urllib.urlopen` / `Image.open` 为网络/图像读取，不涉及文本编码 |
| I8 | 结论 | — | ✅ **除已修复的 `requirements.txt` 外，未发现其他受 Windows locale 影响的文本读取点** |

> 特别说明：`config.example.json` 含中文本身**不是**问题——关键在于读取方显式指定了 `utf-8-sig`。
> 以后新增“会被外部工具（pip、pandas 等）直接读取的文件”时，才需要像 `requirements.txt` 一样保持纯 ASCII。
> 该坑已写进 `requirements.txt` 的注释（第 4-7 行），避免后来者改回非 ASCII 注释。

---

## 11. 问题清单与处理结果（第 1 轮发现 → 第 2 轮已全部修复）

### P1（**已修复 ✅**，第 1 轮为阻断项）`requirements.txt` UTF-8 中文注释导致 pip 崩溃

- **状态**：2026-10-20 已修复（由 lead 直接修改），修复后复测通过（见 §D-复测）。
- **位置（修复前）**：`requirements.txt:1-2`（UTF-8 中文注释）
- **现象（修复前）**：中文 Windows（默认 locale=cp936）上执行 `pip install -r requirements.txt`
  直接失败：`UnicodeDecodeError: 'gbk' codec can't decode byte 0xaa in position 28`（exit 2），
  即 README §5 的第一步就走不通。
- **根因**：文件是 UTF-8，但无 BOM、无 PEP 263 coding cookie；pip 的 `auto_decode`
  回退到 `locale.getpreferredencoding(False)`（本机 cp936）解码 → 崩溃。与网络无关。
- **修复方式**：`requirements.txt` 改为**纯 ASCII**（英文注释 + 保持纯 ASCII 的原因说明；
  726 字节 / 非 ASCII=0 / 无 BOM / 依赖 `Pillow>=9.0`）。未用 PEP 263 cookie——纯 ASCII 天然与 locale 无关。
- **修复后复测**：干净 venv 中 `pip install -r requirements.txt` **exit 0、`Successfully installed Pillow-13.0.0`**；
  原 `UnicodeDecodeError` 不再出现（详见 §D-复测）。
- **归属/处理**：第 1 轮判定退回 task-1；实际由 lead 直接修复。本任务（验收）未修改任何源码。
- **复验方式**：§D-复测已执行（干净 clone 里 `pip install -r requirements.txt` → `--status` → mock 闭环，全部通过）。

### P2（**已修复 ✅**，第 1 轮为文档陈旧项）`docs/pitfalls.md` 的提示词版本差异说明已过期

- **状态**：2026-10-20 已修复（由 lead 直接修改），复验核对通过（见 §F4）。
- **位置（修复前）**：`docs/pitfalls.md:48-56`（《关于 prompts/PROMPTS.md 与代码内联提示词的版本差异》一节）
- **现象（修复前）**：该节仍在说“存档文档中 0.7 与 1.3 两条是较早的压缩版”“本仓库迁移时刻意没有改动任何一条提示词
  （存档与代码都保持原样）”。但 PROMPTS.md 已按代码重新生成，实测源/仓库字节相同、7 块与代码 7/7 逐字一致，
  该描述会误导维护者。
- **修复后内容（已核验）**：已改写为“`prompts/PROMPTS.md` 是从代码自动生成（AST 提取）、与代码逐字一致（7/7）；
  维护规则：改提示词只改代码再重新生成，若不一致以代码为准；历史背景（0.7/1.3 早期压缩版差异）已解决”。
  与 `prompts/PROMPTS.md:3-4` 的维护规则、以及本次 F 组结论完全一致。
- **归属/处理**：第 1 轮建议退回 task-1；实际由 lead 直接修复。不阻断发布。

### P3（说明，不判不通过）

- 验收标准表述“换假模型名跑 `--status` 应清晰报错”，实际 `--status` 只报产物状态、不连推理服务，故假模型名在
  `--status` 下是“正常运行”；模型名校验入口是 `--check-llm` 与真实运行前预检，两者均给出**可读报错、无 traceback**
  （B3/B4/B6/B9 实测）。不构成缺陷，仅记录口径。
- `examples/sample_input.jsonl` 的样例行本身不含群号字段，`123456789` 出现在 `config.example.json` 与 README 示例中；
  样例中媒体路径为相对路径 `media/xxx.jpg`（与 README §7 约定一致），无绝对路径 → 不构成泄露，判通过。

### P4（本报告的自我保护，已处理）

- 本报告写入仓库后，已重跑 A1–A3 真实字面量扫描，命中仍为 0（敏感字面量以占位符表示，见开头脱敏声明）。
- **第 2 轮**：本报告在 P1/P2 修复后做了大幅更新（新增 §D-复测、§F4、§I 等），更新后再次重跑 A1–A7 全仓库扫描
  与源数据黑名单强化扫描，命中数仍为 **0**（见 §A8 第 2 轮记录）。
- 复验命令建议（维护者本地执行，含真实字面量）：
  ```bash
  # A 组（把 <...> 换成本机真实字面量）
  grep -rn "<个人绝对路径前缀>" .
  grep -rn "<真实群号>\|<真实群名>" .
  grep -rn "<真实昵称>" .
  find . -type f \( -name "*.db" -o -name "*.jsonl" -o -name "*.parquet" -o -name "*.csv" -o -name "*.jpg" -o -name "*.png" \)
  cat .gitignore
  # B/C 组
  grep -rin "qwen" --include=*.py .
  grep -rn "sqlite3\|pysqlcipher\|PRAGMA key\|nt_msg\|decrypt\|cipher" --include=*.py .
  find . \( -name "*extract*" -o -name "*decrypt*" \)
  # F 组
  diff prompts/PROMPTS.md <源项目>/PROMPTS.md
  ```

---

## 12. 最终判定

- **A / B / C / E / F / G：通过。**
- **D：通过（第 2 轮复测）**：P1 修复后干净 venv 中 `pip install -r requirements.txt` exit 0 并成功安装 Pillow；
  `--status`、mock 12 步闭环再次全部通过（详见 §D-复测）。
- **P1（阻断项）：已修复 ✅**（`requirements.txt` 纯 ASCII，复测通过）。
- **P2（文档陈旧）：已修复 ✅**（`docs/pitfalls.md` 已改写并与实现一致）。
- **同类问题排查：完成 ✅**（未发现其他受 Windows locale 影响的文本读取点，见 §I）。
- **发布判定：✅ 可以发布。** A–G 七组全部通过；本报告不修改任何源码，仓库内无新增数据文件。
  建议发布前由维护者本地再跑一次 §P4 中的 A 组复验命令（真实字面量），确认本机无残留。

*本报告只做检查与记录，未修改仓库内任何源码/文档/配置；所有运行产物都在临时目录，仓库内无新增数据文件。*
