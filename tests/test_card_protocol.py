from __future__ import annotations

import base64
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from integrations import account_run_guard
from integrations.card_protocol import (
    TASKS,
    TASKS_LOCK,
    card_protocol_status,
    inspect_card_checkout_context,
    load_card_elements_context,
    preflight_card_protocol_proxies,
    prepare_card_protocol,
    start_card_protocol_task,
)
from internal.cardprotocol.protocol.ph_shortlink_extractor import (
    Mode8Config,
    Mode8Extractor,
    resolved_checkout_amount,
    token_account_id,
)


class CardProtocolTests(unittest.TestCase):
    @staticmethod
    def fixture_token(account_id: str = "acct-shared-lock") -> str:
        payload = base64.urlsafe_b64encode(json.dumps({
            "https://api.openai.com/auth": {"chatgpt_account_id": account_id},
            "https://api.openai.com/profile": {"email": "shared-lock@example.com"},
        }).encode()).decode().rstrip("=")
        return f"fixture.{payload}.signature"

    def test_shared_account_guard_uses_account_id_and_releases(self) -> None:
        token = self.fixture_token()
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.object(
            account_run_guard, "ACCOUNT_RUN_LOCK_DIR", Path(temp_dir)
        ), mock.patch.object(account_run_guard, "ACCOUNT_RUN_GUARD_ENABLED", True):
            first = account_run_guard.acquire_account_run(token, "one", "提炼")
            with self.assertRaisesRegex(account_run_guard.AccountRunBusy, "ACCOUNT_ALREADY_RUNNING.*提炼"):
                account_run_guard.acquire_account_run(token + "-renewed-signature", "two", "纸卡协议")
            account_run_guard.release_account_run(first)
            second = account_run_guard.acquire_account_run(token, "three", "纸卡协议")
            account_run_guard.release_account_run(second)

    def test_direct_card_job_holds_guard_for_full_task(self) -> None:
        token = self.fixture_token("acct-direct-executor")
        started = threading.Event()
        finish = threading.Event()

        def held_prepare(_payload):
            started.set()
            finish.wait(2)
            return {"ok": True, "result": {"url": "https://chatgpt.com/checkout/openai_llc/oaics_fixture"}}

        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.object(
            account_run_guard, "ACCOUNT_RUN_LOCK_DIR", Path(temp_dir)
        ), mock.patch.object(account_run_guard, "ACCOUNT_RUN_GUARD_ENABLED", True), mock.patch(
            "integrations.card_protocol.prepare_card_protocol", side_effect=held_prepare
        ):
            first = start_card_protocol_task({"accessToken": token})
            self.assertTrue(started.wait(1))
            with self.assertRaisesRegex(account_run_guard.AccountRunBusy, "ACCOUNT_ALREADY_RUNNING"):
                start_card_protocol_task({"accessToken": token})
            finish.set()
            deadline = time.time() + 2
            while time.time() < deadline:
                with TASKS_LOCK:
                    status = str((TASKS.get(first["id"]) or {}).get("status") or "")
                if status == "ready":
                    break
                time.sleep(0.01)
            self.assertEqual(status, "ready")
            third = start_card_protocol_task({"accessToken": token})
            finish.set()
            self.assertTrue(third["id"].startswith("card-"))

    def test_internal_protocol_subtask_reuses_exact_parent_lease(self) -> None:
        token = self.fixture_token("acct-parent-lease")
        parent_id = "probe-parent-123"
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.object(
            account_run_guard, "ACCOUNT_RUN_LOCK_DIR", Path(temp_dir)
        ), mock.patch.object(account_run_guard, "ACCOUNT_RUN_GUARD_ENABLED", True), mock.patch(
            "integrations.card_protocol.prepare_card_protocol",
            return_value={"ok": True, "result": {"url": "https://chatgpt.com/checkout/openai_llc/oaics_fixture"}},
        ):
            parent = account_run_guard.acquire_account_run(token, parent_id, "加载卡片会话")
            with self.assertRaises(account_run_guard.AccountRunBusy):
                start_card_protocol_task({"accessToken": token, "accountRunLease": "wrong-parent"})
            child = start_card_protocol_task({"accessToken": token, "accountRunLease": parent_id})
            deadline = time.time() + 2
            while time.time() < deadline:
                with TASKS_LOCK:
                    status = str((TASKS.get(child["id"]) or {}).get("status") or "")
                if status == "ready":
                    break
                time.sleep(0.01)
            self.assertEqual(status, "ready")
            # The child adopted rather than released the parent's lock.
            with self.assertRaises(account_run_guard.AccountRunBusy):
                account_run_guard.acquire_account_run(token, "other-job", "其他支付任务")
            account_run_guard.release_account_run(parent)

    def test_status_exposes_official_checkout_workflow(self) -> None:
        status = card_protocol_status()
        self.assertTrue(status["ok"])
        self.assertTrue(status["checkoutGenerationAvailable"])
        self.assertTrue(status["officialCheckoutHandoffAvailable"])
        self.assertTrue(status["existingCheckoutContextAvailable"])
        self.assertTrue(status["protocolWorkspaceAvailable"])

    def test_existing_checkout_context_exposes_protocol_readiness(self) -> None:
        context = {
            "checkout_session_id": "oaics_existing_fixture",
            "publishable_key": "pk_live_fixture",
            "customer_session_client_secret": "cuss_secret_fixture",
            "payment_method_types": ["card", "link"],
            "setup_future_usage": "off_session",
            "confirm_return_url": "https://chatgpt.com/checkout/openai_ie/oaics_existing_fixture",
            "checkout_state": {
                "currency": "php",
                "country": "PH",
                "total": {"total": {"minorUnitsAmount": 0}},
            },
        }
        with mock.patch("integrations.card_protocol.Mode8Extractor") as extractor:
            extractor.return_value._session.return_value = mock.Mock()
            extractor.return_value._resolve_checkout_context.return_value = context
            result = inspect_card_checkout_context({
                "accessToken": "aaa.bbb.ccc",
                "checkoutUrl": "https://chatgpt.com/checkout/openai_ie/oaics_existing_fixture",
                "proxy": "proxy.test:8080",
                "billingDetails": {
                    "name": "Fixture User",
                    "email": "fixture@example.com",
                    "line1": "1 Test Street",
                    "city": "Manila",
                    "postalCode": "1000",
                    "country": "PH",
                },
                "cardSummary": {
                    "brand": "VISA",
                    "last4": "4242",
                    "panLength": 16,
                    "expiryMonth": "12",
                    "expiryYear": "30",
                    "cvcLength": 3,
                },
                "protocolOptions": {
                    "mode": "auto",
                    "paymentMethodType": "card",
                    "setupFutureUsage": "off_session",
                    "finalConcurrency": 5,
                },
            })
        self.assertEqual(result["status"], "prepared")
        self.assertEqual(result["amountDisplay"], "₱0.00")
        self.assertTrue(result["cardSupported"])
        self.assertTrue(result["publishableKeyReady"])
        self.assertTrue(result["customerSessionReady"])
        self.assertTrue(result["billing"]["ready"])
        self.assertEqual(result["protocol"]["mode"], "setup")
        self.assertTrue(result["protocol"]["materialsReady"])
        self.assertEqual(result["protocol"]["card"]["last4"], "4242")
        self.assertEqual(result["protocol"]["finalConcurrency"], 5)
        self.assertNotIn("publishable_key", result)
        self.assertNotIn("customer_session_client_secret", result)

    def test_elements_context_exposes_only_public_stripe_key(self) -> None:
        context = {
            "publishable_key": "pk_test_fixture",
            "customer_session_client_secret": "cuss_secret_fixture",
            "payment_method_types": ["card"],
            "checkout_state": {"currency": "php", "total": {"total": {"minorUnitsAmount": 0}}},
        }
        with mock.patch("integrations.card_protocol.Mode8Extractor") as extractor:
            extractor.return_value._session.return_value = mock.Mock()
            extractor.return_value._resolve_checkout_context.return_value = context
            result = load_card_elements_context({
                "accessToken": "aaa.bbb.ccc",
                "checkoutUrl": "https://chatgpt.com/checkout/openai_ie/oaics_elements_fixture",
                "proxy": "proxy.test:8080",
            })
        self.assertEqual(result["elements"]["publishableKey"], "pk_test_fixture")
        self.assertNotIn("customer_session_client_secret", result)

    def test_nonmatching_amount_rebuilds_until_gate_matches(self) -> None:
        responses = [
            {"ok": True, "checkout_id": "oaics_nonzero_fixture", "processor_entity": "openai_llc", "country": "PH", "currency": "PHP", "amount": "982.14", "amount_source": "total", "context_verified": True, "url": "https://chatgpt.com/checkout/openai_llc/oaics_nonzero_fixture"},
            {"ok": True, "checkout_id": "oaics_zero_fixture00", "processor_entity": "openai_llc", "country": "PH", "currency": "PHP", "amount": "0", "amount_source": "total", "context_verified": True, "url": "https://chatgpt.com/checkout/openai_llc/oaics_zero_fixture00"},
        ]
        with mock.patch("integrations.card_protocol.Mode8Extractor") as extractor:
            extractor.return_value.run.side_effect = responses
            result = prepare_card_protocol({
                "accessToken": "aaa.bbb.ccc",
                "proxyPool1": "proxy.test:8080",
                "proxyPool2": "proxy.test:8081",
                "amountGate": "strict_zero",
                "maxAttempts": 2,
            })
        self.assertEqual(result["attempt"], 2)
        self.assertEqual(result["result"]["amount"], "0")
        self.assertEqual(result["result"]["amountMinor"], 0)
        self.assertEqual(result["result"]["country"], "PH")

    def test_unknown_amount_is_rejected_by_default(self) -> None:
        response = {"ok": True, "checkout_id": "oaics_unknown_fixture", "processor_entity": "openai_llc", "country": "PH", "currency": "PHP", "amount": "unknown", "amount_source": "", "context_verified": True, "url": "https://chatgpt.com/checkout/openai_llc/oaics_unknown_fixture"}
        with mock.patch("integrations.card_protocol.Mode8Extractor") as extractor:
            extractor.return_value.run.return_value = response
            with self.assertRaisesRegex(ValueError, "金额未知"):
                prepare_card_protocol({
                    "accessToken": "aaa.bbb.ccc",
                    "proxyPool1": "proxy.test:8080",
                    "proxyPool2": "proxy.test:8081",
                    "maxAttempts": 1,
                })

    def test_integer_php_amount_is_treated_as_minor_units(self) -> None:
        response = {"ok": True, "checkout_id": "oaics_minor_fixture0", "processor_entity": "openai_llc", "country": "PH", "currency": "PHP", "amount": "98214", "amount_source": "total_summary.due", "context_verified": True, "url": "https://chatgpt.com/checkout/openai_llc/oaics_minor_fixture0"}
        with mock.patch("integrations.card_protocol.Mode8Extractor") as extractor:
            extractor.return_value.run.return_value = response
            result = prepare_card_protocol({
                "accessToken": "aaa.bbb.ccc",
                "proxyPool1": "proxy.test:8080",
                "proxyPool2": "proxy.test:8081",
                "amountGate": "at_most",
                "amountThreshold": "1000",
                "maxAttempts": 1,
            })
        self.assertEqual(result["result"]["amountDisplay"], "₱982.14")
        self.assertEqual(result["result"]["amountMinor"], 98214)

    def test_resolved_checkout_amount_uses_authoritative_minor_units(self) -> None:
        amount, source, currency = resolved_checkout_amount({
            "checkout_state": {
                "currency": "php",
                "total": {"total": {"minorUnitsAmount": 0}},
            },
            "total_summary": {"due": 98214},
        })
        self.assertEqual(str(amount), "0")
        self.assertEqual(source, "checkout_state.total.total.minorUnitsAmount")
        self.assertEqual(currency, "php")

    def test_resolved_checkout_amount_does_not_invent_zero(self) -> None:
        amount, source, currency = resolved_checkout_amount({
            "checkout_state": {"currency": "php", "total": {}},
        })
        self.assertEqual(str(amount), "0")
        self.assertEqual(source, "")
        self.assertEqual(currency, "php")

    def test_token_account_id_reads_nested_auth_claim(self) -> None:
        import base64
        import json

        payload = base64.urlsafe_b64encode(json.dumps({
            "https://api.openai.com/auth": {"chatgpt_account_id": "acct_fixture"},
        }).encode()).decode().rstrip("=")
        self.assertEqual(token_account_id(f"header.{payload}.signature"), "acct_fixture")

    def test_extractor_assigns_one_persistent_device_identity(self) -> None:
        extractor = Mode8Extractor(Mode8Config(token="aaa.bbb.ccc"))
        self.assertEqual(extractor.config.device_id, "")
        with mock.patch.object(extractor, "_session") as session, mock.patch.object(
            extractor, "_create_checkout"
        ) as create, mock.patch.object(extractor, "_update_checkout") as update, mock.patch.object(
            extractor, "_resolve_checkout_context"
        ) as resolve:
            fake = mock.Mock()
            session.return_value = fake
            create.return_value = {
                "checkout_session_id": "oaics_device_fixture",
                "processor_entity": "openai_ie",
            }
            update.return_value = {
                "checkout_session_id": "oaics_device_fixture",
                "processor_entity": "openai_ie",
            }
            resolve.return_value = {
                "checkout_session_id": "oaics_device_fixture",
                "payment_method_types": ["card"],
                "checkout_state": {
                    "currency": "php",
                    "total": {"total": {"minorUnitsAmount": 0}},
                },
            }
            result = extractor.run("proxy.test:8080", "proxy.test:8081")
        self.assertRegex(extractor.config.device_id, r"^[0-9a-f-]{36}$")
        self.assertEqual(result["checkout_device_id"], extractor.config.device_id)
        self.assertIn("checkout_user_agent", result)

    def test_optional_local_snapshot_identity_inputs_are_forwarded(self) -> None:
        response = {"ok": True, "checkout_id": "oaics_identity_fixture", "processor_entity": "openai_ie", "country": "PH", "currency": "PHP", "amount": "0", "amount_source": "checkout_state.total.total.minorUnitsAmount", "context_verified": True, "url": "https://chatgpt.com/checkout/openai_ie/oaics_identity_fixture"}
        with mock.patch("integrations.card_protocol.Mode8Extractor") as extractor:
            extractor.return_value.run.return_value = response
            prepare_card_protocol({
                "accessToken": "aaa.bbb.ccc",
                "proxyPool1": "proxy.test:8080",
                "proxyPool2": "proxy.test:8081",
                "accountId": "acct_fixture",
                "deviceId": "device-fixture",
                "sessionTraceId": "session-fixture",
                "userAgent": "Fixture Browser",
                "sessionCookies": '{"oai-test":"cookie-fixture"}',
                "maxAttempts": 1,
            })
        config = extractor.call_args.args[0]
        self.assertEqual(config.account_id, "acct_fixture")
        self.assertEqual(config.device_id, "device-fixture")
        self.assertEqual(config.chatgpt_session_id, "session-fixture")
        self.assertEqual(config.user_agent, "Fixture Browser")
        self.assertEqual(config.session_cookies, {"oai-test": "cookie-fixture"})

    def test_proxy_preflight_requires_us_and_tr_exits(self) -> None:
        with mock.patch("integrations.card_protocol._proxy_country") as country:
            country.side_effect = lambda proxy, timeout: "US" if "one" in proxy else "TR"
            result = preflight_card_protocol_proxies({
                "proxyPool1": "one.test:8080",
                "proxyPool2": "two.test:8081",
            })
        self.assertTrue(result["ok"])
        self.assertEqual(result["pool1"]["valid"], 1)
        self.assertEqual(result["pool2"]["valid"], 1)
        self.assertTrue(result["regionOk"])

    def test_proxy_preflight_keeps_reachable_region_mismatch(self) -> None:
        with mock.patch("integrations.card_protocol._proxy_country") as country:
            country.side_effect = lambda proxy, timeout: "US" if "one" in proxy else "SY"
            result = preflight_card_protocol_proxies({
                "proxyPool1": "one.test:8080",
                "proxyPool2": "two.test:8081",
            })
        self.assertTrue(result["ok"])
        self.assertFalse(result["regionOk"])
        self.assertEqual(result["pool2"]["reachable"], 1)
        self.assertEqual(result["pool2"]["regionMatched"], 0)
        self.assertEqual(result["pool2"]["validProxies"], ["http://two.test:8081"])


if __name__ == "__main__":
    unittest.main()
