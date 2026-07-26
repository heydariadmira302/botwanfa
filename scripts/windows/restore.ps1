param([Parameter(Mandatory = $true)][string]$Source)
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '../..')
$SourcePath = (Resolve-Path -LiteralPath $Source).Path
$Workspace = (Get-Location).Path
$BackupRoot = Join-Path $Workspace 'backups'
if (-not $SourcePath.StartsWith($BackupRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Restore file must be inside the project backups directory.'
}
$Name = [IO.Path]::GetFileName($SourcePath)
$Stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$HostRaw = Join-Path $Workspace backups/.restore-$Stamp.dump
$ContainerRaw = /tmp/restore-$Stamp.dump
docker compose run --rm worker python -m botwanfa.backup_crypto decrypt /app/backups/$Name /app/backups/.restore-$Stamp.dump
$Container = docker compose ps -q postgres
docker cp $HostRaw ${Container}:$ContainerRaw
try {
    docker compose exec -T postgres dropdb -U botwanfa --if-exists botwanfa_verify
    docker compose exec -T postgres createdb -U botwanfa botwanfa_verify
    docker compose exec -T postgres pg_restore -U botwanfa -d botwanfa_verify --exit-on-error $ContainerRaw
    docker compose exec -T postgres dropdb -U botwanfa botwanfa_verify
    & (Join-Path $PSScriptRoot 'backup.ps1')
    docker compose stop bot scheduler worker sender
    docker compose exec -T postgres dropdb -U botwanfa --if-exists botwanfa
    docker compose exec -T postgres createdb -U botwanfa botwanfa
    docker compose exec -T postgres pg_restore -U botwanfa -d botwanfa --exit-on-error $ContainerRaw
    docker compose up -d bot scheduler worker sender
} finally {
    Remove-Item -LiteralPath $HostRaw -Force -ErrorAction SilentlyContinue
    docker compose exec -T postgres rm -f $ContainerRaw
}
Write-Host 'Restore complete. A pre-restore snapshot is in backups.'
