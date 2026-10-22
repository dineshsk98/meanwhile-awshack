# Shared settings and helpers for deploy.ps1. Every setting is read from the repo root's .env (MEANWHILE_CLOUD_* and
# MEANWHILE_SECRETS_DIR; meanings in .env.example). The SSH key and the box's known-hosts file live in the secrets folder.

# Reads NAME=value lines from a .env file into a hashtable (values stay in this process; nothing is printed).
function Read-EnvFile([string]$Path) {
    if (-not (Test-Path $Path)) { throw "Missing $Path. Copy it from .env.example and fill it in." }
    $values = @{}
    foreach ($line in Get-Content $Path) {
        $line = $line.Trim()
        if ($line -and -not $line.StartsWith('#') -and $line.Contains('=')) {
            $name, $value = $line.Split('=', 2)
            $values[$name.Trim()] = $value.Trim()
        }
    }
    return $values
}

$Script:EnvPath = Join-Path $PSScriptRoot '..\..\.env'
$Script:Settings = Read-EnvFile $Script:EnvPath

# A required setting from the repo root's .env, or a clear error naming it.
function Get-Setting([string]$Name) {
    if (-not $Script:Settings[$Name]) { throw "$Name is not set in .env at the repo root (see .env.example)." }
    return $Script:Settings[$Name]
}

$Script:Name = Get-Setting 'MEANWHILE_CLOUD_NAME'
$Script:PublicIp = Get-Setting 'MEANWHILE_CLOUD_PUBLIC_IP'
$Script:PublicHost = Get-Setting 'MEANWHILE_CLOUD_PUBLIC_HOST'
if ($Script:PublicHost -notmatch '^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$') {
    throw 'MEANWHILE_CLOUD_PUBLIC_HOST in .env must be a bare host name (e.g. meanwhile.example.com), without a scheme or path.'
}
$Script:SecretsDir = Get-Setting 'MEANWHILE_SECRETS_DIR'
$Script:KeyPath = Join-Path $Script:SecretsDir "$Script:Name.pem"
$Script:KnownHosts = Join-Path $Script:SecretsDir "$Script:Name-known-hosts"

# Common ssh/scp options: key file, pinned known_hosts, no password prompts.
function Get-SshOptions {
    return @('-i', $Script:KeyPath, '-o', "UserKnownHostsFile=$Script:KnownHosts",
        '-o', 'StrictHostKeyChecking=accept-new', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15')
}
