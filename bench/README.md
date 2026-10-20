# 基准测试脚本

这里是从论文/数据集中复现验证结论用的脚本，**不包含任何数据集**。
所需数据请自行下载，放到 `bench/data/`；结果写到 `bench/results/`（两者都已在
`.gitignore` 中忽略）。

## 需要的数据

| 脚本 | 数据文件 | 说明 |
|---|---|---|
| `bench_ubuntu_irc.py` | `bench/data/ubuntu_irc_dev.parquet` | Ubuntu IRC / Conversation Disentanglement；每行一个 20 条消息的窗口 |
| `bench_tiage.py` | `bench/data/tiage_test.json` | TIAGE 双人闲聊话题分段 |
| `bench_alimeeting.py` | `bench/data/alimeeting_dev.csv` | Alimeeting4MUG 中文会议（TSV，第二列内嵌 JSON） |

数据不是本仓库的一部分，也不随仓库分发。

## 运行

所有脚本通过 `--model` 指定被测模型（任意 OpenAI 兼容端点上的模型都可以），
端点地址来自 `config.json` 的 `base_url`（也可以用 `--base-url` 覆盖）：

```bash
# 第一次运行前：cp config.example.json config.json，填好 base_url
pip install pandas pyarrow        # 基准脚本额外依赖（pyarrow 仅 Ubuntu parquet 需要）

python bench/bench_ubuntu_irc.py --model <模型ID>
python bench/bench_tiage.py --model <模型ID>
python bench/bench_alimeeting.py --model <模型ID> --meetings 3
```

## 输出

- `bench/results/ubuntu_irc_result_<model>.json`：混淆矩阵 + P/R/F1/准确率
- `bench/results/ubuntu_irc_preds_<model>.json`：逐样本预测（便于错误分析）
- `bench/results/tiage_result_<model>.json`、`bench/results/alimeeting_result_<model>.json`：汇总指标

参考数值与结论见 [`docs/validation-report.md`](../docs/validation-report.md)。
