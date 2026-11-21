# AutoBackup — Windows desktop backup to Cloudflare R2

A bilingual (Hebrew/English) **Windows 10/11 x64 desktop app** for encrypted
file-level backups directly to **Cloudflare R2 Standard storage**. No Cloudflare
Worker, hosted server, Node.js, Python, or developer tools are needed on a
computer running the **built installer**.

## What is and is not backed up

The default selection is the **entire current Windows user profile**, including
Desktop, Documents, Downloads, Pictures, Videos, Music, AppData, and Windows
theme files accessible to that account. You may add other folders and drives.
The app also exports selected HKCU Windows wallpaper/desktop/theme registry
entries. File access failures cause the job to fail rather than silently
declaring the computer fully backed up.

**This is not a bootable disk image** and does not fully restore Windows,
drivers, installed applications, all registry hives, locked files, BitLocker
keys, credential stores, or the state of every running app. Windows VSS is
optional and needs elevated privileges. Cloud-only placeholder files must
be downloaded locally before they can reliably be included.

For full bare-metal recovery, keep an additional Windows-compatible system
image and a separate offline copy of critical data.

## Setup (non-developer PC)

1. In Cloudflare R2, create a **Standard** bucket (e.g. personal-pc-backups).
2. Create an **R2 API token** with **Object Read & Write**, restricted to that
   bucket. Obtain the **Access Key ID** and **Secret Access Key**. Do not use
   your Cloudflare account login password.
3. Download **AutoBackup-Windows-Installer** from
   https://github.com/lionsgrandson/autoBackup/actions/workflows/windows-installer.yml
   after a successful Windows installer workflow, unzip the artifact and run
   **AutoBackup-Setup-Windows-x64.exe**. The installer bundles restic.
4. Start AutoBackup and select **עברית** or **English**. Enter Cloudflare
   Account ID (32 characters), bucket, unique backup name (e.g. my-laptop),
   R2 access keys, and a **separate encryption password** of at least
   12 characters. For EU buckets use the optional endpoint field:
   https://ACCOUNT_ID.eu.r2.cloudflarestorage.com
5. Save the recovery password **outside the computer** (e.g. password manager).
   Cloudflare cannot recover it. Click Connect / sign in.
6. Check folders, set time, click **Save settings and schedule**, then click
   **Back up now** for the first copy. Keep the desktop app open while a
   manually started backup is running. Windows Task Scheduler performs a daily
   due check. You may close the desktop app.
7. Test recovery: Show available backups, select a snapshot, restore into a
   **new empty folder**, and check restored files.

R2 keys and the backup password are stored through Windows Credential Manager
(via Python keyring), **not** in source code or the settings JSON.
Scheduled tasks run under the same Windows user account; **the PC must be on
and that Windows user logged in**. Missed tasks are configured to run when
available on a best-effort basis. Running while logged out or waking a sleeping
computer reliably requires more Windows service infrastructure.

### Recovering on another computer

Install the app on the replacement PC. Enter the **same Account ID, bucket,
backup name, R2 access keys, and encryption password**. Connect, list
snapshots and restore into an empty folder. The exported connection details
JSON omits all passwords and secrets and is not sufficient by itself.

## Retention and safety

- restic provides encryption, compression and content deduplication.
- **Only after a successful upload and repository metadata check**,
  the app tags a verified snapshot as complete and keeps the most recent two complete snapshots in the dedicated repository.
- Retention uses restic 'forget --tag autobackup-complete --keep-last 2 --group-by "" --prune'.
- Backup failure or a metadata check failure prevents pruning.
- Failed restic operations can leave incomplete, untagged-as-complete snapshots; these are not included in the two-version rotation and must be cleaned up manually after inspection. Investigate
  failures and storage usage before relying on a backup.
- Restore targets a **new/empty directory**; it does not silently overwrite
  files on the current computer.
- Keys need R2 delete rights for retention; anyone gaining the Windows
  user's credentials might be able to delete backups. Use an additional
  immutable or offline copy for ransomware resilience.
- Network interruption, file permissions, file locks, unavailable drives,
  and cloud quota can prevent successful backup.

## Cloudflare R2 Standard cost (approximate)

Official pricing as of November 2026: **$0.015 per GB-month** with the first
10 GB-month free, plus request charges and no egress bandwidth fee.
A constant 500 GB is approximately **$7.35/month**; 1,000 GB is
**$14.85/month**. If both retained copies are completely different,
two 500 GB copies cost **$14.85/month** and two 1 TB copies **$29.85/month**.
Deduplication/compression can lower stored bytes. Bills are based on daily
peak storage and operations: https://developers.cloudflare.com/r2/pricing/

## Development (Windows)

Requires Python 3.12+.

    py -m venv .venv
    .venv\Scripts\activate
    pip install -r requirements.txt
    python -m unittest discover -s tests -v
    python main.py

For development install restic independently and add it to PATH or set
AUTOBACKUP_RESTIC. Production installers include the pinned, SHA-256-verified
restic 0.20.0 Windows binary. The GitHub Actions workflow builds the native
PyInstaller application and an Inno Setup per-user installer, and runs
unit tests and a local encrypted backup/restore test.

Remote R2 connection must still be tested against a bucket owned by the user:
the CI workflow does **not** have R2 credentials.

## Files and tasks

- Local settings/logs: %LOCALAPPDATA%\AutoBackup
- Saved keys and encryption password: Windows Credential Manager (current user)
- Storage location: s3:https://ACCOUNT.r2.cloudflarestorage.com/BUCKET/autobackup/PROFILE
- Task Scheduler name: CodeCrafter AutoBackup - 30 day check
- Uninstalling the app does **not** remove the remote backups or R2 bucket.

## Known MVP limitations

- Windows x64 only; no macOS or Linux installer.
- R2 API keys are used for sign-in, not Cloudflare OAuth or a multiuser
  account service.
- File-level backup, not a full Windows bootable image or bare-metal restore.
- Windows scheduling and actual R2 integration must be validated on a
  real non-developer Windows PC before this is the only backup.
