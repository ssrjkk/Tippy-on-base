#!/usr/bin/env bash
#
# One-shot bootstrap for the Tippy bot on Oracle Cloud Free Tier.
# Run this ONCE on a fresh Ubuntu 22.04/24.04 (x86_64 or ARM) VM as root:
#
#     curl -fsSL https://raw.githubusercontent.com/ssrjkk/Tippy-on-base/main/deploy/bootstrap_oracle.sh | sudo bash
#
# It installs Docker, clones the repo, prepares .env, and brings up the stack
# (postgres + app + cloudflared). Everything restarts automatically on reboot
# or crash (restart: unless-stopped). Pass env vars interactively or pre-seed
# them by exporting before running (no local machine involved).
#
# After it finishes:
#   1) cp deploy/prod.env.example .env   (if not pre-seeded) and fill it in
#   2) docker compose -f docker-compose.yml up -d --build
#   3) follow the Cloudflare named-tunnel step in .env.example (free, once)
#
# The Telegram Mini App button needs a STABLE https URL from a named Cloudflare
# tunnel; quick tunnels change URL every restart and can't be pinned in BotFather.

set -euo pipefail

log() { printf '[bootstrap] %s\n' "$*"; }
fatal() { printf '[bootstrap] ERROR: %s\n' "$*" >&2; exit 1; }

: "${REPO_URL:=https://github.com/ssrjkk/Tippy-on-base.git}"
: "${APP_DIR:=/opt/tippy}"
: "${BRANCH:=main}"

# --- root check (Oracle images default to root via sudo -s) -----------------
[ "$(id -u)" -eq 0 ] || fatal "run as root (sudo bash deploy/bootstrap_oracle.sh)"

# --- detect OS / package manager -------------------------------------------
if command -v apt-get >/dev/null 2>&1; then
    PKG=apt
elif command -v dnf >/dev/null 2>&1; then
    PKG=dnf
else
    PKG=apt
fi

log "Installing Docker Engine + compose plugin ($PKG)..."
if [ "$PKG" = "apt" ]; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get install -y ca-certificates curl git
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    . /etc/os-release
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] " \
      "https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
      > /etc/apt/sources.list.d/docker.list
    apt-get update -y
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
else
    dnf install -y 'dnf-command(config-manager)'
    dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
    dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker
log "Docker installed: $(docker --version 2>&1 || true) | compose: $(docker compose version 2>&1 || true)"

# --- clone / refresh the repo ----------------------------------------------
if [ ! -d "$APP_DIR/.git" ]; then
    mkdir -p "$APP_DIR"
    git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
else
    log "Repo present; pulling latest $BRANCH..."
    git -C "$APP_DIR" fetch --all --prune
    git -C "$APP_DIR" reset --hard "origin/$BRANCH"
fi

# --- .env: respect pre-seeded env, else copy template -----------------------
cd "$APP_DIR"
if [ ! -f .env ]; then
    if cp deploy/prod.env.example .env; then
        log "Created .env from deploy/prod.env.example — EDIT IT, then:"
        log "    nano $APP_DIR/.env"
        log "    docker compose -f $APP_DIR/docker-compose.yml up -d --build"
        fatal "aborting before start: .env requires your real secrets/token."
    fi
else
    log "Existing .env found; leaving it untouched."
fi

# If env vars were pre-seeded (exported before running), write them in.
: > /tmp/tippy_env_seed
vars="BOT_TOKEN BOT_USERNAME ADMIN_TG_ID HOT_WALLET_KEY WALLET_ENC_KEY \
POSTGRES_PASSWORD CLOUDFLARE_TUNNEL_TOKEN MINI_APP_URL AI_API_KEY \
BASE_RPC_URL BASE_RPC_FALLBACK_URLS"
changed=0
for v in $vars; do
    eval "val=\${$v:-}"
    if [ -n "${val}" ]; then
        grep -q "^$v=" .env 2>/dev/null || true
        if grep -q "^${v}=" .env; then
            sed -i "s|^${v}=.*|${v}=${val}|" .env
        else
            printf '%s=%s\n' "$v" "$val" >> .env
        fi
        changed=1
    fi
done
[ "$changed" = 1 ] && log "Merged pre-seeded env vars into .env."

# --- bring it up ------------------------------------------------------------
log "Building and starting Tippy (db + app + cloudflared)..."
if ! docker compose -f "$APP_DIR/docker-compose.yml" up -d --build; then
    fatal "docker compose up failed — fix .env, then re-run: docker compose -f $APP_DIR/docker-compose.yml up -d --build"
fi

# --- verify the app is healthy (waits for migrations + web server) ----------
log "Waiting for the app to become healthy..."
healthy=0
for i in $(seq 1 60); do
    st=$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose -f "$APP_DIR/docker-compose.yml" ps -q app)" 2>/dev/null || true)
    if [ "$st" = "healthy" ]; then healthy=1; break; fi
    sleep 5
done
if [ "$healthy" != 1 ]; then
    log "WARNING: app container not reported healthy in time. Check logs:"
    docker compose -f "$APP_DIR/docker-compose.yml" logs --tail=40 app || true
else
    log "app is healthy (web server up, migrations applied)."
fi

# --- verify the Cloudflare tunnel is connected ------------------------------
log "Checking Cloudflare tunnel connector is up..."
cf_ok=0
for i in $(seq 1 12); do
    if docker compose -f "$APP_DIR/docker-compose.yml" exec -T cloudflared cloudflared tunnel info 2>/dev/null | grep -qi 'connector'; then
        cf_ok=1; break
    fi
    sleep 5
done
[ "$cf_ok" = 1 ] && log "Cloudflare tunnel connected." || log "WARNING: could not confirm the tunnel. Is CLOUDFLARE_TUNNEL_TOKEN set and the tunnel created?"

# --- verify the public Mini App URL returns 200 ------------------------------
mini=$(grep -E '^MINI_APP_URL=' "$APP_DIR/.env" | cut -d= -f2- || true)
if [ -n "$mini" ]; then
    log "Verifying Mini App reaches the internet: ${mini}/app"
    reach=0
    for i in $(seq 1 12); do
        code=$(curl -fsS -o /dev/null -w '%{http_code}' "${mini}/app" 2>/dev/null || true)
        if [ "$code" = "200" ]; then reach=1; break; fi
        sleep 5
    done
    if [ "$reach" = 1 ]; then
        log "SUCCESS: Mini App is live at ${mini}/app (HTTP 200)."
    else
        log "WARNING: ${mini}/app did not return 200 yet. If your domain is new on Cloudflare,"
        log "SSL may take a minute. Re-run this check: curl -I ${mini}/app"
    fi
else
    log "MINI_APP_URL is empty — the Mini App button in Telegram will NOT be usable."
    log "Set MINI_APP_URL=https://<your-domain> in .env and rerun this script."
fi

log "Done. Services:"
docker compose -f "$APP_DIR/docker-compose.yml" ps
log "Everything restarts automatically (restart: unless-stopped). Logs: docker compose -f $APP_DIR/docker-compose.yml logs -f app"
