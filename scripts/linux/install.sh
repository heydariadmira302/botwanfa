#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

log() { printf '\n[botwanfa] %s\n' "$*"; }
fail() { printf '\n[botwanfa] 错误：%s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

if [ "${EUID:-$(id -u)}" -eq 0 ]; then
  SUDO=()
else
  have sudo || fail "需要 sudo。请用 root 运行，或先安装 sudo。"
  SUDO=(sudo)
fi

export DEBIAN_FRONTEND=noninteractive

if [ -r /etc/os-release ]; then
  . /etc/os-release
  if [ "${ID:-}" != "ubuntu" ]; then
    log "此脚本主要面向 Ubuntu 24.x；当前检测到 ${PRETTY_NAME:-unknown}，继续尝试安装。"
  fi
else
  fail "没有找到 /etc/os-release；此脚本需要 Ubuntu 24.x 或兼容系统。"
fi

install_base_packages() {
  log "正在检查并安装基础依赖..."
  "${SUDO[@]}" apt-get update
  "${SUDO[@]}" apt-get install -y ca-certificates curl gnupg lsb-release openssl git uidmap
}

install_docker_official() {
  if have docker && docker compose version >/dev/null 2>&1; then
    log "已安装 Docker 和 Docker Compose。"
    return
  fi

  log "正在从 Docker 官方仓库安装 Docker Engine 和 Compose 插件..."
  "${SUDO[@]}" install -m 0755 -d /etc/apt/keyrings
  if [ ! -s /etc/apt/keyrings/docker.gpg ]; then
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | "${SUDO[@]}" gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    "${SUDO[@]}" chmod a+r /etc/apt/keyrings/docker.gpg
  fi

  ARCH="$(dpkg --print-architecture)"
  CODENAME="${VERSION_CODENAME:-noble}"
  echo "deb [arch=$ARCH signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $CODENAME stable" | \
    "${SUDO[@]}" tee /etc/apt/sources.list.d/docker.list >/dev/null

  "${SUDO[@]}" apt-get update
  "${SUDO[@]}" apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

  if have systemctl; then
    "${SUDO[@]}" systemctl enable --now docker
  else
    "${SUDO[@]}" service docker start
  fi

  if [ "${EUID:-$(id -u)}" -ne 0 ]; then
    "${SUDO[@]}" usermod -aG docker "$USER" || true
  fi
}

select_docker_command() {
  if docker info >/dev/null 2>&1; then
    DOCKER=(docker)
  elif "${SUDO[@]}" docker info >/dev/null 2>&1; then
    DOCKER=("${SUDO[@]}" docker)
  else
    DOCKER=(docker)
  fi
}

wait_for_docker() {
  log "正在等待 Docker 引擎启动..."
  select_docker_command
  for _ in $(seq 1 60); do
    if "${DOCKER[@]}" info >/dev/null 2>&1; then
      "${DOCKER[@]}" version --format 'Docker Engine {{.Server.Version}}'
      "${DOCKER[@]}" compose version
      return
    fi
    sleep 2
  done

  if have journalctl; then
    "${SUDO[@]}" journalctl -u docker --no-pager -n 80 || true
  fi
  fail "Docker 引擎没有正常启动。请查看上方日志，处理后重新运行本脚本。"
}

write_env_file() {
  if [ -f .env ]; then
    read -r -p "检测到已存在 .env，是否重新生成？[y/N]: " RECREATE_ENV
    case "$RECREATE_ENV" in
      y|Y|yes|YES) ;;
      *) log "保留现有 .env"; return ;;
    esac
  fi

  read -r -s -p "请输入机器人 Bot Token（输入时不显示）： " BOT_TOKEN
  printf '\n'
  read -r -p "请输入超级管理员 Telegram 数字ID（多个用英文逗号分隔，例如 123456789,987654321）： " SUPER_ADMIN_IDS
  read -r -s -p "请输入备份加密密钥（至少12个字符，输入时不显示）： " BACKUP_PASSPHRASE
  printf '\n'

  [ -n "$BOT_TOKEN" ] || fail "Bot Token 不能为空。"
  [ -n "$SUPER_ADMIN_IDS" ] || fail "超级管理员 ID 不能为空。"
  [[ "$SUPER_ADMIN_IDS" =~ ^[0-9]+(,[0-9]+)*$ ]] || fail "超级管理员 ID 必须是 Telegram 数字ID；多个 ID 请用英文逗号分隔，不能有空格。"
  [ "${#BACKUP_PASSPHRASE}" -ge 12 ] || fail "备份加密密钥至少需要12个字符。"

  POSTGRES_PASSWORD="$(openssl rand -hex 24)"
  REDIS_PASSWORD="$(openssl rand -hex 24)"

  umask 077
  cat > .env <<EOF
COMPOSE_PROJECT_NAME=botwanfa
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
  chmod 600 .env
}

start_stack() {
  mkdir -p backups
  log "正在构建应用镜像..."
  "${DOCKER[@]}" compose build
  log "正在启动服务并执行数据库迁移..."
  "${DOCKER[@]}" compose up -d
  log "当前服务状态："
  "${DOCKER[@]}" compose ps
}

install_base_packages
install_docker_official
wait_for_docker
write_env_file
start_stack

log "安装完成。查看状态：bash scripts/linux/status.sh"
if [ "${DOCKER[0]}" = "sudo" ]; then
  log "提示：当前使用 sudo 运行 Docker。重新登录服务器后，通常可以直接使用 docker 命令。"
fi
