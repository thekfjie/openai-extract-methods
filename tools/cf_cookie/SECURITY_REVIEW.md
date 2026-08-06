# Security review (2026-07-22)
sha256: b16b4d95fb8221a1750d9307eb96300e4c9e85181d87de922810725a6bf3d9a7

- No malware backdoor in project scripts.
- Purpose: Cloudflare cookie capture via Puppeteer.
- Uses child_process/net/fs as expected for browser automation.
- Bundled node_modules retained for offline use.
- Sample proxies not installed into runtime data; use data/cf_cookie/proxies/proxies.txt
