#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

if [ "${EUID:-$(id -u)}" -eq 0 ] || docker info >/dev/null 2>&1; then
  DOCKER=(docker)
else
  DOCKER=(sudo docker)
fi

"${DOCKER[@]}" compose ps

if [ "$(
  "${DOCKER[@]}" compose exec -T postgres \
    psql -X -U botwanfa -d botwanfa -tAc \
    "SELECT to_regclass('public.deployment_control') IS NOT NULL" 2>/dev/null \
    | tr -d '[:space:]'
)" = "t" ]; then
  printf '\n[botwanfa] 平滑更新状态：\n'
  "${DOCKER[@]}" compose exec -T postgres \
    psql -X -U botwanfa -d botwanfa -c \
    "SELECT CASE WHEN draining THEN '正在排空' ELSE '正常运行' END AS status,
            requested_at AS started_at
     FROM deployment_control WHERE id = 1"
fi

printf '\n[botwanfa] 最近日志：\n'
"${DOCKER[@]}" compose logs --tail=80 bot scheduler worker sender
