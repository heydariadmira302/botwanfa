#!/usr/bin/env bash
set -euo pipefail
cd $(dirname $0)/../..
mkdir -p backups
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
RAW=backups/.backup-$STAMP.dump
TARGET=backups/botwanfa-$STAMP.bwf
docker compose exec -T postgres pg_dump -U botwanfa -d botwanfa -Fc > $RAW
docker compose run --rm worker python -m botwanfa.backup_crypto encrypt /app/$RAW /app/$TARGET
rm -f $RAW
echo 备份完成：$TARGET
