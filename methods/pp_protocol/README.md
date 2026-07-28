# PP Protocol

Brazil public skeleton as base, aligned to UK HAR (`E:\Code\auto\pp\uk_pp.har`).

This is the **downstream PayPal protocol** (BA/EC ? signup/2FA/SignUpNewMember ? uplift ? `billing.authorize`).
Upstream long-link extraction stays in `openai-pay-pp-src` / `upi_go`.

## Install

```bat
cd /d E:\Code\auto\pp\pp_protocol
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run

UK path (default):

```bat
python main.py --ba-token BA-xxx --phone +4475xxxxxxxx --country GB --locale en_GB
```

Brazil skeleton path:

```bat
python main.py --ba-token BA-xxx --phone +5591xxxxxxxx --country BR --locale pt_BR
```

Optional:

```bat
set PAYPAL_PROXY_ENABLED=1
set PAYPAL_PROXY_URL=http://user:pass@host:port
python main.py --ba-token BA-xxx --phone +4475... --country GB --proxy --ec-token EC-xxx --debug
```

## Protocol gates (hard)

1. Same cookie jar / UA / proxy for whole run
2. Capture latest EUAT after `SignUpNewMember` and persist cookie
3. Require `buyer.userId` + EUAT before `billing.authorize`
4. Authorize uses original EC token when present
5. Prefer `guest_user` + `fromSignupLite=true` + `addFIContingency=noretry`

## UK HAR alignment notes

- SignUp uses GB address / crsData (no CPF)
- 2FA locale uses `{country, lang}` from CLI
- Authorize fundingPreference: `OPT_IN` for non-BR, `OPT_OUT` for BR
- Authorize headers include `X-PayPal-Internal-EUAT` + separate CMID

## Smoke tests

```bat
python -m py_compile main.py config.py paypal\*.py
python test_protocol_gates.py
```

## Remaining risks

- UK HAR ends with `INVALID_RESOURCE_ID` in one capture; not a perfect success-only sample
- Card-verify skip for TH/manual regions is directionally supported, not live-proven from this HAR alone
- DataDome / captcha / phone OTP still need live path conditions
- Proxy must be correct for target country; do not hardcode one proxy as "the fix"
