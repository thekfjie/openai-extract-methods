# Security review (2026-07-24)
sha256 (original): 98cefb9999b34d9215e623a2d52711108ca0c9efa062d4861d08228cd2b8eb5f

- No malware / reverse shell / miner found on static review.
- Expected behavior: curl_cffi Microsoft signup + CaptchaRun PxCaptcha2 + OAuth2 refresh token + optional mail_manager import.
- Runtime copy removes hardcoded CaptchaRun token / import URL / import password defaults.
- Configure via:
  - CLI: --cr-token --import-url --import-password --proxy/--proxy-file
  - ENV: CAPTCHARUN_TOKEN, OUTLOOK_REGISTER_IMPORT_URL, OUTLOOK_REGISTER_IMPORT_PASSWORD, OUTLOOK_REGISTER_OUTPUT
- Original files under refs/outlook_register
