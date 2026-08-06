# Reg153 PH Short Protocol Portal

`https://app.example.com/ph-short/` 的菲律宾短链任务页。

当前版本使用纯后端任务状态机，不创建临时 Chromium：

1. 从 reg153 内部桥接接口读取已完成的 `ph_short` 提链任务；
2. 校验官方短链、Checkout Session、PH/PHP 和金额；
3. 确认原任务仍保留可用账号环境；
4. 生成官方 Checkout 跳转；
5. 前端统一展示任务进度、停止、错误和结果。

## API

- `GET /api/source/tasks`：读取已完成的菲律宾短链任务。
- `POST /api/jobs`：创建协议准备任务，参数为 `{"task_id":"..."}`。
- `GET /api/jobs`：读取任务列表。
- `GET /api/jobs/<id>`：读取单个任务。
- `POST /api/jobs/<id>/cancel`：停止任务。
- `DELETE /api/jobs/<id>`：清除已结束任务。
- `GET /jobs/<id>/open`：跳转到任务对应的官方 Checkout。
- `GET /healthz`：健康检查；`temporary_browser` 固定为 `false`。

## 环境变量

- `REG_SESSION_SECRET`：复用 reg153 登录会话。
- `REG_SESSION_COOKIE`：登录 Cookie 名，默认 `reg_access`。
- `REG_INTERNAL_BASE`：reg153 内部服务地址。
- `PH_SHORT_BRIDGE_KEY`：内部桥接密钥。
- `PH_PORTAL_JOB_TTL`：已结束任务保留秒数，默认 86400。
- `PH_PORTAL_MAX_JOBS`：内存任务数量上限，默认 120。
