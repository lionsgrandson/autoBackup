import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from autobackup import core


def config():
    return {
        "account_id": "a" * 32,
        "endpoint": "",
        "bucket": "test-backup-bucket",
        "profile": "my-laptop",
        "sources": [str(Path.home())],
        "time": "02:00",
        "vss": False,
    }


class CoreTests(unittest.TestCase):
    def test_schedule_interval(self):
        now = datetime(2026, 11, 21, tzinfo=timezone.utc)
        self.assertTrue(core.is_due(None, now))
        self.assertFalse(core.is_due((now - timedelta(days=29)).isoformat(), now))
        self.assertTrue(core.is_due((now - timedelta(days=30)).isoformat(), now))
        self.assertFalse(core.is_due((now + timedelta(days=1)).isoformat(), now))

    def test_r2_repository_path(self):
        self.assertEqual(
            core.repo_url(config()),
            "s3:https://" + "a"*32 +
            ".r2.cloudflarestorage.com/test-backup-bucket/autobackup/my-laptop"
        )

    def test_endpoint_restriction(self):
        cfg = config()
        cfg["endpoint"] = "https://attacker.example.com"
        with self.assertRaises(core.BackupError):
            core.validate(cfg)
        cfg["endpoint"] = "https://" + "a"*32 + ".eu.r2.cloudflarestorage.com"
        core.validate(cfg)

    def test_invalid_profile(self):
        cfg = config()
        cfg["profile"] = "../other-repo"
        with self.assertRaises(core.BackupError):
            core.validate(cfg)

    def test_missing_source_rejected(self):
        cfg = config()
        cfg["sources"] = ["/this/path/should/not/exist/abc123"]
        with self.assertRaises(core.BackupError):
            core.source_paths(cfg)

    def test_nonempty_restore_folder_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "existing.txt").write_text("safe")
            with self.assertRaises(core.BackupError):
                core.restore(config(), "a"*64, d)

    def test_failed_backup_never_prunes(self):
        cfg = config()
        calls = []
        def fake_execute(args, env, callback=None, timeout=None):
            calls.append(args[0])
            return core.Result(3, "Warning: cannot read file")
        with patch.object(core, "get_secrets", return_value={}), \
             patch.object(core, "environment", return_value={}), \
             patch.object(core, "source_paths", return_value=["test"]), \
             patch.object(core, "export_personalization", return_value=None), \
             patch.object(core, "execute", side_effect=fake_execute), \
             patch.object(core, "log"):
            with self.assertRaises(core.BackupError):
                core.run_backup(cfg)
        self.assertEqual(calls, ["backup"])

    def test_check_failure_never_prunes(self):
        calls = []
        def fake_execute(args, env, callback=None, timeout=None):
            calls.append(args[0])
            return core.Result(2 if args[0] == "check" else 0, "error")
        with patch.object(core, "get_secrets", return_value={}), \
             patch.object(core, "environment", return_value={}), \
             patch.object(core, "source_paths", return_value=["test"]), \
             patch.object(core, "export_personalization", return_value=None), \
             patch.object(core, "execute", side_effect=fake_execute), \
             patch.object(core, "log"):
            with self.assertRaises(core.BackupError):
                core.run_backup(config())
        self.assertEqual(calls, ["backup", "check"])

    def test_success_preserves_two(self):
        calls = []
        changed = {}
        def fake_execute(args, env, callback=None, timeout=None):
            calls.append(args)
            return core.Result(0, "")
        with patch.object(core, "get_secrets", return_value={}), \
             patch.object(core, "environment", return_value={}), \
             patch.object(core, "source_paths", return_value=["test"]), \
             patch.object(core, "export_personalization", return_value=None), \
             patch.object(core, "execute", side_effect=fake_execute), \
             patch.object(core, "load_config", return_value=config()), \
             patch.object(core, "save_config", side_effect=lambda c: changed.update(c)), \
             patch.object(core, "log"):
            self.assertEqual(core.run_backup(config()), "success")
        self.assertEqual([call[0] for call in calls], ["backup", "check", "forget"])
        self.assertEqual(calls[-1], ["forget", "--keep-last", "2", "--group-by", "", "--prune"])
        self.assertTrue(changed["last_success"])

    def test_no_new_backup_when_not_due(self):
        cfg = config()
        cfg["last_success"] = datetime.now(timezone.utc).isoformat()
        with patch.object(core, "execute") as execute:
            self.assertEqual(core.run_backup(cfg, only_if_due=True), "not_due")
            execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
