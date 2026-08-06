from __future__ import annotations

import unittest

from converters.openai_formats import convert_openai, parse_openai_input


class OpenAIFormatsTests(unittest.TestCase):
    def test_raw_refresh_token_is_accepted(self) -> None:
        docs = parse_openai_input("RT")
        self.assertEqual(docs, [{"refresh_token": "RT"}])

        bundle = convert_openai(docs, "sub2api")
        self.assertEqual(bundle["type"], "sub2api-data")
        self.assertEqual(bundle["version"], 1)
        self.assertEqual(bundle["proxies"], [])
        account = bundle["accounts"][0]
        self.assertEqual(account["platform"], "openai")
        self.assertEqual(account["type"], "oauth")
        self.assertEqual(account["credentials"]["refresh_token"], "RT")
        self.assertNotIn("access_token", account["credentials"])
        self.assertNotIn("id_token", account["credentials"])

    def test_raw_refresh_tokens_support_batch_and_labels(self) -> None:
        docs = parse_openai_input("refresh_token=RT-1\nBearer RT-2")
        self.assertEqual(docs, [{"refresh_token": "RT-1"}, {"refresh_token": "RT-2"}])
        accounts = convert_openai(docs, "sub2api")["accounts"]
        self.assertEqual([item["name"] for item in accounts], ["OpenAI RT #1", "OpenAI RT #2"])

    def test_current_sub2api_nested_credentials_roundtrip(self) -> None:
        docs = parse_openai_input(
            '{"type":"sub2api-data","version":1,"proxies":[],"accounts":'
            '[{"name":"x","platform":"openai","type":"oauth",'
            '"credentials":{"refresh_token":"RT","chatgpt_account_id":"acct"}}]}'
        )
        self.assertEqual(docs[0]["name"], "x")
        account = convert_openai(docs, "sub2api")["accounts"][0]
        self.assertEqual(account["credentials"]["refresh_token"], "RT")
        self.assertEqual(account["credentials"]["chatgpt_account_id"], "acct")

if __name__ == "__main__":
    unittest.main()
