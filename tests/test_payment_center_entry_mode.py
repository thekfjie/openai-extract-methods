import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PaymentCenterEntryModeTest(unittest.TestCase):
    def test_bare_payment_center_always_enters_paypal(self):
        source = (ROOT / "frontend" / "src" / "pages" / "PaymentCenter.jsx").read_text(encoding="utf-8")
        self.assertIn("new URLSearchParams(window.location.search).get('mode') === 'card' ? 'card' : 'paypal'", source)
        self.assertNotIn("window.localStorage.getItem(PAYMENT_CENTER_MODE_KEY) || 'paypal'", source)

    def test_all_saved_string_fields_migrate_legacy_arrays(self):
        source = (ROOT / "frontend" / "src" / "pages" / "PaymentCenter.jsx").read_text(encoding="utf-8")
        self.assertIn("if (typeof defaultValue === 'string')", source)
        self.assertIn("Array.isArray(value)", source)
        self.assertIn(".filter(Boolean).join('\\n')", source)
        self.assertIn("Number.isFinite(numberValue)", source)

    def test_card_mode_pause_controls_are_defined(self):
        source = (ROOT / "frontend" / "src" / "pages" / "PaymentCenter.jsx").read_text(encoding="utf-8")
        self.assertIn("const waitForResume = async (pausedMessage) =>", source)
        self.assertIn("const togglePause = () =>", source)
        self.assertIn("onClick={togglePause}", source)


if __name__ == "__main__":
    unittest.main()
