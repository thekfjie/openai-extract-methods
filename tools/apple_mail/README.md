# Apple Mail

Automyai 独立通道：用 **Apple/iCloud 邮箱** 在 ChatGPT 页面完成自动填表、收验证码、抓 session、导号。

## 原则

- 独立页面，不与 OpenAI 1/2/3 / Outlook 主线混用
- 默认 **dry-run**
- 真实使用必须走项目代理（默认 `http://172.19.0.1:7905`）
- 禁止本地真实 IP 直连注册
- 指纹不过关优先 **Firefox 147**

## 面板

`/ui/pages/apple_mail.html`

## 静态验证

```bash
python /opt/automyai/tools/apple_mail/static_dry_run.py --proxy http://172.19.0.1:7905 --probe-import
```

只检查：
- 本地资产/号池/脚本逻辑
- 代理出口身份
- mail/import 主机经代理可达

不会：
- 打开 ChatGPT
- 自动注册
- 自动导号

## 真实使用前

1. 浏览器走项目代理
2. 优先 Firefox 147
3. 面板保存配置并复制注入脚本
4. 在 ChatGPT 页控制台执行 `await AppleMail.auto()`
