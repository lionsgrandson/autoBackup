# Build on administrator's Windows PC. NO GitHub Actions.
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
Set-Location (Resolve-Path (Join-Path $PSScriptRoot ".."))
if (-not (Test-Path "config/client.json")) { throw "Copy config/client.example.json to config/client.json and configure your public Supabase URL/key." }
$json = Get-Content "config/client.json" -Raw | ConvertFrom-Json
if (-not $json.supabase_url -or $json.supabase_url -match "REPLACE" -or -not $json.supabase_publishable_key -or $json.supabase_publishable_key -match "REPLACE") { throw "Configure public Supabase project URL and publishable key." }
if (-not (Test-Path ".venv/Scripts/python.exe")) {
    py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Python 3.12 required on developer computer." }
}
$python = ".venv/Scripts/python.exe"
& $python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed." }
& $python -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw "Tests failed." }
Invoke-WebRequest -Uri "https://github.com/restic/restic/releases/download/v0.20.0/restic_0.20.0_windows_amd64.zip" -OutFile "restic.zip"
$actual = (Get-FileHash "restic.zip" -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne "bba9061c3dce081701ab353ec8cb73a78be73b71a538aad560f2d1067602b7fe") { throw "restic checksum mismatch!" }
if (Test-Path "restic_unpacked") { Remove-Item "restic_unpacked" -Recurse -Force }
Expand-Archive "restic.zip" -DestinationPath "restic_unpacked"
$restic = Get-ChildItem "restic_unpacked" -Recurse -Filter "*windows_amd64.exe" | Select-Object -First 1
if (-not $restic) { throw "restic missing" }
Copy-Item $restic.FullName "restic.exe" -Force
$env:AUTOBACKUP_RESTIC = (Resolve-Path "restic.exe").Path
& $python -m unittest discover -s tests -p "test_restic_integration.py" -v
if ($LASTEXITCODE -ne 0) { throw "restic roundtrip test failed" }
& $python -m PyInstaller --noconfirm --clean --onedir --windowed --name AutoBackup --paths src --collect-all keyring --add-binary "restic.exe;." --add-data "config/client.json;config" main.py
if ($LASTEXITCODE -ne 0) { throw "Desktop build failed" }
$iscc = if ($env:ISCC_PATH) { $env:ISCC_PATH } else { "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" }
if (-not (Test-Path $iscc)) { throw "Install Inno Setup 6 or set ISCC_PATH." }
& $iscc "installer/AutoBackup.iss"
if ($LASTEXITCODE -ne 0) { throw "Installer compilation failed" }
Write-Host "Installer created: output/AutoBackup-Setup-Windows-x64.exe"
