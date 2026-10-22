# One-command deploy to the Meanwhile cloud box: packs this repo's working tree (without secrets, venvs, data, git
# history, experiments or laptop-only scripts) into ~/meanwhile on the box and runs box-update.sh there, which adds
# missing settings, rewrites the ones built from the public host, installs packages, validates the Caddyfile, restarts
# the services and prints their health.
# The detector weights (about 300 MB) stay on the box between deploys; -WithWeights ships them too (a new box or new weights).
# -Install runs install.sh instead of box-update.sh: the first deploy to a new box (use it with -WithWeights).
# Usage: powershell -ExecutionPolicy Bypass -File deploy.ps1 [-Repo <repo folder>] [-WithWeights] [-Install]
param(
    [string]$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [switch]$WithWeights,
    [switch]$Install
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$target = "ubuntu@$Script:PublicIp"
$archive = Join-Path $env:TEMP 'meanwhile-code.tgz'
$exclude = '.venv', '.env', '.data', '__pycache__', '.pytest_cache', 'videos', 'experiments', 'weights/clip', '*.pem',
    '*.key', '.git', 'infra/sagemaker', 'infra/cloud/*.ps1', './android'
if (-not $WithWeights) { $exclude += './weights' }

Write-Host "Packing $Repo" -ForegroundColor Cyan
$tarArgs = @('-czf', $archive, '-C', $Repo) + ($exclude | ForEach-Object { "--exclude=$_" }) + @('.')
& "$env:SystemRoot\System32\tar.exe" @tarArgs
if ($LASTEXITCODE -ne 0) { throw 'tar failed.' }
Write-Host ("  {0:N0} MB" -f ((Get-Item $archive).Length / 1MB))

Write-Host "Copying to $target" -ForegroundColor Cyan
& scp @(Get-SshOptions) $archive "${target}:/tmp/meanwhile-code.tgz"
if ($LASTEXITCODE -ne 0) { throw 'scp failed: is the box running, and does its SSH rule allow this IP?' }
Remove-Item $archive

Write-Host "Updating the box" -ForegroundColor Cyan
$remote = 'mkdir -p ~/meanwhile && tar -xzf /tmp/meanwhile-code.tgz -C ~/meanwhile && rm /tmp/meanwhile-code.tgz && sed -i ''s/\r$//'' ~/meanwhile/infra/cloud/* && ' +
    "MEANWHILE_PUBLIC_HOST='$Script:PublicHost' bash ~/meanwhile/infra/cloud/$(if ($Install) { 'install.sh' } else { 'box-update.sh' })"
& ssh @(Get-SshOptions) $target $remote
if ($LASTEXITCODE -ne 0) { throw 'The box update failed; see the output above.' }
Write-Host 'Deployed. Reload the camera pages and the Alexa panel.' -ForegroundColor Green
