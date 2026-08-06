# OpenAI5 协议/无头注册准备基线

生成时间：2026-08-04（UTC）

## 已找到的 HAR

- `/tmp/openai3-capture-1.har`
- `/tmp/openai3-capture-2.har`

原始文件只读分析；下方清单不包含 Cookie、Bearer、OTP、挑战正文、邮箱或账号标识。

## HAR 结论

两份录制都经过 Cloudflare/Turnstile 和 Sentinel，不能把挑战正文当作可重放协议字段。可稳定抽象的业务步骤是：

1. `chatgpt.com`：发现 provider、获取 CSRF、发起 OpenAI OAuth。
2. `auth.openai.com`：授权会话；新账号分支接收邮箱 OTP，已有账号分支提交标识后接收邮箱 OTP。
3. 新账号分支调用 `create_account`（字段形状为 `name`、`birthdate`），随后回到 `chatgpt.com/api/auth/callback/openai`。
4. 已有账号分支跳过创建，直接走 callback。
5. callback 后可选地选择 session、发送/验证手机 OTP、选择 workspace。

HAR1 的 `create_account` 分支最终成功；HAR2 先出现 `500` 与 `409`，随后转入已有账号登录分支并成功 callback。这两个结果应作为状态机的显式分支，而不是把任何 4xx/5xx 当作可盲目重试。

## 指纹接入基线

现有 `openai4` 已接入 `automyai-fingerprint-api`：

- `POST http://127.0.0.1:50001/oai/fingerprint/generate`
- OpenAI 协议引擎使用 `entry=openai3`
- 默认 `preset=windows-11-chrome`
- 默认浏览器版本 `150.0.0.0`
- 输出包括 HTTP headers、Chromium 启动参数、CDP 注入命令、Sentinel navigator、`device_id`、`profile_id` 与 provenance。

无头实现每个邮箱事务只生成一次 profile，并把同一 `profile_id`、`device_id` 绑定到注册、`500/409` 已有账号恢复以及一次传输级重启。正常路径不创建 Chromium context。邮箱 OTP、手机 OTP、代理、状态存储仍使用 typed slots：`EMAIL_OTP_PROVIDER`、`PHONE_OTP_PROVIDER`、`PROXY`、`REGISTRATION_STATE_STORE`。

## 实现边界

- provider、CSRF、OAuth、OTP、资料提交、callback、session 与认证导入均走协议 client。
- Sentinel 使用与同一 profile 绑定的现有 HTTP 实现；Cloudflare/Turnstile 浏览器挑战不做静态重放，返回 `challenge_required` 并停止当前邮箱事务。
- 每个状态都记录请求方法、官方 host、路径、字段名、响应状态和下一跳；值全部运行时注入。
- `create_account` 每个事务只提交一次；只有 HAR 已确认的 `409` 或 `5xx` 进入 passwordless existing-login recovery，`registration_disallowed` 等显式业务拒绝立即终止。
- OpenAI5 作为编排入口复用 OpenAI3 协议引擎，不再复制第三套注册状态机。

机器可读清单见：`tools/openai5/protocol-manifest.json`。
