import importlib.util
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "openai4" / "webapp.py"
SPEC = importlib.util.spec_from_file_location("automyai_openai4_webapp_test", MODULE_PATH)
assert SPEC and SPEC.loader
webapp = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = webapp
SPEC.loader.exec_module(webapp)


class OpenAI4WebappTests(unittest.TestCase):
    def setUp(self) -> None:
        webapp._reset_current_run_logs()
        webapp._preflight_cache = {}
        with webapp._lock:
            webapp._state.update({
                "running": False,
                "phase": "idle",
                "current_step": "",
                "error": "",
            })

    def test_preflight_starts_with_a_clean_visible_log_window(self) -> None:
        result = {"ok": True, "mail": {"accounts": []}}
        with (
            patch.object(webapp, "load_config", return_value={}),
            patch.object(webapp, "_run_preflight", return_value=result),
            patch.object(webapp, "_save_state"),
        ):
            response = webapp.preflight(webapp.StartReq())

        self.assertTrue(response["ok"])
        self.assertEqual(webapp._state["phase"], "ready")
        messages = [item["message"] for item in webapp._logs]
        self.assertIn("启动前检查开始；检查过程会逐项显示", messages)
        self.assertTrue(any(message.startswith("启动前检查通过") for message in messages))

    def test_reset_current_run_logs_keeps_disk_history(self) -> None:
        with patch.object(webapp.Path, "unlink") as unlink:
            webapp._logs.append({"time": "now", "level": "info", "message": "old"})
            webapp._reset_current_run_logs()

        self.assertEqual(webapp._logs, [])
        unlink.assert_not_called()

    def test_preflight_reuses_same_recent_inputs(self) -> None:
        request = webapp.StartReq(total=1)
        cached = {"ok": True, "mail": {"accounts": [], "checked": 1}, "proxy": {}, "sub2api": {}}
        webapp._remember_preflight(request, {}, cached)
        with (
            patch.object(webapp, "load_config", return_value={}),
            patch.object(webapp, "_run_preflight") as run_preflight,
            patch.object(webapp, "_save_state"),
        ):
            response = webapp.preflight(request)

        self.assertTrue(response["cached"])
        run_preflight.assert_not_called()

    def test_mail_opus_probes_share_inventory_and_run_concurrently(self) -> None:
        active = 0
        peak = 0
        guard = threading.Lock()

        class FakeReader:
            timeout = 30

            def probe_mapping_mail_access(self, mapping):
                nonlocal active, peak
                with guard:
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.04)
                with guard:
                    active -= 1
                return {"reachable": True, "mappingId": mapping["id"], "mailCount": 0}

        accounts = [
            {
                "id": index + 1,
                "email": f"mail-{index}@example.test",
                "group_name": webapp.OPUS_PENDING_GROUP,
                "status": "active",
                "provider": "opusMail",
                "opus_id": f"mapping-{index}",
            }
            for index in range(4)
        ]
        with (
            patch.object(webapp, "_outlook_accounts", return_value=accounts),
            patch.object(webapp, "_outlook_clients", return_value=(None, None)),
            patch.object(webapp.OpusMailAdminReader, "from_project", return_value=FakeReader()),
        ):
            result = webapp._prepare_accounts(
                webapp.StartReq(
                    total=2,
                    mail_source_group=webapp.OPUS_PENDING_GROUP,
                    mail_pending_group="oai_pending",
                    mail_success_group="oai_success",
                    mail_bad_group="badmail",
                ),
                {},
            )

        self.assertGreaterEqual(peak, 2)
        self.assertGreaterEqual(result["checked"], 2)


if __name__ == "__main__":
    unittest.main()
