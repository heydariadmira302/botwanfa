$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '../..')
docker compose ps
docker compose logs --tail=80 bot scheduler worker sender
