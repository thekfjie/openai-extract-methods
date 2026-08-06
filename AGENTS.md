# AutoMyAI repository rules

- Run `./scripts/check.sh` before handing off a change.
- Use `config/ports.env` as the only source of project-owned port numbers.
- Use `./scripts/automyai-compose.sh`; never mix rootless and system Docker.
- Keep secrets and runtime files out of Git and Docker image layers.
- Do not edit `/etc/nginx` or systemd units without updating `deploy/` first.
- Preserve `refs/` as read-only upstream material; runtime imports must not use it.
- Do not add backup copies such as `.bak`, `_old`, `_copy` or numbered tool trees.
- Prefer extracting modules from `server.py` over adding more unrelated logic to it.
- Add API routes to an `AppHandler.handle_*_api` group and register new groups in
  `AppHandler.API_ROUTE_GROUPS`; then run `./scripts/gen-backend-routes.py`.
- UI changes must remain usable at 390px and 1440px widths.
