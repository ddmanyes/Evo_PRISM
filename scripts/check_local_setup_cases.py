"""Regression cases for single-user setup; runs without touching deployment data."""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import ep_local


class LocalSetupCases(unittest.TestCase):
    def test_missing_database_is_actionable_and_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "absent"
            report = ep_local.doctor(path)
            self.assertFalse(report["ready"])
            self.assertFalse(path.exists())
            self.assertTrue(any(c["fix"] for c in report["checks"] if c["required"]))

    def test_existing_config_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            config = path / "mcp.json"
            config.write_text('{"mcpServers":{"other":{}}}')
            original = config.read_bytes()
            with self.assertRaisesRegex(RuntimeError, "refusing to overwrite"):
                ep_local.init(path / "data", config)
            self.assertEqual(config.read_bytes(), original)
            self.assertFalse((path / "data").exists())

    def test_setup_failure_has_log_and_no_success_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            with patch.object(
                ep_local.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "init")
            ):
                with self.assertRaisesRegex(RuntimeError, "preserved log"):
                    ep_local.init(path)
            self.assertFalse((path / "mcp.json").exists())
            self.assertEqual(len(list(path.glob("setup-*.log"))), 1)

    def test_credentials_not_inherited(self):
        with patch.dict(
            ep_local.os.environ,
            {
                "MCP_AUTH_TOKEN": "do-not-leak",
                "ANTHROPIC_API_KEY": "do-not-leak",
                "BIO_DB_ROOT": "/wrong",
            },
        ):
            env = ep_local.process_env(ep_local.local_env(Path("/tmp/new-ep")))
            self.assertNotIn("do-not-leak", json.dumps(env))
            self.assertEqual(env["BIO_DB_ROOT"], "/tmp/new-ep")
            self.assertEqual(env["PYTHON_DOTENV_DISABLED"], "1")

    def test_optional_endpoint_failure_does_not_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(ep_local, "urlopen", side_effect=OSError("offline")):
                result = ep_local.doctor(Path(tmp), True)
            row = next(c for c in result["checks"] if c["name"] == "embedding")
            self.assertEqual(row["status"], "unreachable")
            self.assertFalse(row["required"])

    def test_endpoint_credentials_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "without credentials"):
                ep_local.doctor(Path(tmp), True, "https://user:secret@example.test/v1")


if __name__ == "__main__":
    unittest.main()
