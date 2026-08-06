# Frontend API Contract

This document is the handoff contract for replacing every AutoMyAI UI.

| File | Answers |
| --- | --- |
| this document | what each endpoint the current UI depends on means |
| [`endpoints.json`](./endpoints.json) | which endpoints each existing page calls |
| [`backend-routes.json`](./backend-routes.json) | every route the backend serves, in dispatch order |

Sections 4–12 describe the routes the current pages use. They are not the whole
API: roughly half the `main` surface exists for the legacy console, the SMS
tooling and machine consumers. Section 13 maps those remaining families, and
`backend-routes.json` is the authoritative list. Regenerate it with
`./scripts/gen-backend-routes.py` after changing a route; `tests/test_api_routes.py`
fails when it drifts from `server.py` or `extensions_api.py`.

## 1. Transport conventions

- JSON requests use `Content-Type: application/json`.
- JSON responses use UTF-8 and may contain additional fields not listed here.
- The frontend must send cookies with `credentials: include` in same-origin mode.
- HTTP 401 means the administrator session is missing or invalid.
- Error payloads use one or more of `error`, `message`, `detail`, `type`, `path`.
- The shared SDK selects the first available error message in this order:
  `detail`, `error`, `message`, then `HTTP {status}`.
- GET status/log endpoints are read-only and may be polled.
- POST task endpoints are not idempotent unless their section explicitly says so.

## 2. Services

| Service | Default base | Owner | UI use |
| --- | --- | --- | --- |
| `main` | `/api` | `server.py` + `extensions_api.py` | auth, settings, OpenAI legacy, Grok, mail, conversion, tools |
| `openai2` | `/openai2/api` | OpenAI 2 service | service health and pool statistics |
| `openai3` | `/openai3/api` | Go control API | Profile generation, validation, task/log state |
| `grok2` | `/grok2` | external Grok 2 panel | navigation only; not a JSON contract in this frontend |

## 3. Authentication

### `GET main:/auth/status`

Response:

```json
{ "authRequired": true, "authenticated": false }
```

### `POST main:/auth/login`

Request:

```json
{ "password": "administrator password" }
```

Same-origin mode sets an HttpOnly administrator cookie. Response:

```json
{ "authenticated": true }
```

### `POST main:/auth/logout`

Request body is `{}`. It clears the cookie and returns
`{"authenticated": false}`.

For a separately hosted local frontend, `authMode: "header"` stores the
password in the tab's `sessionStorage` and sends `X-Admin-Password`. The backend
must allow the frontend origin through `CORS_ALLOWED_ORIGINS`.

## 4. Shared status and settings

| Method and path | Request | Minimum response fields used by UI |
| --- | --- | --- |
| `GET main:/health` | — | service health object; HTTP success means online |
| `GET main:/extensions/status` | — | `cpa.enabled`, `cpa.localReady`, `grok2api.ok`, `grok2api.authenticated`, `mailGroups.source`, `mailGroups.domain`, `domain.preferInventory` |
| `GET main:/settings` | — | `settings`, optionally field metadata |
| `POST main:/settings` | partial settings object | updated `settings`; secret values are not returned |
| `GET main:/traffic?tail=N` | query `tail` | `enabled`, `count`, `sessions` or equivalent history array |
| `GET main:/app-settings` | — | `{settings}` for the legacy console |
| `POST main:/app-settings` | `{settings: {...}}` | `{settings, configFile?}` |

The replacement frontend must never assume secret settings are readable. Blank
secret inputs mean “keep the existing value” unless the backend documents a
different rule.

`settings.UI_THEME` is a public persisted UI preference. Supported values are
`light`, `dark-purple`, `dark-cyberpunk`, `dark-matrix`, and `dark-obsidian`;
clients fall back to `dark-purple` when the field is absent.

## 5. OpenAI legacy page

| Method and path | Request | Minimum response fields used by UI |
| --- | --- | --- |
| `GET main:/uc-signup/status` | — | `ucSignupState` with `phase`, `running`, `completed`, `total`, `failed`, `currentEmail/current_email`, optional results/current step fields |
| `GET main:/uc-signup/logs` | — | `{logs:[{time,level?,message}]}` |
| `POST main:/uc-signup/start` | `{emails:[string], ...optional legacy fields}` | current `ucSignupState`, optionally `emailQueue` |
| `POST main:/uc-signup/stop` | `{}` | current `ucSignupState` |
| `GET main:/browser-live/status` | — | `running`, `target`, `page` |

The browser surface is the real interactive noVNC client at
`/novnc/vnc.html?autoconnect=1&resize=scale&path=novnc/websockify`; it is not
served through the main JSON API.

Polling currently uses approximately four seconds for registration state and
three seconds for browser-live state.

## 6. OpenAI 2 page

### `GET openai2:/health`

Minimum response:

```json
{
  "ok": true,
  "stats": { "available": 0, "total": 0, "done": 0, "failed": 0 }
}
```

The page also embeds `openai2UiBase` in an iframe and provides a full-page link.

## 7. OpenAI 3 runner with Go fingerprint module

### Read endpoints

| Method and path | Minimum response fields used by UI |
| --- | --- |
| `GET openai3:/status` | `{state:{running,phase,pid,concurrency,total,completed,failed}, config:{...}}` |
| `GET openai3:/logs?tail=N` | `{logs:[{time,level,message}]}` |
| `GET openai3:/traffic?tail=N` | `{current,items}` |
| `GET main:/outlook-email/accounts` | selectable OutlookEmail inventory for manual account choice |

The runner state is owned by the restored `tools/openai3/webapp.py`. Fingerprints are
not the application itself: the runner passes the selected source and seed to the
loopback Go fingerprint API before the integrated engine creates its HTTP session.

```json
{
  "running": false,
  "phase": "idle|starting|running|completed|stopped|error",
  "pid": 0,
  "concurrency": 1,
  "total": 1,
  "completed": 0,
  "failed": 0
}
```

### `POST openai3:/config`

```json
{
  "proxy": "",
  "traffic_meter": false,
  "mail_pass": "***",
  "sub2api_group": "auto",
  "fingerprint_enabled": true,
  "fingerprint_source": "local",
  "fingerprint_seed": "",
  "fingerprint_strict": true
}
```

Response is `{ "config": updatedConfig }`.

### `POST openai3:/start`

```json
{
  "concurrency": 1,
  "total": 1,
  "selected_account_id": 42,
  "selected_account_email": "selected@example.test",
  "selected_account_group": "默认分组",
  "sub2api_group": "auto",
  "fingerprint_enabled": true,
  "fingerprint_source": "local",
  "fingerprint_seed": "",
  "fingerprint_strict": true
}
```

`fingerprint_source` is `local` or `cloud`. Both go through the Go fingerprint API.
The managed policy fixes Firefox 147.0 and chooses Windows or macOS. A selected account
forces `total=1`; with no selection, the restored runner keeps its original mailbox flow.
Successful credentials are imported directly into the configured OpenAI Sub2API group;
CLIProxyAPI/CPA is not part of this import path.

```json
{ "ok": true, "state": { "running": true, "phase": "running", "pid": 1234 } }
```

### `POST openai3:/stop`

```json
{}
```

## 8. Mail pages

| Method and path | Request | Response use |
| --- | --- | --- |
| `GET main:/email-queue` | — | full queue object shown as JSON |
| `POST main:/email-queue` | `{emailsText}` | `{emailQueue}` or queue object |
| `POST main:/email-queue/allocate` | `{platform,preferInventory}` | allocation result |
| `POST main:/email-queue/generate-domain` | `{domain,count,prefix,preferSubdomain}` | generated addresses/result |
| `GET main:/email-queue/mail/latest?address=...` | optional address | latest mail/code plus queue state |
| `POST main:/email-queue/import-outlook-source` | `{sourceGroupName}` | `emailQueue`, optional `inventory` and skipped summaries |
| `GET main:/outlook-email/inventory?sourceGroupName=...` | optional group | inventory and group/account data |
| `GET main:/outlook-email/accounts` | — | read-only OpenAI 3 account picker with totals, group summaries, selection eligibility and sanitized account status fields |
| `POST main:/outlook-email/groups/ensure` | `{}` | updated group/inventory result |
| `POST main:/outlook-email/groups/replan` | `{}` | group planning result |
| `POST main:/outlook-email/accounts/move` | `{target,identifiersText}` | move result |
| `GET main:/apple-mail/status?tail=N` | log tail | `running`, `ok`, `currentStep`, `currentStepLabel`, `email`, `sourceEmail`, `proxy`, `proxyIdentity`, `fingerprint`, `logs` |

Apple Mail additionally loads four frontend-owned static assets listed in
`endpoints.json`. Its optional browser-console helper can call externally
configured mail and account-import services; those origins and credentials are
user-supplied and are not part of the AutoMyAI backend.

## 9. File library / text assets

The authenticated file library stores small UTF-8 text assets under the
project data volume. List responses contain metadata only; fetch an individual
item when the editor needs its content.

| Method and path | Request | Minimum response fields used by UI |
| --- | --- | --- |
| `GET main:/file-library` | — | `{items:[{id,name,sizeBytes,lineCount,sha256,createdAt,updatedAt}], total, maxFileBytes}` |
| `POST main:/file-library` | `{name,content}` | `{item}`; rejects duplicate names and unsupported/non-text files |
| `GET main:/file-library/{itemId}` | — | `{item:{id,name,content,sizeBytes,charCount,lineCount,sha256,createdAt,updatedAt}}` |
| `POST main:/file-library/{itemId}` | `{name?,content?}` | updated `{item}` |
| `DELETE main:/file-library/{itemId}` | — | `{deleted:true,item}` |

The current limit is 1 MiB per item. The editor uses the individual GET
response for viewing, copying and downloading; all routes require the admin
session.

## 9.1 Outlook 注册机

| Endpoint | Purpose |
| --- | --- |
| `GET main:/outlook-register/status` | status + recent logs + account preview |
| `GET main:/outlook-register/logs` | running logs |
| `GET main:/outlook-register/accounts` | recent 4-part accounts (`raw=1` includes full file) |
| `POST main:/outlook-register/start` | start register or `--fill-auth` job |
| `POST main:/outlook-register/stop` | stop current job |
| `POST main:/outlook-register/proxies` | save proxy pool text under `data/outlook_register/proxies.txt` |

UI entry: 侧边栏「工具与探针」→「Outlook 注册机」（`/ui/tools?sub=outlook_register`）。

## 10. Conversion and tools

| Method and path | Important request fields | Response |
| --- | --- | --- |
| `GET main:/extract/catalog` | — | Go method catalog, capability flags and batch limits |
| `GET main:/extract/jobs?limit=N` | — | recent batch summaries |
| `POST main:/extract/jobs` | `method`, `input`, `concurrency`, `options.proxy` (required), `options.promotionProxy` (optional second proxy); checkout-link methods may set `options.amountGate`, `options.amountThresholdMinor`, and `options.allowUnknownAmount` | accepted asynchronous Go batch and its initial `job` |
| `GET main:/extract/jobs/{jobId}` | — | full job, per-account items, steps, links and payment status |
| `DELETE main:/extract/jobs/{jobId}` | — | delete one terminal batch from persisted recent history |
| `POST main:/extract/jobs/{jobId}/cancel` | `{}` | updated cancelled job |
| `GET main:/extract-methods/catalog` | — | compatibility alias for the Go catalog |
| `POST main:/extract-methods/run` | `method`, token/JSON input, required request-scoped `proxy`, optional `promotionProxy`, country fields | compatibility batch creation response with `jobId` and `job` |
| `POST main:/convert/openai` | `input`, `target`, `namePrefix`, `planType` | conversion result shown/downloaded by UI; OpenAI RT may be supplied as one token per line and `target=sub2api` returns a `sub2api-data` bundle |
| Local converter page import/export | paste or load `.json/.txt`; choose `Sub2API` and click `导出 Sub2API JSON` | local `sub2api-data` v1 bundle; no remote Sub2API write |
| `POST main:/tools/chatgpt-promo-check` | page-selected checker input | checker result |
| `GET main:/grok/results` | — | Grok result object shown as JSON |

`ExtractionCenter` prefers the `/extract/jobs` batch contract and falls back
to `/extract-methods/catalog` plus `/extract-methods/run` when a deployed panel
does not yet expose the newer routes. The compatibility method `paper_card` is
shown as `direct_card` / “直卡” in the UI while the original method ID is kept
in requests to that adapter. PayPal BA remains the general/default method;
changing the country only changes the country, currency and browser profile.

For `ph_link` and `direct_card`, `amountGate` defaults to `strict_zero`.
Supported opt-in values are `at_most`, `at_least`, and `any_known`; thresholds
are supplied in Stripe minor units through `amountThresholdMinor`. Amounts that
cannot be read remain rejected unless `allowUnknownAmount` is explicitly true.

### PayPal protocol center

The `/ui/payments/center` page and `/paypal-protocol/api` service are separate
from extraction batches. The compatibility URL `/ui/payments?sub=center`
redirects to the standalone route.

| Endpoint | Input | Output |
| --- | --- | --- |
| `GET paypalProtocol:/status` | — | protocol scope, phases and service availability |
| `GET paypalProtocol:/countries` | — | complete country/region directory |
| `POST paypalProtocol:/prepare` | `{paypalUrl,country,locale,phone,proxies}` | validated protocol parameters and agreement URL |
UPI, MoMo, Kakao, PH, iDEAL and GoPay run only when their channel is selected
explicitly. Extraction never reads a saved/default proxy: the main proxy must
be supplied for each batch, with one optional Promotion-stage override. The UI
remembers these two proxy inputs independently for each channel while the page
is open, so switching back restores only values previously entered for that
channel. UPI results expose `upiPayload`, `upiInstructionUrl`, `qrPngUrl` and
`qrSvgUrl`; `longUrl` remains the best single fallback for older clients. If
the provider returns only `upiPayload`, the UI renders a local QR image from
that payload instead of requiring a separate upstream PNG/SVG URL.

## 11. Grok workspace

| Method and path | Request / response role |
| --- | --- |
| `GET main:/grok/registration/logs?tail=N` | registration log lines |
| `GET main:/grok/ttk/status` | current task/config status |
| `GET main:/grok/ttk/results` | `results` and `exports` |
| `GET main:/grok/ttk/export?name=...` | named export artifact |
| `GET main:/grok/ttk/traffic?tail=N` | recent traffic records |
| `POST main:/grok/ttk/config` | current form configuration; returns updated `config` |
| `POST main:/grok/ttk/start` | TTK task configuration |
| `POST main:/grok/ttk/stop` | `{}` |
| `POST main:/grok/signup/start` | Outlook-provider task configuration |
| `POST main:/grok/signup/stop` | `{}` |
| `POST main:/grok/ttk/sync` | `{targets:["grok2api"|"cpa"], pool?, autoNsfw?}` |
| `POST main:/convert/grok/import` | `{input,targets}` |
| `POST main:/grok/import/grok2api` | imported account payload selected by current UI |
| `GET main:/cpa/monitor/status` | CPA monitor status |
| `POST main:/cpa/monitor/check` | `{trigger,force?}` |

The current UI chooses `/grok/signup/start` for the Outlook provider and
`/grok/ttk/start` for other configured providers.

## 12. Legacy console-only API

These routes remain required until the legacy page is retired:

| Method and path | UI purpose |
| --- | --- |
| `GET main:/sub2api/compliance` | Sub2API readiness |
| `GET main:/sub2api/groups` | group selector and counts |
| `GET main:/sub2api/monitor/status` | monitor status |
| `POST main:/sub2api/monitor/check` | manual monitor check |
| `GET main:/sub2api/openai-auth-url` | create OAuth URL |
| `GET main:/purchase-settings` | purchase group settings |
| `POST main:/purchase-settings` | replace purchase group settings |
| `GET main:/purchase-catalog/countries` | country search/cache |
| `POST main:/purchase-catalog/countries/refresh` | refresh country cache |
| `GET main:/purchase-catalog/operators` | operator list by service/country |
| `GET main:/phones/pool?limit=N` | current phone pool |

## 13. Route families not used by the current pages

These routes are served but no shipped page calls them. A replacement UI is free
to use, ignore or retire them — but it must know they exist, because the backend
keeps answering them. Exact methods and paths are in
[`backend-routes.json`](./backend-routes.json).

| Family | Routes | What it is |
| --- | --- | --- |
| Public status | `GET main:/public/status`, `/public/lab-status`, `/public/cpa`, `/public/cpa-pool`, `/public/logs`, `/public/ttk/logs`; `POST main:/public/wake`, `/public/cpa/wake`, `/public/cpa-pool/wake` | Unauthenticated. Intended for a blog or status page, gated by `PUBLIC_STATUS_ENABLED` and an optional bearer token. `/cpa` and `/cpa-pool` are aliases of `/status`. |
| API index | `GET main:/api` | Self-description of the service; useful as a smoke check. |
| Runtime config | `GET main:/config` | `appSettings` plus purchase config in one read. |
| SMS activations | `GET main:/activations`, `/activations/latest`, `/activations/{id}`, `/activations/{id}/code`; `POST main:/activations`, `/activations/import`, `/activations/sync`, `/activations/{id}`, `/purchase` | HeroSMS/TeleAuto activation records behind the phone-verification flow. |
| Phone pool | `GET main:/phones/{id}`, `/phones/{id}/code`, `/phones/{id}/history`; `POST main:/phones/{id}` | Per-number reads and actions. `GET main:/phones/pool` (section 11) is the list view. |
| Purchase catalog | `GET main:/options`, `/country-lookup`, `/balance`, `/pricing`, `/catalog` | Service/country/operator lookups and HeroSMS balance. |
| Temp mail | `GET main:/temp-mail/settings`, `/temp-mail/address/{address}/mails`, `/temp-mail/address/{address}/mails/latest`; `POST main:/temp-mail/address`; `DELETE main:/temp-mail/address/{address}` | Temp-mail provider passthrough. `{address}` is URL-encoded in the path. The settings response includes `configured`, `providerConfigured`, and `adminConfigured`; an optional provider that is not configured returns HTTP 200 with an empty `settings` object. |
| Mail queue extras | `POST main:/email-queue/generate`, `/email-queue/allocation/result`; `GET main:/email-queue/platform-usage` | Queue generation and allocation bookkeeping. |
| Grok extras | `GET main:/grok/signup/status`, `/grok/signup/logs`, `/grok/ttk/config`, `/grok/ttk/logs`, `/grok/ttk/exports`; `POST main:/grok/import/sso`, `/grok/signup/result`, `/convert/grok` | Read-only compatibility routes plus the direct SSO import path. |
| Apple Mail | `GET main:/apple-mail/logs` | Log-only companion to `/apple-mail/status`. |
| OAuth import | `POST main:/sub2api/openai-callback` | Completes the OAuth flow started by `GET main:/sub2api/openai-auth-url`. |
| Retired | `ANY main:/codex-oauth*` | Always HTTP 410. Do not build against it. |

Two routes are defined twice. `GET main:/cpa/monitor/status` and
`POST main:/cpa/monitor/check` are served by `server.py`; the copies in
`extensions_api.py` are never reached and are marked `shadowedBy` in
`backend-routes.json`.

## 14. Fingerprint source settings

The settings page exposes two independent integrations:

- `OAI_FINGERPRINT_CLOUD_ENABLED` selects authorized cloud base records for
  UA/WebGL, device name and optional MAC data. Its base URL and protected JSON
  headers file are configured separately; the headers file path must refer to a
  mode-0600 file outside Git.
- `ROXY_OPENAPI_ENABLED` connects to the official Roxy desktop OpenAPI at the
  configured loopback URL. Its Key is stored in a separate mode-0600 file. This
  API manages Roxy workspaces and browser environments; it is not treated as a
  complete cloud fingerprint-record provider.

The Go fingerprint service additionally exposes authenticated local endpoints:

| Method and path | Purpose |
| --- | --- |
| `GET /roxy/openapi/status` | connectivity and authentication state |
| `GET /roxy/openapi/workspace` | Roxy workspace response |
| `GET /roxy/openapi/browser/detail?dirId=...` | one environment detail |
| `POST /roxy/openapi/random-env` | forward documented environment randomization flags without starting a browser |

## 15. Required UI states

Every replacement page must implement:

- initial loading;
- authenticated and unauthenticated states;
- empty lists/results;
- HTTP error with a retry action;
- queued, running, completed and failed task states;
- stale polling protection (do not overlap the same poll request);
- mobile layouts at 390px and desktop layouts at 1440px;
- safe rendering of server text (no unsanitized `innerHTML`).
