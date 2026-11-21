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
from urllib.parse import urlparse

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
        "language": "he", "account_id": "", "endpoint": "", "bucket": "",
        "profile": "", "sources": [str(Path.home())], "time": DEFAULT_HOUR,
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
    if not re.fullmatch(r"[0-9a-fA-F]{32}", config.get("account_id", "")):
        raise BackupError("Cloudflare Account ID must contain 32 hexadecimal characters.")
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", config.get("bucket", "")):
        raise BackupError("Invalid R2 bucket name.")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{1,46}[a-z0-9])?", config.get("profile", "")):
        raise BackupError("Use 3-48 lowercase letters, digits or hyphens for backup name.")
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", config.get("time", "")):
        raise BackupError("Invalid backup time.")
    endpoint = config.get("endpoint", "").strip() or (
        f"https://{config['account_id'].lower()}.r2.cloudflarestorage.com"
    )
    parsed = urlparse(endpoint)
    host = (parsed.hostname or "").lower()
    account_id = config["account_id"].lower()
    approved = {
        f"{account_id}.r2.cloudflarestorage.com",
        f"{account_id}.eu.r2.cloudflarestorage.com",
    }
    if parsed.scheme != "https" or host not in approved or parsed.port or parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.username:
        raise BackupError("R2 endpoint must use your Cloudflare account's global or EU HTTPS hostname.")
    if not isinstance(config.get("sources"), list) or not config["sources"]:
        raise BackupError("Select at least one folder or drive.")
    for source in config["sources"]:
        if not isinstance(source, str) or not source.strip():
            raise BackupError("Invalid source folder.")


def repo_url(config: dict) -> str:
    validate(config)
    endpoint = config.get("endpoint", "").strip().rstrip("/") or (
        f"https://{config['account_id'].lower()}.r2.cloudflarestorage.com"
    )
    return f"s3:{endpoint}/{config['bucket']}/autobackup/{config['profile']}"


def credential_key(config: dict) -> str:
    return f"{config['account_id'].lower()}/{config['bucket']}/{config['profile']}"


def store_secrets(config: dict, access_id: str, access_secret: str, password: str) -> None:
    import keyring
    keyring.set_password(SERVICE, credential_key(config), json.dumps({
        "access_id": access_id, "access_secret": access_secret, "password": password,
    }))


def get_secrets(config: dict) -> dict:
    import keyring
    raw = keyring.get_password(SERVICE, credential_key(config))
    if not raw:
        raise BackupError("Connect to your R2 backup first. Saved credentials not found.")
    return json.loads(raw)


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


def environment(config: dict, secrets: dict) -> dict:
    return {
        **os.environ,
        "RESTIC_REPOSITORY": repo_url(config),
        "RESTIC_PASSWORD": secrets["password"],
        "AWS_ACCESS_KEY_ID": secrets["access_id"],
        "AWS_SECRET_ACCESS_KEY": secrets["access_secret"],
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


def connect(config: dict, access_id: str, access_secret: str, password: str) -> None:
    validate(config)
    if not (access_id.strip() and access_secret.strip() and len(password) >= 12):
        raise BackupError("Provide R2 keys and an encryption password (at least 12 characters).")
    secrets = {
        "access_id": access_id.strip(), "access_secret": access_secret.strip(),
        "password": password,
    }
    env = environment(config, secrets)
    check = execute(["snapshots", "--json"], env, timeout=120)
    if check.code != 0:
        # Restic init refuses to overwrite an existing repository.
        create = execute(["init"], env, timeout=180)
        if create.code != 0:
            raise BackupError(
                "Could not open or initialize the backup. Check bucket access, endpoint, "
                "backup name and recovery password. If restoring an existing repository, "
                "use the original password. Details:\n" +
                "\n".join(create.output.strip().splitlines()[-5:])[:900]
            )
    store_secrets(config, access_id.strip(), access_secret.strip(), password)
    save_config(config)
    log("R2 connection configured successfully.")


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


def list_snapshots(config: dict) -> list[dict]:
    env = environment(config, get_secrets(config))
    result = execute(["snapshots", "--json"], env, timeout=180)
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
        env = environment(config, get_secrets(config))
        paths = source_paths(config)
        personalization = export_personalization()
        if personalization is not None:
            paths.append(str(personalization))
        args = ["backup", "--json", "--exclude", str(APP_DIR / "cache"),
                "--exclude", str(APP_DIR / "events.log"), "--exclude-caches"]
        if config.get("vss"):
            args.append("--use-fs-snapshot")
        args.extend(paths)
        if callback:
            callback("Backing up encrypted files...")
        backup = execute(args, env, callback=callback)
        require_success(backup, "Backup (incomplete backups never trigger deletion)")
        if callback:
            callback("Checking repository metadata...")
        check = execute(["check"], env, timeout=14400)
        require_success(check, "Repository check; older snapshots preserved")
        # The repository is dedicated to one device/profile, so disable default
        # grouping by source paths to always enforce exactly two snapshots.
        if callback:
            callback("Retaining the two newest snapshots...")
        retention = execute(["forget", "--keep-last", "2", "--group-by", "", "--prune"], env)
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


def restore(config: dict, snapshot_id: str, destination: str, callback=None) -> None:
    if not re.fullmatch(r"[a-fA-F0-9]{8,64}", snapshot_id):
        raise BackupError("Invalid snapshot identifier.")
    target = Path(destination).expanduser().resolve()
    if target.exists() and any(target.iterdir()):
        raise BackupError("Restore to a new or empty folder to protect existing files.")
    target.mkdir(parents=True, exist_ok=True)
    env = environment(config, get_secrets(config))
    if callback:
        callback("Restoring files to separate folder...")
    result = execute(["restore", snapshot_id, "--target", str(target)], env, callback=callback)
    require_success(result, "Restore")
    log("Restore completed to a user-selected folder.")
