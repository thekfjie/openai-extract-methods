import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PaymentCenterStaleCacheTest(unittest.TestCase):
    def test_paypal_form_migrates_legacy_proxy_arrays(self):
        source = (ROOT / "frontend" / "src" / "pages" / "PaymentCenter.jsx").read_text(encoding="utf-8")
        self.assertIn("function loadPayPalForm()", source)
        self.assertIn("Array.isArray(stored.proxies)", source)
        self.assertIn(".filter(Boolean).join('\\n')", source)
        self.assertIn("const [form, setForm] = useState(loadPayPalForm);", source)

    def test_proxy_count_is_safe_for_old_non_string_values(self):
        source = (ROOT / "frontend" / "src" / "pages" / "PaymentCenter.jsx").read_text(encoding="utf-8")
        self.assertIn("String(form.proxies || '').split(/\\r?\\n/)", source)


if __name__ == "__main__":
    unittest.main()
