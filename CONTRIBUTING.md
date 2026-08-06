# Contributing

## Required workflow

1. Create a topic branch; do not develop directly on `main`.
2. Keep runtime data and credentials outside Git.
3. Run `make check` before committing.
4. Review `git diff` and `git status` before every commit.
5. Deploy only through `./scripts/automyai-compose.sh` and the tracked runtime templates.

Deploy one changed module with `./scripts/automyai-compose.sh deploy SERVICE`.
This builds and replaces only that service and never restarts its neighbours.
Use `build SERVICE` when an image should be validated without changing runtime.
Use `deploy-all` and `down-all` only for an intentional project-wide operation;
plain `down` is blocked to prevent accidental full outages.

## Port ownership

Project-owned ports live in `config/ports.env`. Do not introduce a hard-coded
listener port elsewhere. External services such as Sub2API, CPA, grok2api and
OutlookEmail keep their own configuration and are not renumbered here.

## Repository boundaries

- `server.py`, `extensions_api.py`: backend API and compatibility entrypoints.
  Routes are grouped into `AppHandler.handle_*_api(method, path, query) -> bool`
  methods listed in `AppHandler.API_ROUTE_GROUPS`. Add a route to an existing
  group, or register a new group there; an unregistered group is dead code and
  fails the tests. Run `./scripts/gen-backend-routes.py` after any route change.
- `integrations/`, `converters/`: reusable application code. Upstream service
  clients and domain logic belong in `integrations/` and must not import `server`
  at module scope; import what you need inside the function that needs it.
  That is not only about the import cycle: `reload_runtime_config` rebinds
  `CLIENT`, `TELE_AUTO`, `TEMP_MAIL`, `OUTLOOK_EMAIL`, `OUTLOOK_EMAIL_ADMIN`,
  `SUB2API`, `STORE` and `APP_CONFIG_VALUES`, so a module-scope import would keep
  serving the pre-reload object. `server.py` must stay the owner of those names;
  `tests/test_sms_domain.py` fails if one moves or if that list changes.
- `frontend/`: all browser UI, runtime service configuration and API contracts.
- `tools/`: integrated tools that are actually executed.
- `refs/`: read-only upstream references; application code must not import them.
- `deploy/`: tracked systemd and Nginx configuration.
- `config/`: non-secret shared configuration.
- `data/`, `logs/`, `.env`, `config.json`: local runtime state, never tracked.

Do not commit `.bak*`, browser profiles, virtual environments, generated
accounts, credentials, logs or copied dependency directories.

## Changes

- Prefer small, single-purpose commits.
- Add or update tests for behavior changes.
- Avoid copying an entire tool directory to make a variant; extract shared code
  or make differences configuration-driven.
- Do not enable automatic commit, rebase or push jobs.
