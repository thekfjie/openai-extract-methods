# Security review (2026-07-22)
sha256: c4edadab31a8a25cd2a3f2b7243e39d2ca0b7183cab467178d3875da6d222d54

- No malware / reverse shell / miner found.
- Expected: curl_cffi OpenAI register + Sentinel PoW + mail OTP.
- Third-party defaults (CPA URL/key, mail URL/pass, webshare proxy) removed from runtime copy.
- Configure via env: CPA_BASE, CPA_KEY, MAIL_BASE, MAIL_PASS, CHATGPT_REGISTER_PROXY
- Original zip under refs/chatgpt_register
