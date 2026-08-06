# PH/PHP 直卡协议模块

该模块位于支付中心，与 PayPal 协议并列，不属于提炼中心。

## 合并来源

- `thekfjie/zkky`，导入提交 `b4aa1beec683320799d8aa268f82062eab7b2a94` 的纯 HTTP PH Checkout 两阶段提炼核心。
- `protocol-card-payment-sanitized-20260803-203919.zip`，导入其 Checkout 上下文二次读取与应付金额校验：创建/更新完成后重新读取官方 OAICS 上下文，以 `checkout_state.total.total.minorUnitsAmount` 作为最终金额依据。压缩包内带 `<REPLACE_ME>` 的失效端点、浏览器路径和确认脚本未导入。

## 运行边界

运行服务复用现有 `paypal-protocol` 容器和 `/paypal-protocol/api/` 鉴权入口，不新增公网端口。

当前 Checkout 工作流为：

1. 使用代理池 1 创建 PH/PHP Checkout；
2. 使用代理池 2 更新同一 Checkout 并应用优惠；
3. 两阶段和最终复核固定复用同一设备身份，并从 AT 携带账号标识；
4. 重新读取官方 Checkout 上下文，校验 Session、PH/PHP，并读取最终应付金额；
5. 不满足金额门禁时丢弃本次 Checkout，完整重跑；
6. 满足门禁后返回官方 Checkout 链接并进入页面交接阶段。

## 支付中心输入

以本地压缩包的批量提链页面为主，支付中心现支持：

- 最多 50 条 AT / Session JSON 智能识别；
- 双代理池，每池最多 500 条；
- 1–10 个批量并发，每个 AT 独立执行完整重试；
- 1–50 次完整链路尝试、20–180 秒单请求超时；
- Campaign、优惠资格诊断与金额门禁；
- 可选 Account ID、Device ID、OAI Session Trace ID、User-Agent、Session Cookies。

满足条件后返回并打开官方 Checkout 页面，工作台展示地区、币种、最终金额、金额依据、上下文复核结果、命中尝试和 Session，后续交互在官方页面继续。

## 后半段协议工作区

支付中心的“直卡协议”默认打开独立的后半段工作区，前面的 Checkout 提炼保留为第二个子页。后半段支持：

- 最多 50 组 `AT / Session JSON + 已有 Checkout 链接`；
- 独立协议代理池与 1–10 并发上下文准备；
- 持卡人姓名、邮箱、电话、账单地址、城市、省州、邮编和国家代码；
- 页面内存中的卡号、有效期与 CVC 输入区，刷新或离开页面即清除；
- Account ID、Device ID、Session Trace ID、Session Cookies、User-Agent 和请求超时；
- 协议模式（自动 / Setup / Subscription）、支付方式、setup future usage、返回地址；
- 最终任务并发、单卡重试次数与重试间隔；
- 每行 `AT + Checkout` 或 JSON 数组的批量配对导入；
- 后端逐组生成脱敏的卡片摘要、Elements 参数、CustomerSession/Stripe/返回路径就绪状态；
- 重新读取已有官方 Checkout 上下文；
- 展示最终金额、币种、支付方式、卡协议可用性、Stripe 上下文完整度、支付资料完整度、后续使用方式和 Session；
- 逐任务选择、全选、复制及批量打开官方 Checkout。

本地包中被脱敏为 `<REPLACE_ME>` 的内部服务地址不会被伪造；支付中心使用当前项目可验证的官方 Checkout 上下文接口完成协议准备与页面交接。

## 金额门禁

- `strict_zero`：严格等于 0；
- `at_most`：不高于指定 PHP 金额；
- `at_least`：不低于指定 PHP 金额；
- `any_known`：任意已识别金额；
- 金额未知默认拒绝，只有显式设置 `allowUnknownAmount` 才放行。

PHP 按两位小数处理；例如上游最小单位 `98214` 显示并比较为 `₱982.14`。
