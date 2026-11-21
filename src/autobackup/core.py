"""Backup operations. No Cloudflare Worker, web server, or hard-coded credentials."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

APP_DIR = Path(os.getenv("LOCALAPPDATA") or (Path.home() / ".local" / "share")) / "AutoBackup"
CONFIG_FILE = APP_DIR / "config.json"
LOG_FILE = APP_DIR / "events.log"
SERVICE = "CodeCrafter.AutoBackup"
DEFAULT_HOUR = "02:00"
_BACKUP_LOCK = threading.Lock()


class BackupError(Exception):
    pass


def default_config() -> dict:
    return {
        "language": "he", "username": "", "user_id": "", "device_id": "",
        "sources": [str(Path.home())], "time": DEFAULT_HOUR,
        "interval_days": 30, "last_success": None, "last_result": "",
        "vss": False,
    }


def load_config() -> dict:
    config = default_config()
    if CONFIG_FILE.exists():
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise BackupError("Invalid configuration file.")
        config.update(data)
    return config


def save_config(config: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".new")
    tmp.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, CONFIG_FILE)


def log(message: str) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat()} {message[:800]}\n")


def is_due(last_success: str | None, now: datetime | None = None, days: int = 30) -> bool:
    if not last_success:
        return True
    if days < 1:
        raise ValueError("days must be positive")
    now = now or datetime.now(timezone.utc)
    previous = datetime.fromisoformat(last_success.replace("Z", "+00:00"))
    if previous.tzinfo is None:
        previous = previous.replace(tzinfo=timezone.utc)
    return now >= previous + timedelta(days=days)


def validate(config: dict) -> None:
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", config.get("time", "")):
        raise BackupError("Invalid backup time.")
    if not isinstance(config.get("sources"), list) or not config["sources"]:
        raise BackupError("Select at least one source directory.")
    if not config.get("user_id") or not config.get("device_id"):
        raise BackupError("Sign in before running backups.")


def restic_binary() -> str:
    candidates = [
        Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])) / "restic.exe",
        Path(sys.executable).parent / "restic.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    located = os.getenv("AUTOBACKUP_RESTIC") or shutil.which("restic")
    if located:
        return located
    raise BackupError("restic.exe is missing. Install the built Windows installer or add restic to PATH.")


def environment(grant: dict) -> dict:
    return {
        **os.environ,
        "RESTIC_REPOSITORY": grant["repository"],
        "RESTIC_PASSWORD": grant["password"],
        "AWS_ACCESS_KEY_ID": grant["access_key_id"],
        "AWS_SECRET_ACCESS_KEY": grant["secret_access_key"],
        "AWS_SESSION_TOKEN": grant["session_token"],
        "AWS_DEFAULT_REGION": "auto",
        "AWS_REGION": "auto",
        "RESTIC_CACHE_DIR": str(APP_DIR / "cache"),
    }


@dataclass
class Result:
    code: int
    output: str


def execute(args: list[str], env: dict, callback=None, timeout: int | None = None) -> Result:
    """Stream progress; never print credentials, construct shell commands, or show a console."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.Popen(
            [restic_binary(), *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", env=env,
            creationflags=flags, bufsize=1,
        )
    except OSError as exc:
        raise BackupError(f"Unable to start restic: {exc}") from exc
    output = []
    start = datetime.now(timezone.utc)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            output.append(line)
            if len(output) > 2500:
                output.pop(0)
            if callback:
                try:
                    event = json.loads(line)
                    if isinstance(event, dict) and event.get("message_type") == "status":
                        callback(
                            f"{event.get('percent_done', 0) * 100:.1f}% · "
                            f"{event.get('total_files', 0)} files"
                        )
                except (ValueError, TypeError):
                    pass
            if timeout and (datetime.now(timezone.utc) - start).total_seconds() > timeout:
                proc.kill()
                raise BackupError("Operation timed out.")
        code = proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    return Result(code, "".join(output))


def require_success(result: Result, operation: str) -> None:
    if result.code != 0:
        # Never print stored S3 credentials or restic environment variables.
        message = "\n".join(result.output.strip().splitlines()[-8:])
        raise BackupError(f"{operation} failed (exit {result.code}).\n{message[:1300]}")


def ensure_repository(env: dict) -> None:
    """Create a repository only if no repository currently exists."""
    result = execute(["snapshots", "--json"], env, timeout=120)
    if result.code == 0:
        return
    created = execute(["init"], env, timeout=180)
    if created.code != 0:
        raise BackupError(
            "Unable to open or initialize encrypted repository. "
            "Check your connection and administrator's R2 permissions."
        )


def export_personalization() -> Path | None:
    """Preserve some HKCU personalization; not a complete Windows/system image."""
    if os.name != "nt":
        return None
    target = APP_DIR / "personalization"
    target.mkdir(parents=True, exist_ok=True)
    names = [
        (r"HKCU\Control Panel\Desktop", "desktop.reg"),
        (r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Wallpapers", "wallpapers.reg"),
        (r"HKCU\Software\Microsoft\Windows\CurrentVersion\Themes", "themes.reg"),
    ]
    for regkey, filename in names:
        subprocess.run(
            ["reg.exe", "export", regkey, str(target / filename), "/y"],
            capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    return target


def source_paths(config: dict) -> list[str]:
    sources = [str(Path(p).expanduser().resolve()) for p in config["sources"]]
    missing = [p for p in sources if not Path(p).exists()]
    if missing:
        raise BackupError("Source path is not available: " + missing[0])
    return list(dict.fromkeys(sources))


def _snapshot_count(env: dict) -> int:
    result = execute(["snapshots", "--json"], env, timeout=180)
    require_success(result, "Reading snapshots")
    try:
        data = json.loads(result.output or "[]")
    except json.JSONDecodeError as exc:
        raise BackupError("Invalid snapshot response.") from exc
    return len(data)


def list_snapshots(config: dict, device_id: str | None = None) -> list[dict]:
    from .auth import grant
    env = environment(grant(config, device_id))
    result = execute(["snapshots", "--json", "--tag", "autobackup-complete"], env, timeout=180)
    require_success(result, "Reading snapshots")
    try:
        data = json.loads(result.output or "[]")
        return sorted(data, key=lambda item: item.get("time", ""), reverse=True)
    except (ValueError, TypeError) as exc:
        raise BackupError("Could not parse snapshot listing.") from exc


def run_backup(config: dict, callback=None, only_if_due: bool = False) -> str:
    """Retain two snapshots ONLY after fully successful backup and metadata check."""
    if only_if_due and not is_due(config.get("last_success"), days=30):
        return "not_due"
    if not _BACKUP_LOCK.acquire(blocking=False):
        raise BackupError("A backup is already running in this process.")
    try:
        from .auth import grant
        validate(config)
        env = environment(grant(config))
        ensure_repository(env)
        paths = source_paths(config)
        personalization = export_personalization()
        if personalization is not None and not any(Path(p) in personalization.parents or Path(p) == personalization for p in paths):
            paths.append(str(personalization))
        args = ["backup", "--json", "--tag", "autobackup-attempt", "--exclude", str(APP_DIR / "cache"),
                "--exclude", str(APP_DIR / "events.log"), "--exclude-caches"]
        if config.get("vss"):
            args.append("--use-fs-snapshot")
        args.extend(paths)
        if callback:
            callback("Backing up encrypted files...")
        backup = execute(args, env, callback=callback)
        require_success(backup, "Backup (incomplete backups never trigger deletion)")
        snapshot_id = None
        for line in reversed(backup.output.splitlines()):
            try:
                event = json.loads(line)
                if event.get("message_type") == "summary":
                    snapshot_id = event.get("snapshot_id")
                    break
            except (ValueError, AttributeError):
                continue
        if not snapshot_id or not re.fullmatch(r"[a-fA-F0-9]{8,64}", snapshot_id):
            raise BackupError("Backup finished without a confirmed snapshot ID; old versions kept.")
        if callback:
            callback("Checking repository metadata...")
        check = execute(["check"], env, timeout=14400)
        require_success(check, "Repository check; older snapshots preserved")
        tag_result = execute(["tag", "--set", "autobackup-complete", snapshot_id], env)
        require_success(tag_result, "Marking snapshot as complete; older snapshots preserved")
        # The repository is dedicated to one device/profile, so disable default
        # grouping by source paths to always enforce exactly two snapshots.
        if callback:
            callback("Retaining the two newest snapshots...")
        retention = execute(["forget", "--tag", "autobackup-complete", "--keep-last", "2", "--group-by", "", "--prune"], env)
        if retention.code != 0:
            log("Backup uploaded; cleanup FAILED. No further snapshot removal attempted.")
            raise BackupError("Backup was uploaded, but cleanup failed; older copies remain. " +
                              "\n".join(retention.output.splitlines()[-6:])[:700])
        fresh = load_config()
        fresh["last_success"] = datetime.now(timezone.utc).isoformat()
        fresh["last_result"] = "success"
        save_config(fresh)
        log("Backup complete; repository check and two-snapshot retention successful.")
        return "success"
    except Exception as exc:
        log("Backup failed or needs attention: " + type(exc).__name__)
        raise
    finally:
        _BACKUP_LOCK.release()


def restore(config: dict, snapshot_id: str, destination: str, callback=None,
            device_id: str | None = None) -> None:
    if not re.fullmatch(r"[a-fA-F0-9]{8,64}", snapshot_id):
        raise BackupError("Invalid snapshot identifier.")
    target = Path(destination).expanduser().resolve()
    if target.exists() and any(target.iterdir()):
        raise BackupError("Restore to a new or empty folder to protect existing files.")
    target.mkdir(parents=True, exist_ok=True)
    from .auth import grant
    env = environment(grant(config, device_id))
    if callback:
        callback("Restoring files to separate folder...")
    result = execute(["restore", snapshot_id, "--target", str(target)], env, callback=callback)
    require_success(result, "Restore")
    log("Restore completed to a user-selected folder.")
