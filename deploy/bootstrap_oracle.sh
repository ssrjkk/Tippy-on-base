#!/usr/bin/env bash
#
# One-shot bootstrap for the Tippy bot on Oracle Cloud Free Tier.
# Run this ONCE on a fresh Ubuntu 22.04/24.04 (x86_64 or ARM) VM as root:
#
#     curl -fsSL https://raw.githubusercontent.com/ssrjkk/Tippy-on-base/main/deploy/bootstrap_oracle.sh | sudo bash
#
# It installs Docker, clones the repo, prepares .env, brings up the stack
# (postgres + app + cloudflared), and verifies the app/tunnel/Mini App.
# It AUTO-GENERATES a fresh hot-wallet private key and WALLET_ENC_KEY on the
# server (keys never leave the VM) and prints the DEPOSIT_ADDRESS to top up.
#
# Minimum one-shot call (pass your real BOT_TOKEN):
#     sudo BOT_TOKEN=<token> bash -c "curl -fsSL \
#       https://raw.githubusercontent.com/ssrjkk/Tippy-on-base/main/deploy/bootstrap_oracle.sh | bash"
#
# Then fund the printed DEPOSIT_ADDRESS with a little ETH (gas) + USDC on Base.
#
# The Telegram Mini App button needs a STABLE https URL. Simplest free option:
# a Cloudflare Tunnel to a hostname you control — set MINI_APP_URL and
# CLOUDFLARE_TUNNEL_TOKEN in .env, or set those env vars on the command above.
# Quick tunnels (trycloudflare) change URL on restart and can't be pinned.

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
    apt-get install -y ca-certificates curl git openssl
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

# --- .env: respect pre-seeded env, else use template -------------------------
cd "$APP_DIR"
if [ ! -f .env ]; then
    cp deploy/prod.env.example .env
    log "Created .env from deploy/prod.env.example."
else
    log "Existing .env found; leaving it untouched."
fi

# Merge env vars that were pre-seeded (exported before running).
vars="BOT_TOKEN BOT_USERNAME ADMIN_TG_ID HOT_WALLET_KEY WALLET_ENC_KEY \
POSTGRES_PASSWORD CLOUDFLARE_TUNNEL_TOKEN MINI_APP_URL AI_API_KEY \
BASE_RPC_URL BASE_RPC_FALLBACK_URLS"
changed=0
for v in $vars; do
    eval "val=\${$v:-}"
    if [ -n "${val}" ]; then
        if grep -q "^${v}=" .env; then
            sed -i "s|^${v}=.*|${v}=${val}|" .env
        else
            printf '%s=%s\n' "$v" "$val" >> .env
        fi
        changed=1
    fi
done
[ "$changed" = 1 ] && log "Merged pre-seeded env vars into .env."

# --- auto-generate missing secrets (key NEVER leaves the server) ------------
# Hot wallet: any 32 random bytes (well below secp256k1 order, ~2^-127 chance
# of collision) is a valid Ethereum private key. Generate ONLY if not set.
gen_key=$(grep -E '^HOT_WALLET_KEY=' .env | tail -1 | cut -d= -f2- || true)
case "$gen_key" in
    0x0000000000000000000000000000000000000000000000000000000000000000|"")
        raw=$(openssl rand -hex 32)
        key="0x${raw}"
        if grep -q '^HOT_WALLET_KEY=' .env; then
            sed -i "s|^HOT_WALLET_KEY=.*|HOT_WALLET_KEY=${key}|" .env
        else
            printf 'HOT_WALLET_KEY=%s\n' "$key" >> .env
        fi
        log "Generated a fresh hot-wallet private key (stored only in $APP_DIR/.env)."
        ;;
esac

# Wallet encryption key (32+ hex bytes). Auto-generate when absent.
wek=$(grep -E '^WALLET_ENC_KEY=' .env | tail -1 | cut -d= -f2- || true)
if [ -z "$wek" ] || [ "${#wek}" -lt 32 ]; then
    enc=$(openssl rand -hex 32)
    if grep -q '^WALLET_ENC_KEY=' .env; then
        sed -i "s|^WALLET_ENC_KEY=.*|WALLET_ENC_KEY=${enc}|" .env
    else
        printf 'WALLET_ENC_KEY=%s\n' "$enc" >> .env
    fi
    log "Generated WALLET_ENC_KEY (stored only in $APP_DIR/.env)."
fi

# --- sanity: bot token must be real before the app can run --------------------
btoken=$(grep -E '^BOT_TOKEN=' .env | tail -1 | cut -d= -f2- || true)
case "$btoken" in
    ""|123456:ABC*|*YourBotToken*)
        fatal "BOT_TOKEN is not set or still a placeholder. Pass it when running:
    sudo bash -c \"BOT_TOKEN=$btoken ... curl ... | bash\"
    or edit $APP_DIR/.env. (Hot-wallet key is already generated and saved.)"
        ;;
esac

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

    # Print the hot-wallet DEPOSIT address (public, safe) so it can be funded.
    dep=$(docker compose -f "$APP_DIR/docker-compose.yml" exec -T app \
        python -c "import os;from web3 import Web3;print(Web3().eth.account.from_key(os.environ['HOT_WALLET_KEY']).address)" 2>/dev/null || true)
    if [ -n "$dep" ]; then
        log "============================================================"
        log "  DEPOSIT_ADDRESS = ${dep}"
        log "  Send ETH (for gas) + USDC on Base to this address."
        log "  (Private key lives only in $APP_DIR/.env — never share it.)"
        log "============================================================"
    else
        log "WARNING: could not derive the deposit address. Check app logs."
    fi
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
