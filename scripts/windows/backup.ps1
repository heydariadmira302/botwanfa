$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '../..')
New-Item -ItemType Directory -Force -Path 'backups' | Out-Null
$Stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$Container = docker compose ps -q postgres
$ContainerRaw = /tmp/backup-$Stamp.dump
$HostRaw = Join-Path (Get-Location) backups/.backup-$Stamp.dump
$Target = backups/botwanfa-$Stamp.bwf
docker compose exec -T postgres pg_dump -U botwanfa -d botwanfa -Fc -f $ContainerRaw
docker cp ${Container}:$ContainerRaw $HostRaw
try {
    docker compose run --rm worker python -m botwanfa.backup_crypto encrypt /app/backups/.backup-$Stamp.dump /app/$Target
} finally {
    Remove-Item -LiteralPath $HostRaw -Force -ErrorAction SilentlyContinue
    docker compose exec -T postgres rm -f $ContainerRaw
}
Write-Host 备份完成：$Target
