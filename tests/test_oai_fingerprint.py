import unittest
import json
import tempfile
from pathlib import Path
from unittest import mock

from integrations import oai_fingerprint as ofp


def sample_fingerprint(*, cloud: bool = False) -> dict:
    return {
        "profile_id": "profile-1",
        "preset": "windows-11-chrome",
        "source": "test",
        "impersonate": "chrome145",
        "impersonate_candidates": ["chrome145", "chrome"],
        "user_agent": "Mozilla/5.0 Chrome/145.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Chromium";v="145"',
        "sec_ch_ua_platform": '"Windows"',
        "sec_ch_ua_mobile": "?0",
        "screen_width": 1920,
        "screen_height": 1080,
        "lang": "en-US",
        "lang_full": "en-US,en;q=0.9",
        "languages": ["en-US", "en"],
        "timezone": "America/New_York",
        "platform": "Win32",
        "hardware_concurrency": 12,
        "device_memory": 8,
        "max_touch_points": 0,
        "do_not_track": "1",
        "device_id": "device-1",
        "device_name": "DESKTOP-TEST",
        "webgl_vendor": "Google Inc. (Intel)",
        "webgl_renderer": "ANGLE (Intel Test)",
        "runtime_config": {
            "launchArgs": [
                "--disable-background-mode",
                "--remote-debugging-port=0",
                "--user-data-dir=<USER_DATA_DIR>",
                "--start-maximized",
            ]
        },
        "profile": {
            "generator": {"baseDataSource": "authorized-provider" if cloud else "local-template"},
            "engine": {
                "userAgentMetadata": {
                    "brands": [{"brand": "Chromium", "version": "145"}],
                    "platform": "Windows",
                    "mobile": False,
                }
            },
            "navigator": {"mobile": False},
            "screen": {
                "width": 1920,
                "height": 1080,
                "availWidth": 1920,
                "availHeight": 1042,
                "colorDepth": 24,
                "pixelDepth": 24,
                "devicePixelRatio": 1.25,
            },
            "graphics": {
                "webglVendor": "Google Inc. (Intel)",
                "webglRenderer": "ANGLE (Intel Test)",
            },
            "runtime": {"colorScheme": "dark"},
        },
    }


class OaiFingerprintIntegrationTests(unittest.TestCase):
    def test_registry_has_exactly_the_four_supported_entries(self) -> None:
        self.assertEqual(
            set(ofp.ENTRY_FINGERPRINT_SPECS),
            {"uc_signup", "openai2", "openai3", "chatgpt_register"},
        )

    def test_uc_identity_is_non_linux_random_and_persistent(self) -> None:
        with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
            first = ofp.load_or_create_uc_fingerprint_identity(Path(first_dir))
            restarted = ofp.load_or_create_uc_fingerprint_identity(Path(first_dir))
            second = ofp.load_or_create_uc_fingerprint_identity(Path(second_dir))

            self.assertEqual(first, restarted)
            self.assertIn(first["preset"], ofp.UC_CHROMIUM_PRESETS)
            self.assertNotIn("linux", first["preset"])
            self.assertNotEqual(first["seed"], second["seed"])
            saved = json.loads((Path(first_dir) / ofp.UC_FINGERPRINT_IDENTITY_FILE).read_text())
            self.assertEqual(saved["seed"], first["seed"])
            self.assertEqual(saved["preset"], first["preset"])

    def test_entry_generation_uses_central_spec_and_marks_entry(self) -> None:
        value = sample_fingerprint()
        with mock.patch.object(ofp, "generate_oai_fingerprint", return_value=value) as generate:
            result = ofp.generate_entry_fingerprint("openai3", seed="fixed")
        self.assertEqual(result["entry"], "openai3")
        generate.assert_called_once_with(
            scope="openai3",
            default_preset="windows-11-chrome",
            default_browser_version="150.0.0.0",
            seed="fixed",
        )

    def test_cloud_source_is_derived_from_profile_provenance(self) -> None:
        self.assertFalse(ofp.fingerprint_is_cloud_based(sample_fingerprint()))
        self.assertTrue(ofp.fingerprint_is_cloud_based(sample_fingerprint(cloud=True)))
        self.assertTrue(ofp.fingerprint_summary(sample_fingerprint(cloud=True))["cloud"])

    def test_force_fingerprint_screen_aligns_runtime_and_generated_commands(self) -> None:
        fingerprint = sample_fingerprint()
        fingerprint["chromium_base_args"] = ["--no-sandbox", "--window-size=1920,1080"]
        fingerprint["chromium_cdp_commands"] = [
            {
                "method": "Page.addScriptToEvaluateOnNewDocument",
                "params": {"source": "globalThis.__automyaiFingerprintV2 = 'old-screen'"},
            },
            {
                "method": "Emulation.setDeviceMetricsOverride",
                "params": {
                    "width": 1920,
                    "height": 1080,
                    "screenWidth": 1920,
                    "screenHeight": 1080,
                    "deviceScaleFactor": 1.25,
                    "mobile": False,
                },
            }
        ]

        forced = ofp.force_fingerprint_screen(fingerprint, 1280, 800)

        self.assertEqual(forced["screen"], "1280x800")
        self.assertEqual(forced["screen_width"], 1280)
        self.assertEqual(forced["screen_height"], 800)
        self.assertEqual(forced["profile"]["screen"]["width"], 1280)
        self.assertEqual(forced["profile"]["screen"]["height"], 800)
        self.assertEqual(forced["profile"]["screen"]["devicePixelRatio"], 1)
        self.assertIn("--window-size=1280,800", forced["chromium_base_args"])
        self.assertNotIn("--window-size=1920,1080", forced["chromium_base_args"])
        metrics = forced["chromium_cdp_commands"][0]["params"]
        self.assertEqual(metrics["width"], 1280)
        self.assertEqual(metrics["height"], 800)
        self.assertEqual(metrics["screenWidth"], 1280)
        self.assertEqual(metrics["screenHeight"], 800)
        self.assertEqual(metrics["deviceScaleFactor"], 1)
        self.assertFalse(
            any(
                "old-screen" in str(item.get("params") or "")
                for item in forced["chromium_cdp_commands"]
            )
        )
        self.assertEqual(fingerprint["screen_width"], 1920)

    def test_http_session_kwargs_keep_tls_headers_and_proxy_coherent(self) -> None:
        result = ofp.curl_cffi_session_kwargs(
            sample_fingerprint(),
            fallback_impersonate="chrome",
            proxy="socks5://127.0.0.1:1080",
        )
        self.assertEqual(result["impersonate"], "chrome145")
        self.assertEqual(result["headers"]["User-Agent"], sample_fingerprint()["user_agent"])
        self.assertEqual(result["headers"]["DNT"], "1")
        self.assertEqual(result["proxies"]["https"], "socks5://127.0.0.1:1080")

    def test_proxy_region_alignment_updates_all_browser_locale_surfaces(self) -> None:
        fingerprint = sample_fingerprint()
        fingerprint["http_headers"] = {"Accept-Language": "pt-BR,pt;q=0.9"}
        fingerprint["sentinel_navigator"] = {"language": "pt-BR", "languages": "pt-BR,pt"}
        fingerprint["chromium_cdp_commands"] = [
            {"method": "Emulation.setTimezoneOverride", "params": {"timezoneId": "America/Sao_Paulo"}},
            {"method": "Emulation.setLocaleOverride", "params": {"locale": "pt-BR"}},
            {"method": "Network.setUserAgentOverride", "params": {"userAgent": fingerprint["user_agent"]}},
        ]

        aligned = ofp.align_fingerprint_locale_to_region(fingerprint, "KR")

        self.assertEqual(aligned["lang"], "ko-KR")
        self.assertEqual(aligned["languages"], ["ko-KR", "ko", "en"])
        self.assertEqual(aligned["timezone"], "Asia/Seoul")
        self.assertEqual(aligned["profile"]["locale"]["appLocale"], "ko-KR")
        self.assertEqual(aligned["http_headers"]["Accept-Language"], "ko-KR,ko;q=0.8,en;q=0.6")
        commands = dict(ofp.chromium_cdp_commands(aligned))
        self.assertEqual(commands["Emulation.setTimezoneOverride"]["timezoneId"], "Asia/Seoul")
        self.assertEqual(commands["Emulation.setLocaleOverride"]["locale"], "ko-KR")
        self.assertEqual(commands["Network.setUserAgentOverride"]["acceptLanguage"], "ko-KR,ko,en")
        self.assertEqual(fingerprint["lang"], "en-US")

    def test_vietnam_proxy_region_uses_vietnamese_locale_and_timezone(self) -> None:
        aligned = ofp.align_fingerprint_locale_to_region(sample_fingerprint(), "VN")

        self.assertEqual(aligned["lang"], "vi-VN")
        self.assertEqual(aligned["languages"], ["vi-VN", "vi", "en"])
        self.assertEqual(aligned["timezone"], "Asia/Ho_Chi_Minh")

    def test_sentinel_values_follow_the_same_profile(self) -> None:
        fingerprint = sample_fingerprint()
        self.assertEqual(
            ofp.sentinel_navigator_value(
                "appVersion",
                fingerprint,
                fallback_user_agent="fallback",
            ),
            "5.0 Chrome/145.0.0.0 Safari/537.36",
        )
        self.assertEqual(
            ofp.sentinel_navigator_value("doNotTrack", fingerprint, fallback_user_agent="fallback"),
            "1",
        )
        self.assertIn("GMT", ofp.fingerprint_browser_date(fingerprint))

    def test_chromium_plan_consumes_runtime_profile_without_placeholders(self) -> None:
        fingerprint = sample_fingerprint()
        args = ofp.chromium_launch_args(
            fingerprint,
            user_data_dir=Path("/tmp/profile-1"),
            proxy="http://127.0.0.1:8080",
            user_agent=fingerprint["user_agent"],
        )
        self.assertIn("--disable-background-mode", args)
        self.assertNotIn("--start-maximized", args)
        self.assertIn("--user-data-dir=/tmp/profile-1", args)
        self.assertIn("--proxy-server=http://127.0.0.1:8080", args)
        self.assertFalse(any("<USER_DATA_DIR>" in item for item in args))
        self.assertFalse(any(item.startswith("--remote-debugging-port=") for item in args))

        commands = dict(ofp.chromium_cdp_commands(fingerprint))
        self.assertIn("Page.addScriptToEvaluateOnNewDocument", commands)
        self.assertIn("Network.setUserAgentOverride", commands)
        self.assertIn("Emulation.setTimezoneOverride", commands)
        self.assertIn("Emulation.setDeviceMetricsOverride", commands)
        self.assertIn("Emulation.setHardwareConcurrencyOverride", commands)
        self.assertIn("Google Inc. (Intel)", commands["Page.addScriptToEvaluateOnNewDocument"]["source"])
        self.assertIn("getHighEntropyValues", commands["Page.addScriptToEvaluateOnNewDocument"]["source"])
        self.assertIn("Runtime.evaluate", commands)
        self.assertEqual(commands["Network.setUserAgentOverride"]["acceptLanguage"], "en-US,en")

    def test_cdp_failures_are_isolated(self) -> None:
        class Driver:
            def __init__(self):
                self.calls = []

            def execute_cdp_cmd(self, method, params):
                self.calls.append((method, params))
                if method == "Emulation.setLocaleOverride":
                    raise RuntimeError("unsupported")

        driver = Driver()
        failures = ofp.apply_chromium_fingerprint(driver, sample_fingerprint())
        self.assertEqual(len(failures), 1)
        self.assertIn("Emulation.setLocaleOverride", failures[0])
        self.assertGreater(len(driver.calls), 4)

    def test_go_generated_execution_fields_take_precedence(self) -> None:
        fingerprint = sample_fingerprint()
        fingerprint["http_headers"] = {
            "User-Agent": "go-generated-ua",
            "Accept-Language": "fr-FR,fr;q=0.9",
        }
        fingerprint["sentinel_navigator"] = {"appVersion": "go-app-version"}
        fingerprint["chromium_base_args"] = ["--no-sandbox", "--go-generated"]
        fingerprint["chromium_cdp_commands"] = [
            {"method": "Page.addScriptToEvaluateOnNewDocument", "params": {"source": "go-script"}},
            {"method": "Network.setUserAgentOverride", "params": {"userAgent": "go-generated-ua"}},
        ]

        self.assertEqual(ofp.fingerprint_http_headers(fingerprint)["User-Agent"], "go-generated-ua")
        self.assertEqual(
            ofp.sentinel_navigator_value("appVersion", fingerprint, fallback_user_agent="fallback"),
            "go-app-version",
        )
        args = ofp.chromium_launch_args(fingerprint, user_data_dir=Path("/tmp/go-profile"))
        self.assertIn("--go-generated", args)
        commands = ofp.chromium_cdp_commands(fingerprint, override_user_agent=False)
        self.assertEqual(commands[0], ("Page.addScriptToEvaluateOnNewDocument", {"source": "go-script"}))
        self.assertFalse(any(method == "Network.setUserAgentOverride" for method, _ in commands))
        self.assertEqual(commands[-1][0], "Runtime.evaluate")
        self.assertIn("getHighEntropyValues", commands[-1][1]["expression"])


    def test_chrome_proxy_server_strips_embedded_credentials(self) -> None:
        self.assertEqual(
            ofp.chrome_proxy_server_arg("http://user:pass@us.cliproxy.io:3010"),
            "http://us.cliproxy.io:3010",
        )
        args = ofp.chromium_launch_args(
            {},
            user_data_dir=Path("/tmp/proxy-auth"),
            proxy="http://user:p%40ss@host.example:3010",
        )
        self.assertIn("--proxy-server=http://host.example:3010", args)
        self.assertFalse(any("user:p" in item for item in args))


if __name__ == "__main__":
    unittest.main()
