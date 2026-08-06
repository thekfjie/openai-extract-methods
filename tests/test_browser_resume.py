from __future__ import annotations

import json
import stat
import tempfile
import unittest
from pathlib import Path

from integrations import browser_resume as br


class FakeSwitch:
    def __init__(self, driver):
        self.driver = driver

    def window(self, handle):
        self.driver.current_window_handle = handle


class FakeDriver:
    def __init__(self):
        self.window_handles = ["one", "two"]
        self.current_window_handle = "one"
        self.switch_to = FakeSwitch(self)
        self.pages = {
            "one": {"url": "https://chatgpt.com/auth/login?intent=signup", "title": "Signup"},
            "two": {"url": "https://auth.openai.com/oauth/authorize?code=secret&state=secret", "title": "Authorize"},
        }
        self.restored_cookies = []
        self.restored_payloads = []

    @property
    def current_url(self):
        return self.pages[self.current_window_handle]["url"]

    @property
    def title(self):
        return self.pages[self.current_window_handle]["title"]

    def execute_cdp_cmd(self, method, params):
        if method == "Network.getAllCookies":
            return {"cookies": [{"name": "session", "value": "secret", "domain": ".chatgpt.com", "path": "/", "secure": True}]}
        if method == "Network.setCookie":
            self.restored_cookies.append(params)
            return {"success": True}
        return {}

    def execute_script(self, script, *args):
        if "const dump = (storage)" in script:
            return {
                "localStorage": {"theme": "dark"},
                "sessionStorage": {"step": "email"},
                "fields": [{"name": "email", "id": "", "autocomplete": "email", "type": "email", "value": "person@example.com", "checked": False}],
            }
        if script.startswith("window.open"):
            handle = f"restored-{len(self.window_handles)}"
            self.window_handles.append(handle)
            self.pages[handle] = {"url": "about:blank", "title": ""}
            return None
        if args:
            self.restored_payloads.append(args[0])
        return None

    def get(self, url):
        self.pages[self.current_window_handle] = {"url": url, "title": "Restored"}

    def get_cookies(self):
        return []


class BrowserResumeTests(unittest.TestCase):
    def test_sanitize_url_removes_oauth_secrets_and_rejects_external_hosts(self):
        self.assertEqual(
            br.sanitize_resume_url("https://auth.openai.com/oauth/authorize?client_id=abc&code=secret&state=secret"),
            "https://auth.openai.com/oauth/authorize?client_id=abc",
        )
        self.assertEqual(br.sanitize_resume_url("http://localhost:1455/auth/callback?code=secret"), "https://chatgpt.com/")
        self.assertEqual(br.sanitize_resume_url("https://evil.example/path"), "")

    def test_capture_and_restore_tabs_storage_forms_and_cookies(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            captured_driver = FakeDriver()
            result = br.capture_browser_checkpoint(captured_driver, profile, email="person@example.com", stage="授权邮箱")
            path = profile / br.RESUME_STATE_FILE
            saved = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(result["tabs"], 2)
            self.assertEqual(saved["stage"], "授权邮箱")
            self.assertEqual(saved["tabs"][1]["url"], "https://auth.openai.com/oauth/authorize")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

            restored_driver = FakeDriver()
            restored_driver.window_handles = ["one"]
            restored_driver.pages = {"one": {"url": "about:blank", "title": ""}}
            restored = br.restore_browser_checkpoint(restored_driver, profile)
            self.assertTrue(restored["restored"])
            self.assertEqual(restored["tabs"], 2)
            self.assertEqual(restored["cookies"], 1)
            self.assertEqual(len(restored_driver.restored_payloads), 2)
            self.assertEqual(restored_driver.restored_payloads[0]["localStorage"]["theme"], "dark")


if __name__ == "__main__":
    unittest.main()
