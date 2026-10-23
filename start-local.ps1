# Starts Meanwhile on this laptop: the core (with phone cameras over HTTPS), the voice side, and
# Chrome with the speech flag. Run from anywhere: powershell -ExecutionPolicy Bypass -File start-local.ps1 [-CheckOnly]

param(
    [switch]$CheckOnly,
    [switch]$NoChrome
)

$ErrorActionPreference = "Stop"
$CoreDir = $PSScriptRoot
$VoiceDir = Join-Path $PSScriptRoot "voice"

# Reads NAME=value lines from a .env file into a hashtable (values stay in this process; nothing is printed).
function Read-EnvFile([string]$Path) {
    if (-not (Test-Path $Path)) { throw "Missing $Path. Copy it from .env.example next to it and fill it in." }
    $values = @{}
    foreach ($line in Get-Content $Path) {
        $line = $line.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $name, $value = $line.Split("=", 2)
            $values[$name.Trim()] = $value.Trim()
        }
    }
    return $values
}

# The value of a required setting, or a clear error naming the file it should be in.
function Get-Setting([hashtable]$Values, [string]$Name, [string]$File) {
    if (-not $Values[$Name]) { throw "$Name is not set in $File (see .env.example)." }
    return $Values[$Name]
}

$CoreEnv = Join-Path $CoreDir ".env"
$VoiceEnv = Join-Path $VoiceDir ".env"
$core = Read-EnvFile $CoreEnv
$voice = Read-EnvFile $VoiceEnv
$Ollama = Get-Setting $voice "OLLAMA_URL" $VoiceEnv
$Model = Get-Setting $voice "ALEXA_SIM_MODEL" $VoiceEnv
$CorePort = [int](Get-Setting $core "MEANWHILE_PORT" $CoreEnv)
$LanPort = [int](Get-Setting $core "MEANWHILE_LAN_PORT" $CoreEnv)
$VoicePort = [int](Get-Setting $voice "MEANWHILE_VOICE_PORT" $VoiceEnv)
$KeyHeader = @{ "X-Meanwhile-Key" = (Get-Setting $core "MEANWHILE_KEY" $CoreEnv) }
$CoreUrl = "http://127.0.0.1:$CorePort"
$VoiceUrl = "http://127.0.0.1:$VoicePort"

# Whether something is listening on a local port.
function Test-Port([int]$Port) {
    return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

# Wait until a URL answers 200 (sending $Headers), or stop with a message after $Seconds.
function Wait-Url([string]$Url, [int]$Seconds, [string]$What, [hashtable]$Headers = @{}) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $reply = Invoke-WebRequest -Uri $Url -Headers $Headers -UseBasicParsing -TimeoutSec 3
            if ($reply.StatusCode -eq 200) { return }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    throw "$What didn't answer at $Url within $Seconds s. Look at its window for the error."
}

# Start a command in its own PowerShell window with a title, from a folder.
function Start-Window([string]$Title, [string]$Folder, [string]$Command) {
    $script = "`$Host.UI.RawUI.WindowTitle = '$Title'; Set-Location -LiteralPath '$Folder'; $Command"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $script | Out-Null
}

Write-Host "Checking..." -ForegroundColor Cyan

if (-not (Test-Path (Join-Path $CoreDir "backend\local\devserver.py"))) { throw "No core at $CoreDir. Run the start-local.ps1 at the repo root." }
if (-not (Test-Path (Join-Path $CoreDir ".venv\Scripts\python.exe"))) { throw "The core's venv is missing: $CoreDir\.venv" }
if (-not (Test-Path (Join-Path $VoiceDir ".venv\Scripts\python.exe"))) { throw "The voice venv is missing: $VoiceDir\.venv" }
if ($voice["MEANWHILE_CORE_KEY"] -cne $core["MEANWHILE_KEY"]) { throw "MEANWHILE_CORE_KEY in $VoiceEnv must be the same as MEANWHILE_KEY in $CoreEnv." }
Write-Host "  The core's key is set, and the voice side has the same one."

try {
    $tags = Invoke-RestMethod -Uri "$Ollama/api/tags" -TimeoutSec 3
} catch {
    throw "Ollama isn't running. Start it from the Start menu (or 'ollama serve'), then run this again."
}
if (-not ($tags.models.name -contains $Model)) { throw "The model $Model isn't pulled. Run: ollama pull $Model" }
Write-Host "  Ollama is up and $Model is pulled."

$busy = @()
foreach ($port in $VoicePort, $CorePort, $LanPort) {
    if (Test-Port $port) {
        $owner = (Get-NetTCPConnection -LocalPort $port -State Listen | Select-Object -First 1).OwningProcess
        $name = (Get-Process -Id $owner -ErrorAction SilentlyContinue).ProcessName
        $busy += "  port $port is in use by $name (process $owner)"
    }
}
if ($busy) {
    $busy | ForEach-Object { Write-Host $_ -ForegroundColor Yellow }
    throw "Close the windows of the old core / devservers first (or: Stop-Process -Id <process>), then run this again."
}
Write-Host "  Ports $VoicePort, $CorePort and $LanPort are free."

if ($CheckOnly) {
    Write-Host "All checks passed. Run again without -CheckOnly to start." -ForegroundColor Green
    return
}

Write-Host "Starting the core (window 'Meanwhile core')..." -ForegroundColor Cyan
Start-Window "Meanwhile core" (Join-Path $CoreDir "backend") "..\.venv\Scripts\python.exe -m local.devserver --lan"
Wait-Url "$CoreUrl/api/cameras" 240 "The core" $KeyHeader
Write-Host "  The core answers on $CoreUrl"
$PairCode = (Invoke-RestMethod -Method Post -Uri "$CoreUrl/api/pairing-codes" -Headers $KeyHeader -TimeoutSec 5).code
$PairUrl = "$CoreUrl/pair?code=$PairCode"

Write-Host "Starting the voice side (window 'Meanwhile voice')..." -ForegroundColor Cyan
$voiceCommand = "..\.venv\Scripts\python.exe -m meanwhile.voice.devserver --lan"
Start-Window "Meanwhile voice" (Join-Path $VoiceDir "backend") $voiceCommand
Wait-Url "$VoiceUrl/voice/state" 120 "The voice side"
Write-Host "  The voice side answers on $VoiceUrl (wait for 'Alexa's model is ready' in its window)"

if (-not $NoChrome) {
    $chrome = @(
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $chrome) { throw "Chrome isn't installed. Open $VoiceUrl in Chrome yourself." }
    $profileDir = Join-Path $env:LOCALAPPDATA "MeanwhileChrome"
    Start-Process $chrome -ArgumentList @(
        "--user-data-dir=`"$profileDir`"",
        "--autoplay-policy=no-user-gesture-required",
        "--new-window",
        $PairUrl,
        $VoiceUrl,
        "$CoreUrl/camera?label=Desk",
        "$CoreUrl/debug.html"
    ) | Out-Null
    Write-Host "  Chrome opened (its own profile, so the speech flag applies). Press Pair on its first tab, then reload the"
    Write-Host "  camera and debug tabs: the pairing code lasts MEANWHILE_PAIR_CODE_TTL_S seconds."
}

Write-Host ""
Write-Host "Ready." -ForegroundColor Green
Write-Host "  Home screen + Alexa panel : $VoiceUrl   (press Start, allow the microphone)"
Write-Host "  Laptop camera             : $CoreUrl/camera?label=Desk   (allow the camera)"
Write-Host "  Phone cameras             : the 'Phones:' line in the 'Meanwhile core' window (pair with Pair a device on the debug page)"
Write-Host "  People (name, role, shirt): $CoreUrl/debug.html"
Write-Host ""
Write-Host "Before the scenario:" -ForegroundColor Cyan
Write-Host "  1. Register the people on the debug page (one owner). Unregistered people are 'someone I don't recognise'."
Write-Host "  2. Put the object at rest where the Desk camera sees it, then wait a few seconds for a fresh thumbnail."
Write-Host "  3. Say: 'Alexa, I'm stepping away, keep an eye on my <object>'."
Write-Host "To stop: close the 'Meanwhile core' and 'Meanwhile voice' windows."
