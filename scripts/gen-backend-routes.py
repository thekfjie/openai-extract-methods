#!/usr/bin/env python3
"""Generate the backend route inventory consumed by the frontend handoff docs.

The AutoMyAI API is dispatched by ordered `if` chains rather than a framework
router, so the only reliable inventory is the source itself. This script parses
`server.py` and `extensions_api.py` statically and writes
`frontend/docs/backend-routes.json`.

Run it after adding, renaming or deleting an API route:

    ./scripts/gen-backend-routes.py

`tests/test_api_routes.py` fails when the checked-in inventory drifts from the
source, so the file never silently goes stale.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server.py"
EXTENSIONS = ROOT / "extensions_api.py"
OUTPUT = ROOT / "frontend" / "docs" / "backend-routes.json"

# Route groups that run before the administrator check in AppHandler.handle_api.
UNAUTHENTICATED_GROUPS = {"handle_auth_api", "handle_public_status_api"}

GROUP_DESCRIPTIONS = {
    "handle_auth_api": "Administrator session",
    "handle_public_status_api": "Public status surface",
    "handle_grok_log_api": "Grok registration logs",
    "handle_system_api": "Health, runtime settings and API index",
    "handle_address_profiles_api": "Allow-listed remote address fixtures",
    "handle_file_library_api": "Administrator text-file library",
    "handle_paypal_protocol_api": "Payment protocol module inventory",
    "handle_browser_live_api": "Signup browser live view",
    "handle_mail_queue_api": "Signup email queue and OutlookEmail",
    "handle_temp_mail_api": "Temp-mail addresses",
    "handle_sub2api_api": "Sub2API, CPA monitor and OpenAI OAuth import",
    "handle_purchase_api": "Purchase settings and HeroSMS catalog",
    "handle_activation_api": "SMS activations",
    "handle_phone_api": "Phone pool",
    "handle_uc_signup_api": "OpenAI browser signup",
    "handle_apple_mail_api": "Apple Mail channel",
    "handle_outlook_register_api": "Outlook/Hotmail pure-protocol mailbox registration",
    "handle_extract_api": "Authenticated bridge to the loopback Go extraction service",
    "handle_extension_api": "Grok, converters, checkout-link extraction and mail policy extensions",
}

# `handle_extract_api` deliberately delegates the implementation to the
# loopback Go service, so its paths cannot be recovered from Python `if` chains.
# Keep the bridge contract explicit here; the Go service remains the runtime
# source of behavior and this table only feeds the checked-in handoff docs.
DELEGATED_ROUTES = {
    "handle_extract_api": [
        {"methods": ["GET"], "paths": ["/api/extract/catalog"]},
        {"methods": ["GET", "POST"], "paths": ["/api/extract/jobs"]},
        {"methods": ["GET", "DELETE"], "prefix": "/api/extract/jobs/"},
        {"methods": ["POST"], "prefix": "/api/extract/jobs/", "suffix": "/cancel"},
        {"methods": ["GET"], "paths": ["/api/extract-methods/catalog"]},
        {"methods": ["POST"], "paths": [
            "/api/extract-methods/run",
            "/api/long-link-task",
            "/api/extract-pp",
            "/api/paper-card-task",
            "/api/ph-link-task",
            "/api/momo-eligibility",
            "/api/kakao-long-link-task",
            "/api/upi-long-link-task",
            "/api/ideal-long-link-task",
            "/api/gopay-long-link-task",
        ]},
    ],
}


class UnsupportedCondition(Exception):
    """Raised when a route condition uses a shape this extractor cannot read."""


def _string(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _call_on(node: ast.AST, target: str, attribute: str) -> str | None:
    """Return the literal argument of `target.attribute("literal")`, else None."""
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    if node.func.attr != attribute:
        return None
    if not isinstance(node.func.value, ast.Name) or node.func.value.id != target:
        return None
    if len(node.args) != 1:
        return None
    return _string(node.args[0])


def _flatten_and(test: ast.AST) -> list[ast.AST]:
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        parts: list[ast.AST] = []
        for value in test.values:
            parts.extend(_flatten_and(value))
        return parts
    return [test]


def _parse_condition(test: ast.AST, public_status_paths: list[str]) -> dict[str, Any] | None:
    """Translate one route condition into an inventory entry, or None if it is not a route."""
    methods: list[str] = []
    paths: list[str] = []
    prefix = ""
    suffix = ""

    for part in _flatten_and(test):
        if isinstance(part, ast.Compare) and len(part.ops) == 1:
            left, op, right = part.left, part.ops[0], part.comparators[0]
            name = left.id if isinstance(left, ast.Name) else None
            if name == "method" and isinstance(op, ast.Eq):
                literal = _string(right)
                if literal is None:
                    raise UnsupportedCondition(ast.unparse(test))
                methods.append(literal)
                continue
            if name == "path" and isinstance(op, ast.Eq):
                literal = _string(right)
                if literal is None:
                    raise UnsupportedCondition(ast.unparse(test))
                paths.append(literal)
                continue
            if name in {"method", "path"} and isinstance(op, ast.In) and isinstance(right, (ast.Set, ast.Tuple, ast.List)):
                literals = [_string(element) for element in right.elts]
                if any(literal is None for literal in literals):
                    raise UnsupportedCondition(ast.unparse(test))
                (methods if name == "method" else paths).extend(literal for literal in literals if literal)
                continue
            return None

        literal = _call_on(part, "path", "startswith")
        if literal is not None:
            prefix = literal
            continue
        literal = _call_on(part, "path", "endswith")
        if literal is not None:
            suffix = literal
            continue

        # `is_public_status_path(path)` expands to the configured public path set.
        if (
            isinstance(part, ast.Call)
            and isinstance(part.func, ast.Name)
            and part.func.id == "is_public_status_path"
        ):
            paths.extend(public_status_paths)
            continue

        return None

    if not paths and not prefix:
        return None
    if not methods:
        methods = ["ANY"]

    entry: dict[str, Any] = {"methods": sorted(set(methods))}
    if paths:
        entry["paths"] = sorted(set(paths))
    if prefix:
        entry["prefix"] = prefix
    if suffix:
        entry["suffix"] = suffix
    return entry


def _public_status_paths(tree: ast.Module) -> list[str]:
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "PUBLIC_STATUS_PATHS":
                if isinstance(node.value, (ast.Set, ast.Tuple, ast.List)):
                    return sorted(
                        literal for literal in (_string(element) for element in node.value.elts) if literal
                    )
    return []


def _route_group_order(tree: ast.Module) -> list[str]:
    """Read AppHandler.API_ROUTE_GROUPS so the inventory follows real dispatch order."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != "AppHandler":
            continue
        for statement in node.body:
            if not isinstance(statement, ast.Assign):
                continue
            for target in statement.targets:
                if isinstance(target, ast.Name) and target.id == "API_ROUTE_GROUPS":
                    return [
                        literal
                        for literal in (_string(element) for element in statement.value.elts)
                        if literal
                    ]
    raise RuntimeError("AppHandler.API_ROUTE_GROUPS not found in server.py")


def _collect(path: Path, function_names: list[str], public_status_paths: list[str]) -> list[dict[str, Any]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    functions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    routes: list[dict[str, Any]] = []
    for name in function_names:
        node = functions.get(name)
        if node is None:
            raise RuntimeError(f"{path.name}: route group {name} not found")
        for statement in node.body:
            if not isinstance(statement, ast.If):
                continue
            entry = _parse_condition(statement.test, public_status_paths)
            if entry is None:
                continue
            entry["group"] = name
            entry["source"] = path.name
            entry["auth"] = "public" if name in UNAUTHENTICATED_GROUPS else "admin"
            routes.append(entry)
    return routes


def _delegated_collect(group_name: str, source: Path) -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []
    for template in DELEGATED_ROUTES.get(group_name, []):
        entry = dict(template)
        entry["group"] = group_name
        entry["source"] = source.name
        entry["auth"] = "public" if group_name in UNAUTHENTICATED_GROUPS else "admin"
        routes.append(entry)
    return routes


def build_inventory() -> dict[str, Any]:
    server_tree = ast.parse(SERVER.read_text(encoding="utf-8"))
    public_status_paths = _public_status_paths(server_tree)

    server_groups = ["handle_auth_api", "handle_public_status_api", *_route_group_order(server_tree)]
    routes = []
    for group_name in server_groups:
        routes.extend(_collect(SERVER, [group_name], public_status_paths))
        routes.extend(_delegated_collect(group_name, SERVER))
    routes += _collect(EXTENSIONS, ["handle_extension_api"], public_status_paths)

    # A path served by server.py is never reached in extensions_api: the server
    # groups run first. Mark the losers so nobody debugs an unreachable branch.
    server_exact = {
        (method, path)
        for route in routes
        if route["source"] == "server.py"
        for method in route["methods"]
        for path in route.get("paths", [])
    }
    for route in routes:
        if route["source"] != "extensions_api.py":
            continue
        pairs = {(method, path) for method in route["methods"] for path in route.get("paths", [])}
        if pairs and pairs <= server_exact:
            route["shadowedBy"] = "server.py"

    return {
        "version": 1,
        "generatedBy": "scripts/gen-backend-routes.py",
        "note": (
            "Complete inventory of the main service (/api) as dispatched by "
            "AppHandler.handle_api. Regenerate after changing a route; "
            "tests/test_api_routes.py fails when this file drifts."
        ),
        "dispatchOrder": server_groups + ["handle_extension_api"],
        "groupDescriptions": GROUP_DESCRIPTIONS,
        "publicStatusPaths": public_status_paths,
        "routes": routes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail instead of writing when the file is stale")
    args = parser.parse_args()

    payload = json.dumps(build_inventory(), ensure_ascii=False, indent=2) + "\n"
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != payload:
            raise SystemExit(f"{OUTPUT.relative_to(ROOT)} is stale; run ./scripts/gen-backend-routes.py")
        print(f"{OUTPUT.relative_to(ROOT)} is up to date")
        return

    OUTPUT.write_text(payload, encoding="utf-8")
    total = len(json.loads(payload)["routes"])
    print(f"wrote {OUTPUT.relative_to(ROOT)} with {total} route entries")


if __name__ == "__main__":
    main()
