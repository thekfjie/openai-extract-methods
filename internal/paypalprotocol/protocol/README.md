# PP Protocol Skeleton

Canonical AutoMyAI location: `internal/paypalprotocol/protocol`.

This source is owned by the standalone PayPal protocol module, not by the
payment extraction engine. The web console validates BA, country, phone and
proxy inputs, starts an asynchronous GB/BR full-chain task, accepts the SMS code
in the UI, and continues through identity uplift, billing.authorize, and the
merchant return. Other catalog countries retain parameter validation only.

General protocol skeleton, currently aligned to the checked-in UK reference set.
The AutoMyAI payment center treats GB / `en_GB` as the active capture baseline
while offering the full checked-in PayPal country/region directory.

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
6. Treat addCard/R_ERROR plus an access token as a recoverable identity
   elevation; never submit SignUpNewMember again after that token exists
7. Mark payment confirmed only when the merchant return contains an explicit
   successful redirect_status; intermediate status=success remains authorized

## Web task API

- POST /api/jobs starts the full chain.
- GET /api/jobs/{id} returns the current phase and a secret-free result.
- POST /api/jobs/{id}/otp supplies a six-digit code or replacement phone.
- Terminal tasks are appended to data/paypal-protocol/results.jsonl and loaded
  after a service restart. Return URLs and client secrets are not persisted.

## UK HAR alignment notes

- The original HAR is stored outside the repository at
  `/home/ubuntu/备份英国pp过付款.har` (2026-07-24 capture, SHA-256
  `fa847b5ac4b8e84b0bf33df02b0876593a73a06f3b41defc130eb7b4280d3b68`).
- `_uk_refs` contains 9 split request groups and 27 request/response/header
  artifacts; all 9 groups match the original HAR request, response and selected
  header values.
- The read-only inventory derives operation names, variable names, header names
  and response roots without exposing captured values.
- The captured `SignUpNewMemberMutation` response is HTML rather than JSON and
  must not be treated as the direct success signal. The later `authorize`
  response contains a billing authorization object and the provider return
  redirects to a successful `cs_live_` checkout.
- Current query conformance is exact for CookieBanner, InitialData,
  DeferredFeature, CheckoutSessionDataQuery, GriffinMetadata, initiate/confirm
  phone verification, signup and authorize. `getOtpChallengeOperation` remains
  reference-only because the flow uses the direct checkoutweb risk-based phone
  confirmation path.
- The UK post-signup sequence now loads `/checkoutweb/drop`, then one canonical
  Hermes URL with `fromSignupLite=true`, `addFIContingency=noretry`,
  `redirectToHermes=true`, `fallback=1`, and the captured `R_ERROR` reason
  (`Ul9FUlJPUg==`) before the final `billing.authorize` call.
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

- The supplied UK HAR has a successful `billing.authorize` and successful
  provider return; two later genericError navigations are recorded after that
  success and must not be used to relabel the earlier authorization.
- Card-verify skip for TH/manual regions is directionally supported, not live-proven from this HAR alone
- DataDome / captcha / phone OTP still need live path conditions
- Proxy must be correct for target country; do not hardcode one proxy as "the fix"
