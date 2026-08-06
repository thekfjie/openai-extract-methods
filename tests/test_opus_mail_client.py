from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from integrations import opus_mail_client as omc


class OpusMailClientTests(unittest.TestCase):
    def test_build_pending_payload_does_not_require_oauth_tokens(self):
        payload = omc.build_opus_pending_payload(email="person@example.com")
        self.assertEqual(payload["email"], "person@example.com")
        self.assertEqual(payload["outlookManagerEmail"], "")
        self.assertEqual(payload["toEmail"], "person@example.com")
        self.assertTrue(payload["autoFlag"])
        self.assertNotIn("accessToken", payload)

    def test_build_payload_carries_access_and_refresh_tokens(self):
        payload = omc.build_opus_openai_payload(
            {
                "tokens": {
                    "access_token": "access-secret",
                    "refresh_token": "refresh-secret",
                    "id_token": "id-secret",
                }
            },
            email="person@example.com",
        )
        self.assertEqual(payload["accessToken"], "access-secret")
        self.assertEqual(payload["refreshToken"], "refresh-secret")
        self.assertEqual(payload["oauthTokens"]["id_token"], "id-secret")
        self.assertEqual(payload["credential"], "person@example.com---access-secret")
        self.assertEqual(payload["toEmail"], "person@example.com")
        self.assertEqual(payload["outlookManagerEmail"], "")

        failed_payload = omc.build_opus_openai_payload(
            {"access_token": "access-secret", "statusMessage": "group bind failed"},
            email="person@example.com",
        )
        self.assertEqual(failed_payload["statusMessage"], "group bind failed")

    def test_registered_payload_moves_account_out_of_pending_without_tokens(self):
        payload = omc.build_opus_registered_payload(
            email="person@example.com",
            password="saved-password",
            reason="phone wall",
        )
        self.assertEqual(payload["note"], omc.OPENAI_REGISTERED_PENDING_NOTE)
        self.assertEqual(payload["toEmail"], "person@example.com")
        self.assertEqual(payload["outlookManagerEmail"], "")
        self.assertEqual(payload["statusMessage"], "phone wall")
        self.assertEqual(payload["password"], "saved-password")
        self.assertNotIn("accessToken", payload)

    def test_project_config_and_import_return_only_token_presence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data" / "apple_mail"
            data.mkdir(parents=True)
            (data / "config.json").write_text(json.dumps({"enabled": True, "importBase": "https://cloud.opus.test", "proxyUrl": "http://proxy.test:8080"}))
            (data / "secrets.json").write_text(json.dumps({"importApiKey": "api-secret"}))
            client = omc.OpusMailClient.from_project(root)
            with mock.patch.object(omc, "http_json", return_value=(200, {"item": {"id": 99}}, '{"item":{"id":99}}')) as request:
                result = client.import_openai_oauth(
                    {"access_token": "access-secret", "refresh_token": "refresh-secret"},
                    email="person@example.com",
                )

            self.assertTrue(result["imported"])
            self.assertTrue(result["hasAccessToken"])
            self.assertTrue(result["hasRefreshToken"])
            self.assertEqual(result["accountId"], 99)
            self.assertNotIn("access-secret", json.dumps(result))
            kwargs = request.call_args.kwargs
            self.assertEqual(kwargs["proxy_url"], "http://proxy.test:8080")
            self.assertEqual(kwargs["body"]["refreshToken"], "refresh-secret")

    def test_pending_import_uses_same_write_only_account_api(self):
        client = omc.OpusMailClient("https://cloud.opus.test", "api-secret", proxy_url="http://proxy.test:8080")
        with mock.patch.object(omc, "http_json", return_value=(200, {"item": {"id": 100}}, '{"item":{"id":100}}')) as request:
            result = client.import_pending_email(email="person@example.com")

        self.assertTrue(result["imported"])
        self.assertTrue(result["pending"])
        self.assertFalse(result["hasAccessToken"])
        self.assertEqual(request.call_args.kwargs["body"]["outlookManagerEmail"], "")

    def test_registered_import_uses_same_write_only_account_api(self):
        client = omc.OpusMailClient("https://cloud.opus.test", "api-secret", proxy_url="http://proxy.test:8080")
        with mock.patch.object(omc, "http_json", return_value=(200, {"item": {"id": 101}}, '{"item":{"id":101}}')) as request:
            result = client.import_registered_email(
                email="person@example.com",
                password="saved-password",
                reason="phone wall",
            )

        self.assertTrue(result["imported"])
        self.assertTrue(result["registered"])
        self.assertTrue(result["oauthPending"])
        self.assertFalse(result["hasAccessToken"])
        self.assertEqual(request.call_args.args[:2], ("POST", "https://cloud.opus.test/api/v1/accounts"))
        self.assertEqual(request.call_args.kwargs["body"]["password"], "saved-password")
        self.assertEqual(request.call_args.kwargs["body"]["statusMessage"], "phone wall")

    def test_oauth_payload_clears_registered_pending_status(self):
        payload = omc.build_opus_openai_payload(
            {"access_token": "access-secret", "refresh_token": "refresh-secret"},
            email="person@example.com",
        )
        self.assertEqual(payload["statusMessage"], "")

    def test_web_session_payload_keeps_rt_pending_status(self):
        payload = omc.build_opus_openai_payload(
            {
                "access_token": "web-access-secret",
                "session_token": "web-session-secret",
                "credentialKind": "chatgpt_web_session",
                "statusMessage": "Web AT saved; OAuth RT pending",
            },
            email="person@example.com",
        )
        self.assertEqual(payload["note"], omc.OPENAI_WEB_SESSION_PENDING_NOTE)
        self.assertEqual(payload["statusMessage"], "Web AT saved; OAuth RT pending")
        self.assertEqual(payload["accessToken"], "web-access-secret")
        self.assertNotIn("refreshToken", payload)


if __name__ == "__main__":
    unittest.main()
