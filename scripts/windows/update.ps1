$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '../..')
docker compose build --pull
docker compose run --rm migrate alembic upgrade head
docker compose up -d --remove-orphans
docker compose ps
