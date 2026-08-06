# Outlook Register（微软邮箱纯协议注册）

本目录是 **Microsoft Outlook/Hotmail 邮箱注册机**，不是 ChatGPT 注册。

| 项 | 路径/说明 |
|---|---|
| 运行脚本 | `tools/outlook_register/outlook_register.py` |
| 原始附件/归档 | `refs/outlook_register/` |
| 数据目录 | `data/outlook_register/` |
| 产物格式 | `email----password----client_id----refresh_token` |
| 面板入口 | 侧边栏「工具与探针」→「Outlook 注册机」 `/ui/tools?sub=outlook_register` |
| API | `/api/outlook-register/*`（见 `integrations/outlook_register_manager.py`） |

与现有模块关系：

- `tools/gpt_outlook` / `tools/gpt_outlook2`：用 **已有 Outlook 4 段账号** 去注册 ChatGPT
- `tools/outlook_register`：先 **注册出 Outlook 4 段账号**，给上面的号池补货
- 可选导入：mail_manager / OutlookEmail（`--import-url` / env）

## 默认入库（已接）
- 注册成功后自动写入 OutlookEmail 源分组：`默认分组`
- 管理器：`importToDefaultGroup=true`（可关）
- 格式：`email----password----client_id----refresh_token`
- 坏号请放 `badmail`；新号进入空的默认分组供后续流程取用

