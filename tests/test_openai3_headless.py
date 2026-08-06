from __future__ import annotations

import ast
import asyncio
import sys
import types
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class OpenAI3HeadlessTests(unittest.TestCase):
    @staticmethod
    def _method_source(class_name: str, method_name: str) -> str:
        source = (ROOT / "tools/chatgpt_register/chatgpt_register.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        class_node = next(
            node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name
        )
        method = next(
            node
            for node in class_node.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method_name
        )
        return ast.get_source_segment(source, method) or ""

    def test_sentinel_cache_is_scoped_to_flow_and_device(self) -> None:
        source = (ROOT / "tools/chatgpt_register/sentinel_token.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        provider = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "SentinelTokenProvider"
        )
        init_method = next(
            node for node in provider.body if isinstance(node, ast.FunctionDef) and node.name == "__init__"
        )
        init_source = ast.get_source_segment(source, init_method) or ""
        self.assertIn("self._cached_flow", init_source)

        token_method = next(
            node for node in provider.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "get_token"
        )
        token_source = ast.get_source_segment(source, token_method) or ""
        self.assertIn("self._cached_flow != flow", token_source)
        self.assertIn("self._device_id != device_id", token_source)

    def test_headless_signup_matches_har_passwordless_start(self) -> None:
        source = (ROOT / "tools/chatgpt_register/chatgpt_register.py").read_text(encoding="utf-8")
        init_source = self._method_source("OpenAIAuthClient", "init_page_email")
        self.assertIn('screen_hint="login_or_signup"', init_source)
        self.assertIn("login_hint=email", init_source)
        tree = ast.parse(source)
        auth_class = next(
            node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "OpenAIAuthClient"
        )
        method_names = {
            node.name
            for node in auth_class.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertNotIn("register_password_email", method_names)
        self.assertNotIn("send_email_otp", method_names)
        self.assertNotIn('"screen_hint": "signup"', source)

    def test_signin_identity_matches_har_cookie_and_uses_fresh_logging_id(self) -> None:
        authorization = self._method_source("OpenAIAuthClient", "_start_chatgpt_authorization")
        cookie_identity = self._method_source("OpenAIAuthClient", "_ensure_chatgpt_device_cookie")
        self.assertIn('"ext-oai-did": device_id', authorization)
        self.assertIn('"auth_session_logging_id": str(uuid.uuid4())', authorization)
        self.assertIn('cookie.name == "oai-did"', cookie_identity)
        self.assertIn('self.device_id = str(cookie.value)', cookie_identity)
        self.assertIn('session.cookies.set("oai-did", self.device_id', cookie_identity)
        self.assertNotIn('"oai-device-id"', authorization)

        first = str(uuid.uuid4())
        second = str(uuid.uuid4())
        self.assertNotEqual(first, second)
        uuid.UUID(first)
        uuid.UUID(second)

    def test_recovery_uses_username_only_login_and_flow_specific_sentinel(self) -> None:
        recovery = self._method_source("OpenAIAuthClient", "begin_passwordless_login")
        authorization = self._method_source("OpenAIAuthClient", "_start_chatgpt_authorization")
        validate = self._method_source("OpenAIAuthClient", "validate_email_otp")
        self.assertIn('screen_hint="login"', recovery)
        self.assertNotIn("login_hint=", recovery)
        self.assertIn('"ext-oai-did": device_id', authorization)
        self.assertIn('"auth_session_logging_id": str(uuid.uuid4())', authorization)
        self.assertIn('json={"username": {"value": email, "kind": "email"}}', recovery)
        self.assertIn('"authorize_continue"', recovery)
        self.assertIn("include_session_observer=False", recovery)
        self.assertIn('"email_otp_validate"', validate)
        self.assertNotIn("include_session_observer=False", validate)

    def test_registration_disallowed_reauthorizes_without_second_profile_submit(self) -> None:
        source = (ROOT / "tools/chatgpt_register/chatgpt_register.py").read_text(encoding="utf-8")
        reauth = self._method_source("OpenAIAuthClient", "reauthorize_for_session")
        self.assertIn('params.pop("prompt", None)', reauth)
        self.assertIn("allow_redirects=False", reauth)
        self.assertIn('"/api/auth/callback/openai"', reauth)
        self.assertIn('"code=" in current_url and "state=" in current_url', reauth)
        method_source = self._method_source("OpenAIAuthClient", "reauthorize_for_session")
        self.assertNotIn("create_account", method_source)
        register_source = source.split("async def _try_register_one_email", 1)[1]
        self.assertIn('auth.error_code(create_result) == "registration_disallowed"', register_source)
        self.assertIn("auth.reauthorize_for_session", register_source)
        self.assertIn("direct_callback_url or await _callback_url(final_payload)", register_source)
        self.assertIn('registration_disallowed_recovery_init_failed', register_source)
        self.assertIn('未直接捕获 callback，切换到无密码登录恢复', register_source)

    def test_reauthorize_for_session_follows_redirects_without_consuming_callback(self) -> None:
        source = self._method_source("OpenAIAuthClient", "reauthorize_for_session")
        self.assertIn("urljoin(current_url, location)", source)

        # The repository's protocol tests do not require the optional TLS
        # transport package; provide a minimal import stub for this pure
        # redirect test when it is unavailable.  The script also imports its
        # sibling ``sentinel_token`` as a top-level module, so expose that
        # directory while loading it.
        fake_curl = None
        try:
            import curl_cffi  # type: ignore  # noqa: F401
        except ModuleNotFoundError:
            fake_curl = types.ModuleType("curl_cffi")
            fake_curl.requests = types.SimpleNamespace(AsyncSession=object)
            sys.modules["curl_cffi"] = fake_curl
        sibling_dir = str(ROOT / "tools" / "chatgpt_register")
        sys.path.insert(0, sibling_dir)
        try:
            from tools.chatgpt_register.chatgpt_register import OpenAIAuthClient
        finally:
            sys.path.remove(sibling_dir)
            if fake_curl is not None:
                sys.modules.pop("curl_cffi", None)

        class FakeResponse:
            def __init__(self, location: str = "", url: str = ""):
                self.headers = {"location": location} if location else {}
                self.url = url

        class FakeSession:
            def __init__(self):
                self.urls = []
                self.responses = [
                    FakeResponse("/authorize/step-2"),
                    FakeResponse(
                        "https://auth.openai.com/api/auth/callback/openai?code=CODE&state=STATE"
                    ),
                ]

            async def get(self, url, *, allow_redirects=False):
                self.urls.append((url, allow_redirects))
                return self.responses.pop(0)

        async def exercise():
            client = OpenAIAuthClient.__new__(OpenAIAuthClient)
            session = FakeSession()

            async def get_session():
                return session

            client._get_session = get_session
            callback = await client.reauthorize_for_session(
                "https://auth.openai.com/authorize?prompt=login&client_id=CLIENT&state=STATE"
            )
            return callback, session.urls

        callback, urls = asyncio.run(exercise())
        self.assertEqual(
            callback,
            "https://auth.openai.com/api/auth/callback/openai?code=CODE&state=STATE",
        )
        self.assertEqual(len(urls), 2)
        self.assertTrue(all(allow_redirects is False for _, allow_redirects in urls))
        self.assertNotIn("prompt=login", urls[0][0])
        self.assertNotIn("callback/openai", [url for url, _ in urls])

    def test_profile_submit_is_single_shot_and_ambiguous_failure_uses_login_recovery(self) -> None:
        source = (ROOT / "tools/chatgpt_register/chatgpt_register.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        method = next(
            node
            for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_try_register_one_email"
        )
        method_source = ast.get_source_segment(source, method) or ""
        self.assertEqual(method_source.count("auth.create_account("), 1)
        self.assertIn("auth.begin_passwordless_login(email)", method_source)
        self.assertIn('recovery.get("otp_not_before")', method_source)
        recoverable = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_is_recoverable_create_failure"
        )
        recoverable_source = ast.get_source_segment(source, recoverable) or ""
        self.assertIn("status == 409 or status >= 500", recoverable_source)
        self.assertNotIn("auth.error_code(payload)", recoverable_source)
        self.assertIn('return _failure(create_error or "create_account_rejected")', method_source)

    def test_no_commit_mode_stops_before_profile_submission(self) -> None:
        source = (ROOT / "tools/chatgpt_register/chatgpt_register.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        method = next(
            node
            for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_try_register_one_email"
        )
        method_source = ast.get_source_segment(source, method) or ""
        guard = method_source.index('os.environ.get("OPENAI3_NO_COMMIT"')
        submit = method_source.index("auth.create_account(")
        self.assertLess(guard, submit)
        self.assertIn('"failure_reason": "no_commit_reached_about_you"', method_source)

    def test_stop_after_at_does_not_import_or_persist_auth_file(self) -> None:
        source = (ROOT / "tools/chatgpt_register/chatgpt_register.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        method = next(
            node
            for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_try_register_one_email"
        )
        method_source = ast.get_source_segment(source, method) or ""
        guard = method_source.index('os.environ.get("OPENAI3_STOP_AFTER_AT"')
        import_target = method_source.index('cpa_base = str(os.environ.get("CPA_BASE")')
        self.assertLess(guard, import_target)
        self.assertIn('"access_token_acquired": True', method_source)

    def test_registration_disallowed_is_structured_and_never_restarts_full_flow(self) -> None:
        source = (ROOT / "tools/chatgpt_register/chatgpt_register.py").read_text(encoding="utf-8")
        self.assertIn("create_error = auth.error_code(create_result)", source)
        retryable_block = source.split("retryable =", 1)[1].split("}", 1)[0]
        self.assertNotIn("signup_unknown_landing", retryable_block)
        self.assertNotIn("registration_disallowed", retryable_block)
        self.assertIn("if email and not retryable:", source)

    def test_preflighted_mailbox_reuses_one_fingerprint_profile(self) -> None:
        source = (ROOT / "tools/chatgpt_register/chatgpt_register.py").read_text(encoding="utf-8")
        self.assertIn("fixed_email_sentinel = None", source)
        self.assertIn("attempt_sentinel = fixed_email_sentinel or provided_sentinel", source)
        self.assertIn('self.fingerprint["device_id"] = self.device_id', source)

    def test_otp_retry_reuses_action_id_and_replacement_excludes_old_code(self) -> None:
        source = (ROOT / "tools/chatgpt_register/chatgpt_register.py").read_text(encoding="utf-8")
        self.assertIn("invocation_id = str(uuid.uuid4())", source)
        self.assertGreaterEqual(source.count("invocation_id=invocation_id"), 2)
        self.assertIn("_otp_validation_did_not_advance(result)", source)
        self.assertIn("_resend_and_poll(exclude_code=code)", source)
        self.assertIn("if resend_used:", source)
        self.assertIn('failure_reason = "otp_not_received_after_resend"', source)

    def test_no_initial_otp_gets_one_bounded_resend(self) -> None:
        source = (ROOT / "tools/chatgpt_register/chatgpt_register.py").read_text(encoding="utf-8")
        self.assertIn("code = await _resend_and_poll()", source)
        self.assertEqual(source.count("resend_result = await auth.resend_email_otp()"), 1)

    def test_otp_delivery_failure_is_reported_for_mail_admin_routing(self) -> None:
        engine = (ROOT / "tools/chatgpt_register/chatgpt_register.py").read_text(encoding="utf-8")
        webapp = (ROOT / "tools/openai3/webapp.py").read_text(encoding="utf-8")
        self.assertIn('marker["failure_reason"]', engine)
        self.assertIn('failure_reason == "otp_not_received_after_resend"', webapp)
        self.assertIn('target = str(_state.get("badGroup"))', webapp)

    def test_selected_mailbox_rebuilds_session_only_for_transport_failures(self) -> None:
        source = (ROOT / "tools/chatgpt_register/chatgpt_register.py").read_text(encoding="utf-8")
        self.assertIn('failure_reason = "transport_error"', source)
        self.assertIn('"signup_init_transient"', source)
        self.assertIn("if email:", source)
        self.assertIn("if email and not retryable:", source)
        self.assertIn("max_retries=2 if assigned_email else 10", source)

    def test_zero_success_structured_run_is_reported_as_error(self) -> None:
        webapp = (ROOT / "tools/openai3/webapp.py").read_text(encoding="utf-8")
        self.assertIn("structured_terminal_error = (", webapp)
        self.assertIn("success == 0", webapp)
        self.assertIn("failed > 0", webapp)
        self.assertIn("code == 0 and not structured_terminal_error", webapp)

    def test_resend_and_unknown_landing_logs_are_diagnostic_but_redacted(self) -> None:
        source = (ROOT / "tools/chatgpt_register/chatgpt_register.py").read_text(encoding="utf-8")
        self.assertIn("验证码重发响应: status={resend_status}", source)
        self.assertIn("status={landing_status} page={landing_path[:80]}", source)
        self.assertNotIn("landing_url={", source)

    def test_cloudflare_challenge_stops_without_burning_the_mailbox(self) -> None:
        source = (ROOT / "tools/chatgpt_register/chatgpt_register.py").read_text(encoding="utf-8")
        webapp = (ROOT / "tools/openai3/webapp.py").read_text(encoding="utf-8")
        self.assertIn('reason = "cloudflare_challenge"', source)
        self.assertIn("status={init_status}", source)
        self.assertIn("实际尝试 {attempts_used} 次后失败", source)
        self.assertIn('"challenge_required" if failure_reason == "cloudflare_challenge"', source)
        retryable_block = source.split("retryable =", 1)[1].split("}", 1)[0]
        self.assertNotIn("cloudflare_challenge", retryable_block)
        self.assertIn('_state["phase"] = "challenge_required"', webapp)
        self.assertIn('"novnc_path": NOVNC_PATH', webapp)
        self.assertIn('"novnc_url": NOVNC_URL', webapp)
        self.assertIn("本次任务已停止（未进入人工托管）", webapp)
        self.assertNotIn("任务已暂停", webapp)
        self.assertIn('{"stopped", "challenge_required"}', webapp)

    def test_mail_client_forwards_current_challenge_timestamp(self) -> None:
        source = (ROOT / "tools/chatgpt_register/cpa_codex_oauth.py").read_text(encoding="utf-8")
        self.assertIn('params["since"]', source)
        self.assertIn('params["exclude_code"]', source)

    def test_openai3_fingerprint_is_fixed_windows_chrome_150(self) -> None:
        fingerprint = (ROOT / "integrations/oai_fingerprint.py").read_text(encoding="utf-8")
        webapp = (ROOT / "tools/openai3/webapp.py").read_text(encoding="utf-8")
        extractor = (ROOT / "tools/openai5/har_protocol_extract.py").read_text(encoding="utf-8")
        self.assertIn('EntryFingerprintSpec("openai3", "windows-11-chrome", "150.0.0.0")', fingerprint)
        self.assertIn('OPENAI3_FINGERPRINT_PRESET"] = "windows-11-chrome"', webapp)
        self.assertIn('OPENAI3_FINGERPRINT_BROWSER_VERSION"] = "150.0.0.0"', webapp)
        self.assertIn("OPENAI3_FINGERPRINT_RUN_SEED", webapp)
        self.assertIn('"entry": "openai3"', extractor)
        self.assertIn('"browser_version": "150.0.0.0"', extractor)
        self.assertIn('"context": "No Chromium context on the normal path;', extractor)


if __name__ == "__main__":
    unittest.main()
