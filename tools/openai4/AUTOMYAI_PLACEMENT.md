# OpenAI 注册（控制面服务名 OpenAI4）

页面仍叫 **OpenAI 注册**（`/ui/pages/openai.html`），内部控制面服务名为 openai4。

| 类型 | 入口 | 实现 | 数据 |
|---|---|---|---|
| OpenAI 注册 | /ui/pages/openai.html | tools/openai4/webapp.py + uc_signup | data/openai4 + data/browser_profiles |
| OpenAI 2 | /ui/pages/openai2.html | tools/gpt_outlook2 | data/gpt_outlook2 |
| OpenAI 3 | /ui/pages/openai3.html | tools/openai3/webapp.py + tools/chatgpt_register | data/openai3 |

- 控制面：`automyai-openai4.service` · 端口 `OPENAI4_PORT` · 反代 `/openai4/`
- 浏览器引擎：主站 `uc_signup`（有头 Chromium / Xvfb / noVNC）
- 默认邮箱分组：`默认分组` → `oai_pending` → `oai_success` / `badmail`
