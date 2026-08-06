import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "openai5" / "webapp.py"
SPEC = importlib.util.spec_from_file_location("openai5_webapp", MODULE_PATH)
openai5 = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(openai5)


class OpenAI5SupervisorTests(unittest.TestCase):
    def test_defaults_are_api_only_desktop_and_no_fallback(self):
        config = openai5._public_config(openai5._default_config())
        self.assertEqual(config["mode"], "api-only")
        self.assertEqual(config["fallback"], "disabled")
        self.assertTrue(config["desktop_only"])
        self.assertTrue(config["require_authorized_cloud"])

    def test_local_template_is_not_accepted_as_api_source(self):
        self.assertNotIn("local-template", {"local-api", "authorized-cloud"})

    def test_targets_reject_non_official_hosts(self):
        with self.assertRaises(ValueError):
            openai5.ConfigReq(targets=["https://example.com/"])

    def test_proxy_display_redacts_credentials(self):
        self.assertEqual(
            openai5._public_proxy("http://user:secret@proxy.example:8080"),
            "http://***:***@proxy.example:8080",
        )

    def test_public_config_never_returns_proxy_credentials(self):
        config = openai5._public_config({
            **openai5._default_config(),
            "proxy_url": "http://user:secret@proxy.example:8080",
        })
        self.assertNotIn("user", config["proxy_url"])
        self.assertNotIn("secret", config["proxy_url"])
        self.assertEqual(config["proxy_url"], "http://***:***@proxy.example:8080")

    def test_api_key_requires_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "key"
            path.write_text("secret", encoding="utf-8")
            path.chmod(0o644)
            previous = openai5.API_KEY_FILE
            openai5.API_KEY_FILE = path
            try:
                with self.assertRaisesRegex(RuntimeError, "0600"):
                    openai5._api_key()
            finally:
                openai5.API_KEY_FILE = previous


if __name__ == "__main__":
    unittest.main()
