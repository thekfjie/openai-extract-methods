UPI / PIX Go 提链 slot 二进制（Windows 适配包）
=============================================
打包时间: 2026-07-23 16:54:35 +0800
源码路径: /www/wwwroot/upi_bt_package/tools/pix_extract_go
生产 Linux 二进制: /www/wwwroot/upi_bt_package/bin/pix_extract_slot
main.go mtime: 2026-07-22 15:01:07.281681791 +0800
main.go sha256: c2ab87828586cd3f834b619b97560f3dc29a33155f672cc318e50a5e2d967f4f
Windows exe sha256: 58de00d7b035868f904bd77428654235d387ebf819010320755f9b463b849e10
生产 Linux sha256: 1dd2492cab7d440b26f0fa1b3af47048835346d873ff97e6783267837b969e86

目录
----
bin/pix_extract_slot.exe   Windows amd64 可执行（静态、无 CGO）
src/                       对应源码 main.go go.mod go.sum routing_test.go
run_upi_slot.bat           UPI 示例
run_pix_slot.bat           PIX 示例

UPI 用法（推荐）
--------------
set PIX_CHANNEL=upi
set UPI_SLOT=1
:: 可选：推广国，默认 VN
:: set UPI_PROMOTION_COUNTRY=VN
:: set PIX_PROMOTION_COUNTRY=VN

pix_extract_slot.exe -slot -token "你的ChatGPT_access_token" -proxy "http://user:pass@host:port"

输出：stdout 一行 JSON（与生产 Python worker 合同一致）
  成功 exit 0，含 UPI/PIX 支付材料
  失败 exit 非 0，JSON 含 ok=false / error

常用环境变量
------------
PIX_CHANNEL=upi|pix          渠道（UPI 也可 UPI_SLOT=1 / UPI_EXTRACT=1）
UPI_PROMOTION_COUNTRY / PIX_PROMOTION_COUNTRY
UPI_DEFAULT_PROXY            未传 -proxy 时的默认代理
UPI_MAX_RETRY / UPI_ATTEMPT_MAX   默认 5
UPI_OUTER_LOOP_MAX
UPI_POLL_TIMEOUT / UPI_POLL_MAX_QUERIES
UPI_APPROVE_RETRY_MAX
UPI_FULLPAGE_FALLBACK_ENABLED
PIX_SESSION_TOKEN / UPI_SESSION_TOKEN  可选 session cookie

本机构建（需 Go 1.22+）
--------------------
cd src
set CGO_ENABLED=0
go build -mod=mod -trimpath -ldflags="-s -w" -o ..\bin\pix_extract_slot.exe .

注意
----
1. 本包为生产 2026-07-22 对齐源码的 Windows 交叉编译，协议与 Linux 生产 bin 同源。
2. 需要可用住宅代理；Checkout 路径与生产一致。
3. 不要把真实 token 提交到仓库或发给第三方。
