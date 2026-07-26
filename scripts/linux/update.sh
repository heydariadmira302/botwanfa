#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

log() { printf '\n[botwanfa] %s\n' "$*"; }

if [ "${EUID:-$(id -u)}" -eq 0 ]; then
  DOCKER=(docker)
elif docker info >/dev/null 2>&1; then
  DOCKER=(docker)
else
  DOCKER=(sudo docker)
fi

log "正在拉取最新代码..."
git pull --ff-only || true

log "正在重新构建镜像..."
"${DOCKER[@]}" compose build --pull

log "正在执行数据库迁移..."
"${DOCKER[@]}" compose run --rm migrate alembic upgrade head

log "正在重启服务..."
"${DOCKER[@]}" compose up -d --remove-orphans

log "当前服务状态："
"${DOCKER[@]}" compose ps

UPDATE_MESSAGE="✅ BOTWANFA 更新完成
服务器：$(hostname)
目录：$ROOT_DIR
查看状态：bash scripts/linux/status.sh"
bash scripts/linux/notify_admins.sh "$UPDATE_MESSAGE" || true
