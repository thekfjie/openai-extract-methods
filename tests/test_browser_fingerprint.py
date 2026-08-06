import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from integrations import browser_fingerprint as bf


def sample_bundle() -> dict:
    return {
        "profile": {
            "id": "profile-1",
            "seed": "seed-1",
            "preset": "windows-11-chrome",
            "engine": {
                "family": "Chrome",
                "version": "145.0.0.0",
                "userAgent": "Mozilla/5.0 Chrome/145.0.0.0 Safari/537.36",
                "userAgentMetadata": {
                    "brands": [
                        {"brand": "Chromium", "version": "145"},
                        {"brand": "Google Chrome", "version": "145"},
                    ],
                    "platform": "Windows",
                    "mobile": False,
                },
            },
            "locale": {
                "appLocale": "en-US",
                "acceptLanguage": "en-US,en;q=0.9",
                "timezone": "America/New_York",
            },
            "navigator": {
                "platform": "Win32",
                "hardwareConcurrency": 12,
                "deviceMemory": 16,
                "maxTouchPoints": 0,
                "doNotTrack": True,
            },
            "screen": {"width": 1920, "height": 1080},
            "machine": {"computerName": "DESKTOP-TEST"},
        },
        "roxyConfig": {"userAgent": "test"},
        "runtimeConfig": {"engine": "chrome"},
    }


class FakeHttpResponse:
    def __init__(self, payload: object):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self, _limit: int) -> bytes:
        return self.payload


class BrowserFingerprintTests(unittest.TestCase):
    def test_disabled_keeps_existing_flow(self) -> None:
        with mock.patch.object(bf, "_load_app_config", return_value={}):
            with mock.patch.dict(os.environ, {"OAI_FINGERPRINT_ENABLED": "false"}, clear=False):
                self.assertIsNone(
                    bf.generate_oai_fingerprint(
                        scope="openai2",
                        default_preset="macos-intel-chrome",
                        default_browser_version="145.0.0.0",
                    )
                )

    def test_normalize_bundle_preserves_full_profile_and_legacy_fields(self) -> None:
        result = bf.normalize_bundle(sample_bundle(), source="test")
        self.assertEqual(result["impersonate"], "chrome145")
        self.assertEqual(result["impersonate_candidates"], ["chrome145", "chrome"])
        self.assertEqual(result["screen"], "1920x1080")
        self.assertEqual(result["lang_full"], "en-US,en;q=0.9")
        self.assertEqual(result["platform"], "Win32")
        self.assertEqual(result["hardware_concurrency"], 12)
        self.assertEqual(result["sec_ch_ua_platform"], '"Windows"')
        self.assertIn('"Chromium";v="145"', result["sec_ch_ua"])
        self.assertEqual(result["profile"]["machine"]["computerName"], "DESKTOP-TEST")

    def test_generator_uses_fixed_seed_without_putting_headers_in_command(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout=json.dumps(sample_bundle()), stderr="")
        env = {
            "OAI_FINGERPRINT_ENABLED": "true",
            "OAI_FINGERPRINT_PROVIDER": "local",
            "OAI_FINGERPRINT_SEED": "fixed",
        }
        with mock.patch.object(bf, "_load_app_config", return_value={}):
            with mock.patch.object(bf, "_find_sdk_dir", return_value=Path("/tmp/sdk")):
                with mock.patch.object(bf.shutil, "which", return_value="/usr/bin/node"):
                    with mock.patch.object(bf.subprocess, "run", return_value=completed) as run:
                        with mock.patch.dict(os.environ, env, clear=False):
                            result = bf.generate_oai_fingerprint(
                                scope="openai2",
                                default_preset="macos-intel-chrome",
                                default_browser_version="145.0.0.0",
                            )
        self.assertIsNotNone(result)
        command = run.call_args.args[0]
        self.assertIn("automyai:openai2:fixed", command)
        self.assertNotIn("--headers-file", command)

    def test_local_api_generates_through_api(self) -> None:
        env = {
            "OAI_FINGERPRINT_ENABLED": "true",
            "OAI_FINGERPRINT_PROVIDER": "local-api",
        }
        attestation = {
            "provider": "local-api",
            "api_url": "http://127.0.0.1:50001",
            "verified": True,
            "official": False,
            "authority": "local-api",
            "status": "local-key-accepted",
        }
        generated_profile = bf.normalize_bundle(sample_bundle(), source="automyai-fingerprint-api")
        generated_profile["entry"] = "openai3"
        with mock.patch.object(bf, "_load_app_config", return_value={}):
            with mock.patch.object(bf, "_local_api_attestation", return_value=attestation):
                with mock.patch.object(
                    bf, "_local_api_generate_oai", return_value=generated_profile
                ) as generate:
                    with mock.patch.object(bf.subprocess, "run") as local_run:
                        with mock.patch.dict(os.environ, env, clear=False):
                            result = bf.generate_oai_fingerprint(
                                scope="openai3",
                                default_preset="windows-11-chrome",
                                default_browser_version="150.0.0.0",
                            )
        self.assertIsNotNone(result)
        self.assertTrue(result["provenance"]["verified"])
        self.assertEqual(result["source"], "automyai-fingerprint-api")
        generate.assert_called_once()
        self.assertEqual(generate.call_args.kwargs["preset"], "windows-11-chrome")
        self.assertEqual(generate.call_args.kwargs["browser_version"], "150.0.0.0")
        local_run.assert_not_called()

    def test_local_api_failure_falls_back_to_full_local_bundle(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout=json.dumps(sample_bundle()), stderr="")
        env = {
            "OAI_FINGERPRINT_ENABLED": "true",
            "OAI_FINGERPRINT_PROVIDER": "local-api",
            "OAI_FINGERPRINT_STRICT": "false",
        }
        with mock.patch.object(bf, "_load_app_config", return_value={}):
            with mock.patch.object(bf, "_find_sdk_dir", return_value=Path("/tmp/sdk")):
                with mock.patch.object(bf.shutil, "which", return_value="/usr/bin/node"):
                    with mock.patch.object(
                        bf,
                        "_local_api_attestation",
                        side_effect=bf.FingerprintError("fingerprint API is unavailable"),
                    ):
                        with mock.patch.object(bf.subprocess, "run", return_value=completed):
                            with mock.patch.dict(os.environ, env, clear=False):
                                result = bf.generate_oai_fingerprint(
                                    scope="openai2",
                                    default_preset="windows-11-chrome",
                                    default_browser_version="145.0.0.0",
                                )
        self.assertIsNotNone(result)
        self.assertEqual(result["provenance"]["status"], "local-fallback")
        self.assertEqual(result["source"], "automyai-fingerprint-local-fallback")
        self.assertEqual(result["profile"]["machine"]["computerName"], "DESKTOP-TEST")

    def test_api_key_file_must_be_private(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "fingerprint.key"
            path.write_text("not-a-real-key\n", encoding="utf-8")
            path.chmod(0o644)
            with mock.patch.dict(
                os.environ,
                {"OAI_FINGERPRINT_API_KEY_FILE": str(path)},
                clear=True,
            ):
                with self.assertRaisesRegex(bf.FingerprintError, "0600"):
                    bf._read_api_key({}, "openai2")

    def test_local_api_attestation_uses_health_then_read_only_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "fingerprint.key"
            path.write_text("test-key-that-is-not-real\n", encoding="utf-8")
            path.chmod(0o600)
            env = {
                "OAI_FINGERPRINT_API_URL": "http://127.0.0.1:50001",
                "OAI_FINGERPRINT_API_KEY_FILE": str(path),
            }
            responses = [
                FakeHttpResponse({"code": 0, "data": {"service": "automyai-fingerprint-api"}}),
                FakeHttpResponse({"code": 0, "data": {"rows": []}}),
            ]
            with mock.patch.dict(os.environ, env, clear=True):
                with mock.patch.object(bf, "urlopen", side_effect=responses) as open_url:
                    result = bf._local_api_attestation({}, "openai3", 5.0)
        self.assertTrue(result["verified"])
        health_request = open_url.call_args_list[0].args[0]
        workspace_request = open_url.call_args_list[1].args[0]
        self.assertEqual(health_request.full_url, "http://127.0.0.1:50001/health")
        self.assertEqual(workspace_request.full_url, "http://127.0.0.1:50001/browser/workspace")
        self.assertIsNone(health_request.get_header("Token"))
        self.assertEqual(workspace_request.get_header("Token"), "test-key-that-is-not-real")

    def test_local_api_attestation_rejects_another_service(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "fingerprint.key"
            path.write_text("test-key-that-is-not-real\n", encoding="utf-8")
            path.chmod(0o600)
            env = {
                "OAI_FINGERPRINT_API_URL": "http://127.0.0.1:50001",
                "OAI_FINGERPRINT_API_KEY_FILE": str(path),
            }
            responses = [
                FakeHttpResponse({"code": 0, "data": {"service": "another-service"}}),
            ]
            with mock.patch.dict(os.environ, env, clear=True):
                with mock.patch.object(bf, "urlopen", side_effect=responses):
                    with self.assertRaisesRegex(bf.FingerprintError, "unexpected"):
                        bf._local_api_attestation({}, "openai2", 5.0)


if __name__ == "__main__":
    unittest.main()
