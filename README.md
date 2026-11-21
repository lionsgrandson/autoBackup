# AutoBackup — centrally managed Cloudflare R2 backups

Windows desktop app with Hebrew RTL and English UI. Admin configures **one R2
bucket**, creates usernames and passwords, and distributes a configured Windows
installer. Users **only sign in**. No Cloudflare Worker or GitHub Actions.

## Architecture

- Supabase Auth manages accounts (admin-created, no public self-signup).
- A Supabase Edge Function checks authenticated user and admin-controlled
  app_metadata, then requests temporary R2 credentials **restricted to the
  signed-in user's device path**.
- restic on Windows encrypts and uploads files **directly to R2**.
- Each device repository path is users/USER_UUID/devices/DEVICE_UUID.
- Default backup selection is the whole Windows user folder. Other folders
  can be added. A daily Task Scheduler trigger checks 30-day interval.
- Retain the last **two complete** encrypted/deduplicated backup snapshots.
- Users can restore old snapshots from their own devices to empty folders.
- Windows Credential Manager holds the rotating Supabase refresh token.

## One-time setup: Cloudflare R2 (administrator)

Create an R2 Standard bucket and parent R2 API token with Object Read & Write,
restricted to that bucket, that may issue temporary credentials. Record:

1. Cloudflare Account ID (32 hexadecimal characters).
2. R2 bucket name and jurisdiction (global or EU).
3. Parent R2 API **Bearer Token** (server-side SECRET).
4. Parent R2 **Access Key ID** (server-side).

No permanent R2 token/key is ever installed on customer computers.

## One-time setup: Supabase (administrator)

Create or select your Supabase project and **disable public/self sign-up**
under Authentication settings. Enable appropriate Auth login rate limits.
Run supabase/migrations/20261121000000_autobackup_devices.sql in SQL Editor.
The devices table enables RLS and grants **no user-facing table access**.

Configure these secrets on the Supabase Edge Function:

    CF_ACCOUNT_ID=<Cloudflare Account ID>
    CF_R2_BUCKET=<R2 bucket name>
    CF_R2_API_TOKEN=<parent R2 API bearer token - SECRET>
    CF_R2_PARENT_ACCESS_KEY_ID=<parent access key ID>
    CF_R2_REGION=global
    RESTIC_MASTER_KEY=<random high-entropy secret, at least 40 characters>
    R2_TTL_SECONDS=172800

Use CF_R2_REGION=eu for an EU jurisdiction bucket. The broker also requires
SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY, normally automatically supplied
to hosted Supabase functions. Check they are present and keep them secret.

From a linked Supabase CLI project run:

    supabase functions deploy r2-broker

Keep JWT verification enabled. Never ship service role or master key to users.

## Create your users

On your admin computer set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in your
private environment. Run:

    python scripts/admin-users.py create customer1

The script prompts for password and prints user UUID. Give the credentials
to the user. To manage accounts:

    python scripts/admin-users.py disable USER_UUID
    python scripts/admin-users.py enable USER_UUID
    python scripts/admin-users.py reset-password USER_UUID

Under the hood the username is mapped to a Supabase Auth alias such as
customer1@users.autobackup.example.com. Users never see that email.
There is no self-registration or email-based recovery in this MVP.

## Build the Windows installer LOCALLY (developer computer only)

Copy config/client.example.json to config/client.json and set ONLY your
Supabase Project URL and **public** publishable/anon key. This is intentionally
ignored by Git. Do NOT put Cloudflare parent token, service role or master
key in it.

On your Windows developer PC install Python 3.12 and Inno Setup 6. Run:

    .\scripts\build-windows.ps1

The output is output/AutoBackup-Setup-Windows-x64.exe.
It bundles the desktop application and restic. Recipient computers need no
Python, Node.js, GitHub runner or developer tools.

Users install, enter username/password, configure backup folders and time,
then run the first backup. Verify a restore to an empty folder. After that,
the daily Windows scheduled task runs backups when due.

## Critical limitations

- This protects accessible **files**, including AppData and some Windows
  theme registry data. It does **NOT** restore Windows OS, drivers,
  installed applications, every locked file, or create a bootable disk image.
- Scheduled operation requires the Windows user logged in and PC powered on.
- First backups that exceed the temporary credential TTL (default 48 hours)
  can fail; adjust R2_TTL_SECONDS up to Cloudflare's 7-day maximum.
  Revoking a user prevents new credentials but old temporary credentials
  persist until expiry unless the parent token is revoked.
- The backend **can derive each device's restic encryption password** via a
  master key. It encrypts data before upload but is not zero-knowledge.
  Losing/changing RESTIC_MASTER_KEY without migration means previously stored
  backups cannot be decrypted.
- A user's credentials grant read/write/delete for that user's own device
  prefix. Preserve an independent immutable/offline backup for ransomware.
- No per-user quota or billing enforcement; monitor the bucket for abuse.
- This version is not yet tested against the administrator's live R2
  account or a real configured Supabase project. Validate real upload,
  restore, locked files, Windows scheduling and expired tokens.
- Previously built GitHub Actions installers are version 0.1 (direct R2
  connection) and DO NOT contain this centrally managed sign-in; rebuild
  the new version locally.

R2 Temporary Credentials: https://developers.cloudflare.com/r2/api/s3/temporary-credentials/
R2 Pricing: https://developers.cloudflare.com/r2/pricing/
