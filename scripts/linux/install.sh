#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

log() { printf '\n[botwanfa] %s\n' "$*"; }
fail() { printf '\n[botwanfa] ERROR: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

if [ "${EUID:-$(id -u)}" -eq 0 ]; then
  SUDO=()
else
  have sudo || fail "sudo is required. Re-run as root or install sudo first."
  SUDO=(sudo)
fi

export DEBIAN_FRONTEND=noninteractive

if [ -r /etc/os-release ]; then
  . /etc/os-release
  if [ "${ID:-}" != "ubuntu" ]; then
    log "This installer is tuned for Ubuntu 24.x; detected ${PRETTY_NAME:-unknown}. Continuing."
  fi
else
  fail "/etc/os-release not found; this installer expects Ubuntu 24.x."
fi

install_base_packages() {
  log "Checking and installing base packages..."
  "${SUDO[@]}" apt-get update
  "${SUDO[@]}" apt-get install -y ca-certificates curl gnupg lsb-release openssl git uidmap
}

install_docker_official() {
  if have docker && docker compose version >/dev/null 2>&1; then
    log "Docker and Compose are already installed."
    return
  fi

  log "Installing Docker Engine and Compose plugin from the official Docker repository..."
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
  log "Waiting for Docker engine..."
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
  fail "Docker engine did not become ready. Check the log above and re-run this script."
}

write_env_file() {
  if [ -f .env ]; then
    read -r -p ".env already exists. Recreate it? [y/N]: " RECREATE_ENV
    case "$RECREATE_ENV" in
      y|Y|yes|YES) ;;
      *) log "Keeping existing .env"; return ;;
    esac
  fi

  read -r -s -p "Bot Token: " BOT_TOKEN
  printf '\n'
  read -r -p "Super admin IDs, comma separated: " SUPER_ADMIN_IDS
  read -r -s -p "Backup passphrase, at least 12 characters: " BACKUP_PASSPHRASE
  printf '\n'

  [ -n "$BOT_TOKEN" ] || fail "Bot Token is empty."
  [ -n "$SUPER_ADMIN_IDS" ] || fail "Super admin IDs are empty."
  [ "${#BACKUP_PASSPHRASE}" -ge 12 ] || fail "Backup passphrase must have at least 12 characters."

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
  log "Building application image..."
  "${DOCKER[@]}" compose build
  log "Starting services and running database migration..."
  "${DOCKER[@]}" compose up -d
  log "Current service status:"
  "${DOCKER[@]}" compose ps
}

install_base_packages
install_docker_official
wait_for_docker
write_env_file
start_stack

log "Install complete. Use: bash scripts/linux/status.sh"
if [ "${DOCKER[0]}" = "sudo" ]; then
  log "Tip: log out and back in to use docker without sudo after group membership refresh."
fi
