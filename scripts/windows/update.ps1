$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '../..')
git pull --ff-only
docker compose build --pull
docker compose run --rm migrate alembic upgrade head
docker compose up -d --remove-orphans
docker compose ps
& (Join-Path $PSScriptRoot 'notify_admins.ps1') -Message "✅ BOTWANFA 更新完成`n服务器：$env:COMPUTERNAME`n目录：$(Get-Location)`n查看状态：scripts/windows/status.ps1"
