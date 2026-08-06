# OpenAI 3

恢复后的 `chatgpt_register` 独立运行控制程序。Go 指纹服务是其底层模块，
不再替代任务启动、停止、配置、状态、日志和流量统计控制面。

| 类型 | 入口 | 实现 | 数据 |
|---|---|---|---|
| OpenAI 1 | /ui/pages/openai.html | uc_signup | data/browser_profiles |
| OpenAI 2 | /ui/pages/openai2.html | tools/gpt_outlook2 | data/gpt_outlook2 |
| OpenAI 3 | /ui/pages/openai3.html | tools/openai3/webapp.py + tools/chatgpt_register | data/openai3 |

指纹链路：`tools/chatgpt_register` → `integrations/oai_fingerprint.py` →
`automyai-fingerprint-api`（Go）→ 本地或授权云端数据源。

服务：`automyai-openai3.service` · 端口来自 `config/ports.env` · 反代 `/openai3/`。
