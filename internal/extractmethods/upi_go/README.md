# 新 UPI 提炼方式（生产 Go slot）

目录: E:\Code\auto\pp\upi_go

## 快速跑

```bat
cd /d E:\Code\auto\pp\upi_go
set TOKEN=你的ChatGPT_access_token
set PROXY=http://user:pass@host:port
run_upi.bat
```

或：

```bat
run_upi.bat "TOKEN" "PROXY"
```

结果：
- `out\last_upi.json` 成功/失败 JSON
- `out\last_upi.err` 运行日志

## 默认

- 渠道：`PIX_CHANNEL=upi`
- 推广国：`UPI_PROMOTION_COUNTRY=VN`
- 可执行：`bin\pix_extract_slot.exe`（生产 2026-07-22 对齐）

## 注意

- 这是新 UPI 提炼，不是面板旧 Python UPI 分支
- 需要住宅代理；Checkout 会走代理
- 不要把真实 token 提交仓库
