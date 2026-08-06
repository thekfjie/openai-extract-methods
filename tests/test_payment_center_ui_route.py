import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PaymentCenterUiRouteTest(unittest.TestCase):
    def test_short_payment_routes_redirect_to_ui(self):
        source = (ROOT / "server.py").read_text(encoding="utf-8")
        self.assertIn('{"/payments", "/payments/extract", "/payments/center"}', source)
        self.assertIn('location = f"/ui{parsed.path}"', source)

    def test_main_server_proxies_paypal_protocol_for_local_ui(self):
        source = (ROOT / "server.py").read_text(encoding="utf-8")
        self.assertIn('if parsed.path.startswith("/paypal-protocol/api/"):', source)
        self.assertIn('self.handle_paypal_protocol_proxy(method, parsed)', source)
        self.assertIn('def handle_paypal_protocol_proxy(self, method: str, parsed: Any) -> None:', source)
        self.assertIn('port = int(os.getenv("PAYPAL_PROTOCOL_PORT", "18795"))', source)
        self.assertIn('target_path = "/api/" + suffix', source)
        self.assertIn('"PAYPAL_PROTOCOL_UNAVAILABLE"', source)


if __name__ == "__main__":
    unittest.main()
