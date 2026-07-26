$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '../..')
$DockerCliDir = 'C:\Program Files\Docker\Docker\resources\bin'
$DockerCli = Join-Path $DockerCliDir 'docker.exe'
$DockerDesktop = 'C:\Program Files\Docker\Docker\Docker Desktop.exe'

if (-not (Get-Command docker -ErrorAction SilentlyContinue) -and (Test-Path -LiteralPath $DockerCli)) {
    $env:Path = $DockerCliDir + [IO.Path]::PathSeparator + $env:Path
}

if (-not (Test-Path -LiteralPath $DockerCli)) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw 'Install winget before running this script.'
    }
    winget install --exact --id Docker.DockerDesktop --accept-source-agreements --accept-package-agreements
    Write-Host 'Docker Desktop installed. Start it once, finish WSL2 setup, then run this script again.'
    exit 0
}

$RebootPending = (
    (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending') -or
    (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired') -or
    ($null -ne (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager' -Name PendingFileRenameOperations -ErrorAction SilentlyContinue))
)
if ($RebootPending) {
    throw 'Windows has a pending restart after Docker or WSL setup. Restart Windows, start Docker Desktop, then run this script again.'
}

$Cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
if (-not $Cpu.SecondLevelAddressTranslationExtensions -or -not $Cpu.VMMonitorModeExtensions) {
    throw 'Docker Desktop needs hardware or nested virtualization with SLAT and VM Monitor support. Enable AMD-V/SVM/VT-x in BIOS, or enable nested virtualization on the VM/cloud host, then restart Windows.'
}

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Starting Docker Desktop and waiting for the container engine...'
    Start-Process -FilePath $DockerDesktop
    $Ready = $false
    for ($Attempt = 0; $Attempt -lt 60; $Attempt++) {
        Start-Sleep -Seconds 2
        docker info *> $null
        if ($LASTEXITCODE -eq 0) {
            $Ready = $true
            break
        }
    }
    if (-not $Ready) {
        throw 'Docker engine did not start. Finish the Docker Desktop or WSL2 setup shown on screen, restart Windows if requested, then run this script again.'
    }
}

$BotToken = Read-Host 'Bot Token'
$AdminIds = Read-Host 'Super admin IDs, comma separated'
$BackupSecure = Read-Host 'Backup passphrase, at least 12 characters' -AsSecureString
$Bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($BackupSecure)
try { $BackupPassphrase = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Bstr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Bstr) }
if ($BackupPassphrase.Length -lt 12) { throw 'Backup passphrase must have at least 12 characters.' }
function New-HexSecret {
    $Bytes = New-Object byte[] 24
    [Security.Cryptography.RandomNumberGenerator]::Fill($Bytes)
    return ($Bytes | ForEach-Object { $_.ToString('x2') }) -join ''
}
$PostgresPassword = New-HexSecret
$RedisPassword = New-HexSecret
$Environment = @(
    'BOT_TOKEN=' + $BotToken
    'SUPER_ADMIN_IDS=' + $AdminIds
    'POSTGRES_PASSWORD=' + $PostgresPassword
    'REDIS_PASSWORD=' + $RedisPassword
    'DATABASE_URL=postgresql+asyncpg://botwanfa:' + $PostgresPassword + '@postgres:5432/botwanfa'
    'REDIS_URL=redis://:' + $RedisPassword + '@redis:6379/0'
    'BACKUP_PASSPHRASE=' + $BackupPassphrase
    'LOG_LEVEL=INFO'
    'TIMEZONE=Asia/Shanghai'
) -join [Environment]::NewLine
$EnvPath = Join-Path (Get-Location) '.env'
[IO.File]::WriteAllText($EnvPath, $Environment, [Text.UTF8Encoding]::new($false))
New-Item -ItemType Directory -Force -Path 'backups' | Out-Null
docker compose build
docker compose up -d
docker compose ps
Write-Host 'Install complete. Diagnostics: scripts/windows/status.ps1'
