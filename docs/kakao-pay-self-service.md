# Kakao Pay 批量自助流程

更新时间：2026-07-30（UTC）

## 入口

- 页面：`/ui/extract`，选择“Kakao Pay”。
- 文件库：`/ui/file-library`，查看 `kakao-pay-self-service-guide.txt`，也可在提炼页把运行记录保存为 `kakao-self-service-results.txt`。
- API：`POST /api/extract/jobs`；查询任务使用 `GET /api/extract/jobs/{jobId}`。

Kakao 页面和 API 支持批量账号与可配置并发。每个账号独立执行、独立计算最多尝试次数；系统不会替用户完成付款。

## 两种运行模式

### 1. `eligibility`：资格观察（默认）

目的：只确认当前 KR checkout 的 Stripe bootstrap methods 是否由上游真实返回 `kakao_pay`。

流程：

1. 使用主流程代理创建 KR / KRW ChatGPT checkout。
2. 激活 Stripe checkout 页面并读取 bootstrap init。
3. 记录上游实际返回的 amount、currency、methods。
4. 返回 `eligible` 或 `ineligible`，随后立即停止。

该模式按“每账号最多尝试次数”观察上游，不执行 Promotion，不创建 payment method，不执行 confirm / approve，也不会产生 NicePay 链。某个账号命中 `kakao_pay` 后立即停止该账号，不影响同批其他账号。

### 2. `provider_link`：支付链提炼

目的：在上游真实展示 `kakao_pay` 的前提下，生成用户可自行打开的 NicePay/Kakao 待支付长链。

流程：

1. 使用 KR 主流程代理创建并激活 ChatGPT / Stripe checkout。
2. 在 bootstrap init 严格确认上游 methods 包含 `kakao_pay`；没有则停止，不注入隐藏支付方式。
3. 使用 Promotion 代理更新优惠：显式填写时原样使用；留空时默认按原始链路从 KR 主代理派生 VN 出口，也可选择 TR 或 JP。
4. 回到与 checkout 完全相同的 KR 主代理 sticky identity，重新读取 Stripe init。
5. 严格确认 Promotion 后为零金额 KRW，且 methods 仍包含 `kakao_pay`。
6. 同步 ChatGPT 账单税务和 Stripe tax region，再次确认零金额 KRW 与 `kakao_pay`。
7. 执行 `pre_confirm`，创建 `kakao_pay` payment method，执行 confirm / approve，并轮询 Stripe provider redirect。
8. 仅接受最终 host 属于 NicePay 或 Kakao 的链接。
9. 返回 `provider_link_ready` / `awaiting_kakao_payment`，随后停止，付款由用户自行完成。

## 页面参数

- 账号凭证：支持多行 Bearer/JWT、逐行 JSON 或 JSON 数组；每个账号独立执行。
- Kakao 运行模式：默认 `eligibility`；只有显式选择 `provider_link` 才会进入支付链步骤。
- 主流程代理：必填。支持 `http://user:pass@host:port`、`host:port:user:pass`，也支持无认证的 `host:port`。
- 优惠代理：仅支付链模式显示。显式填写后只做语法标准化，不改地区、不替换 SID；留空时才复用主流程代理的 sticky seed 并切换 Promotion 地区。
- Promotion 兜底地区：仅在优惠代理留空时生效，默认 VN，可选 TR、JP。checkout / provider / approve 使用显式 KR 主代理。
- 每账号最多尝试次数：资格观察按该次数执行 bootstrap 观察；支付链模式允许 1–100，未填写时默认 10 次。每一次都是“新 Checkout → Promotion → Taxes → Confirm → NicePay/Kakao”的完整链；任一后半段失败都会废弃当前 Checkout，从头重跑，成功出链立即停止该账号。
- Promotion Campaign ID：默认 `plus-1-month-free`。
- 金额上限：默认 100（最小货币单位）；支付链要求最终为零金额 KRW，超过上限立即停止。
- 超时：5–180 秒；默认 45 秒。

## 浏览器本地还原

- 主代理、优惠代理、模式、并发、次数和其他表单参数会按渠道及批次写入当前浏览器的 `localStorage`，刷新、重新打开页面或稍后打开历史批次时仍可还原。
- 升级前保存在当前标签页 `sessionStorage` 的批次快照会自动迁移为不含账号 token 的本地配置快照。
- 账号 token 不写入长期任务历史或 `localStorage`，只保留在当前标签页 `sessionStorage`；因此关闭该标签页后仍可还原代理/参数，但需要重新提供账号 token 才能再次发起上游请求。
- 资格观察完成且当前标签页仍保留该批账号 token 时，运行监控会显示“整批转支付链（10 次起）”，可直接沿用当时代理/参数，把整批账号提交到支付链模式。
- 服务端若为旧资格批次登记了支付链续跑请求，页面刷新后会优先打开该待续跑批次；只要提交该批时的标签页仍保留账号 token，页面会自动沿用当时的代理、并发和参数创建支付链任务，无需再次点击。

## 代理与地区标准化

支付链模式按以下规则处理代理，避免页面选择器覆盖用户已经填好的代理：

1. 先把原始代理标准化为 URL。
2. 显式主代理只做语法标准化，并校验其可识别的出口选择器为 KR；不暗改地区或 SID。
3. 显式优惠代理只做语法标准化，不暗改地区或 SID；它优先于页面保存过的兜底地区。
4. Kakao provider / approve 强制复用 checkout 的完整 KR 代理身份。
5. 只有优惠代理留空时，Promotion 才按所选兜底地区改写主代理，并保留同一个 sticky seed；默认地区为 VN。

如果代理提供商的用户名没有可识别的地区字段，后端无法凭空改变出口国家；此时请直接填写已经指向目标地区的代理。

## API 示例

资格观察：

```json
{
  "method": "kakao",
  "input": "<access-token-1>\n<access-token-2>",
  "concurrency": 2,
  "options": {
    "kakaoMode": "eligibility",
    "proxyMode": "custom",
    "proxy": "http://user-country-KR:pass@proxy.example:3010",
    "timeoutSeconds": 45
  }
}
```

支付链提炼：

```json
{
  "method": "kakao",
  "input": "<access-token-1>\n<access-token-2>",
  "concurrency": 2,
  "options": {
    "kakaoMode": "provider_link",
    "proxyMode": "custom",
    "proxy": "proxy.example:3010:user-country-KR-sid-example-t-10:pass",
    "promotionProxy": "proxy.example:3010:user-country-TR-sid-explicit-t-10:pass",
    "usePromo": true,
    "promoCampaignId": "plus-1-month-free",
    "maxAttempts": 10,
    "maxAmountMinor": 100,
    "timeoutSeconds": 45
  }
}
```

未知 `kakaoMode`、缺少代理或无效代理会在创建任务时直接返回 400，不会启动上游请求。

## 结果判断

- `probe_complete` + `eligible`：bootstrap methods 真实包含 `kakao_pay`，资格观察已在付款前停止。
- `probe_complete` + `ineligible`：本次 bootstrap 观察成功，但 methods 没有 `kakao_pay`。
- `provider_link_ready` + `awaiting_kakao_payment`：已生成 NicePay/Kakao 待支付长链，等待用户操作。
- `approval_blocked` / approve blocked：已走到上游 approve，但被上游拦截，没有生成新链。
- Promotion update HTTP 403：checkout 已创建，但优惠更新被上游拒绝。
- helper unavailable：已验证的 `curl_cffi` transport 不可用；系统会失败关闭，不会静默回退到旧 Go 流程。

## 已验证依据与限制

2026-07-30 的原始 Python/`curl_cffi` 对照流程曾在 KR checkout bootstrap 真实观察到 `card,kakao_pay,naver_pay`；同一路线也曾进入 Kakao confirm。部分后续尝试在 Promotion update 返回 HTTP 403，另有尝试在 approve 返回 blocked，因此“账号具备 Kakao 资格”不等于“每次一定生成新 NicePay 链”。

当前部署复用已验证的 `curl_cffi` Chrome 136 TLS impersonation + Chrome 147 User-Agent 组合，并保留每个实际步骤和上游 methods 供排查。它不会伪造资格，也不会绕过上游对账号、IP、地区、优惠或 approve 的判断。
