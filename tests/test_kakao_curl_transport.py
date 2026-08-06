from __future__ import annotations

import contextlib
import io
import unittest
from unittest import mock

from integrations import kakao_curl_transport as transport


class KakaoCurlTransportTests(unittest.TestCase):
    def test_upstream_kakao_is_a_hard_requirement(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(transport.FlowError, "did not advertise kakao_pay"):
                transport.require_upstream_kakao(
                    "bootstrap init",
                    "0",
                    "KRW",
                    ["card", "link"],
                    require_zero=False,
                )
            transport.require_upstream_kakao(
                "bootstrap init",
                "29000",
                "KRW",
                ["card", "kakao_pay", "naver_pay"],
                require_zero=False,
            )

    def test_post_promotion_stage_requires_zero_krw(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(transport.FlowError, "not a zero KRW checkout"):
                transport.require_upstream_kakao(
                    "post-promotion init",
                    "29000",
                    "KRW",
                    ["card", "kakao_pay"],
                    require_zero=True,
                )

    def test_request_validation_keeps_provider_on_exact_checkout_identity(self) -> None:
        proxy = "http://user-country-KR:password@proxy.example:3010"
        payload = transport.validate_request(
            {
                "accessToken": "secret-access-token",
                "checkoutProxy": proxy,
                "promotionProxy": proxy.replace("country-KR", "country-TR"),
                "providerProxy": proxy,
            }
        )
        self.assertEqual(payload["providerProxy"], payload["checkoutProxy"])
        self.assertEqual(payload["mode"], transport.KAKAO_MODE_ELIGIBILITY)
        self.assertTrue(payload["eligibilityOnly"])

        provider_payload = transport.validate_request(
            {
                "accessToken": "provider-secret-token",
                "checkoutProxy": proxy,
                "promotionProxy": proxy.replace("country-KR", "country-JP"),
                "providerProxy": proxy,
                "mode": "provider_link",
                "eligibilityOnly": False,
            }
        )
        self.assertEqual(provider_payload["mode"], transport.KAKAO_MODE_PROVIDER_LINK)
        self.assertFalse(provider_payload["eligibilityOnly"])

        with self.assertRaisesRegex(transport.FlowError, "exact checkout proxy identity"):
            transport.validate_request(
                {
                    "accessToken": "another-secret-token",
                    "checkoutProxy": proxy,
                    "promotionProxy": proxy,
                    "providerProxy": proxy.replace("proxy.example", "other.example"),
                }
            )

        with self.assertRaisesRegex(transport.FlowError, "disagree"):
            transport.validate_request(
                {
                    "accessToken": "conflicting-mode-token",
                    "checkoutProxy": proxy,
                    "promotionProxy": proxy,
                    "providerProxy": proxy,
                    "mode": "provider_link",
                    "eligibilityOnly": True,
                }
            )

    def test_registered_tokens_and_proxy_credentials_are_redacted(self) -> None:
        token = "redaction-test-access-token"
        proxy = "http://redaction-user:redaction-password@proxy.example:3010"
        transport.register_secret(token)
        transport.register_secret(proxy)
        redacted = transport.redact_text(f"token={token} proxy={proxy} user=redaction-user")
        self.assertNotIn(token, redacted)
        self.assertNotIn(proxy, redacted)
        self.assertNotIn("redaction-user", redacted)
        self.assertIn("[redacted]", redacted)

    def test_payment_methods_are_observed_from_upstream_fields_only(self) -> None:
        payload = {
            "payment_method_types": ["card", "kakao_pay"],
            "nested": {"ordered_payment_method_types": ["naver_pay", "card"]},
            "unrelated": "kakao_pay",
        }
        self.assertEqual(
            transport.payment_methods(payload),
            ["card", "kakao_pay", "naver_pay"],
        )

    def test_checkout_attempts_alternate_only_legal_promo_shapes(self) -> None:
        self.assertEqual(
            transport.checkout_variants(True, 5),
            [
                ("创建时带 Promotion", True),
                ("创建时不带 Promotion，命中后再正常 update", False),
                ("创建时带 Promotion", True),
                ("创建时不带 Promotion，命中后再正常 update", False),
                ("创建时带 Promotion", True),
            ],
        )
        self.assertEqual(
            transport.checkout_variants(False, 3),
            [("创建时不带 Promotion", False)] * 3,
        )

    def test_eligibility_probe_stops_after_bootstrap_hit(self) -> None:
        partial = {
            "ok": False,
            "method": "kakao",
            "country": "KR",
            "currency": "KRW",
            "extractionStatus": "failed",
            "paymentStatus": "not_started",
        }
        checkout_session = mock.Mock()
        bootstrap_result = (
            checkout_session,
            "cs_live_probe",
            "pk_live_probe",
            "openai_llc",
            {},
            "https://checkout.stripe.com/c/pay/cs_live_probe",
        )
        request = {
            "accessToken": "probe-token",
            "checkoutProxy": "http://user:pass@proxy.example:3010",
            "promotionProxy": "http://user:pass@proxy.example:3010",
            "providerProxy": "http://user:pass@proxy.example:3010",
            "eligibilityOnly": True,
            "promoEnabled": False,
        }
        with mock.patch.object(transport, "bootstrap_kakao_checkout", return_value=bootstrap_result), \
             mock.patch.object(transport, "update_checkout_promotion") as promotion, \
             mock.patch.object(transport, "create_payment_method") as payment_method, \
             contextlib.redirect_stdout(io.StringIO()):
            result = transport.run_flow(request, partial)

        self.assertTrue(result["ok"])
        self.assertEqual(result["decision"], "eligible")
        self.assertEqual(result["extractionStatus"], "probe_complete")
        self.assertEqual(result["paymentStatus"], "not_started")
        self.assertTrue(result["metadata"]["stoppedBeforePayment"])
        self.assertNotIn("checkoutId", result)
        self.assertNotIn("paymentMethodId", result)
        self.assertNotIn("longUrl", result)
        checkout_session.close.assert_called_once()
        promotion.assert_not_called()
        payment_method.assert_not_called()

    def test_eligibility_probe_returns_ineligible_without_payment_actions(self) -> None:
        partial = {
            "ok": False,
            "method": "kakao",
            "country": "KR",
            "currency": "KRW",
            "availableMethods": ["card", "link"],
            "metadata": {"bootstrapMethods": ["card", "link"]},
            "extractionStatus": "failed",
            "paymentStatus": "not_started",
        }
        request = {
            "accessToken": "probe-token",
            "checkoutProxy": "http://user:pass@proxy.example:3010",
            "promotionProxy": "http://user:pass@proxy.example:3010",
            "providerProxy": "http://user:pass@proxy.example:3010",
            "eligibilityOnly": True,
            "promoEnabled": False,
        }
        with mock.patch.object(
            transport,
            "bootstrap_kakao_checkout",
            side_effect=transport.KakaoNotAdvertised("methods=card,link"),
        ), mock.patch.object(transport, "create_payment_method") as payment_method, contextlib.redirect_stdout(io.StringIO()):
            result = transport.run_flow(request, partial)

        self.assertTrue(result["ok"])
        self.assertEqual(result["decision"], "ineligible")
        self.assertEqual(result["availableMethods"], ["card", "link"])
        self.assertTrue(result["metadata"]["stoppedBeforePayment"])
        payment_method.assert_not_called()

    def test_provider_link_retries_the_entire_chain_and_stops_on_success(self) -> None:
        request = {
            "accessToken": "provider-token",
            "checkoutProxy": "http://user:pass@proxy.example:3010",
            "promotionProxy": "http://promo:pass@proxy.example:3010",
            "providerProxy": "http://user:pass@proxy.example:3010",
            "mode": transport.KAKAO_MODE_PROVIDER_LINK,
            "eligibilityOnly": False,
            "checkoutAttempts": 10,
        }
        partial = transport.new_partial_result()
        success = {
            **transport.new_partial_result(),
            "ok": True,
            "providerRedirectUrl": "https://example.nicepay.co.kr/pay/ready",
            "longUrl": "https://example.nicepay.co.kr/pay/ready",
            "extractionStatus": "provider_link_ready",
            "paymentStatus": "awaiting_kakao_payment",
        }
        with mock.patch.object(
            transport,
            "run_provider_link_attempt",
            side_effect=[transport.FlowError("promotion failed"), transport.FlowError("TLS failed"), success],
        ) as full_attempt, mock.patch.object(transport.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
            result = transport.run_flow(request, partial)

        self.assertTrue(result["ok"])
        self.assertEqual(full_attempt.call_count, 3)
        self.assertEqual(
            [entry["status"] for entry in result["metadata"]["fullChainAttempts"]],
            ["failed", "failed", "success"],
        )
        self.assertEqual([call.args[8] for call in full_attempt.call_args_list], [1, 2, 3])
        self.assertTrue(all(call.args[9] == 10 for call in full_attempt.call_args_list))

    def test_provider_link_honors_ten_full_chain_attempts(self) -> None:
        request = {
            "accessToken": "provider-token",
            "checkoutProxy": "http://user:pass@proxy.example:3010",
            "promotionProxy": "http://promo:pass@proxy.example:3010",
            "providerProxy": "http://user:pass@proxy.example:3010",
            "mode": transport.KAKAO_MODE_PROVIDER_LINK,
            "eligibilityOnly": False,
            "checkoutAttempts": 10,
        }
        partial = transport.new_partial_result()
        with mock.patch.object(
            transport,
            "run_provider_link_attempt",
            side_effect=transport.FlowError("retryable late-stage failure"),
        ) as full_attempt, mock.patch.object(transport.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(transport.FlowError, "after 10 full attempts"):
                transport.run_flow(request, partial)

        self.assertEqual(full_attempt.call_count, 10)
        self.assertEqual(len(partial["metadata"]["fullChainAttempts"]), 10)
        self.assertTrue(all(entry["status"] == "failed" for entry in partial["metadata"]["fullChainAttempts"]))



    def test_tls_error_detection_covers_openssl_invalid_library(self) -> None:
        self.assertTrue(
            transport.is_transport_tls_error(
                "Stripe activation failed: Failed to perform, curl: (35) TLS connect error: "
                "error:00000000:invalid library (0):OPENSSL_internal:invalid library (0)."
            )
        )
        self.assertFalse(transport.is_transport_tls_error("approve result=blocked"))

    def test_tls_soft_retry_keeps_fingerprint_and_budget(self) -> None:
        request = {
            "accessToken": "provider-token",
            "checkoutProxy": "http://user:pass@proxy.example:3010",
            "promotionProxy": "http://promo:pass@proxy.example:3010",
            "providerProxy": "http://user:pass@proxy.example:3010",
            "mode": transport.KAKAO_MODE_PROVIDER_LINK,
            "eligibilityOnly": False,
            "checkoutAttempts": 3,
            "tlsSoftRetries": 4,
            "browserProfile": "chrome131-win",
        }
        partial = transport.new_partial_result()
        success = {
            **transport.new_partial_result(),
            "ok": True,
            "providerRedirectUrl": "https://example.nicepay.co.kr/pay/ready",
            "longUrl": "https://example.nicepay.co.kr/pay/ready",
            "extractionStatus": "provider_link_ready",
            "paymentStatus": "awaiting_kakao_payment",
        }
        tls_error = transport.FlowError(
            "Stripe activation failed: Failed to perform, curl: (35) TLS connect error: "
            "error:00000000:invalid library (0):OPENSSL_internal:invalid library (0)."
        )
        with mock.patch.object(
            transport,
            "run_provider_link_attempt",
            side_effect=[tls_error, tls_error, success],
        ) as full_attempt, mock.patch.object(
            transport,
            "select_browser_profile",
            wraps=transport.select_browser_profile,
        ) as select_profile, mock.patch.object(
            transport.time,
            "sleep",
        ), contextlib.redirect_stdout(io.StringIO()) as stdout:
            result = transport.run_flow(request, partial)

        self.assertTrue(result["ok"])
        # Soft TLS retries stay on the same full-chain attempt index.
        self.assertEqual([call.args[8] for call in full_attempt.call_args_list], [1, 1, 1])
        self.assertEqual(full_attempt.call_count, 3)
        # No fingerprint rotation for pure TLS noise.
        self.assertNotIn("fingerprint.rotate", stdout.getvalue())
        self.assertIn("TLS 软重试", stdout.getvalue())
        self.assertEqual(result["metadata"]["fullChainAttempts"][-1]["status"], "success")
        self.assertEqual(result["metadata"]["fullChainAttempts"][-1]["tlsSoftRetries"], 2)
        # Profile selection happens for the real full attempt path, not for each TLS soft retry.
        # run_flow picks an initial profile once, then run_provider_link_flow picks again for attempt 1.
        self.assertEqual(select_profile.call_count, 2)
        self.assertTrue(all(call.kwargs.get("attempt") == 1 for call in select_profile.call_args_list if "attempt" in call.kwargs))

    def test_tls_soft_retry_exhaustion_does_not_burn_remaining_attempts(self) -> None:
        request = {
            "accessToken": "provider-token",
            "checkoutProxy": "http://user:pass@proxy.example:3010",
            "promotionProxy": "http://promo:pass@proxy.example:3010",
            "providerProxy": "http://user:pass@proxy.example:3010",
            "mode": transport.KAKAO_MODE_PROVIDER_LINK,
            "eligibilityOnly": False,
            "checkoutAttempts": 6,
            "tlsSoftRetries": 2,
            "browserProfile": "chrome131-win",
        }
        partial = transport.new_partial_result()
        tls_error = transport.FlowError(
            "Stripe activation failed: Failed to perform, curl: (35) TLS connect error: "
            "error:00000000:invalid library (0):OPENSSL_internal:invalid library (0)."
        )
        with mock.patch.object(
            transport,
            "run_provider_link_attempt",
            side_effect=tls_error,
        ) as full_attempt, mock.patch.object(transport.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(transport.FlowError, "TLS transport failed after 2 soft retries"):
                transport.run_flow(request, partial)

        # 1 initial try + 2 soft retries, then stop. Never burn remaining 5 full attempts.
        self.assertEqual(full_attempt.call_count, 3)
        self.assertTrue(all(call.args[8] == 1 for call in full_attempt.call_args_list))


if __name__ == "__main__":
    unittest.main()
