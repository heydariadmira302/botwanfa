$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '../..')
docker compose stop
