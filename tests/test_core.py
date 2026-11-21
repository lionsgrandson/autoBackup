import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from autobackup import core

def config():
    return {"user_id": "a"*36, "device_id": "b"*36, "username": "customer1",
            "sources": [str(Path.home())], "time": "02:00", "vss": False}

def temp_grant():
    return {"repository": "s3:https://r2.example.com/bucket/path",
            "password": "encryption-password", "access_key_id": "access",
            "secret_access_key": "secret", "session_token": "session"}

class CoreTests(unittest.TestCase):
    def test_interval(self):
        now = datetime(2026, 11, 21, tzinfo=timezone.utc)
        self.assertTrue(core.is_due(None, now))
        self.assertFalse(core.is_due((now-timedelta(days=29)).isoformat(), now))
        self.assertTrue(core.is_due((now-timedelta(days=30)).isoformat(), now))

    def test_temporary_credentials(self):
        self.assertEqual(core.environment(temp_grant())["AWS_SESSION_TOKEN"], "session")

    def test_login_required(self):
        with self.assertRaises(core.BackupError):
            core.validate({**config(), "user_id": ""})

    def test_missing_source(self):
        with self.assertRaises(core.BackupError):
            core.source_paths({**config(), "sources": ["/not/existing/path/for/test"]})

    def test_nonempty_restore_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "existing.txt").write_text("keep")
            with self.assertRaises(core.BackupError):
                core.restore(config(), "f"*64, d)

    def test_failed_backup_never_prunes(self):
        calls = []
        def fake(args, env, callback=None, timeout=None):
            calls.append(args[0])
            return core.Result(3 if args[0] == "backup" else 0, "")
        with patch("autobackup.auth.grant", return_value=temp_grant()), \
             patch.object(core, "ensure_repository"), \
             patch.object(core, "source_paths", return_value=["test"]), \
             patch.object(core, "export_personalization", return_value=None), \
             patch.object(core, "execute", side_effect=fake), \
             patch.object(core, "log"):
            with self.assertRaises(core.BackupError):
                core.run_backup(config())
        self.assertEqual(calls, ["backup"])

    def test_failed_check_never_prunes(self):
        calls = []
        def fake(args, env, callback=None, timeout=None):
            calls.append(args[0])
            return core.Result(2 if args[0] == "check" else 0,
                '{"message_type":"summary","snapshot_id":"' + "f"*64 + '"}\n'
                if args[0] == "backup" else "failure")
        with patch("autobackup.auth.grant", return_value=temp_grant()), \
             patch.object(core, "ensure_repository"), \
             patch.object(core, "source_paths", return_value=["test"]), \
             patch.object(core, "export_personalization", return_value=None), \
             patch.object(core, "execute", side_effect=fake), \
             patch.object(core, "log"):
            with self.assertRaises(core.BackupError):
                core.run_backup(config())
        self.assertEqual(calls, ["backup", "check"])

    def test_retains_two_complete(self):
        calls = []
        saved = {}
        def fake(args, env, callback=None, timeout=None):
            calls.append(args)
            return core.Result(0, '{"message_type":"summary","snapshot_id":"' +
                "f"*64 + '"}\n' if args[0] == "backup" else "")
        with patch("autobackup.auth.grant", return_value=temp_grant()), \
             patch.object(core, "ensure_repository"), \
             patch.object(core, "source_paths", return_value=["test"]), \
             patch.object(core, "export_personalization", return_value=None), \
             patch.object(core, "execute", side_effect=fake), \
             patch.object(core, "load_config", return_value=config()), \
             patch.object(core, "save_config", side_effect=lambda x: saved.update(x)), \
             patch.object(core, "log"):
            self.assertEqual(core.run_backup(config()), "success")
        self.assertEqual([c[0] for c in calls], ["backup", "check", "tag", "forget"])
        self.assertEqual(calls[-1], ["forget", "--tag", "autobackup-complete",
            "--keep-last", "2", "--group-by", "", "--prune"])
        self.assertTrue(saved.get("last_success"))

    def test_not_due_without_auth_request(self):
        cfg = config()
        cfg["last_success"] = datetime.now(timezone.utc).isoformat()
        with patch("autobackup.auth.grant") as grant:
            self.assertEqual(core.run_backup(cfg, only_if_due=True), "not_due")
            grant.assert_not_called()

if __name__ == "__main__":
    unittest.main()
