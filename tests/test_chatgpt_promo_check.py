from __future__ import annotations

import unittest

from tools.chatgpt_promo_check import extract_credentials, normalize_proxy_url, summarize_check, transport_error_message


class ChatGptPromoCredentialTests(unittest.TestCase):
    def test_sub2api_nested_account_credentials_are_extracted(self) -> None:
        result = extract_credentials(
            {
                "type": "sub2api-data",
                "version": 1,
                "accounts": [
                    {
                        "platform": "openai",
                        "type": "oauth",
                        "credentials": {
                            "access_token": "ACCESS",
                            "chatgpt_account_id": "ACCOUNT",
                            "email": "user@example.test",
                        },
                    }
                ],
            }
        )
        self.assertEqual(result["accessToken"], "ACCESS")
        self.assertEqual(result["accountId"], "ACCOUNT")
        self.assertEqual(result["email"], "user@example.test")
        self.assertTrue(result["deviceId"])

    def test_existing_top_level_shape_remains_supported(self) -> None:
        result = extract_credentials(
            {"accessToken": "ACCESS", "account": {"id": "ACCOUNT"}, "deviceId": "DEVICE"}
        )
        self.assertEqual(
            result,
            {"accessToken": "ACCESS", "accountId": "ACCOUNT", "deviceId": "DEVICE", "email": ""},
        )

    def test_proxy_notations_are_normalized(self) -> None:
        self.assertEqual(normalize_proxy_url("proxy.example:8080"), "http://proxy.example:8080")
        self.assertEqual(
            normalize_proxy_url("proxy.example:8080:user:pass"),
            "http://user:pass@proxy.example:8080",
        )
        self.assertEqual(
            normalize_proxy_url("user:pass@proxy.example:8080"),
            "http://user:pass@proxy.example:8080",
        )

    def test_incomplete_proxy_has_clear_error_and_transport_redacts_value(self) -> None:
        with self.assertRaisesRegex(ValueError, "代理格式不完整"):
            normalize_proxy_url("us.102xxxxxxxxxxxxx")
        message = transport_error_message(
            ValueError("Failed to parse: http://user:secret@us.102xxxxxxxxxxxxx"),
            proxy_used=True,
        )
        self.assertEqual(message, "代理地址解析失败，请检查代理格式和端口")
        self.assertNotIn("secret", message)

    def test_summary_matches_actual_accounts_check_campaign_shape(self) -> None:
        result = summarize_check({
            "accounts": {
                "ACCOUNT": {
                    "account": {"plan_type": "free"},
                    "entitlement": {"subscription_plan": "chatgptfreeplan"},
                    "eligible_promo_campaigns": {
                        "plus": {
                            "id": "plus-1-month-free",
                            "metadata": {
                                "plan_name": "chatgptplusplan",
                                "duration": {"num_periods": 1, "period": "month"},
                                "discount": {"percentage": 100},
                            },
                        }
                    },
                    "eligible_offers": {"default_offer_id": "chatgptplusplan"},
                }
            }
        }, account_id="ACCOUNT")
        self.assertTrue(result["monthlyPromoGuess"])
        self.assertEqual(result["monthlyPromoCampaign"]["id"], "plus-1-month-free")
        self.assertIn("eligible_promo_campaigns.plus.id", result["monthlyPromoEvidence"])
        self.assertEqual(result["planType"], "free")

    def test_summary_reports_false_when_campaign_list_is_present_without_target(self) -> None:
        result = summarize_check({
            "accounts": {"ACCOUNT": {"account": {"plan_type": "plus"}, "eligible_promo_campaigns": {}}}
        }, account_id="ACCOUNT")
        self.assertFalse(result["monthlyPromoGuess"])
        self.assertIn("未匹配", result["monthlyPromoEvidence"])


if __name__ == "__main__":
    unittest.main()
