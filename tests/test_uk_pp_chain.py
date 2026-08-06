from __future__ import annotations

import base64
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_ROOT = ROOT / "internal" / "paypalprotocol" / "protocol"
if str(PROTOCOL_ROOT) not in sys.path:
    sys.path.insert(0, str(PROTOCOL_ROOT))

try:
    from paypal.flow import PayPalFlow  # noqa: E402
    from paypal.models import BillingAddress, CardInfo, UserInfo  # noqa: E402
    from paypal.session import PayPalAuthChallengeError, PayPalSession  # noqa: E402
    from paypal import graphql  # noqa: E402
    DEPENDENCIES_AVAILABLE = True
except ModuleNotFoundError:
    PayPalFlow = None
    BillingAddress = CardInfo = UserInfo = None
    PayPalAuthChallengeError = PayPalSession = None
    graphql = None
    DEPENDENCIES_AVAILABLE = False


class _Response:
    status_code = 200
    content = b"fixture"
    text = ""
    headers = {}
    url = "https://www.paypal.com/checkoutweb/drop"


class _ChallengeResponse:
    status_code = 200
    content = b"fixture-html"
    text = """<!DOCTYPE html><html><script>pgrp=authchallengenodeweb/layouts/master.html.dust&amp;</script><div id=\"captcha-standalone\">SECRET-FIXTURE</div></html>"""
    headers = {"content-type": "text/html; charset=utf-8", "paypal-debug-id": "DEBUG-FIXTURE"}

    def json(self):
        raise ValueError("not json")


class _CookieJar:
    def set(self, *_args, **_kwargs):
        return None


class _Client:
    cookies = _CookieJar()


class _Session:
    def __init__(self):
        self.client = _Client()
        self.calls = []
        self.state = None

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Response()

    def _sync_state_cookies(self):
        return None


def _flow() -> PayPalFlow:
    user = UserInfo(
        first_name="A", last_name="B", email="a@example.test",
        phone="+447512345678", phone_local="7512345678", phone_country_code="+44",
        password="fixture", dob="01/01/1990", nationality="GB",
    )
    card = CardInfo(number="4111111111111111", expiry="01/2030", cvv="123")
    address = BillingAddress(
        street="1 Test Street", house_number="", district="", city="London",
        state="", postal_code="SW1A 1AA", country="GB",
    )
    return PayPalFlow(
        ba_token="BA-FIXTURE", user=user, card=card, address=address,
        country="GB", locale="en_GB", ec_token="EC-FIXTURE",
    )


@unittest.skipUnless(DEPENDENCIES_AVAILABLE, "PayPal protocol dependencies are not installed in the base test environment")
class UKPPChainTests(unittest.TestCase):
    def test_graphql_html_is_typed_as_auth_challenge_without_body_leak(self):
        session = PayPalSession.__new__(PayPalSession)
        session.state = SimpleNamespace(
            ec_token="EC-FIXTURE", ba_token="BA-FIXTURE", paypal_client_metadata_id="",
            signup_url="https://www.paypal.com/checkoutweb/signup", euat_token="",
            country="GB", locale="en_GB",
        )
        session.post = lambda *_args, **_kwargs: _ChallengeResponse()
        with self.assertRaises(PayPalAuthChallengeError) as caught:
            session.graphql("SignUpNewMemberMutation", "mutation Fixture { fixture }", {"token": "EC-FIXTURE"})
        error = caught.exception
        self.assertEqual(error.status, 200)
        self.assertEqual(error.paypal_debug_id, "DEBUG-FIXTURE")
        self.assertEqual(error.page_family, "authchallengenodeweb")
        self.assertEqual(error.challenge_kind, "recaptcha")
        self.assertNotIn("SECRET-FIXTURE", str(error))

    def test_signup_challenge_stops_without_card_retry(self):
        flow = _flow()
        calls = []
        events = []
        flow.event_callback = events.append

        def challenge(*_args):
            calls.append(True)
            raise PayPalAuthChallengeError(
                operation="SignUpNewMemberMutation",
                status=200,
                paypal_debug_id="DEBUG-FIXTURE",
                page_family="authchallengenodeweb",
                challenge_kind="recaptcha",
            )

        flow._send_signup_attempt = challenge
        with self.assertRaises(PayPalAuthChallengeError):
            flow._signup_with_card_retry("EC-FIXTURE", "https://www.paypal.com/checkoutweb/signup")
        self.assertEqual(len(calls), 1)
        self.assertEqual(events[-1]["phase"], "account")
        self.assertTrue(events[-1]["challenge"]["manualActionRequired"])

    def test_hermes_url_matches_uk_capture_flags(self):
        flow = _flow()
        flow.state.ssrt = "SSRT-FIXTURE"
        url = flow._build_hermes_url()
        query = parse_qs(urlparse(url).query)
        self.assertEqual(query["modxo_redirect_reason"], ["guest_user"])
        self.assertEqual(query["fromSignupLite"], ["true"])
        self.assertEqual(query["addFIContingency"], ["noretry"])
        self.assertEqual(query["redirectToHermes"], ["true"])
        self.assertEqual(query["fallback"], ["1"])
        self.assertEqual(
            base64.b64decode(query["reason"][0]).decode(),
            "R_ERROR",
        )
        self.assertNotIn("billingLite", query)
        self.assertEqual(parse_qs(urlparse(flow._build_hermes_url(billing_lite=True)).query)["billingLite"], ["1"])

    def test_checkoutweb_drop_is_loaded_after_signup_with_euat(self):
        flow = _flow()
        flow.state.signup_url = "https://www.paypal.com/checkoutweb/signup?fixture=1"
        flow.state.euat_token = "EUAT-FIXTURE"
        session = _Session()
        flow.session = session
        flow._load_checkoutweb_drop()
        self.assertTrue(flow.state.checkout_drop_loaded)
        self.assertEqual(len(session.calls), 1)
        url, kwargs = session.calls[0]
        self.assertEqual(url, "https://www.paypal.com/checkoutweb/drop")
        self.assertEqual(kwargs["headers"]["Referer"], flow.state.signup_url)
        self.assertEqual(kwargs["headers"]["X-PayPal-Internal-EUAT"], "EUAT-FIXTURE")

    def test_fn_dt_cookie_becomes_separate_authorize_cmid(self):
        flow = _flow()
        flow.session._sync_state_cookies = lambda: None
        flow.session.client.cookies = _CookieJar()
        flow.state.update_from_cookies({"fn_dt": "CMID-FIXTURE"})
        self.assertEqual(flow.state.paypal_client_metadata_id, "CMID-FIXTURE")

    def test_graphql_queries_match_uk_reference_payloads(self):
        pairs = {
            "CookieBannerQuery": graphql.COOKIE_BANNER_QUERY,
            "DeferredFeature": graphql.DEFERRED_FEATURE_QUERY,
            "CheckoutSessionDataQuery": graphql.CHECKOUT_SESSION_DATA_QUERY,
            "GriffinMetadataQuery": graphql.GRIFFIN_METADATA_QUERY,
            "SignUpNewMemberMutation": graphql.SIGNUP_NEW_MEMBER_MUTATION,
        }
        for name, query in pairs.items():
            ref = json.loads((PROTOCOL_ROOT / "_uk_refs" / f"{name}.req.json").read_text())
            if isinstance(ref, list):
                ref = ref[0]
            self.assertEqual(query.split(), ref["query"].split(), name)

    def test_addcard_contingency_with_euat_enters_identity_elevation(self):
        flow = _flow()
        calls = []
        flow._send_signup_attempt = lambda *_args: calls.append(True) or {
            "errors": [{
                "message": "R_ERROR",
                "checkpoints": ["addCard"],
                "contingency": True,
                "errorData": {
                    "accessToken": "EUAT-FIXTURE",
                    "0": {"field": "cardNumber", "code": "CARD_GENERIC_ERROR"},
                },
            }],
        }
        flow._signup_with_card_retry("EC-FIXTURE", "https://www.paypal.com/checkoutweb/signup")
        self.assertEqual(len(calls), 1)
        self.assertEqual(flow.state.euat_token, "EUAT-FIXTURE")
        self.assertEqual(flow.buyer_mode, "identity_elevation")
        self.assertEqual(flow.identity_elevation["funding_errors"], ["R_ERROR"])
        self.assertEqual(flow.identity_elevation["funding_checkpoints"], ["addCard"])

    def test_success_redirect_is_classified_as_confirmed(self):
        result = PayPalFlow._classify_merchant_result(
            "https://pay.example.test/c/pay/fixture?redirect_status=succeeded"
            "&return_url=https%3A%2F%2Fmerchant.test%2Fverify"
        )
        self.assertEqual(result["redirect_status"], "succeeded")
        self.assertEqual(result["settlement_status"], "confirmed")
        self.assertEqual(result["pending_url"], "")

    def test_intermediate_status_success_is_only_authorized(self):
        result = PayPalFlow._classify_merchant_result(
            "https://merchant.example.test/return?status=success&token=EC-FIXTURE"
        )
        self.assertEqual(result["redirect_status"], "")
        self.assertEqual(result["settlement_status"], "authorized")
        self.assertTrue(result["pending_url"])

    def test_pending_redirect_is_pending_verification(self):
        result = PayPalFlow._classify_merchant_result(
            "https://pay.example.test/c/pay/fixture?redirect_status=pending"
            "&return_url=https%3A%2F%2Fmerchant.test%2Fverify"
        )
        self.assertEqual(result["redirect_status"], "pending")
        self.assertEqual(result["settlement_status"], "pending_verification")
        self.assertTrue(result["pending_url"])


if __name__ == "__main__":
    unittest.main()
