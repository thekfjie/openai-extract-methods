import json
import tempfile
import time
import unittest
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread
from unittest.mock import patch

import server


class ServerRuntimeTests(unittest.TestCase):
    def test_uc_signup_log_timestamp_is_beijing_time(self) -> None:
        manager = server.UcSignupManager()
        manager.append_log("timezone-check")

        timestamp = manager.get_logs()[-1]["time"]
        self.assertTrue(timestamp.endswith("+08:00"), timestamp)

    def test_registered_retryable_signup_is_promoted_to_mail_admin(self) -> None:
        manager = server.UcSignupManager()
        with (
            patch.object(manager, "_run_one", return_value=("retryable", "phone wall", 75)),
            patch.object(manager, "_move_completed_mail_account") as move_mail,
            patch.object(server, "mark_signup_email_claimed"),
            patch.object(server, "mark_signup_email_retryable_hold"),
            patch.object(server, "registered_signup_email_keys", return_value={"person@example.com"}),
            patch.object(
                server,
                "promote_registered_opus_email",
                return_value={"configured": True, "imported": True},
            ) as promote,
        ):
            manager._run(["person@example.com"], {"moveMail": True, "mailPendingGroup": "pending"})

        promote.assert_called_once_with("person@example.com", "phone wall")
        move_mail.assert_called_once_with("person@example.com", "pending", "待授权")
        self.assertEqual(manager.get_state()["failed"], 1)

    def test_openai_callback_persists_opus_before_failed_sub2api_import(self) -> None:
        calls: list[str] = []
        opus_client = unittest.mock.Mock()
        opus_client.import_openai_oauth.side_effect = lambda *args, **kwargs: (
            calls.append("opus") or {"configured": True, "imported": True}
        )
        document = {
            "accounts": [
                {
                    "credentials": {
                        "access_token": "access-secret",
                        "refresh_token": "refresh-secret",
                    }
                }
            ]
        }

        class Handler:
            response = None

            @staticmethod
            def read_json_body():
                return {"code": "oauth-code", "state": "oauth-state", "email": "person@example.com"}

            def send_json(self, status, payload):
                self.response = (status, payload)

        handler = Handler()
        with (
            patch.object(server.SUB2API, "openai_exchange_code", return_value={"access_token": "access-secret"}),
            patch.object(server, "build_sub2api_document_from_openai_oauth", return_value=document),
            patch.object(server.OpusMailClient, "from_project", return_value=opus_client),
            patch.object(
                server.SUB2API,
                "import_accounts_document",
                side_effect=lambda payload: calls.append("sub2api") or (_ for _ in ()).throw(RuntimeError("down")),
            ),
            patch.object(
                server,
                "bind_sub2api_import_to_target_groups",
                side_effect=lambda *args, **kwargs: calls.append("bind") or {"success": True},
            ),
        ):
            handled = server.AppHandler.handle_sub2api_api(
                handler,
                "POST",
                "/api/sub2api/openai-callback",
                {},
            )

        self.assertTrue(handled)
        self.assertEqual(calls, ["opus", "sub2api", "bind", "opus"])
        self.assertEqual(handler.response[0], 200)
        self.assertTrue(handler.response[1]["opusMail"]["imported"])
        self.assertTrue(handler.response[1]["opusMail"]["statusMessageUpdated"])
        self.assertFalse(handler.response[1]["sub2api"]["success"])
        self.assertFalse(handler.response[1]["success"])

    def test_promote_registered_opus_email_uses_saved_signup_password(self) -> None:
        client = unittest.mock.Mock()
        client.import_registered_email.return_value = {"configured": True, "imported": True}
        with (
            patch.object(
                server,
                "load_signup_email_stage_state",
                return_value={
                    "person@example.com": {
                        "registered": True,
                        "password": "saved-password",
                    }
                },
            ),
            patch.object(server.OpusMailClient, "from_project", return_value=client),
        ):
            result = server.promote_registered_opus_email("Person@Example.com", "phone wall")

        self.assertTrue(result["imported"])
        client.import_registered_email.assert_called_once_with(
            email="Person@Example.com",
            password="saved-password",
            reason="phone wall",
        )

    def test_promote_registered_opus_email_does_not_overwrite_saved_web_at(self) -> None:
        with patch.object(
            server,
            "load_signup_email_stage_state",
            return_value={
                "person@example.com": {
                    "registered": True,
                    "webAccessTokenStoredInMailAdmin": True,
                    "oauthRefreshTokenStoredInMailAdmin": False,
                }
            },
        ), patch.object(server.OpusMailClient, "from_project") as client:
            result = server.promote_registered_opus_email("Person@Example.com", "phone wall")

        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "web_session_already_stored_rt_pending")
        client.assert_not_called()

    def test_zero_proxy_usage_limit_keeps_the_single_dynamic_gateway_available(self) -> None:
        candidate = {
            "url": "http://172.19.0.1:7905",
            "key": "http|172.19.0.1|7905||",
            "name": "Mihomo TW",
            "proxy": {"name": "Mihomo TW", "protocol": "http", "host": "172.19.0.1", "port": 7905},
        }
        original_limit = server.CONFIG.proxy_usage_max_per_window
        original_window = server.CONFIG.proxy_usage_window_seconds
        original_randomize = server.CONFIG.proxy_randomize
        try:
            server.CONFIG.proxy_usage_max_per_window = "0"
            server.CONFIG.proxy_usage_window_seconds = "86400"
            server.CONFIG.proxy_randomize = False
            with tempfile.TemporaryDirectory() as temporary_directory:
                usage_path = Path(temporary_directory) / "proxy_usage.json"
                usage_path.write_text(
                    json.dumps(
                        {
                            "events": [
                                {
                                    "id": f"old-{index}",
                                    "email": f"old-{index}@example.test",
                                    "proxyKey": candidate["key"],
                                    "status": "success",
                                    "reservedAtTs": time.time(),
                                }
                                for index in range(10)
                            ]
                        }
                    ),
                    encoding="utf-8",
                )
                with (
                    patch.object(server, "PROXY_USAGE_PATH", usage_path),
                    patch.object(server, "configured_signup_proxy_candidates", return_value=[candidate]),
                    patch.object(
                        server,
                        "get_email_proxy_binding",
                        return_value={
                            "proxyUrl": "http://172.19.0.1:7901",
                            "proxyKey": "http|172.19.0.1|7901||",
                            "region": "US",
                        },
                    ),
                    patch.object(server, "bind_email_proxy", return_value={}),
                ):
                    reservation = server.reserve_signup_proxy("new@example.test")
        finally:
            server.CONFIG.proxy_usage_max_per_window = original_limit
            server.CONFIG.proxy_usage_window_seconds = original_window
            server.CONFIG.proxy_randomize = original_randomize

        self.assertEqual(reservation["proxyUrl"], "http://172.19.0.1:7905")
        self.assertEqual(reservation["usageLimit"], 0)

    def test_explicit_task_proxy_overrides_global_mode(self) -> None:
        original_mode = server.CONFIG.signup_proxy_mode
        original_region = server.CONFIG.signup_proxy_region
        original_custom = server.CONFIG.signup_proxy_custom_url
        try:
            server.CONFIG.signup_proxy_mode = "custom"
            server.CONFIG.signup_proxy_region = "JP"
            server.CONFIG.signup_proxy_custom_url = ""
            candidates = server.configured_signup_proxy_candidates(
                "http://user:pass@us.cliproxy.io:3010"
            )
            empty = server.configured_signup_proxy_candidates("")
        finally:
            server.CONFIG.signup_proxy_mode = original_mode
            server.CONFIG.signup_proxy_region = original_region
            server.CONFIG.signup_proxy_custom_url = original_custom

        self.assertEqual(
            [candidate["url"] for candidate in candidates],
            ["http://user:pass@us.cliproxy.io:3010"],
        )
        self.assertEqual(candidates[0]["name"], "自定义注册代理")
        self.assertEqual(empty, [])

    def test_regional_mode_no_longer_auto_selects_mihomo(self) -> None:
        original_mode = server.CONFIG.signup_proxy_mode
        original_region = server.CONFIG.signup_proxy_region
        original_custom = server.CONFIG.signup_proxy_custom_url
        try:
            server.CONFIG.signup_proxy_mode = "regional"
            server.CONFIG.signup_proxy_region = "JP"
            server.CONFIG.signup_proxy_custom_url = ""
            empty = server.configured_signup_proxy_candidates("")
        finally:
            server.CONFIG.signup_proxy_mode = original_mode
            server.CONFIG.signup_proxy_region = original_region
            server.CONFIG.signup_proxy_custom_url = original_custom
        self.assertEqual(empty, [])

    def test_explicit_task_proxy_rebinds_over_email_binding(self) -> None:
        original_limit = server.CONFIG.proxy_usage_max_per_window
        original_window = server.CONFIG.proxy_usage_window_seconds
        original_randomize = server.CONFIG.proxy_randomize
        bound = {
            "proxyUrl": "http://172.19.0.1:7903",
            "proxyKey": "http|172.19.0.1|7903||",
            "proxyName": "Mihomo JP",
            "region": "JP",
        }
        try:
            server.CONFIG.proxy_usage_max_per_window = "0"
            server.CONFIG.proxy_usage_window_seconds = "86400"
            server.CONFIG.proxy_randomize = False
            with tempfile.TemporaryDirectory() as temporary_directory:
                usage_path = Path(temporary_directory) / "proxy_usage.json"
                usage_path.write_text(json.dumps({"events": []}), encoding="utf-8")
                with (
                    patch.object(server, "PROXY_USAGE_PATH", usage_path),
                    patch.object(server, "get_email_proxy_binding", return_value=bound),
                    patch.object(server, "bind_email_proxy", return_value={}) as bind_mock,
                ):
                    reservation = server.reserve_signup_proxy(
                        "ScottAnderson3615@outlook.com",
                        "http://user:pass@us.cliproxy.io:3010",
                    )
        finally:
            server.CONFIG.proxy_usage_max_per_window = original_limit
            server.CONFIG.proxy_usage_window_seconds = original_window
            server.CONFIG.proxy_randomize = original_randomize

        self.assertEqual(reservation["proxyUrl"], "http://user:pass@us.cliproxy.io:3010")
        self.assertEqual(reservation["proxyName"], "自定义注册代理")
        self.assertFalse(reservation["emailWasBound"] and reservation["proxyUrl"] == bound["proxyUrl"])
        bind_mock.assert_called()

    def test_cliproxy_mode_uses_the_visible_configured_url(self) -> None:
        original_mode = server.CONFIG.signup_proxy_mode
        original_url = server.CONFIG.cliproxy_proxy_url
        try:
            server.CONFIG.signup_proxy_mode = "cliproxy"
            server.CONFIG.cliproxy_proxy_url = "http://user:pass@us.cliproxy.io:3010"
            candidates = server.configured_signup_proxy_candidates()
        finally:
            server.CONFIG.signup_proxy_mode = original_mode
            server.CONFIG.cliproxy_proxy_url = original_url

        self.assertEqual([candidate["url"] for candidate in candidates], ["http://user:pass@us.cliproxy.io:3010"])

    def test_sub2api_signup_proxy_is_controlled_by_a_separate_switch(self) -> None:
        original = server.CONFIG.sub2api_import_use_signup_proxy
        regional = {"name": "Mihomo JP"}
        signup = {"name": "Cliproxy Dynamic"}
        try:
            with (
                patch.object(server, "configured_sub2api_proxy", return_value=regional),
                patch.object(server, "latest_signup_proxy_for_email", return_value=signup),
            ):
                server.CONFIG.sub2api_import_use_signup_proxy = False
                self.assertIs(server.sub2api_import_proxy_for_email("one@example.test"), regional)
                server.CONFIG.sub2api_import_use_signup_proxy = True
                self.assertIs(server.sub2api_import_proxy_for_email("one@example.test"), signup)
        finally:
            server.CONFIG.sub2api_import_use_signup_proxy = original

    def test_cliproxy_hostname_does_not_fake_the_browser_region(self) -> None:
        self.assertEqual(
            server.proxy_region_for_url(
                "http://user:pass@us.cliproxy.io:3010",
                "http://us.cliproxy.io:3010",
            ),
            "",
        )

    def test_custom_proxy_region_comes_from_the_detected_exit(self) -> None:
        candidate = {
            "url": "http://user:pass@proxy.example.test:3010",
            "key": "http|proxy.example.test|3010|user|pass",
            "name": "Custom proxy",
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            cache_path = Path(temporary_directory) / "proxy_geo_cache.json"
            with (
                patch.object(server, "PROXY_GEO_CACHE_PATH", cache_path),
                patch.object(server, "probe_proxy_location", return_value={"countryCode": "VN"}),
            ):
                region = server.proxy_region_for_candidate(candidate)

        self.assertEqual(region, "VN")

    def test_default_bind_is_loopback(self) -> None:
        self.assertEqual(server.DEFAULT_APP_SETTINGS["HOST"], "127.0.0.1")
        self.assertEqual(server.DEFAULT_APP_SETTINGS["PORT"], "13030")

    def test_ui_theme_defaults_to_purple_and_is_public(self) -> None:
        self.assertEqual(server.DEFAULT_APP_SETTINGS["UI_THEME"], "dark-purple")
        self.assertIn("UI_THEME", server.APP_SETTING_FIELDS)
        self.assertIn("UI_THEME", server.UI_SETTINGS_PUBLIC_FIELDS)
        with tempfile.TemporaryDirectory() as temporary_directory:
            config_path = Path(temporary_directory) / "config.json"
            config_path.write_text("{}", encoding="utf-8")
            with patch.object(server, "CONFIG_PATH", config_path):
                self.assertEqual(server.get_ui_settings()["settings"]["UI_THEME"], "dark-purple")

    def test_ui_theme_rejects_unknown_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "不支持的界面主题"):
            server.update_ui_settings({"UI_THEME": "neon-random"})

    def test_ui_theme_is_persisted_through_ui_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            config_path = Path(temporary_directory) / "config.json"
            config_path.write_text("{}", encoding="utf-8")
            with (
                patch.object(server, "CONFIG_PATH", config_path),
                patch.object(server, "reload_runtime_config"),
            ):
                result = server.update_ui_settings({"UI_THEME": "dark-matrix"})
            persisted = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(result["settings"]["UI_THEME"], "dark-matrix")
        self.assertEqual(persisted["UI_THEME"], "dark-matrix")

    def test_http_server_supports_clean_restarts(self) -> None:
        self.assertTrue(server.AutomyaiHTTPServer.allow_reuse_address)
        self.assertTrue(server.AutomyaiHTTPServer.daemon_threads)

    def test_oai_fingerprint_is_opt_in(self) -> None:
        self.assertEqual(server.DEFAULT_APP_SETTINGS["OAI_FINGERPRINT_ENABLED"], "false")
        self.assertIn("OAI_FINGERPRINT_PROVIDER", server.APP_SETTING_FIELDS)
        self.assertIn("OAI_FINGERPRINT_API_URL", server.APP_SETTING_FIELDS)
        self.assertIn("OAI_FINGERPRINT_API_KEY_FILE", server.APP_SETTING_FIELDS)

    def test_ui_root_redirects_to_trailing_slash(self) -> None:
        httpd = server.AutomyaiHTTPServer(("127.0.0.1", 0), server.AppHandler)
        thread = Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            connection = HTTPConnection("127.0.0.1", httpd.server_port, timeout=2)
            connection.request("GET", "/ui?view=overview")
            response = connection.getresponse()
            self.assertEqual(response.status, 302)
            self.assertEqual(response.getheader("Location"), "/ui/?view=overview")
            response.read()
            connection.close()
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_temp_mail_settings_reports_unconfigured_without_error(self) -> None:
        original_admin_password = server.CONFIG.admin_password
        original_api_url = server.CONFIG.temp_mail_api_url
        original_temp_mail_password = server.CONFIG.temp_mail_admin_password
        server.CONFIG.admin_password = ""
        server.CONFIG.temp_mail_api_url = ""
        server.CONFIG.temp_mail_admin_password = ""
        httpd = server.AutomyaiHTTPServer(("127.0.0.1", 0), server.AppHandler)
        thread = Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            connection = HTTPConnection("127.0.0.1", httpd.server_port, timeout=2)
            connection.request("GET", "/api/temp-mail/settings")
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(response.status, 200)
            self.assertEqual(
                payload,
                {
                    "configured": False,
                    "providerConfigured": False,
                    "adminConfigured": False,
                    "settings": {},
                },
            )
            connection.close()
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server.CONFIG.admin_password = original_admin_password
            server.CONFIG.temp_mail_api_url = original_api_url
            server.CONFIG.temp_mail_admin_password = original_temp_mail_password

    def test_outlook_account_control_payload_excludes_badmail(self) -> None:
        original_bad_group = server.CONFIG.mail_bad_group_name
        server.CONFIG.mail_bad_group_name = "badmail"
        try:
            payload = server.build_outlook_account_control_payload(
                [
                    {
                        "id": 1,
                        "email": "good@example.test",
                        "group_name": "gpt_old_account",
                        "status": "active",
                        "last_refresh_status": "success",
                    },
                    {
                        "id": 2,
                        "email": "bad@example.test",
                        "group_name": "badmail",
                        "status": "active",
                        "last_refresh_status": "success",
                    },
                    {
                        "id": 3,
                        "email": "failed@example.test",
                        "group_name": "默认分组",
                        "status": "active",
                        "last_refresh_status": "failed",
                    },
                ]
            )
        finally:
            server.CONFIG.mail_bad_group_name = original_bad_group
        self.assertEqual(payload["total"], 3)
        self.assertEqual(payload["selectable"], 1)
        self.assertEqual(payload["blocked"], 2)
        self.assertEqual(payload["badmail"], 1)
        by_id = {item["id"]: item for item in payload["accounts"]}
        self.assertTrue(by_id[1]["selectable"])
        self.assertFalse(by_id[2]["selectable"])
        self.assertEqual(by_id[2]["healthLabel"], "已隔离")
        self.assertFalse(by_id[3]["selectable"])
        self.assertEqual(by_id[3]["healthLabel"], "刷新失败")


if __name__ == "__main__":
    unittest.main()
