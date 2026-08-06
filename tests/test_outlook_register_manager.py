import tempfile
import unittest
from pathlib import Path
from unittest import mock

from integrations import outlook_register_manager as orm


class OutlookRegisterManagerTests(unittest.TestCase):
    def test_parse_account_lines(self):
        text = "\n".join([
            "a@outlook.com----pass----cid----rtoken",
            "# comment",
            "b@hotmail.com----pass2----cid2----",
            "not-a-line",
        ])
        items = orm.parse_account_lines(text)
        self.assertEqual(len(items), 2)
        self.assertTrue(items[0]["hasRefreshToken"])
        self.assertFalse(items[1]["hasRefreshToken"])

    def test_start_requires_token_and_proxy(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty_proxy = Path(tmp) / "proxies.txt"
            missing_token = Path(tmp) / "captcharun.token"
            empty_proxy.write_text("", encoding="utf-8")
            with mock.patch.object(orm, "PROXY_FILE_PATH", empty_proxy), mock.patch.object(
                orm, "CAPTCHA_TOKEN_PATH", missing_token
            ), mock.patch.dict("os.environ", {"CAPTCHARUN_TOKEN": ""}):
                manager = orm.OutlookRegisterManager()
                result = manager.start({"domain": "outlook.com", "threads": 1})
                self.assertIn("error", result)

    def test_mask_secret(self):
        self.assertEqual(orm.mask_secret(""), "")
        self.assertIn("…", orm.mask_secret("abcdefghij"))

    def test_default_import_settings_shape(self):
        manager = orm.OutlookRegisterManager()
        settings = manager.default_import_settings()
        self.assertIn("enabledByDefault", settings)
        self.assertIn("groupName", settings)
        self.assertTrue(settings.get("enabledByDefault"))

    def test_import_registered_accounts_skips_incomplete_lines(self):
        manager = orm.OutlookRegisterManager()
        result = manager.import_registered_accounts([
            "a@outlook.com----p----cid----",
            "# comment",
            "not-valid",
        ])
        self.assertFalse(result.get("success"))
        self.assertIn("没有带 refresh_token", result.get("error", ""))


if __name__ == "__main__":
    unittest.main()
