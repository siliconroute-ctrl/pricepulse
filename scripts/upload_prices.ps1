# Send the checked price files from this PC to the server and load them (one command).
#
#   .\scripts\upload_prices.ps1                  # check, send, load; shows what examples would go
#   .\scripts\upload_prices.ps1 -RemoveExamples  # same, and remove the example prices
#
# Needs: PP_SERVER set to deploy@YOUR_SERVER_IP (setx PP_SERVER "deploy@1.2.3.4"), and your
# normal SSH key. No password is stored anywhere: ssh asks for your key passphrase.
param(
    [string]$Server = $env:PP_SERVER,
    [string]$Key = "$env:USERPROFILE\.ssh\id_ed25519",
    [switch]$RemoveExamples
)
$ErrorActionPreference = "Stop"
if (-not $Server) { throw "Set PP_SERVER first, for example: setx PP_SERVER ""deploy@1.2.3.4"" (then open a new PowerShell window)." }
$backend = Join-Path $PSScriptRoot "..\backend"
$py = Join-Path $backend ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Python not found at $py" }

Push-Location $backend
try {
    $out = & $py upload_check.py
    $code = $LASTEXITCODE
} finally { Pop-Location }
$out | Where-Object { $_ -notmatch '^FILE ' } | ForEach-Object { Write-Host $_ }
if ($code -ne 0) { Write-Host "Stopped: nothing was sent." -ForegroundColor Red; exit 1 }
$files = @($out | Where-Object { $_ -match '^FILE ' } | ForEach-Object { $_.Substring(5).Trim() })

$what = if ($RemoveExamples) { "and REMOVE the example prices" } else { "(example prices stay for now)" }
$answer = Read-Host "Send $($files.Count) files to $Server and load them $what? Type y to continue"
if ($answer -ne "y") { Write-Host "Cancelled. Nothing was sent."; exit 0 }

Write-Host "`nSending files (ssh may ask for your key passphrase)..."
& scp -i $Key @files "${Server}:pricepulse-upload/"
if ($LASTEXITCODE -ne 0) { throw "Sending failed. Does ~/pricepulse-upload exist on the server? See docs/DEPLOY.md." }

Write-Host "`nLoading on the server (it may ask for the passphrase and the server password for sudo)..."
$flag = if ($RemoveExamples) { "yes" } else { "no" }
& ssh -t -i $Key $Server "bash ~/pricepulse/scripts/load_prices.sh $flag"
if ($LASTEXITCODE -ne 0) { throw "Loading on the server failed. Read the lines above; nothing is lost, run it again after fixing." }
Write-Host "`nDone." -ForegroundColor Green
