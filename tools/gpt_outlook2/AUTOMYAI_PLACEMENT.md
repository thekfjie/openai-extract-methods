# OpenAI 注册 2（隔离类型）

与旧 OpenAI/UC 注册完全分离，命名对标 Grok 2：

| 项 | 旧 OpenAI 注册 | OpenAI 2 |
|---|---|---|
| 入口 | `/ui/pages/openai.html` | `/openai2/` |
| 实现 | `uc_signup.py` + server UC 流程 | `tools/gpt_outlook2` 纯协议 |
| 数据 | `data/browser_profiles` / `uc_signup_*` | `data/gpt_outlook2` |
| 进程 | automyai 主服务 :13030 | `automyai-openai2.service` :18790 |

不要把号池/凭证写回旧 UC 目录。
