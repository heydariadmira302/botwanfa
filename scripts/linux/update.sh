#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

DRAIN_TIMEOUT_SECONDS="${DRAIN_TIMEOUT_SECONDS:-900}"
FORCE_UPDATE=0
DRAIN_REQUESTED=0

for argument in "$@"; do
  case "$argument" in
    --force) FORCE_UPDATE=1 ;;
    *)
      printf '未知参数：%s\n' "$argument" >&2
      printf '用法：bash scripts/linux/update.sh [--force]\n' >&2
      exit 2
      ;;
  esac
done

log() { printf '\n[botwanfa] %s\n' "$*"; }

if [ "${EUID:-$(id -u)}" -eq 0 ]; then
  DOCKER=(docker)
elif docker info >/dev/null 2>&1; then
  DOCKER=(docker)
else
  DOCKER=(sudo docker)
fi

compose() { "${DOCKER[@]}" compose "$@"; }

db_scalar() {
  compose exec -T postgres \
    psql -X -v ON_ERROR_STOP=1 -U botwanfa -d botwanfa -tAc "$1" \
    | tr -d '[:space:]'
}

notify_admins() {
  bash scripts/linux/notify_admins.sh "$1" || true
}

on_error() {
  local code=$?
  trap - ERR
  if [ "$DRAIN_REQUESTED" -eq 1 ]; then
    log "更新未完成，系统保持排空状态，不会自动开启新期次。处理错误后重新执行本脚本；如需恢复游戏，可在管理员界面取消更新准备。"
    notify_admins "⚠️ BOTWANFA 更新未完成
服务器：$(hostname)
系统仍处于排空状态，不会开始新期次。
请查看服务器终端错误，处理后重新执行更新脚本。"
  fi
  exit "$code"
}
trap on_error ERR

wait_for_postgres() {
  local attempt
  for attempt in $(seq 1 60); do
    if compose exec -T postgres pg_isready -U botwanfa -d botwanfa >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  log "PostgreSQL 在 120 秒内没有就绪。"
  return 1
}

deployment_table_exists() {
  [ "$(db_scalar "SELECT to_regclass('public.deployment_control') IS NOT NULL")" = "t" ]
}

enable_drain() {
  compose exec -T postgres psql -X -v ON_ERROR_STOP=1 -U botwanfa -d botwanfa <<'SQL' >/dev/null
INSERT INTO deployment_control (
    id, draining, generation, requested_at, requested_by, outbox_start_id,
    ready_notified_at, updated_at
)
VALUES (
    1, true, 1, now(), NULL,
    COALESCE((SELECT max(id) FROM outbox_messages), 0), NULL, now()
)
ON CONFLICT (id) DO UPDATE SET
    draining = true,
    generation = CASE
        WHEN deployment_control.draining THEN deployment_control.generation
        ELSE deployment_control.generation + 1
    END,
    requested_at = CASE
        WHEN deployment_control.draining THEN deployment_control.requested_at
        ELSE now()
    END,
    requested_by = CASE
        WHEN deployment_control.draining THEN deployment_control.requested_by
        ELSE NULL
    END,
    outbox_start_id = CASE
        WHEN deployment_control.draining THEN deployment_control.outbox_start_id
        ELSE COALESCE((SELECT max(id) FROM outbox_messages), 0)
    END,
    ready_notified_at = CASE
        WHEN deployment_control.draining THEN deployment_control.ready_notified_at
        ELSE NULL
    END,
    updated_at = now();
SQL
  DRAIN_REQUESTED=1
}

drain_counts() {
  db_scalar "
WITH control AS (
    SELECT outbox_start_id, requested_at FROM deployment_control WHERE id = 1
), round_counts AS (
    SELECT
        count(*) FILTER (WHERE r.status <> 'completed') AS active,
        count(*) FILTER (WHERE r.status = 'betting') AS betting,
        count(*) FILTER (WHERE r.status = 'waiting_for_player_dice') AS player_dice,
        count(*) FILTER (WHERE r.status IN ('closed', 'bot_rolling')) AS rolling,
        count(*) FILTER (WHERE r.status = 'settling') AS settling,
        count(*) FILTER (
            WHERE r.status <> 'completed'
              AND (
                  NOT g.enabled OR g.paused
                  OR r.status IN ('waiting', 'paused', 'failed', 'manual_review')
              )
        ) AS blocked
    FROM rounds r
    JOIN telegram_groups g ON g.id = r.group_id
), message_counts AS (
    SELECT
        count(*) FILTER (WHERE o.status IN ('pending', 'processing')) AS pending,
        count(*) FILTER (
            WHERE o.status = 'failed'
              AND (
                  o.id > COALESCE((SELECT outbox_start_id FROM control), 0)
                  OR EXISTS (
                      SELECT 1 FROM rounds current_round
                      WHERE current_round.id = (o.payload->>'round_id')::bigint
                        AND (
                            current_round.completed_at IS NULL
                            OR current_round.completed_at >= (SELECT requested_at FROM control)
                        )
                  )
              )
        ) AS failed
    FROM outbox_messages o
    WHERE o.message_type IN (
        'round_open', 'round_closed', 'player_dice_invite', 'player_dice_ack', 'dice_round',
        'round_result', 'trend_result', 'settlement_summary'
    )
)
SELECT active || '|' || betting || '|' || player_dice || '|' || rolling || '|' ||
       settling || '|' || blocked || '|' || pending || '|' || failed
FROM round_counts CROSS JOIN message_counts"
}

show_blockers() {
  log "当前阻塞期次："
  compose exec -T postgres psql -X -U botwanfa -d botwanfa -c \
    "SELECT group_id, round_number, status FROM rounds WHERE status <> 'completed' ORDER BY group_id" || true
  log "本次排空后发送失败的关键消息："
  compose exec -T postgres psql -X -U botwanfa -d botwanfa -c \
    "SELECT o.id, o.group_id, o.message_type, left(coalesce(o.last_error, ''), 100) AS error
     FROM outbox_messages o
     WHERE o.status = 'failed'
       AND o.message_type IN ('round_open', 'round_closed', 'player_dice_invite', 'player_dice_ack', 'dice_round', 'round_result', 'trend_result', 'settlement_summary')
       AND (
           o.id > COALESCE((SELECT outbox_start_id FROM deployment_control WHERE id = 1), 0)
           OR EXISTS (
               SELECT 1 FROM rounds current_round
               WHERE current_round.id = (o.payload->>'round_id')::bigint
                 AND (
                     current_round.completed_at IS NULL
                     OR current_round.completed_at >= (SELECT requested_at FROM deployment_control WHERE id = 1)
                 )
           )
       )
     ORDER BY o.id" || true
}

wait_for_drain() {
  local started now elapsed values active betting player_dice rolling settling blocked pending failed
  local last_values=""
  started="$(date +%s)"
  log "已进入全局排空：各群当前期并行完成，期间不再创建新期次。"
  while true; do
    values="$(drain_counts)"
    IFS='|' read -r active betting player_dice rolling settling blocked pending failed <<< "$values"
    if [ "$values" != "$last_values" ]; then
      log "排空进度：未完成期次 ${active}（下注 ${betting}，等玩家掷骰 ${player_dice}，掷骰/封盘 ${rolling}，结算 ${settling}，异常 ${blocked}）；待发送 ${pending}，发送失败 ${failed}。"
      last_values="$values"
    fi
    if [ "$active" = "0" ] && [ "$pending" = "0" ] && [ "$failed" = "0" ]; then
      log "所有群当前期和关键消息均已完成，可以切换服务。"
      return 0
    fi
    now="$(date +%s)"
    elapsed=$((now - started))
    if [ "$elapsed" -ge "$DRAIN_TIMEOUT_SECONDS" ]; then
      show_blockers
      if [ "$FORCE_UPDATE" -eq 1 ]; then
        log "已指定 --force，将在保留数据库期次状态的情况下继续切换服务。"
        return 0
      fi
      log "等待超过 ${DRAIN_TIMEOUT_SECONDS} 秒，已中止本次更新并保持排空状态。"
      return 1
    fi
    sleep 2
  done
}

wait_for_app_services() {
  local attempt running service ready consecutive
  consecutive=0
  for attempt in $(seq 1 60); do
    running="$(compose ps --status running --services 2>/dev/null || true)"
    ready=1
    for service in bot scheduler worker sender; do
      if ! grep -qx "$service" <<< "$running"; then
        ready=0
        break
      fi
    done
    if [ "$ready" -eq 1 ]; then
      consecutive=$((consecutive + 1))
      if [ "$consecutive" -ge 5 ]; then
        return 0
      fi
    else
      consecutive=0
    fi
    sleep 2
  done
  log "应用服务在 120 秒内没有全部进入运行状态。"
  compose ps
  return 1
}

clear_drain() {
  compose exec -T postgres psql -X -v ON_ERROR_STOP=1 -U botwanfa -d botwanfa <<'SQL' >/dev/null
UPDATE deployment_control
SET draining = false,
    requested_at = NULL,
    requested_by = NULL,
    outbox_start_id = 0,
    ready_notified_at = NULL,
    updated_at = now()
WHERE id = 1;
SQL
  DRAIN_REQUESTED=0
}

log "正在拉取最新代码..."
git pull --ff-only

log "正在构建新镜像；当前机器人服务继续运行。"
compose build --pull

log "正在确认数据库服务可用..."
compose up -d postgres redis
wait_for_postgres

if ! deployment_table_exists; then
  log "首次启用平滑更新，正在安装排空控制表并切换新版调度器..."
  compose run --rm migrate alembic upgrade head
  compose up -d --no-deps scheduler
fi

enable_drain
wait_for_drain

log "正在停止应用进程；数据库和缓存保持运行。"
compose stop bot scheduler worker sender

log "正在执行数据库迁移..."
compose run --rm migrate alembic upgrade head

log "正在启动新版本服务..."
compose up -d --remove-orphans
wait_for_app_services

log "新版本服务已正常运行，正在恢复自动开新期次..."
clear_drain

log "当前服务状态："
compose ps

notify_admins "✅ BOTWANFA 更新完成
服务器：$(hostname)
目录：$ROOT_DIR
所有群已恢复自动开新期次。
查看状态：bash scripts/linux/status.sh"
