#!/usr/bin/env bash
set -euo pipefail
cd $(dirname $0)/../..
if ! command -v docker >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y docker.io docker-compose-v2 openssl
  sudo systemctl enable --now docker
fi
read -r -p 'Bot Token: ' BOT_TOKEN
read -r -p '超级管理员ID（多个用逗号分隔）: ' SUPER_ADMIN_IDS
read -r -s -p '备份密钥（至少12字符）: ' BACKUP_PASSPHRASE
printf '\n'
POSTGRES_PASSWORD=$(openssl rand -hex 24)
REDIS_PASSWORD=$(openssl rand -hex 24)
umask 077
cat > .env <<EOF
BOT_TOKEN=$BOT_TOKEN
SUPER_ADMIN_IDS=$SUPER_ADMIN_IDS
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
REDIS_PASSWORD=$REDIS_PASSWORD
DATABASE_URL=postgresql+asyncpg://botwanfa:$POSTGRES_PASSWORD@postgres:5432/botwanfa
REDIS_URL=redis://:$REDIS_PASSWORD@redis:6379/0
BACKUP_PASSPHRASE=$BACKUP_PASSPHRASE
LOG_LEVEL=INFO
TIMEZONE=Asia/Shanghai
EOF
mkdir -p backups
docker compose build
docker compose up -d
docker compose ps
echo '安装完成。诊断命令：scripts/linux/status.sh'
