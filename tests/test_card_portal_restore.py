import ast
import contextlib
import base64
import fcntl
import hashlib
import io
import json
import random
import re
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PORTAL = ROOT / "internal" / "cardprotocol" / "portal"

class CardPortalRestoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_source = (PORTAL / "app.py").read_text()
        cls.bind_source = (PORTAL / "card_bind_session.py").read_text()
        cls.default_source = (PORTAL / "card_set_default.py").read_text()

    @classmethod
    def source_function(cls, source: str, name: str, namespace=None):
        tree = ast.parse(source)
        node = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == name)
        values = dict(namespace or {})
        exec(compile(ast.Module(body=[node], type_ignores=[]), "<restored-card-function>", "exec"), values)
        return values[name]

    def test_helpers_report_missing_account_api_without_bad_hostname(self):
        for source in (self.bind_source, self.default_source):
            main = self.source_function(source, "main", {"ACCOUNT_API_BASE": "", "json": json})
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                return_code = main()
            payload = json.loads(output.getvalue().strip().splitlines()[-1])
            self.assertEqual(return_code, 11)
            self.assertEqual(payload["error"], "ACCOUNT_API_BASE_MISSING")
            self.assertNotIn("Bad hostname", output.getvalue())

    def test_missing_legacy_account_archive_is_optional(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = str(Path(temp_dir) / "success_accounts.jsonl")
            load_account = self.source_function(self.default_source, "load_account", {"ACCOUNT_FILE": missing, "Path": Path, "json": json})
            self.assertIsNone(load_account("user@example.com"))

    def test_local_tax_free_billing_is_complete(self):
        auto_billing = self.source_function(self.bind_source, "auto_us_billing", {"random": random})
        billing = auto_billing("person@example.com")
        self.assertTrue(billing["name"])
        self.assertEqual(billing["email"], "person@example.com")
        self.assertTrue(billing["phone"])
        address = billing["address"]
        self.assertEqual(address["country"], "US")
        for key in ("line1", "city", "state", "postal_code"):
            self.assertTrue(address[key], key)

    def test_configuration_error_does_not_start_probe(self):
        start = self.app_source.index("def card_bind_session():")
        end = self.app_source.index("\n@app.get(\"/\")", start)
        route = self.app_source[start:end]
        config_branch = route.index('if payload.get("error") == "ACCOUNT_API_BASE_MISSING":')
        probe_start = route.index("_start_key_probe(")
        self.assertLess(config_branch, probe_start)
        self.assertIn("return card_helper_error_response(payload)", route[config_branch:probe_start])
        self.assertIn('"account_api_configured": bool(CARD_ACCOUNT_API_BASE)', self.app_source)

    def test_probe_keeps_submitted_billing_details(self):
        normalize = self.source_function(self.app_source, "normalize_billing_details")
        billing = normalize({
            "name": "Card Holder",
            "email": "person@example.com",
            "phone": "+19075550123",
            "address": {
                "line1": "750 West 5th Avenue",
                "city": "Anchorage",
                "state": "AK",
                "postal_code": "99501",
                "country": "US",
            },
        })
        self.assertEqual(billing["name"], "Card Holder")
        self.assertEqual(billing["email"], "person@example.com")
        self.assertEqual(billing["address"]["postal_code"], "99501")
        self.assertIn('"_billing_details": dict(billing_details or {})', self.app_source)
        self.assertIn('refreshed["billing_details"] = dict(job["_billing_details"])', self.app_source)

    def test_account_run_lock_rejects_duplicate_account_then_releases(self):
        class Busy(RuntimeError):
            pass

        account_key = self.source_function(self.app_source, "_account_run_key", {
            "base64": base64, "hashlib": hashlib, "json": json,
        })
        namespace = {
            "_account_run_key": account_key, "AccountRunBusy": Busy,
            "fcntl": fcntl, "hashlib": hashlib, "json": json,
            "os": __import__("os"), "time": time,
        }
        acquire = self.source_function(self.app_source, "_acquire_account_run", namespace)
        acquire.__globals__["ACCOUNT_RUN_GUARD_ENABLED"] = True
        release = self.source_function(self.app_source, "_release_account_run", {"fcntl": fcntl})
        payload = base64.urlsafe_b64encode(json.dumps({
            "https://api.openai.com/auth": {"chatgpt_account_id": "acct-lock-fixture"},
            "https://api.openai.com/profile": {"email": "lock@example.com"},
        }).encode()).decode().rstrip("=")
        token = "eyJfixture." + payload + "." + "x" * 40
        with tempfile.TemporaryDirectory() as temp_dir:
            acquire.__globals__["ACCOUNT_RUN_LOCK_DIR"] = Path(temp_dir)
            first = acquire(token, "job-one", "iDEAL")
            with self.assertRaisesRegex(Busy, "ACCOUNT_ALREADY_RUNNING.*iDEAL"):
                acquire(token, "job-two", "纸卡协议")
            release(first)
            second = acquire(token, "job-three", "纸卡协议")
            release(second)

        self.assertIn("AUTOMYAI_ACCOUNT_RUN_LOCK_DIR", self.app_source)
        self.assertIn("AUTOMYAI_ACCOUNT_RUN_GUARD", self.app_source)
        self.assertIn('"code": "ACCOUNT_ALREADY_RUNNING"', self.app_source)
        self.assertIn('"error": str(exc)', self.app_source)

    def test_account_run_guard_has_runnable_opt_out(self):
        acquire = self.source_function(self.app_source, "_acquire_account_run", {
            "ACCOUNT_RUN_GUARD_ENABLED": False,
        })
        self.assertIsNone(acquire("unused", "unused", "unused"))

    def test_checkout_context_fallback_resolves_missing_stripe_key(self):
        protocol_source = (PORTAL / "ph_checkout_protocol.py").read_text()
        self.assertIn("def _resolve_initialized_checkout_context", self.app_source)
        self.assertIn("CARD_CHECKOUT_CONTEXT_HELPER", self.app_source)
        self.assertIn('result.get("checkout_url")', self.app_source)
        self.assertIn('result.get("checkoutId")', self.app_source)
        self.assertIn('for attempt in range(1, 17):', self.app_source)
        self.assertIn('account_run_lease_id=probe_id', self.app_source)
        self.assertIn('"accountRunLease": str(request_payload.get("account_run_lease_id")', self.app_source)
        self.assertIn('display_error = str(exc)', self.app_source)
        self.assertIn('error=display_error[:500]', self.app_source)
        self.assertIn('https://chatgpt.com/checkout/{processor}/{session_id}', protocol_source)
        self.assertNotIn('<REPLACE_ME>checkout/{processor}/{session_id}', protocol_source)

    def test_hosted_checkout_url_normalizes_to_real_chatgpt_host(self):
        normalize = self.source_function(
            self.app_source,
            "_normalize_protocol_checkout_url",
            {"urlparse": __import__("urllib.parse", fromlist=["urlparse"]).urlparse, "re": re, "validate_short_url": lambda value: (value, "openai_llc", "unused")},
        )
        session_id = "cs_live_1234567890abcdef"
        url, processor, normalized_session = normalize(f"https://pay.openai.com/c/pay/{session_id}")
        self.assertEqual(url, f"https://chatgpt.com/checkout/openai_llc/{session_id}")
        self.assertEqual(processor, "openai_llc")
        self.assertEqual(normalized_session, session_id)
        self.assertNotIn('return f"<REPLACE_ME>checkout/openai_llc/{session_id}"', self.app_source)

    def test_final_payment_runtime_uses_real_payment_methods_host(self):
        source = (PORTAL / "standalone_protocol_pay.py").read_text()
        self.assertIn('"https://chatgpt.com/backend-api/payments/payment_methods"', source)
        self.assertIn('"Referer":"https://chatgpt.com/"', source)
        self.assertNotIn('http.get("<REPLACE_ME>backend-api/payments/payment_methods"', source)

    def test_batch_payment_releases_prepared_subset_after_prepare(self):
        source = (ROOT / "frontend" / "src" / "pages" / "PaymentCenter.jsx").read_text()
        self.assertIn("if (!prepared.length)", source)
        self.assertIn("个准备失败但不阻断", source)
        self.assertIn("confirmProtocolBatch(prepared.map((item) => item.jobID))", source)
        self.assertNotIn("if (prepared.length !== ready.length)", source)
        self.assertNotIn("本轮未放行任何最终支付", source)
        self.assertNotIn("准备完成，因其他账号失败未提交", source)
        self.assertIn("wrong_version_number", source.lower())

    def test_one_account_final_confirmation_runs_once(self):
        import importlib.util
        import sys
        import types

        portal_path = str(PORTAL)
        if portal_path not in sys.path:
            sys.path.insert(0, portal_path)
        spec = importlib.util.spec_from_file_location("standalone_protocol_pay_test", PORTAL / "standalone_protocol_pay.py")
        module = importlib.util.module_from_spec(spec)
        previous_protocol = sys.modules.get("ph_checkout_protocol")
        sys.modules["ph_checkout_protocol"] = types.SimpleNamespace()
        try:
            spec.loader.exec_module(module)
        finally:
            if previous_protocol is None:
                sys.modules.pop("ph_checkout_protocol", None)
            else:
                sys.modules["ph_checkout_protocol"] = previous_protocol
        calls = []
        module.confirm = lambda prepared: calls.append(dict(prepared)) or {"status": "success"}
        result = module.confirm_burst({"protocol_payload": {"confirmation_token": "ctoken_test"}}, 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["burst_count"], 1)
        self.assertEqual(result["burst_attempt"], 1)
        standalone_source = (PORTAL / "standalone_protocol_pay.py").read_text()
        self.assertIn('"card_retry_count":0', standalone_source)
        self.assertIn('try:burst_count=1', self.app_source)

    def test_checkout_confirm_block_remains_a_terminal_error(self):
        frontend = (ROOT / "frontend" / "src" / "pages" / "PaymentCenter.jsx").read_text()
        protocol = (PORTAL / "ph_checkout_protocol.py").read_text()
        self.assertNotIn("_is_checkout_browser_handoff", self.app_source)
        self.assertNotIn("_checkout_browser_handoff_result", self.app_source)
        self.assertNotIn("_mark_checkout_browser_handoff", self.app_source)
        self.assertNotIn("BROWSER_HANDOFF_REQUIRED", protocol)
        self.assertNotIn("等待官方 Checkout 确认", frontend)
        self.assertIn("OPENAI_CONFIRM_BLOCKED", protocol)

    def test_checkout_context_jsonl_repairs_literal_newline_writer(self):
        reader = self.source_function(self.app_source, "_read_checkout_context_rows", {"Path": Path, "json": json, "CHECKOUT_CONTEXT_PATH": Path("missing")})
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "contexts.jsonl"
            rows = [{"checkout_session_id": "oaics_first"}, {"checkout_session_id": "oaics_second"}]
            target.write_text("\\n".join(json.dumps(item) for item in rows) + "\\n")
            self.assertEqual([item["checkout_session_id"] for item in reader(target)], ["oaics_first", "oaics_second"])
        self.assertIn('+ "\\n" for item in rows[-1000:]', self.app_source)
        self.assertNotIn('+ "\\\\n" for item in rows[-1000:]', self.app_source)

    def test_final_payment_preserves_extended_sticky_proxy_identity(self):
        affinity = self.source_function(self.app_source, "_proxy_affinity_key", {"normalize_user_proxy": lambda value: value, "re": re})
        original = "http://user-session-abc-t-10:pass@proxy.example:8080"
        extended = "http://user-session-abc-t-120:pass@proxy.example:8080"
        other = "http://user-session-other-t-120:pass@proxy.example:8080"
        self.assertEqual(affinity(original), affinity(extended))
        self.assertNotEqual(affinity(original), affinity(other))
        self.assertIn('append_card_audit("protocol-pay",stage="同步最后支付",status="failed"', self.app_source)

    def test_direct_card_page_contract(self):
        source = (ROOT / "frontend" / "src" / "pages" / "PaymentCenter.jsx").read_text()
        css = (ROOT / "frontend" / "src" / "index.css").read_text()
        exported = source[source.index("export default function PaymentCenter()") :]
        direct = source[source.index("function CardBindLinkWorkspace()") :]
        self.assertIn("payment-center-mode-switch", exported)
        self.assertIn('role="tablist"', exported)
        self.assertIn("PP 协议支付", exported)
        self.assertIn("直卡协议", exported)
        self.assertNotIn("CDK", direct)
        self.assertNotIn("classic-sticky-progress", source + css)
        self.assertNotIn("const { accessToken: _discarded, ...preferences } = form;", direct)
        self.assertIn("const preparedResults = await Promise.all(ready.map(async (row) =>", direct)
        self.assertIn("defer_confirm: true", direct)
        self.assertIn("confirmProtocolBatch(prepared.map((item) => item.jobID))", direct)
        self.assertIn("if (!prepared.length)", direct)
        self.assertIn("个准备失败但不阻断", direct)
        self.assertNotIn("if (prepared.length !== ready.length)", direct)
        self.assertNotIn("本轮未放行任何最终支付", direct)
        self.assertNotIn("for (let index = 0; index < ready.length; index += 1)", direct)
        self.assertIn("cardFlowState: 'automyai.card.unified.card-flow-state.v2'", source)
        self.assertIn("storeBrowserValue(browserStorageKeys.protocolForm, form)", direct)
        self.assertIn("previousTokenSignatureRef.current === tokenSignature", direct)
        self.assertNotIn("首个 AT 或 US 代理已变化", direct)
        self.assertIn('data-payment-mode="synchronized-batch"', direct)
        self.assertIn("同步执行最后支付", direct)
        self.assertIn("const [paymentBusy, setPaymentBusy]", direct)
        self.assertIn("const batchRunningAtStart = busy && ['binding', 'extracting'].includes(phase)", direct)
        self.assertIn("disabled={paymentBusy || Boolean(row.retrying)}", direct)
        self.assertIn("批次运行中也可支付已完成行", direct)
        self.assertNotIn("pauseRequestedRef.current = false; setPaused(false);\n    setError(null); setBusy(true); setPhase('protocol');", direct)
        self.assertIn("if (cached?.address && cached?.profile) return;", source)
        hotfix = (ROOT / "frontend" / "js" / "card-batch-hotfix.js").read_text()
        self.assertIn("automyai.card.unified.card-flow-state.v2", hotfix)
        self.assertIn("migrateCurrentPage();", hotfix)
        self.assertIn("if (document.querySelector('[data-payment-mode=\"synchronized-batch\"]')) return;", hotfix)
        self.assertIn("const retryExtract = async (row) =>", direct)
        self.assertIn("const canRegenerate = row?.status === 'done'", direct)
        self.assertIn(">重新提链</GlassButton>", direct)
        self.assertIn("const retryBind = async (row) =>", direct)
        self.assertIn("row.bindSucceeded && row.failureStage === '生成 Checkout 提链'", direct)
        self.assertIn("row.failureStage !== '生成 Checkout 提链'", direct)
        self.assertIn("'；不会再次绑卡。'", direct)
        self.assertIn("从设置默认卡继续", direct)
        self.assertIn("本账号固定使用分配到的 US 节点", direct)
        self.assertIn("PROXY_PREFLIGHT_FAILED", source)
        for label in ("填写卡片", "输入多个 AT", "串行绑卡", "批量提链"):
            self.assertIn(label, direct)

    def test_main_server_proxies_card_api_for_local_ui(self):
        source = (ROOT / "server.py").read_text()
        self.assertIn('def do_PATCH(self) -> None:', source)
        self.assertIn('if parsed.path.startswith("/card-payment-api/"):', source)
        self.assertIn('self.handle_card_payment_proxy(method, parsed)', source)
        self.assertIn('target_path = "/api/" + suffix', source)
        self.assertIn('for name in ("Content-Type", "Cookie", "X-Admin-Password"):', source)


if __name__ == "__main__":
    unittest.main()
