#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

MESSAGE="${1:-BOTWANFA 通知}"

if [ ! -f .env ]; then
  exit 0
fi

get_env_value() {
  local key="$1"
  grep -m1 "^${key}=" .env | sed "s/^${key}=//"
}

BOT_TOKEN="$(get_env_value BOT_TOKEN || true)"
SUPER_ADMIN_IDS="$(get_env_value SUPER_ADMIN_IDS || true)"

if [ -z "$BOT_TOKEN" ] || [ -z "$SUPER_ADMIN_IDS" ]; then
  exit 0
fi

if ! command -v curl >/dev/null 2>&1; then
  exit 0
fi

IFS=',' read -ra ADMINS <<< "$SUPER_ADMIN_IDS"
for admin_id in "${ADMINS[@]}"; do
  admin_id="$(printf '%s' "$admin_id" | tr -d '[:space:]')"
  [ -n "$admin_id" ] || continue
  curl -fsS \
    -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
    -d "chat_id=${admin_id}" \
    --data-urlencode "text=${MESSAGE}" >/dev/null || true
done
