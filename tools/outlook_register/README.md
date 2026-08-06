# Outlook Register

Microsoft Outlook / Hotmail 纯协议注册脚本（Automyai 入库版）。

## 它做什么

1. 纯 HTTP 注册 `outlook.com` / `hotmail.com` 邮箱
2. 走 CaptchaRun `PxCaptcha2` 过 PX 风控
3. 注册成功后做 Microsoft OAuth2，拿到 Graph 邮件相关 `refresh_token`
4. 输出 4 段账号行，可给 OutlookEmail / OpenAI 2 号池使用
5. 可选自动导入 mail_manager

## 依赖

```bash
pip install -r requirements.txt
```

## 用法

```bash
cd /opt/automyai/tools/outlook_register
export CAPTCHARUN_TOKEN='你的CaptchaRun token'
export OUTLOOK_REGISTER_OUTPUT=/opt/automyai/data/outlook_register/accounts.txt

# 单线程
python outlook_register.py --proxy 'http://user:pass@host:port' --country US

# 多线程 + 代理池
python outlook_register.py --proxy-file /path/to/proxies.txt --threads 5 --domain outlook.com

# 只补 OAuth
python outlook_register.py --fix-auth --output /opt/automyai/data/outlook_register/accounts.txt --proxy-file proxies.txt
```

## 主要参数

| 参数 | 说明 |
|---|---|
| `--cr-token` | CaptchaRun API token（可用 env `CAPTCHARUN_TOKEN`） |
| `--proxy` / `--proxy-file` | 出口代理 |
| `--domain` | `outlook.com` 或 `hotmail.com` |
| `--threads` | 并发线程 |
| `--output` | 输出文件（默认 `accounts.json`，建议改到 `data/outlook_register/`） |
| `--import-url` / `--import-password` | 可选 mail_manager 导入 |
| `--fix-auth` | 扫描输出文件，给缺 refresh_token 的账号补授权 |

## 输出

每行一条：

```text
email----password----client_id----refresh_token
```

其中 `client_id` 默认是脚本内置的 Outlook 移动客户端 ID。

## 和 Automyai 其他工具的区别

| 工具 | 作用 |
|---|---|
| `tools/outlook_register` | 注册微软邮箱，产出 4 段 Outlook 账号 |
| `tools/gpt_outlook2` | 用现成 Outlook 4 段账号注册 ChatGPT（OpenAI 2） |
| `tools/openai3` | 另一套邮箱协议注册 ChatGPT（OpenAI 3） |
