#!/usr/bin/env bash
set -euo pipefail
cd $(dirname $0)/../..
SOURCE=${1:?用法: restore.sh backups/文件名.bwf}
RAW=backups/.restore-$$.dump
docker compose run --rm worker python -m botwanfa.backup_crypto decrypt /app/$SOURCE /app/$RAW
docker compose exec -T postgres dropdb -U botwanfa --if-exists botwanfa_verify
docker compose exec -T postgres createdb -U botwanfa botwanfa_verify
docker compose exec -T postgres pg_restore -U botwanfa -d botwanfa_verify --exit-on-error < $RAW
docker compose exec -T postgres dropdb -U botwanfa botwanfa_verify
scripts/linux/backup.sh
docker compose stop bot scheduler worker sender
docker compose exec -T postgres dropdb -U botwanfa --if-exists botwanfa
docker compose exec -T postgres createdb -U botwanfa botwanfa
docker compose exec -T postgres pg_restore -U botwanfa -d botwanfa --exit-on-error < $RAW
rm -f $RAW
docker compose up -d bot scheduler worker sender
echo 恢复完成；恢复前快照已保存在backups目录
