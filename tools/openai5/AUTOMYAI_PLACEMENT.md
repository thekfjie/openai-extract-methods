# OpenAI5 环境监督器

OpenAI5 是独立的 API-only 桌面环境诊断服务，不执行账号注册。

- 控制面：`automyai-openai5.service` / `OPENAI5_PORT` / `/openai5/`
- 数据：`data/openai5`
- 指纹来源：仅 AutoMyAI 指纹 API；接受 `local-api` 或 `authorized-cloud`
- 回退策略：禁止本地模板回退
- 流程结构参考 FlowPilot 的节点状态、有限重试与停止语义（MIT）
- 检查范围：API 健康、鉴权、来源证明、桌面预设、代理与官方 OpenAI 站点连通性
