"""Use Windows Task Scheduler; the application does not need to remain open."""
from __future__ import annotations
import os
import subprocess
import sys
from pathlib import Path

TASK_NAME = "CodeCrafter AutoBackup - 30 day check"


def command_line() -> str:
    if getattr(sys, "frozen", False):
        return subprocess.list2cmdline([sys.executable, "--scheduled"])
    main = Path(__file__).resolve().parents[2] / "main.py"
    return subprocess.list2cmdline([sys.executable, str(main), "--scheduled"])


def install_schedule(hour: str) -> None:
    if os.name != "nt":
        raise RuntimeError("Task Scheduler is available on Windows only.")
    parts = hour.split(":")
    if len(parts) != 2 or not (0 <= int(parts[0]) < 24 and 0 <= int(parts[1]) < 60):
        raise ValueError("Invalid schedule")
    cmd = ["schtasks", "/Create", "/F", "/TN", TASK_NAME, "/TR", command_line(),
           "/SC", "DAILY", "/ST", hour, "/RL", "LIMITED"]
    done = subprocess.run(cmd, capture_output=True, text=True,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if done.returncode:
        raise RuntimeError(done.stderr.strip() or done.stdout.strip() or "Task Scheduler failed.")
    # Best effort: if PC was off at trigger time, run missed task when available.
    ps = ("$s=New-ScheduledTaskSettingsSet -StartWhenAvailable; "
          "Set-ScheduledTask -TaskName 'CodeCrafter AutoBackup - 30 day check' "
          "-Settings $s | Out-Null")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                   capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def scheduled_main() -> int:
    from .core import load_config, run_backup, log
    try:
        result = run_backup(load_config(), only_if_due=True)
        if result == "not_due":
            log("Daily schedule checked; 30-day interval not reached.")
        return 0
    except Exception as exc:
        # Error details remain in local log; never write credentials to stdout.
        log("Scheduled backup error: " + type(exc).__name__ + ": " + str(exc)[:250])
        return 1
