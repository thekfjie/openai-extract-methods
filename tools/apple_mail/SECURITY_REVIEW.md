# Security review (2026-07-24)
sha256 (original flowgpt_console.js): 294481222490fb818e3a581083bea904a1179a6d0979a01478e9c1489a2037f4

- No malware / reverse shell / miner found on static review.
- Expected: browser console helper for ChatGPT signup + Apple/iCloud mail code polling + optional account import API.
- Runtime copy removes hardcoded:
  - import API key
  - default password
  - embedded 100-email pool (moved to data/apple_mail)
- Secrets should live in browser localStorage / operator env only.
- Original under refs/apple_mail
