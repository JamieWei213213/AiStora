#!/usr/bin/env bash
# AIStora single-server bootstrap for the budget profile (Lightsail, 2 GB).
#
# One-liner on a fresh Ubuntu box (run as the login user, not root):
#   curl -fsSL https://raw.githubusercontent.com/JamieWei213213/AiStora/main/infra/budget/server-setup.sh -o setup.sh && bash setup.sh
#
# Idempotent: safe to re-run. Re-running pulls the latest main and rebuilds.
# It never prints secrets and never runs `docker compose down -v`.
set -euo pipefail

REPO_URL="${AISTORA_REPO_URL:-https://github.com/JamieWei213213/AiStora.git}"
BRANCH="${AISTORA_BRANCH:-main}"
APP_DIR="${AISTORA_DIR:-$HOME/aistora}"
DOMAIN_DEFAULT="ai-stora.com"
COMPOSE=(docker compose --env-file .env.budget -f docker-compose.budget.yml)

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || die "run as the normal login user (ubuntu), not root"

# ---------------------------------------------------------------- 1. swap
if ! swapon --show | grep -q '^/swapfile'; then
  log "Creating 2 GiB swap file (the image build needs it on a 2 GB box)"
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null
  sudo swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
  echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-aistora-swap.conf >/dev/null
  sudo sysctl -q vm.swappiness=10
else
  log "Swap already present"
fi

# -------------------------------------------------------------- 2. docker
if ! command -v docker >/dev/null 2>&1; then
  log "Installing Docker Engine"
  curl -fsSL https://get.docker.com | sudo sh
fi
if ! docker compose version >/dev/null 2>&1; then
  log "Installing the docker compose plugin"
  sudo apt-get update -qq && sudo apt-get install -y -qq docker-compose-plugin
fi
if ! id -nG "$USER" | grep -qw docker; then
  sudo usermod -aG docker "$USER"
  log "Added $USER to the docker group. Log out, log back in, and re-run this script."
  exit 0
fi
docker info >/dev/null 2>&1 || die "docker daemon not reachable; log out/in after the group change and re-run"
sudo apt-get install -y -qq git >/dev/null 2>&1 || true

# ---------------------------------------------------------------- 3. code
if [ -d "$APP_DIR/.git" ]; then
  log "Updating $APP_DIR from $BRANCH"
  git -C "$APP_DIR" fetch --quiet origin "$BRANCH"
  git -C "$APP_DIR" checkout --quiet "$BRANCH"
  git -C "$APP_DIR" reset --quiet --hard "origin/$BRANCH"
else
  log "Cloning $REPO_URL into $APP_DIR"
  git clone --quiet --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"
log "Deploying commit $(git rev-parse --short HEAD)"

# ---------------------------------------------------------- 4. .env.budget
ask() { # ask VAR "prompt" [default] [secret]
  local var="$1" prompt="$2" def="${3:-}" secret="${4:-}" val
  if [ -n "$secret" ]; then
    read -r -s -p "$prompt: " val; echo
  else
    read -r -p "$prompt${def:+ [$def]}: " val
  fi
  printf -v "$var" '%s' "${val:-$def}"
}

if [ ! -f .env.budget ]; then
  log "Creating .env.budget (kept out of git; secrets are never echoed)"
  ask AISTORA_DOMAIN "Domain" "$DOMAIN_DEFAULT"
  ask GEMINI_MODEL "Gemini model" "gemini-3.5-flash-lite"
  ask GEMINI_API_KEY "Gemini API key (from the capped AI Studio project)" "" secret
  ask SMTP_HOST "SMTP host" "smtp.resend.com"
  ask SMTP_PORT "SMTP port (587 STARTTLS / 465 TLS)" "587"
  ask SMTP_USERNAME "SMTP username" "resend"
  ask SMTP_PASSWORD "SMTP password / Resend API key (blank = no reset emails yet)" "" secret
  ask SMTP_FROM "From address" "AIStora <no-reply@${AISTORA_DOMAIN}>"
  SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  POSTGRES_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
  umask 077
  cat > .env.budget <<EOF
AISTORA_DOMAIN=${AISTORA_DOMAIN}
SECRET_KEY=${SECRET_KEY}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
GEMINI_API_KEY=${GEMINI_API_KEY}
GEMINI_MODEL=${GEMINI_MODEL}
SMTP_HOST=${SMTP_HOST}
SMTP_PORT=${SMTP_PORT}
SMTP_FROM=${SMTP_FROM}
SMTP_USERNAME=${SMTP_USERNAME}
SMTP_PASSWORD=${SMTP_PASSWORD}
EOF
  umask 022
else
  log "Using existing .env.budget"
fi
chmod 600 .env.budget

# -------------------------------------------------------- 5. DNS sanity
DOMAIN="$(sed -n 's/^AISTORA_DOMAIN=//p' .env.budget)"
PUBLIC_IP="$(curl -fsS -m 5 https://checkip.amazonaws.com 2>/dev/null | tr -d '[:space:]' || true)"
RESOLVED="$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk 'NR==1{print $1}' || true)"
if [ -n "$PUBLIC_IP" ] && [ "$RESOLVED" != "$PUBLIC_IP" ]; then
  log "WARNING: $DOMAIN resolves to '${RESOLVED:-nothing}' but this server's public IP is $PUBLIC_IP."
  echo "    Caddy will fail to get a certificate until the A record points here."
  echo "    Fix DNS at the registrar, wait a few minutes, then re-run. Continuing anyway."
fi

# ------------------------------------------------------------ 6. launch
log "Validating compose configuration"
"${COMPOSE[@]}" config --quiet

log "Building image and starting the stack (first build takes several minutes on 2 GB)"
"${COMPOSE[@]}" up -d --build --remove-orphans

log "Waiting for the app to report healthy"
for _ in $(seq 1 60); do
  state="$(docker inspect -f '{{.State.Health.Status}}' aistora-beta-web-1 2>/dev/null || echo starting)"
  [ "$state" = "healthy" ] && break
  sleep 5
done
"${COMPOSE[@]}" ps
[ "$state" = "healthy" ] || { "${COMPOSE[@]}" logs --tail=80 web; die "web container is '$state' — see logs above"; }

log "Pruning old build layers"
docker image prune -f >/dev/null 2>&1 || true

log "Done. Checks:"
echo "  curl -sI http://$DOMAIN | head -1        # expect 308 -> https"
echo "  curl -sS https://$DOMAIN/health           # expect {\"status\":\"ok\"...}"
echo "  ${COMPOSE[*]} logs -f web                 # follow app logs"
echo "  free -m && df -h / && docker stats --no-stream"
