# Apple Mail（独立通道）

| 项 | 路径 |
|---|---|
| 面板 | `/ui/pages/apple_mail.html` |
| 运行脚本 | `tools/apple_mail/flowgpt_console.js` |
| 静态 dry-run | `tools/apple_mail/static_dry_run.py` |
| 数据 | `data/apple_mail/` |
| 原件 | `refs/apple_mail/` |

约束：
- 不与 OpenAI 1/2/3 混用
- `requireProxy=true`
- 默认代理 `http://172.19.0.1:7905`
- 指纹建议 Firefox 147
