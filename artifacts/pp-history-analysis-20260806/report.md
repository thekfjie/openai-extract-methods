# PP 提炼与协议支付历史核查

- Snapshot SHA-256 (jobs): `2aa0968125748123c0810469c7a4ff2c13481f40f30a69c830c9acd01afab150`

- 任务：253；条目：1418；可关联身份：125
- PP 总体：23/759（3.0%）
- 英国 PP：14/483（2.9%）

## PP 当前链型

| 范围 | 链型 | 成功/总数 | 成功率 |
|---|---:|---:|---:|
| 全部 | CS_LIVE | 23/478 | 4.8% |
| 全部 | NONE | 0/233 | 0.0% |
| 全部 | OAICS | 0/48 | 0.0% |
| 英国 | CS_LIVE | 14/287 | 4.9% |
| 英国 | NONE | 0/166 | 0.0% |
| 英国 | OAICS | 0/30 | 0.0% |

## 先前链型 → 英国 PP

| 假设 | 成功/总数 | 成功率 | 身份数 |
|---|---:|---:|---:|
| directCardOAICSToGBPayPal | 0/0 | — | 0 |
| cardMethodsOAICSToGBPayPal | 0/53 | 0.0% | 5 |
| anyNonPayPalOAICSToGBPayPal | 0/70 | 0.0% | 7 |
| directCardCSLiveToGBPayPal | 0/9 | 0.0% | 1 |
| cardMethodsCSLiveToGBPayPal | 0/14 | 0.0% | 2 |
| anyNonPayPalCSLiveToGBPayPal | 0/198 | 0.0% | 35 |

## 协议支付审计

- 事件：236；独立任务：140
- 最新任务结果：`{"failed": 132, "succeeded": 1, "verification_required": 7}`
- 精确字符串 OAILIVE 出现次数：0（未与 `cs_live_` 合并）
- 直卡审计中成功‘生成 Checkout 提链’事件：64；事件未记录链型，未强行与 PP 任务关联

## 结论口径

- 顺序队列要求相同的 token hash 或规范化邮箱，并且前一条记录时间更早。
- 队列为 0 表示当前历史没有该模式样本。
- 这些是描述性比例，不构成因果结论。
- verification_required 与已完成支付成功分开统计。
