#!/usr/bin/env bash
set -euo pipefail
cd $(dirname $0)/../..
docker compose build --pull
docker compose run --rm migrate alembic upgrade head
docker compose up -d --remove-orphans
docker compose ps
