# 历史 ChatGPT Register 命令行引擎

本目录是历史独立命令行协议引擎，不再由 OpenAI3 控制服务调用：

- 指纹兼容性面板: /ui/pages/openai3.html
- Go 控制服务: automyai-openai3.service
- 运行代码: /opt/automyai/tools/chatgpt_register
- 数据: /opt/automyai/data/openai3

本目录如被人工、独立调用，可通过 `OAI_FINGERPRINT_ENTRY=chatgpt_register` 和
`OPENAI3_ACCOUNTS_FILE` 注入自己的入口与数据路径；独立命令行默认使用
`chatgpt_register` 入口。
