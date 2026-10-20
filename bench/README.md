# 基准测试脚本

这里是从公开数据集上**复现验证结论**用的脚本，**不包含任何数据集**。
所需数据请自行下载，放到 `bench/data/`；结果写到 `bench/results/`
（两者都已在 `.gitignore` 中忽略）。

依赖：`pandas`（`bench_ubuntu_irc.py` / `bench_irc_merge.py` / `bench_irc_full.py` 读 parquet，
还需要 `pyarrow`）。

## 脚本与数据

| 脚本 | 验证什么 | 需要的数据 |
|---|---|---|
| `bench_ubuntu_irc.py` | 1.1 事件判别（边界 P/R/F1） | `bench/data/ubuntu_irc_dev.parquet` |
| `bench_irc_full.py` | 1.1 + 1.3 复核（完整流程） | 同上 + 上一步产出的预测文件 |
| `bench_irc_merge.py` | 0.7 初审 + 1.2 非连续合并（pairwise F1、规模上限扫描） | 同上 + 预测文件 |
| `bench_tiage.py` | 1.1 在 TIAGE 上的对照 | `bench/data/tiage_test.json` |
| `bench_tiage_full.py` | TIAGE 上的完整流程（含复核） | 同上 |
| `bench_alimeeting.py` | 1.1 在中文会议数据上的对照 | `bench/data/alimeeting_dev.csv` |

数据不是本仓库的一部分，也不随仓库分发：

- **Ubuntu IRC**：Kummerfeld 等 *A Large-Scale Corpus for Conversation Disentanglement*。
  预处理版在 HuggingFace（`timyangyazhou/ubuntu_irc_kummerfeld_ft_20_window_last`），
  下载 dev 分片的 parquet 即可。
- **TIAGE**：HuggingFace `Coldog2333/tiage` 的 `test.json`。
- **Alimeeting4MUG**：阿里中文会议数据集，仓库 `brianxiadong/Qwen2.5-7B-Alimeeting4MUG-Finetune`
  的 `dataset/dev.csv`（TSV，第二列内嵌 JSON）。

## 运行

所有脚本通过 `--model` 指定被测模型（任意 OpenAI 兼容端点上的模型），
端点地址来自 `config.json` 的 `base_url`（也可以用 `--base-url` 覆盖）：

```bash
cp config.example.json config.json    # 填好 base_url
pip install pandas pyarrow

# ① 边界判别（产生预测文件，后面两个脚本要用）
python bench/bench_ubuntu_irc.py --model <模型ID>

# ② 完整流程（1.1 + 1.3 复核）
python bench/bench_irc_full.py --model <模型ID> \
       --review-min 10 --preds results/ubuntu_irc_preds_<模型>.json

# ③ 0.7 + 1.2 合并（pairwise F1 与规模上限扫描）
python bench/bench_irc_merge.py --model <模型ID> \
       --preds results/ubuntu_irc_preds_<模型>.json
# 无上限对照（验证"必须设上限"的结论）
python bench/bench_irc_merge.py --model <模型ID> --max-units 999999 --max-chars 999999

# TIAGE / Alimeeting
python bench/bench_tiage.py --model <模型ID>
python bench/bench_tiage_full.py --model <模型ID> --phase a   # 先跑判别
python bench/bench_tiage_full.py --model <模型ID> --phase b   # 再用复核
python bench/bench_alimeeting.py --model <模型ID> --meetings 3
```

数据目录默认 `bench/data/`，可用 `--data-dir` 覆盖（部分脚本用 `--data` 指定单个文件）。

> 脚本里显式传 `reasoning_effort: 'none'` 只是为了复现 Qwen 系列「关闭思考」的状态，
> 让数字与报告一致；**流水线本身不依赖这个参数**（默认不发送）。换用其他模型时它无意义。

## 输出

- `bench/results/ubuntu_irc_result_<model>.json`：混淆矩阵 + P/R/F1/准确率
- `bench/results/ubuntu_irc_preds_<model>.json`：逐样本预测（便于错误分析、供下游脚本复用）
- `bench/results/tiage_result_<model>.json`、`alimeeting_result_<model>.json`：汇总指标
- `bench/results/irc_link_cache.json`：0.7 初审的逐条结果缓存（重跑免调用）

参考数值与结论见 [`docs/validation-report.md`](../docs/validation-report.md)。
报告里的每个数字都能由上面命令复现；模型与设备配置见报告 §0。
