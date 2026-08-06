from __future__ import annotations

import unittest

from integrations.openai4_control import (
    build_uc_start_payload,
    default_openai4_config,
    map_uc_state_to_openai4,
    normalize_openai4_mail_groups,
    normalize_openai4_proxy_input,
    public_proxy_url,
    resolve_openai4_proxy,
    sanitize_openai4_proxy_display,
)


class OpenAI4ControlTests(unittest.TestCase):
    def test_defaults_have_no_proxy(self) -> None:
        cfg = default_openai4_config()
        self.assertEqual(cfg["mail_source_group"], "默认分组")
        self.assertEqual(cfg["mail_pending_group"], "oai_pending")
        self.assertEqual(cfg["mail_success_group"], "oai_success")
        self.assertEqual(cfg["mail_bad_group"], "badmail")
        self.assertEqual(cfg["custom_proxy_url"], "")
        self.assertTrue(cfg["get_refresh_token"])
        self.assertFalse(cfg["manual_mode"])
        self.assertFalse(cfg["keep_browser_on_failure"])
        self.assertNotIn("proxy_mode", cfg)
        self.assertNotIn("cliproxy_proxy_url", cfg)

    def test_batch_payload_forces_manual_handoff_off(self) -> None:
        payload = build_uc_start_payload(
            emails=["person@example.com"],
            proxy_url="http://127.0.0.1:8080",
            cfg={"manual_mode": True, "keep_browser_on_failure": True},
        )
        self.assertFalse(payload["manualMode"])
        self.assertFalse(payload["keepBrowserOnFailure"])

    def test_requires_custom_proxy(self) -> None:
        with self.assertRaisesRegex(ValueError, "请填写注册代理"):
            resolve_openai4_proxy({"custom_proxy_url": ""})


    def test_display_keeps_user_format(self) -> None:
        raw = "us.cliproxy.io:3010:user:pass"
        self.assertEqual(sanitize_openai4_proxy_display(raw), raw)
        self.assertEqual(
            normalize_openai4_proxy_input(raw),
            "http://user:pass@us.cliproxy.io:3010",
        )
        resolved = resolve_openai4_proxy({"custom_proxy_url": raw})
        self.assertEqual(resolved["displayProxy"], raw)
        self.assertEqual(resolved["proxyUrl"], "http://user:pass@us.cliproxy.io:3010")

    def test_backend_adds_http_scheme(self) -> None:
        self.assertEqual(
            normalize_openai4_proxy_input("user:pass@127.0.0.1:8080"),
            "http://user:pass@127.0.0.1:8080",
        )
        self.assertEqual(
            normalize_openai4_proxy_input("127.0.0.1:8080:user:p@ss"),
            "http://user:p%40ss@127.0.0.1:8080",
        )

    def test_resolve_custom_proxy(self) -> None:
        resolved = resolve_openai4_proxy({
            "custom_proxy_url": "user:secret@us.example.com:3010",
        })
        self.assertEqual(resolved["mode"], "custom")
        self.assertEqual(resolved["proxyUrl"], "http://user:secret@us.example.com:3010")
        self.assertEqual(resolved["proxyName"], "自定义注册代理")

    def test_explicit_override_wins(self) -> None:
        resolved = resolve_openai4_proxy(
            {"custom_proxy_url": "old:pass@127.0.0.1:1"},
            override_proxy="user:pass@127.0.0.1:8080",
        )
        self.assertEqual(resolved["proxyUrl"], "http://user:pass@127.0.0.1:8080")

    def test_map_uc_state(self) -> None:
        mapped = map_uc_state_to_openai4(
            {
                "running": True,
                "phase": "running",
                "total": 2,
                "completed": 1,
                "success": 0,
                "failed": 0,
                "currentEmail": "a@example.com",
                "currentStep": "邮箱验证码",
                "currentProxy": "http://127.0.0.1:8080",
                "currentPhone": "+100000",
                "currentPid": 123,
            },
            run_id="run-1",
            cfg=default_openai4_config(),
        )
        self.assertTrue(mapped["running"])
        self.assertEqual(mapped["current_email"], "a@example.com")
        self.assertEqual(mapped["current_step"], "邮箱验证码")
        self.assertEqual(mapped["engine"], "uc_signup")
        self.assertEqual(mapped["run_id"], "run-1")

    def test_build_uc_start_payload(self) -> None:
        payload = build_uc_start_payload(
            emails=["a@example.com", "b@example.com"],
            proxy_url="http://127.0.0.1:8080",
            cfg={"auth_only": False, "manual_mode": True, "keep_browser_on_failure": True},
            selected_account_email="a@example.com",
            forced_phone="+1555",
        )
        self.assertEqual(payload["emails"], ["a@example.com"])
        self.assertEqual(payload["proxy"], "http://127.0.0.1:8080")
        self.assertFalse(payload["manualMode"])
        self.assertFalse(payload["keepBrowserOnFailure"])
        self.assertTrue(payload["getRefreshToken"])
        self.assertEqual(payload["forcedPhone"], "+1555")
        self.assertEqual(payload["display"], ":1")
        self.assertEqual(payload["mailPendingGroup"], "oai_pending")
        self.assertEqual(payload["mailSuccessGroup"], "oai_success")
        self.assertEqual(payload["mailBadGroup"], "badmail")

    def test_build_uc_start_payload_can_skip_refresh_token_stage(self) -> None:
        payload = build_uc_start_payload(
            emails=["a@example.com"],
            proxy_url="http://127.0.0.1:8080",
            cfg={"get_refresh_token": False},
        )
        self.assertFalse(payload["getRefreshToken"])

    def test_pending_can_be_source_for_resume(self) -> None:
        groups = normalize_openai4_mail_groups("oai_pending", "oai_pending", "oai_success", "badmail")
        self.assertEqual(groups["sourceGroup"], "oai_pending")
        self.assertEqual(groups["pendingGroup"], "oai_pending")

    def test_public_proxy_hides_credentials(self) -> None:
        self.assertEqual(
            public_proxy_url("http://user:secret@host.example:3010"),
            "http://***:***@host.example:3010",
        )


if __name__ == "__main__":
    unittest.main()
