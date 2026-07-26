#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

log() { printf '\n[botwanfa] %s\n' "$*"; }

wait_for_quiet_betting_window() {
  log "正在等待当前下注窗口结束，避免更新打断玩家下单..."
  local active_betting
  for _ in $(seq 1 180); do
    if ! active_betting="$(
      "${DOCKER[@]}" compose exec -T postgres \
        psql -U botwanfa -d botwanfa -tAc \
        "SELECT count(*) FROM rounds WHERE status = 'betting' AND betting_closes_at > now()" \
        2>/dev/null | tr -d '[:space:]'
    )"; then
      log "未能读取当前期次状态，将继续完成更新。"
      return
    fi
    if [ "$active_betting" = "0" ]; then
      log "当前没有开放中的下注窗口，开始切换服务。"
      return
    fi
    sleep 1
  done
  log "等待超过 180 秒，将继续更新；所有投注和期次状态仍保存在数据库中。"
}

if [ "${EUID:-$(id -u)}" -eq 0 ]; then
  DOCKER=(docker)
elif docker info >/dev/null 2>&1; then
  DOCKER=(docker)
else
  DOCKER=(sudo docker)
fi

log "正在拉取最新代码..."
git pull --ff-only

log "正在重新构建镜像..."
"${DOCKER[@]}" compose build --pull

log "正在执行数据库迁移..."
"${DOCKER[@]}" compose run --rm migrate alembic upgrade head

wait_for_quiet_betting_window

log "正在重启服务..."
"${DOCKER[@]}" compose up -d --remove-orphans

log "当前服务状态："
"${DOCKER[@]}" compose ps

UPDATE_MESSAGE="✅ BOTWANFA 更新完成
服务器：$(hostname)
目录：$ROOT_DIR
查看状态：bash scripts/linux/status.sh"
bash scripts/linux/notify_admins.sh "$UPDATE_MESSAGE" || true
