from __future__ import annotations

import json
import unittest

from integrations.openai3_sub2api import extract_multipart_json_file, import_auth_to_sub2api


class FakeSub2ApiClient:
    def __init__(self) -> None:
        self.imported = None
        self.bound = None

    def import_accounts_document(self, document):
        self.imported = document
        return {"success": True, "imported": 1}

    def list_accounts(self):
        return [{
            "id": 42,
            "name": "person@example.com",
            "credentials": {
                "email": "person@example.com",
                "chatgpt_account_id": "acct-1",
            },
        }]

    def bind_accounts_to_group(self, account_ids, group_name, platform="openai"):
        self.bound = (account_ids, group_name, platform)
        return {"success": True, "updated": 1}


class OpenAI3Sub2ApiTests(unittest.TestCase):
    def test_extracts_cpa_compatible_multipart_json(self) -> None:
        boundary = "openai3-test-boundary"
        document = {"type": "codex", "email": "person@example.com", "access_token": "token"}
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="codex.json"\r\n'
            "Content-Type: application/json\r\n\r\n"
            f"{json.dumps(document)}\r\n"
            f"--{boundary}--\r\n"
        ).encode()
        self.assertEqual(
            extract_multipart_json_file(f"multipart/form-data; boundary={boundary}", body),
            document,
        )

    def test_imports_directly_and_binds_auto_group(self) -> None:
        client = FakeSub2ApiClient()
        result = import_auth_to_sub2api(client, {
            "type": "codex",
            "email": "person@example.com",
            "account_id": "acct-1",
            "access_token": "access-token",
            "session_token": "session-token",
        }, "auto")
        self.assertTrue(result["ok"])
        self.assertEqual(result["target"], "sub2api")
        self.assertTrue(result["imported"])
        self.assertEqual(result["updated"], 1)
        self.assertEqual(client.bound, ([42], "auto", "openai"))
        self.assertEqual(client.imported["accounts"][0]["email"], "person@example.com")


if __name__ == "__main__":
    unittest.main()
