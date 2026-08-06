"""Guards for the HTTP route surface handed to the frontend.

`server.py` dispatches with ordered `if` chains instead of a framework router,
so these tests stand in for a route table: they keep the generated inventory in
sync with the source, keep every route group reachable, and keep the page/endpoint
contract pointing at routes that actually exist.
"""
from __future__ import annotations

import ast
import json
import sys
import unittest
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import importlib.util

_spec = importlib.util.spec_from_file_location("gen_backend_routes", ROOT / "scripts" / "gen-backend-routes.py")
assert _spec and _spec.loader
gen_backend_routes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen_backend_routes)

INVENTORY_PATH = ROOT / "frontend" / "docs" / "backend-routes.json"
ENDPOINTS_PATH = ROOT / "frontend" / "docs" / "endpoints.json"


def load_inventory() -> dict:
    return json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))


def route_matches(route: dict, method: str, path: str) -> bool:
    if "ANY" not in route["methods"] and method not in route["methods"]:
        return False
    if path in route.get("paths", []):
        return True
    prefix = route.get("prefix")
    if not prefix or not path.startswith(prefix):
        return False
    suffix = route.get("suffix")
    return not suffix or path.endswith(suffix)


class BackendRouteInventoryTests(unittest.TestCase):
    def test_inventory_matches_source(self) -> None:
        self.assertEqual(
            gen_backend_routes.build_inventory(),
            load_inventory(),
            "frontend/docs/backend-routes.json is stale; run ./scripts/gen-backend-routes.py",
        )

    def test_every_route_group_is_dispatched(self) -> None:
        tree = ast.parse((ROOT / "server.py").read_text(encoding="utf-8"))
        handler = next(
            node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == "AppHandler"
        )
        defined = {
            node.name
            for node in handler.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("handle_") and node.name.endswith("_api")
        }
        dispatched = set(gen_backend_routes._route_group_order(tree))
        # handle_api is the dispatcher itself; the other two run before the
        # administrator check, so handle_api calls them directly.
        pre_auth = {"handle_api", "handle_auth_api", "handle_public_status_api"}

        self.assertTrue(dispatched <= defined, f"API_ROUTE_GROUPS names missing methods: {dispatched - defined}")
        self.assertEqual(
            defined - dispatched - pre_auth,
            set(),
            "route group method is never dispatched; add it to AppHandler.API_ROUTE_GROUPS",
        )

    def test_public_routes_do_not_require_admin(self) -> None:
        public = {
            path
            for route in load_inventory()["routes"]
            if route["auth"] == "public"
            for path in route.get("paths", [])
        }
        self.assertIn("/api/auth/login", public)
        self.assertIn("/api/public/status", public)
        # Anything else public would be an accidental authentication bypass.
        unexpected = {path for path in public if not path.startswith(("/api/auth/", "/api/public/"))}
        self.assertEqual(unexpected, set(), f"unexpected unauthenticated routes: {sorted(unexpected)}")


class FrontendContractTests(unittest.TestCase):
    def test_page_endpoints_exist_in_backend(self) -> None:
        contract = json.loads(ENDPOINTS_PATH.read_text(encoding="utf-8"))
        base = contract["services"]["main"]["defaultBase"]
        routes = load_inventory()["routes"]

        missing: list[str] = []
        for page, calls in contract["pages"].items():
            for method, service, endpoint in calls:
                if service != "main":
                    continue  # owned by a separate service, not by this repository
                path = base + urlparse(endpoint).path
                if not any(route_matches(route, method, path) for route in routes):
                    missing.append(f"{page}: {method} {path}")

        self.assertEqual(missing, [], "frontend calls endpoints the backend does not serve:\n" + "\n".join(missing))


if __name__ == "__main__":
    unittest.main()
