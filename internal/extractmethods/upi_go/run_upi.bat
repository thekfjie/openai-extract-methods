@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if "%TOKEN%"=="" (
  if not "%~1"=="" set "TOKEN=%~1"
)
if "%PROXY%"=="" (
  if not "%~2"=="" set "PROXY=%~2"
)

if "%TOKEN%"=="" (
  echo Usage:
  echo   set TOKEN=eyJ...
  echo   set PROXY=http://user:pass@host:port
  echo   run_upi.bat
  echo or:
  echo   run_upi.bat "TOKEN" "PROXY"
  exit /b 2
)

if "%PROXY%"=="" (
  if exist "E:\Code\auto\pp\local_proxies.json" (
    for /f "usebackq delims=" %%A in (`powershell -NoProfile -Command "(Get-Content -Raw 'E:\Code\auto\pp\local_proxies.json' | ConvertFrom-Json).checkout_approve_url"`) do set "PROXY=%%A"
  )
)

set "PIX_CHANNEL=upi"
set "UPI_SLOT=1"
if "%UPI_PROMOTION_COUNTRY%"=="" set "UPI_PROMOTION_COUNTRY=VN"

echo [UPI-GO] channel=upi promo=%UPI_PROMOTION_COUNTRY%
echo [UPI-GO] proxy=%PROXY%
echo [UPI-GO] running...

if "%PROXY%"=="" (
  "bin\pix_extract_slot.exe" -slot -token "%TOKEN%" > "out\last_upi.json" 2> "out\last_upi.err"
) else (
  "bin\pix_extract_slot.exe" -slot -token "%TOKEN%" -proxy "%PROXY%" > "out\last_upi.json" 2> "out\last_upi.err"
)
set "CODE=%ERRORLEVEL%"
echo [UPI-GO] exit=%CODE%
type "out\last_upi.json"
if exist "out\last_upi.err" (
  echo ----- stderr -----
  type "out\last_upi.err"
)
exit /b %CODE%
