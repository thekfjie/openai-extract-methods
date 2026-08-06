"""Guards for the SMS domain extracted from server.py.

`integrations/sms.py` reads live runtime state that `server.py` owns. It does so
with deferred `from server import ...` inside function bodies, which is what makes
the split safe: `reload_runtime_config` rebinds CLIENT, STORE and the other
clients, and a module-scope import would freeze the pre-reload object.

These tests pin that arrangement, because breaking it fails silently — the code
keeps running against a stale client.
"""
from __future__ import annotations

import ast
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import server
from integrations import sms

ROOT = Path(__file__).resolve().parent.parent

# Anything reload_runtime_config rebinds must stay owned by server.py.
REBOUND_BY_RELOAD = {
    "APP_CONFIG_VALUES",
    "CLIENT",
    "TELE_AUTO",
    "TEMP_MAIL",
    "OUTLOOK_EMAIL",
    "OUTLOOK_EMAIL_ADMIN",
    "SUB2API",
    "STORE",
}


def server_tree() -> ast.Module:
    return ast.parse((ROOT / "server.py").read_text(encoding="utf-8"))


class ReboundGlobalTests(unittest.TestCase):
    def test_reload_rebinds_exactly_the_expected_globals(self) -> None:
        tree = server_tree()
        reload_fn = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "reload_runtime_config"
        )
        declared = {name for node in ast.walk(reload_fn) if isinstance(node, ast.Global) for name in node.names}
        self.assertEqual(
            declared,
            REBOUND_BY_RELOAD,
            "reload_runtime_config's global list changed; update REBOUND_BY_RELOAD and check that no "
            "domain module under integrations/ holds one of these at module scope",
        )

    def test_domain_modules_do_not_own_a_rebound_global(self) -> None:
        for name in REBOUND_BY_RELOAD:
            with self.subTest(name=name):
                owner = getattr(sms, name, None)
                self.assertIsNone(
                    owner,
                    f"integrations.sms defines {name} at module scope, but reload_runtime_config "
                    f"rebinds server.{name}; the module would keep the stale object",
                )

    def test_sms_follows_a_rebound_store(self) -> None:
        original = server.STORE
        sentinel = object()
        try:
            server.STORE = sentinel
            # A deferred import reads server.STORE at call time, so it sees the sentinel.
            with self.assertRaises(AttributeError):
                sms.list_local_tele_activations()
        finally:
            server.STORE = original
        self.assertIsInstance(sms.list_local_tele_activations(), list)


class SmsDomainSmokeTests(unittest.TestCase):
    """Each of these calls a moved function whose body reaches back into server."""

    def test_purchase_settings_are_readable(self) -> None:
        self.assertIsInstance(sms.get_purchase_settings(), dict)
        self.assertIsInstance(sms.get_purchase_config(), dict)

    def test_filters_fall_back_to_defaults(self) -> None:
        filters = sms.get_filters({})
        self.assertEqual(filters["serviceCode"], sms.DEFAULT_SERVICE_CODE)
        self.assertEqual(filters["serviceName"], sms.DEFAULT_SERVICE_NAME)

    def test_phone_pool_payload_shape(self) -> None:
        payload = sms.phone_pool_payload(limit=1)
        for key in ("items", "total", "limit", "updatedAt"):
            self.assertIn(key, payload)

    def test_server_re_exports_the_domain(self) -> None:
        for name in ("get_purchase_settings", "phone_pool_payload", "ActivationStore", "get_filters"):
            with self.subTest(name=name):
                self.assertIs(getattr(server, name), getattr(sms, name))


class TeleAutoQuotaTests(unittest.TestCase):
    def test_phone_proxy_history_never_rejects_a_number(self) -> None:
        old_binding = {"proxyUrl": "http://old.example:8080", "region": "US"}
        new_proxy = {"proxyUrl": "http://new.example:8080", "region": "KR"}
        with patch.object(sms, "get_phone_proxy_binding", return_value=old_binding):
            compatibility = sms.phone_proxy_compatibility("+1 555 000 1000", new_proxy)

        self.assertTrue(compatibility["allowed"])
        self.assertEqual(compatibility["binding"], old_binding)

    def test_purchase_skips_and_releases_a_phone_that_is_still_cooling(self) -> None:
        cooling_phone = "+1 555 000 1001"
        available_phone = "+1 555 000 1002"

        class FakeTeleAuto:
            configured = True

            def __init__(self) -> None:
                self.accounts = [
                    {
                        "id": "tele:cooling",
                        "phoneNumber": cooling_phone,
                        "publicUrl": "https://tele.example.test/?key=cooling",
                        "smsUrl": "https://tele.example.test/?key=cooling",
                    },
                    {
                        "id": "tele:available",
                        "phoneNumber": available_phone,
                        "publicUrl": "https://tele.example.test/?key=available",
                        "smsUrl": "https://tele.example.test/?key=available",
                    },
                ]
                self.released: list[str] = []

            def issue_account(self) -> dict[str, str]:
                return self.accounts.pop(0)

            def release_account(self, account: dict[str, str]) -> dict[str, str]:
                self.released.append(account["phoneNumber"])
                return {"result": "released"}

        class FakeStore:
            def __init__(self) -> None:
                self.records: dict[str, dict] = {}

            def upsert(self, record: dict) -> dict:
                current = self.records.get(str(record["id"]), {})
                current.update(record)
                self.records[str(record["id"])] = current
                return dict(current)

            def list(self) -> list[dict]:
                return list(self.records.values())

            def read_all(self) -> list[dict]:
                return self.list()

        tele = FakeTeleAuto()
        store = FakeStore()
        with tempfile.TemporaryDirectory() as temporary_directory:
            usage_path = Path(temporary_directory) / "phone_code_usage.json"
            usage_path.write_text(
                json.dumps(
                    {
                        "events": [
                            {
                                "phoneNumber": cooling_phone,
                                "phoneKey": sms.normalize_phone_key(cooling_phone),
                                "activationId": "tele:previous",
                                "code": "123456",
                                "eventKey": "tele:previous:123456",
                                "receivedAtTs": time.time(),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch.object(sms, "PHONE_CODE_USAGE_PATH", usage_path),
                patch.object(server, "TELE_AUTO", tele),
                patch.object(server, "STORE", store),
            ):
                result = sms.purchase_with_fallback({"provider": "tele-auto", "teleAttemptLimit": 2})

        self.assertEqual(result["item"]["phoneNumber"], available_phone)
        self.assertEqual(tele.released, [cooling_phone])
        self.assertEqual(result["attempts"][0]["quota"]["reason"], "window_limit")
        self.assertEqual(store.records["tele:cooling"]["status"], "released")
        self.assertIn("冷却", store.records["tele:cooling"]["statusLabel"])

    def test_third_code_marks_local_and_upstream_phone_sold(self) -> None:
        phone = "+1 555 000 1003"

        class FakeTeleAuto:
            def __init__(self) -> None:
                self.sold: list[tuple[str, str]] = []

            def sold_account(self, record: dict, reason: str = "") -> dict[str, str]:
                self.sold.append((record["phoneNumber"], reason))
                return {"result": "sold"}

        class FakeStore:
            def __init__(self) -> None:
                self.records = {
                    "tele:limit": {
                        "id": "tele:limit",
                        "phoneNumber": phone,
                        "publicUrl": "https://tele.example.test/?key=limit",
                        "smsUrl": "https://tele.example.test/?key=limit",
                        "teleAuto": True,
                        "status": "finished",
                    }
                }

            def read_all(self) -> list[dict]:
                return list(self.records.values())

            def upsert(self, record: dict) -> dict:
                current = self.records.get(str(record["id"]), {})
                current.update(record)
                self.records[str(record["id"])] = current
                return dict(current)

        tele = FakeTeleAuto()
        store = FakeStore()
        with tempfile.TemporaryDirectory() as temporary_directory:
            usage_path = Path(temporary_directory) / "phone_code_usage.json"
            old_ts = time.time() - 7200
            usage_path.write_text(
                json.dumps(
                    {
                        "events": [
                            {
                                "phoneNumber": phone,
                                "phoneKey": sms.normalize_phone_key(phone),
                                "activationId": f"tele:old-{index}",
                                "code": str(111110 + index),
                                "eventKey": f"tele:old-{index}:{111110 + index}",
                                "receivedAtTs": old_ts - index,
                            }
                            for index in range(2)
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch.object(sms, "PHONE_CODE_USAGE_PATH", usage_path),
                patch.object(server, "TELE_AUTO", tele),
                patch.object(server, "STORE", store),
            ):
                result = sms.record_phone_code_usage(phone, "tele:limit", "333333")
                lifecycle = sms.phone_lifecycle_status(phone)
                post_sale_quota = sms.phone_code_quota_status(phone, "tele:limit")

        self.assertTrue(result["recorded"])
        self.assertTrue(result["reachedTotalLimit"])
        self.assertTrue(result["allowed"])
        self.assertEqual(lifecycle["status"], "sold")
        self.assertFalse(post_sale_quota["allowed"])
        self.assertEqual(post_sale_quota["reason"], "sold")
        self.assertEqual(store.records["tele:limit"]["status"], "sold")
        self.assertEqual(tele.sold[0][0], phone)

    def test_tele_historical_count_is_carried_into_local_quota(self) -> None:
        phone = "+1 555 000 1005"
        activation_id = "tele:baseline"

        class FakeTeleAuto:
            def __init__(self) -> None:
                self.sold: list[tuple[str, str]] = []

            def sold_account(self, record: dict, reason: str = "") -> dict[str, str]:
                self.sold.append((record["phoneNumber"], reason))
                return {"result": "sold"}

        class FakeStore:
            def __init__(self) -> None:
                self.records = {
                    activation_id: {
                        "id": activation_id,
                        "phoneNumber": phone,
                        "publicUrl": "https://tele.example.test/?key=baseline",
                        "smsUrl": "https://tele.example.test/?key=baseline",
                        "teleAuto": True,
                        "teleSuccessCount": 2,
                        "teleLastUsedAt": "2026-08-01T00:00:00+00:00",
                        "status": "number_issued",
                    }
                }

            def read_all(self) -> list[dict]:
                return list(self.records.values())

            def upsert(self, record: dict) -> dict:
                current = self.records.get(str(record["id"]), {})
                current.update(record)
                self.records[str(record["id"])] = current
                return dict(current)

        tele = FakeTeleAuto()
        store = FakeStore()
        with tempfile.TemporaryDirectory() as temporary_directory:
            usage_path = Path(temporary_directory) / "phone_code_usage.json"
            usage_path.write_text(json.dumps({"events": [], "phones": {}}), encoding="utf-8")
            with (
                patch.object(sms, "PHONE_CODE_USAGE_PATH", usage_path),
                patch.object(server, "TELE_AUTO", tele),
                patch.object(server, "STORE", store),
            ):
                before = sms.phone_code_quota_status(phone, activation_id)
                result = sms.record_phone_code_usage(phone, activation_id, "444444")

        self.assertTrue(before["allowed"])
        self.assertEqual(before["total"], 2)
        self.assertEqual(before["teleBaselineTotal"], 2)
        self.assertTrue(result["reachedTotalLimit"])
        self.assertEqual(result["total"], 3)
        self.assertEqual(tele.sold[0][0], phone)

    def test_whatsapp_cooldown_expires_back_to_available(self) -> None:
        phone = "+1 555 000 1004"
        with tempfile.TemporaryDirectory() as temporary_directory:
            usage_path = Path(temporary_directory) / "phone_code_usage.json"
            with patch.object(sms, "PHONE_CODE_USAGE_PATH", usage_path):
                lifecycle = sms.mark_phone_cooldown(phone, "WhatsApp only", 60, source="self-maintained")
                quota = sms.phone_code_quota_status(phone)
                self.assertEqual(lifecycle["status"], "cooldown")
                self.assertEqual(quota["reason"], "whatsapp_cooldown")

                data = sms.load_phone_code_usage()
                data["phones"][sms.normalize_phone_key(phone)]["cooldownUntilTs"] = time.time() - 1
                data["phones"][sms.normalize_phone_key(phone)]["cooldownUntil"] = "2000-01-01T00:00:00+00:00"
                sms.save_phone_code_usage(data)
                recovered = sms.phone_code_quota_status(phone)

        self.assertTrue(recovered["allowed"])
        self.assertEqual(recovered["lifecycle"]["status"], "available")


if __name__ == "__main__":
    unittest.main()
