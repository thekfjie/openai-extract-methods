from __future__ import annotations

import unittest

from integrations.paypal_protocol import (
    public_paypal_result,
    paypal_protocol_countries,
    paypal_protocol_materials,
    paypal_protocol_status,
    prepare_paypal_protocol,
)


class PayPalProtocolInventoryTests(unittest.TestCase):
    def test_protocol_was_moved_out_of_extraction_methods(self) -> None:
        status = paypal_protocol_status()
        self.assertTrue(status["ok"])
        self.assertEqual(status["supportedScope"], "general")
        self.assertTrue(status["executorAvailable"])
        self.assertTrue(status["preparationAvailable"])
        self.assertEqual(status["executionMode"], "full_chain")
        self.assertEqual(status["mode"], "interactive")
        self.assertEqual(status["defaultCountry"], "GB")
        self.assertEqual(status["defaultLocale"], "en_GB")
        self.assertEqual(status["executorCountries"], ["BR", "GB"])
        self.assertNotIn("sourcePath", status)
        self.assertNotIn("activeReference", status)
        self.assertNotIn("capture", status)

    def test_protocol_inventory_exposes_source_and_reference_materials(self) -> None:
        materials = paypal_protocol_materials()
        self.assertIn("paypal/flow.py", materials["sourceFiles"])
        self.assertIn("paypal/graphql.py", materials["sourceFiles"])
        self.assertTrue(any(name.endswith("SignUpNewMemberMutation.req.json") for name in materials["referenceFiles"]))
        self.assertTrue(any(name.endswith("authorize.req.json") for name in materials["referenceFiles"]))

    def test_uk_capture_manifest_is_value_free_and_marks_html_response(self) -> None:
        materials = paypal_protocol_materials()
        capture = materials["capture"]
        self.assertEqual(capture["profile"]["country"], "GB")
        self.assertEqual(capture["groupCount"], 9)
        self.assertEqual(capture["artifactCount"], 27)
        self.assertEqual(capture["completeGroupCount"], 9)
        self.assertGreaterEqual(capture["operationCount"], 10)

        groups = {group["id"]: group for group in capture["groups"]}
        signup = groups["SignUpNewMemberMutation"]
        self.assertEqual(signup["responseFormat"], "html")
        self.assertIn("billingAddress", signup["operations"][0]["variableNames"])
        self.assertNotIn("variables", signup["operations"][0])

        authorize = groups["authorize"]
        self.assertIn("x-paypal-internal-euat", authorize["headerNames"])
        self.assertEqual(authorize["responseRoots"], ["billing"])

        coverage = capture["implementationCoverage"]
        self.assertEqual(coverage["uniqueOperationCount"], 10)
        self.assertEqual(coverage["coveredOperationCount"], 9)
        self.assertEqual(
            coverage["equivalentHandlers"],
            {"InitialDataQuery": "window.__INITIAL_DATA__ HTML parser"},
        )
        self.assertEqual(
            coverage["referenceOnlyOperations"],
            ["getOtpChallengeOperation"],
        )

    def test_country_catalog_matches_reference_scope(self) -> None:
        catalog = paypal_protocol_countries()
        self.assertEqual(catalog["count"], 197)
        by_code = {item["code"]: item for item in catalog["countries"]}
        for code in ("AE", "BR", "GB", "JP", "TH", "US", "ZA", "ZW"):
            self.assertIn(code, by_code)
        self.assertEqual(by_code["GB"]["calling_code"], "+44")
        self.assertEqual(by_code["GB"]["support_level"], "real_ok")
        self.assertEqual(by_code["BR"]["support_level"], "real_ok")
        self.assertEqual(by_code["DE"]["support_level"], "theoretical_ok")
        self.assertEqual(by_code["ZW"]["support_level"], "unsupported")
        self.assertEqual(by_code["GB"]["internal_logic"], "GB 专项分支（en_GB / GBP / CRS）")
        self.assertEqual(by_code["TH"]["internal_logic"], "TH 专项交接（th_TH / THB / +66 / 无 CPF）")
        self.assertEqual(by_code["TH"]["protocol_profile"]["timezoneOffsetMinutes"], 420)
        self.assertIsNone(by_code["TH"]["protocol_profile"]["identityDocument"])
        self.assertEqual(by_code["DE"]["internal_logic"], "通用地区模板")
        self.assertEqual(catalog["realOkCount"], 13)
        self.assertEqual(catalog["theoreticalOkCount"], 30)
        self.assertEqual(catalog["unsupportedCount"], 154)

    def test_manual_handoff_extracts_token_and_does_not_return_proxy_values(self) -> None:
        result = prepare_paypal_protocol({
            "paypalUrl": "https://www.paypal.com/agreements/approve?ba_token=BA-12345678ABCDEF",
            "country": "GB", "locale": "en_GB", "phone": "07512 345678",
            "proxies": ["127.0.0.1:8080", "http://user:pass@example.com:3128"],
        })
        self.assertTrue(result["ok"])
        self.assertEqual(result["phone"], "+447512345678")
        self.assertEqual(result["proxyCount"], 2)
        self.assertNotIn("proxies", result)
        self.assertIn("country.x=GB", result["approvalUrl"])

    def test_manual_handoff_rejects_country_phone_mismatch(self) -> None:
        with self.assertRaisesRegex(ValueError, "以 \\+44 开头"):
            prepare_paypal_protocol({
                "baToken": "BA-12345678ABCDEF", "country": "GB", "locale": "en_GB", "phone": "+15551234567",
            })

    def test_manual_handoff_accepts_dialing_code_without_plus(self) -> None:
        result = prepare_paypal_protocol({
            "baToken": "BA-12345678ABCDEF", "country": "GB", "locale": "en_GB", "phone": "447512345678",
        })
        self.assertEqual(result["phone"], "+447512345678")

    def test_thailand_handoff_exposes_local_authorization_plan(self) -> None:
        result = prepare_paypal_protocol({
            "baToken": "BA-12345678ABCDEF", "country": "TH", "phone": "0812345678",
        })
        self.assertEqual(result["phone"], "+66812345678")
        self.assertEqual(result["protocolProfile"]["language"], "th")
        self.assertEqual(result["executionBoundary"], "manual_or_sandbox_only")
        self.assertEqual(len(result["authorizationPlan"]), 5)

    def test_public_result_confirms_and_redacts_merchant_secrets(self) -> None:
        result = public_paypal_result({
            "status": "success",
            "ba_token": "BA-1234567890ABCDEF",
            "ec_token": "EC-1234567890ABCDEF",
            "return_url": "https://merchant.test/return?secret=value",
            "final_redirect_url": (
                "https://pay.example.test/c/pay/cs_live_secret?redirect_status=succeeded"
                "&setup_intent_client_secret=seti_secret&returned_from_redirect=true"
            ),
            "redirect_status": "succeeded",
            "settlement_status": "confirmed",
            "payment_action": "SALE",
            "buyer_mode": "identity_elevation",
            "identity_elevation": {"buyer_ready": True, "auth_refreshed": True},
        })
        encoded = str(result)
        self.assertEqual(result["settlement_status"], "confirmed")
        self.assertEqual(result["return_url"], "<redacted>")
        self.assertIn("/REDACTED", result["final_redirect_url"])
        self.assertNotIn("cs_live_secret", encoded)
        self.assertNotIn("seti_secret", encoded)
        self.assertEqual(result["pending_url"], "")

    def test_public_result_preserves_pending_verification(self) -> None:
        result = public_paypal_result({
            "status": "success",
            "ba_token": "BA-1234567890ABCDEF",
            "ec_token": "EC-1234567890ABCDEF",
            "final_redirect_url": (
                "https://pay.example.test/c/pay/cs_live_secret?redirect_status=pending"
                "&setup_intent_client_secret=seti_secret&returned_from_redirect=true"
            ),
            "redirect_status": "pending",
            "settlement_status": "pending_verification",
            "payment_action": "SALE",
        })
        self.assertEqual(result["redirect_status"], "pending")
        self.assertEqual(result["settlement_status"], "pending_verification")
        self.assertIn("redirect_status=pending", result["final_redirect_url"])
        self.assertEqual(result["pending_url"], result["final_redirect_url"])
        self.assertNotIn("cs_live_secret", str(result))
        self.assertNotIn("seti_secret", str(result))


if __name__ == "__main__":
    unittest.main()
