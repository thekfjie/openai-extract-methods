# AutoMyAI Frontend

`frontend/` contains every browser-facing UI shipped by this repository. It is
a static, framework-free application and does not import Python or Go code.

## Directory boundary

```text
frontend/
├── index.html                 # dashboard overview
├── auth/login.html            # administrator login
├── pages/                     # current multi-page console
├── legacy/control-panel.html  # previous single-page console
├── css/                       # UI-only styles
├── js/runtime-config.js       # public service locations; never secrets
├── js/api-client.js           # shared HTTP/auth SDK
├── js/app.js                  # shared navigation and UI helpers
└── docs/                      # page map and API contract
```

`docs/` holds three files with different jobs: `API_CONTRACT.md` explains the
endpoints, `endpoints.json` maps each existing page to the endpoints it calls,
and `backend-routes.json` is the generated inventory of every route the backend
serves. Regenerate the last one with `./scripts/gen-backend-routes.py`.

The backend owns API behavior, authentication, task execution and persistence.
It may serve this directory at `/ui/` for the current deployment, but the
frontend can also be hosted by any static web server.

## Runtime configuration

Load `js/runtime-config.js`, then `js/api-client.js`, before application code.
The config contains public locations only:

| Field | Default | Purpose |
| --- | --- | --- |
| `uiBase` | inferred (`/ui` or empty) | frontend route and asset base |
| `mainApiBase` | `/api` | Python control API |
| `paypalProtocolApiBase` | `/paypal-protocol/api` | Standalone PayPal protocol workbench API |
| `openai2UiBase` | `/openai2` | OpenAI 2 embedded/full-page UI |
| `openai2ApiBase` | `/openai2/api` | OpenAI 2 service API |
| `openai3UiBase` | `/openai3` | OpenAI 3 service root |
| `openai3ApiBase` | `/openai3/api` | Go Profile control API |
| `grok2Base` | `/grok2` | Grok 2 external panel route |
| `authMode` | `cookie` | `cookie` for same-origin; `header` for a separate development origin |
| `requestTimeoutMs` | `30000` | default request timeout |

Never put API keys, passwords, tokens or account data in runtime config.

## API SDK

```js
await AutoMyAIAPI.main.get('/health');
await AutoMyAIAPI.main.post('/settings', { TRAFFIC_METER_ENABLED: 'true' });
await AutoMyAIAPI.openai2.get('/health');
await AutoMyAIAPI.openai3.post('/tasks', { type: 'fingerprint-profile-generate', input: {} });
```

The SDK:

- joins service bases and paths;
- sends JSON with `credentials: include`;
- parses JSON and normalizes HTTP errors;
- applies a request timeout;
- redirects cookie sessions to the frontend login page after HTTP 401;
- supports an optional `X-Admin-Password` session header for cross-origin local development.

## Hosting modes

### Production / same origin

Recommended. Serve the frontend and proxy all service paths from one origin:

```text
/ui/*       -> static frontend
/api/*      -> Python backend
/openai2/*  -> OpenAI 2 service
/openai3/*  -> OpenAI 3 Go service
/grok2/*    -> Grok 2 service
```

Use `authMode: "cookie"`. The backend keeps the administrator session in an
HttpOnly cookie.

### Separate local frontend origin

The cleanest development setup is still a local reverse proxy so the browser
sees one origin. If that is unavailable:

1. set every service base to an absolute backend URL;
2. add the frontend origin to backend `CORS_ALLOWED_ORIGINS`;
3. set `authMode: "header"`;
4. serve `frontend/` as static files.

Header mode keeps the administrator password in `sessionStorage`, so it is
cleared when the tab session ends. It is intended for local development only.

## Rewriting the UI

When replacing the current HTML with a framework application:

1. keep `runtime-config.js` semantics or provide an equivalent environment layer;
2. keep service separation (`main`, `openai2`, `openai3`, `grok2`);
3. implement the authentication flow in `docs/API_CONTRACT.md`;
4. use `docs/endpoints.json` as the page-to-endpoint inventory, and
   `docs/backend-routes.json` when you need a route the current pages never call;
5. preserve loading, empty, error and running states for every polling panel;
6. do not expose values omitted by `/api/settings` or `/api/app-settings`.

Roughly half the `main` surface is not touched by the pages in this directory
(SMS activations, phone pool, temp mail, purchase catalog, public status). It is
still served. `API_CONTRACT.md` section 12 groups those families so a rewrite can
decide deliberately what to keep.

The current frontend polls status/log endpoints. A future UI may replace
polling with SSE or WebSocket only after the backend adds and documents that
transport; do not infer a streaming contract that does not exist.
