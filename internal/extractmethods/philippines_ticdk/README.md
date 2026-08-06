# 菲律宾 PHP 提链分享版

这是截图红框所示的 `Philippines link (PH / PHP)` 专用整套线路。

- 无 CDK
- 无后台账号
- 无韩国、巴西、UPI、菲律宾卡等其他线路
- 固定：`PH / PHP / en-PH`
- 每位使用者在网页自行填写 Access Token / session JSON 与两组菲律宾出口代理

## 安装运行

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8791
```

打开：`http://HOST:8791/ticdk/`

`_deployment/` 包含 Nginx 和 systemd 部署模板。`.env.example` 中没有 CDK；代理默认也留空，由网页使用者自行填写。

## 两池流程

1. `代理池 1 - PH（Checkout）`：创建菲律宾 PHP Checkout，失败时轮换下一个代理。
2. `代理池 2 - PH（Promotion/优惠，可选）`：创建成功后提交 Promotion 更新；留空时沿用代理池 1。
