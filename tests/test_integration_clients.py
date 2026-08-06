"""Guards for the upstream client modules extracted from server.py.

The clients live in `integrations/` but `server.py` re-exports them, because
`extensions_api.py` and the tools import them from there. These tests keep both
halves of that arrangement honest.
"""
from __future__ import annotations

import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import server
from integrations import (
    core_utils,
    herosms,
    mail_text,
    outlook_email_client,
    proxy_config,
    sub2api_client,
    temp_mail_client,
    text_utils,
)

RE_EXPORTS = {
    "HeroSmsClient": herosms,
    "HeroSmsError": herosms,
    "TeleAutoClient": herosms,
    "TeleAutoError": herosms,
    "PurchaseError": herosms,
    "STATUS_LABELS": herosms,
    "NORMALIZED_STATES": herosms,
    "TempMailClient": temp_mail_client,
    "TempMailError": temp_mail_client,
    "OutlookEmailClient": outlook_email_client,
    "OutlookEmailAdminClient": outlook_email_client,
    "OutlookEmailError": outlook_email_client,
    "Sub2ApiClient": sub2api_client,
    "Sub2ApiError": sub2api_client,
    "sub2api_account_group_ids": sub2api_client,
    "normalize_text": text_utils,
    "collect_string_values": text_utils,
    "html_to_text": text_utils,
    "ZH_COUNTRY_CHAR_MAP": text_utils,
    "parse_proxy_url": proxy_config,
    "proxy_url_from_parsed": proxy_config,
    "sub2api_proxy_key": proxy_config,
    "normalize_proxy_region": proxy_config,
    "known_mihomo_proxy_name": proxy_config,
    "proxy_name_for_url": proxy_config,
    "sub2api_proxy_from_url": proxy_config,
    "configured_sub2api_proxy": proxy_config,
    "parse_proxy_pool_urls": proxy_config,
    "configured_signup_proxy_candidates": proxy_config,
    "MIHOMO_SUB2API_PROFILES": proxy_config,
    "MIHOMO_DIRECT_PROXY_URL": proxy_config,
    "decode_mail_payload": mail_text,
    "extract_verification_code_from_mail": mail_text,
    "enrich_temp_mail_item": mail_text,
    "now_iso": core_utils,
    "parse_bool_flag": core_utils,
    "parse_positive_int": core_utils,
    "parse_timestamp": core_utils,
    "timestamp_is_future": core_utils,
    "strip_empty_values": core_utils,
    "decode_jwt_payload": core_utils,
    "email_key": core_utils,
    "load_json_file": core_utils,
    "save_json_file": core_utils,
    "normalize_fixed_price_value": core_utils,
    "generate_random_local_part": core_utils,
}

# proxy_config reads live settings, so it is allowed one deferred `from server`
# import inside a function body. Nothing here may import server at module scope.
MODULES = (
    herosms,
    temp_mail_client,
    outlook_email_client,
    sub2api_client,
    text_utils,
    core_utils,
    proxy_config,
    mail_text,
)


class ClientModuleTests(unittest.TestCase):
    def test_saved_cliproxy_alias_resolves_without_exposing_it_in_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "status.json"
            source.write_text(
                json.dumps({"proxy": "http://user:pass@us.cliproxy.io:3010"}),
                encoding="utf-8",
            )
            with patch.object(proxy_config, "CLIPROXY_SOURCE_PATHS", (source,)):
                urls = proxy_config.parse_proxy_pool_urls("CLIPROXY_SAVED")

        self.assertEqual(urls, ["http://user:pass@us.cliproxy.io:3010"])

    def test_server_re_exports_the_same_objects(self) -> None:
        for name, module in RE_EXPORTS.items():
            with self.subTest(name=name):
                self.assertIs(getattr(server, name), getattr(module, name))

    def test_clients_do_not_import_server_at_module_scope(self) -> None:
        # A module-scope `import server` here would be an import cycle. The one
        # place that needs live settings imports them inside the method instead.
        for module in MODULES:
            with self.subTest(module=module.__name__):
                source = Path(module.__file__).read_text(encoding="utf-8")
                for line in source.splitlines():
                    if line.startswith(("import server", "from server import")):
                        self.fail(f"{module.__name__} imports server at module scope: {line}")

    def test_error_hierarchy_survived_the_move(self) -> None:
        self.assertTrue(issubclass(herosms.TeleAutoError, herosms.HeroSmsError))
        self.assertTrue(issubclass(herosms.PurchaseError, herosms.HeroSmsError))
        for error in (temp_mail_client.TempMailError, outlook_email_client.OutlookEmailError, sub2api_client.Sub2ApiError):
            self.assertTrue(issubclass(error, Exception))
            self.assertFalse(issubclass(error, herosms.HeroSmsError))

    def test_text_helpers_behave(self) -> None:
        self.assertEqual(text_utils.normalize_text(" Hong Kong "), "hongkong")
        self.assertEqual(text_utils.collect_string_values({"a": ["x", ""], "b": {"c": "y"}}), ["x", "y"])
        self.assertEqual(text_utils.html_to_text("<p>hi</p><script>bad()</script>"), "hi")

    def test_proxy_pool_parses_explicit_and_authenticated_urls(self) -> None:
        self.assertEqual(
            proxy_config.parse_proxy_pool_urls("http://172.19.0.1:7905"),
            ["http://172.19.0.1:7905"],
        )
        self.assertEqual(
            proxy_config.parse_proxy_pool_urls("http://user:p%40ss@example.com:3010"),
            ["http://user:p%40ss@example.com:3010"],
        )

    def test_sub2api_proxy_defaults_to_jp_without_inheriting_signup_proxy(self) -> None:
        original = (
            server.CONFIG.sub2api_proxy_region,
            server.CONFIG.sub2api_proxy_url,
            server.CONFIG.sub2api_proxy_name,
            server.CONFIG.uc_signup_proxy,
            server.CONFIG.browser_proxy,
        )
        try:
            server.CONFIG.sub2api_proxy_region = ""
            server.CONFIG.sub2api_proxy_url = ""
            server.CONFIG.sub2api_proxy_name = ""
            server.CONFIG.uc_signup_proxy = "http://signup.example:9000"
            server.CONFIG.browser_proxy = "http://browser.example:9001"
            proxy = proxy_config.configured_sub2api_proxy()
        finally:
            (
                server.CONFIG.sub2api_proxy_region,
                server.CONFIG.sub2api_proxy_url,
                server.CONFIG.sub2api_proxy_name,
                server.CONFIG.uc_signup_proxy,
                server.CONFIG.browser_proxy,
            ) = original

        self.assertEqual(proxy["name"], "Mihomo JP")
        self.assertEqual(proxy["host"], "172.19.0.1")
        self.assertEqual(proxy["port"], 7903)


if __name__ == "__main__":
    unittest.main()


class ProxyFourSegmentParseTests(unittest.TestCase):
    def test_parse_host_port_user_pass(self):
        parsed = proxy_config.parse_proxy_url(
            "gw.dataimpulse.com:823:2f3bacb509260e84736e__cr.jp:724b36c321f332c9"
        )
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["host"], "gw.dataimpulse.com")
        self.assertEqual(parsed["port"], 823)
        self.assertEqual(parsed["username"], "2f3bacb509260e84736e__cr.jp")
        self.assertEqual(parsed["password"], "724b36c321f332c9")
        self.assertEqual(
            proxy_config.proxy_url_from_parsed(parsed),
            "http://2f3bacb509260e84736e__cr.jp:724b36c321f332c9@gw.dataimpulse.com:823",
        )
