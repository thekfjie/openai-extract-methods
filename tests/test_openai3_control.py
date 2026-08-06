from __future__ import annotations

import unittest
from datetime import datetime, timezone

from integrations.openai3_control import (
    classify_openai_signup_transition,
    mail_failure_is_definitive,
    normalize_mail_groups,
    normalize_proxy_url,
    proxy_http_connect_fallback,
    select_latest_verification_code,
)


class OpenAI3ControlTests(unittest.TestCase):
    def test_normalizes_legacy_proxy_format(self) -> None:
        self.assertEqual(
            normalize_proxy_url("proxy.example:8080:user:name:p@ss"),
            "http://user:name%3Ap%40ss@proxy.example:8080",
        )

    def test_preserves_encoded_proxy_credentials(self) -> None:
        self.assertEqual(
            normalize_proxy_url("socks5h://user%40example:p%3Ass@127.0.0.1:1080"),
            "socks5h://user%40example:p%3Ass@127.0.0.1:1080",
        )

    def test_rejects_invalid_proxy(self) -> None:
        with self.assertRaisesRegex(ValueError, "代理格式"):
            normalize_proxy_url("missing-port.example")

    def test_https_proxy_tls_error_can_fall_back_to_http_connect(self) -> None:
        self.assertEqual(
            proxy_http_connect_fallback(
                "https://user:p%40ss@proxy.example:3010",
                RuntimeError("TLS connect error: WRONG_VERSION_NUMBER"),
            ),
            "http://user:p%40ss@proxy.example:3010",
        )

    def test_proxy_fallback_does_not_hide_other_failures(self) -> None:
        self.assertEqual(
            proxy_http_connect_fallback(
                "https://user:pass@proxy.example:3010",
                RuntimeError("HTTP 407 proxy authentication required"),
            ),
            "",
        )
        self.assertEqual(
            proxy_http_connect_fallback(
                "http://user:pass@proxy.example:3010",
                RuntimeError("TLS connect error: WRONG_VERSION_NUMBER"),
            ),
            "",
        )

    def test_mail_groups_must_be_distinct(self) -> None:
        with self.assertRaisesRegex(ValueError, "必须互不相同"):
            normalize_mail_groups("source", "pending", "pending", "badmail")

    def test_mail_failure_classification_is_conservative(self) -> None:
        self.assertTrue(mail_failure_is_definitive(RuntimeError("HTTP 401 invalid_grant")))
        self.assertFalse(mail_failure_is_definitive(RuntimeError("connection refused")))
        self.assertFalse(mail_failure_is_definitive(RuntimeError("temporary timeout")))

    def test_verification_code_selection_uses_newest_mail_and_returns_date(self) -> None:
        result = select_latest_verification_code([
            {
                "date": "2026-07-27T13:32:18Z",
                "subject": "Your temporary ChatGPT verification code",
                "body_preview": "Your code is 446083",
            },
            {
                "date": "2026-07-27T13:49:18Z",
                "subject": "Your temporary ChatGPT verification code",
                "body_preview": "Your code is 284044",
            },
        ])
        self.assertEqual(result["code"], "284044")
        self.assertEqual(result["mail"]["date"], "2026-07-27T13:49:18Z")

    def test_verification_code_selection_rejects_mail_before_current_challenge(self) -> None:
        threshold = datetime(2026, 7, 27, 13, 50, tzinfo=timezone.utc).timestamp()
        result = select_latest_verification_code(
            [
                {
                    "date": "2026-07-27T13:49:18Z",
                    "subject": "Your temporary ChatGPT verification code",
                    "body_preview": "Your code is 284044",
                }
            ],
            not_before=threshold,
        )
        self.assertFalse(result["success"])

    def test_verification_code_selection_waits_when_newest_code_was_already_submitted(self) -> None:
        result = select_latest_verification_code(
            [
                {
                    "date": "2026-07-27T13:49:18Z",
                    "subject": "Your temporary ChatGPT verification code",
                    "body_preview": "Your code is 284044",
                },
                {
                    "date": "2026-07-27T13:50:18Z",
                    "subject": "Your temporary ChatGPT verification code",
                    "body_preview": "Your code is 446083",
                },
            ],
            excluded_codes={"446083"},
        )
        self.assertFalse(result["success"])

    def test_signup_transition_distinguishes_password_otp_and_about_you(self) -> None:
        self.assertEqual(
            classify_openai_signup_transition({"page": {"type": "create_account_password"}})["stage"],
            "password",
        )
        self.assertEqual(
            classify_openai_signup_transition({
                "page": {
                    "type": "email_otp_verification",
                    "payload": {"email_verification_mode": "passwordless_signup"},
                }
            })["stage"],
            "email_otp",
        )
        self.assertEqual(
            classify_openai_signup_transition({"continue_url": "/about-you"})["stage"],
            "about_you",
        )

    def test_signup_transition_detects_oauth_callback(self) -> None:
        result = classify_openai_signup_transition({
            "continue_url": "https://chatgpt.com/api/auth/callback/openai?code=abc&state=def"
        })
        self.assertEqual(result["stage"], "callback")


if __name__ == "__main__":
    unittest.main()
