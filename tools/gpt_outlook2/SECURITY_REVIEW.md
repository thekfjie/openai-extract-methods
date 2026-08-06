# Security review (2026-07-22)

Source: gpt-outlook-register.zip (sha256 fdc676f9442ac4f090588ab1e9f57df3a0caf3e2def0fbfc7e12c480ec0a220a)
Upstream: https://github.com/Regert888/gpt-outlook-register

## Findings
- No obvious malware backdoor / reverse shell / crypto miner found in source.
- Network targets are mainly OpenAI / Microsoft / SMS provider APIs (SmsBower, HeroSMS) and optional CF temp-mail admin API.
- High-risk but expected for this tool:
  - `auth_flow.py`: `subprocess.check_output(..., shell=True)` only when env `OPENAI_PHONE_OTP_CMD` is set (user-controlled OTP command).
  - `sentinel_quickjs.py` / `openai_sentinel_quickjs.js`: runs OpenAI sdk.js via QuickJS/Node `eval` for Sentinel PoW.
  - `start_webui.py`: may pip-install fastapi/uvicorn if missing.
- Packaged `webui.db` was empty; not installed (fresh runtime DB).
- `.git` and `__pycache__` excluded from install.

## Placement
- Runtime tool: `/opt/automyai/tools/gpt_outlook` (parallel to `tools/grok_ttk`)
- Source ref: `/opt/automyai/refs/gpt_outlook_register`
- Data dir: `/opt/automyai/data/gpt_outlook`
