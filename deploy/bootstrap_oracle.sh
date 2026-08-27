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
if docker compose -f "$APP_DIR/docker-compose.yml" up -d --build; then
    log "Stack is up. Services restart automatically (restart: unless-stopped)."
    log "Wait ~30s, then check:"
    log "    docker compose -f $APP_DIR/docker-compose.yml ps"
    log "    docker compose -f $APP_DIR/docker-compose.yml logs -f app"
else
    fatal "docker compose up failed — fix .env, then re-run: docker compose -f $APP_DIR/docker-compose.yml up -d --build"
fi

log "Done. Tippy should be reachable at \${MINI_APP_URL} (set it to your tunnel host)."
