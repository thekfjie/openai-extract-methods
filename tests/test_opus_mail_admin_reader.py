from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from integrations import opus_mail_admin_reader as reader


SAMPLE_NOTE = json.dumps({
    "user": {"id": "user-1", "email": "free.one@icloud.com", "name": "Free One"},
    "account": {"id": "acct-1", "planType": "free"},
    "accessToken": "access-secret",
    "sessionToken": "session-secret",
    "expires": "2026-10-01T00:00:00.000Z",
})


class OpusMailAdminReaderTests(unittest.TestCase):
    def test_public_account_marks_free_unactivated_and_color(self):
        item = {
            "id": "acc-1",
            "email": "free.one@icloud.com",
            "sold": False,
            "manualPlus": False,
            "hasPlus": False,
            "hasDeactivation": False,
            "markColor": "red",
            "note": SAMPLE_NOTE,
            "openaiOAuth": {"hasAccessToken": True, "hasSessionToken": True},
        }
        public = reader.public_mail_admin_account(item)
        self.assertTrue(public["isFreeUnactivated"])
        self.assertEqual(public["markColor"], "red")
        self.assertEqual(public["markColorLabel"], "红")
        self.assertTrue(public["hasCredential"])
        self.assertTrue(public["selectable"])

    def test_build_extraction_credential_from_note(self):
        item = {"id": "acc-1", "email": "free.one@icloud.com", "note": SAMPLE_NOTE}
        credential = reader.build_extraction_credential(item)
        self.assertEqual(credential["email"], "free.one@icloud.com")
        self.assertEqual(credential["accessToken"], "access-secret")
        self.assertEqual(credential["sessionToken"], "session-secret")
        self.assertEqual(credential["source"], "mail_admin")

    def test_list_free_unactivated_filters_and_prefers_marked(self):
        mappings = [
            {
                "id": "plus-1",
                "email": "plus@icloud.com",
                "hasPlus": True,
                "hasDeactivation": False,
                "markColor": "red",
                "note": SAMPLE_NOTE,
            },
            {
                "id": "free-plain",
                "email": "plain@icloud.com",
                "hasPlus": False,
                "hasDeactivation": False,
                "markColor": "",
                "note": SAMPLE_NOTE,
            },
            {
                "id": "free-red",
                "email": "marked@icloud.com",
                "hasPlus": False,
                "hasDeactivation": False,
                "markColor": "red",
                "note": SAMPLE_NOTE,
            },
            {
                "id": "free-dead",
                "email": "dead@icloud.com",
                "hasPlus": False,
                "hasDeactivation": True,
                "markColor": "blue",
                "note": SAMPLE_NOTE,
            },
        ]
        client = reader.OpusMailAdminReader("http://mail.test", "secret")
        with mock.patch.object(client, "list_mappings", return_value=mappings):
            payload = client.list_free_unactivated()
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["accounts"][0]["email"], "marked@icloud.com")
        self.assertEqual(payload["marked"], 1)

        with mock.patch.object(client, "list_mappings", return_value=mappings):
            marked = client.list_free_unactivated(marked_only=True)
        self.assertEqual(marked["total"], 1)
        self.assertEqual(marked["accounts"][0]["id"], "free-red")

    def test_materialize_credentials_returns_input_text(self):
        mappings = [{
            "id": "acc-1",
            "email": "free.one@icloud.com",
            "hasPlus": False,
            "hasDeactivation": False,
            "note": SAMPLE_NOTE,
        }]
        client = reader.OpusMailAdminReader("http://mail.test", "secret")
        with mock.patch.object(client, "list_mappings", return_value=mappings):
            payload = client.materialize_credentials(["acc-1"])
        self.assertEqual(payload["count"], 1)
        self.assertIn("access-secret", payload["inputText"])
        self.assertIn("free.one@icloud.com", payload["inputText"])

    def test_pending_signup_pool_only_contains_tokenless_active_records(self):
        mappings = [
            {"id": "pending", "email": "pending@icloud.com", "note": "automyai OpenAI signup pool pending", "sold": False, "autoFlag": True, "openaiOAuth": {}},
            {"id": "done", "email": "done@icloud.com", "note": "automyai OpenAI signup pool pending", "sold": False, "autoFlag": True, "openaiOAuth": {"hasAccessToken": True}},
            {"id": "manual", "email": "manual@icloud.com", "note": "automyai OpenAI signup pool pending", "sold": False, "autoFlag": False, "openaiOAuth": {}},
        ]
        client = reader.OpusMailAdminReader("http://mail.test", "secret")
        with mock.patch.object(client, "list_mappings", return_value=mappings):
            payload = client.list_pending_signup_accounts()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["accounts"][0]["email"], "pending@icloud.com")

    def test_latest_verification_code_uses_minimal_admin_endpoint(self):
        client = reader.OpusMailAdminReader("http://mail.test", "secret")
        client._cookie = "mail_opus_admin=session"
        with mock.patch.object(client, "find_mapping_by_email", return_value={"id": "mapping id"}), mock.patch.object(
            client,
            "_request",
            return_value=(200, {"ok": True, "item": {"id": "mail-1", "verificationCode": "123456", "created_at": "now", "subject": "code"}}, "", ""),
        ) as request:
            item = client.latest_verification_code("pending@icloud.com")
        self.assertEqual(item["verificationCode"], "123456")
        self.assertIn("mapping%20id", request.call_args.args[1])

    def test_probe_mail_access_checks_mapping_mail_endpoint(self):
        client = reader.OpusMailAdminReader("http://mail.test", "secret")
        client._cookie = "mail_opus_admin=session"
        with mock.patch.object(client, "find_mapping_by_email", return_value={"id": "mapping id"}), mock.patch.object(
            client,
            "_request",
            return_value=(200, {"results": []}, "", ""),
        ) as request:
            probe = client.probe_mail_access("pending@icloud.com")
        self.assertTrue(probe["reachable"])
        self.assertEqual(probe["mailCount"], 0)
        self.assertIn("mapping%20id", request.call_args.args[1])

    def test_from_project_reads_admin_auth_and_prefers_loopback(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data" / "apple_mail"
            data.mkdir(parents=True)
            (data / "config.json").write_text(json.dumps({
                "enabled": True,
                "importBase": "https://cloud.opus.test",
                "proxyUrl": "http://proxy.test:8080",
            }), encoding="utf-8")
            (data / "secrets.json").write_text(json.dumps({"adminAuth": "admin-secret"}), encoding="utf-8")
            with mock.patch.dict("os.environ", {"OPUS_MAIL_ADMIN_USE_LOOPBACK": "1", "OPUS_MAIL_ADMIN_LOOPBACK": "http://127.0.0.1:8789"}, clear=False):
                client = reader.OpusMailAdminReader.from_project(root)
            self.assertEqual(client.base_url, "http://127.0.0.1:8789")
            self.assertEqual(client.admin_password, "admin-secret")
            self.assertEqual(client.proxy_url, "")  # loopback disables proxy


if __name__ == "__main__":
    unittest.main()
