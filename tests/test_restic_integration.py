"""Local restic round trip: smoke test without any Cloudflare credentials."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(os.environ.get("AUTOBACKUP_RESTIC"), "restic binary unavailable")
class ResticIntegrationTest(unittest.TestCase):
    def test_local_backup_retention_and_restore(self):
        restic = os.environ["AUTOBACKUP_RESTIC"]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repository"
            source = root / "source"
            source.mkdir()
            env = {**os.environ, "RESTIC_REPOSITORY": str(repo),
                   "RESTIC_PASSWORD": "integration-test-password-123"}
            def run(*args):
                result = subprocess.run([restic, *args], env=env,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                return result.stdout
            run("init")
            for n in range(3):
                (source / "test.txt").write_text(f"content version {n}", encoding="utf-8")
                output = run("backup", "--json", "--tag", "autobackup-attempt", str(source))
                summary = [json.loads(line) for line in output.splitlines() if line.startswith("{") and '"message_type":"summary"' in line.replace(" ", "")]
                self.assertTrue(summary)
                run("tag", "--set", "autobackup-complete", summary[-1]["snapshot_id"])
            run("check")
            run("forget", "--tag", "autobackup-complete", "--keep-last", "2", "--group-by", "", "--prune")
            snapshots = json.loads(run("snapshots", "--json", "--tag", "autobackup-complete"))
            self.assertEqual(len(snapshots), 2)
            restore_dir = root / "restored"
            run("restore", "latest", "--target", str(restore_dir))
            restored = list(restore_dir.rglob("test.txt"))
            self.assertEqual(len(restored), 1)
            self.assertEqual(restored[0].read_text(encoding="utf-8"), "content version 2")


if __name__ == "__main__":
    unittest.main()
