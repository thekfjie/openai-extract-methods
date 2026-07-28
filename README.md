# openai-extract-methods

专门存放 OpenAI / Stripe 各渠道提炼方法的仓库（`thekfjie`）。

## 包含
- `methods/paper_card`：纸卡短链（Simon + Han）
- `methods/philippines_ticdk`：菲律宾 PH/PHP 短链
- `methods/momo`：越南 MoMo 资格探测
- `methods/kakao`：韩国 Kakao 提炼
- `methods/upi_go`：印度 UPI
- `methods/pp_protocol`：PayPal 协议骨架
- `panel_adapters/extract_methods`：本地面板统一适配层

## 至少支持的 PayPal 渠道国
`US GB DE FR NL CA AU IN PH TH BA AE`

## 安全
- 源码已静态检查，未见木马/反弹壳
- **不要**把真实代理密码、token、CDK 提交进仓库
- 使用 `configs/proxy.example.json` 作为模板

## 本地面板
主运行面板仍在 `openai-pay-pp-src`；本仓库保存提炼方法与适配器源码，方便单独备份与服务器部署。
