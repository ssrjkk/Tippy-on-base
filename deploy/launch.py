"""Tippy launcher - one command to bring the whole demo up, with a
self-healing public tunnel.

It starts a cloudflared quick tunnel to the local web server, captures the
public https URL, exports MINI_APP_URL (so Telegram WebApp buttons are valid),
then launches the web dashboard (uvicorn) and the bot (long polling).

Cloudflare quick tunnels are flaky (the edge can drop them). To keep the Mini
App reachable we monitor the public URL and, after a few consecutive failed
health checks (or if cloudflared dies), restart the tunnel and the bot so the
new URL is picked up automatically.

Usage:
    python launch.py
    MINI_APP_URL=https://my.domain python launch.py   # skip tunnel, fixed domain
    NO_TUNNEL=1 MINI_APP_URL=https://x.trycloudflare.com python launch.py
"""
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("launch")

PORT = int(os.environ.get("WEB_PORT", "8000"))
HOST = os.environ.get("WEB_HOST", "0.0.0.0")
HEALTH_INTERVAL = int(os.environ.get("TUNNEL_HEALTH_INTERVAL", "20"))
HEALTH_FAILS_BEFORE_RESTART = int(os.environ.get("TUNNEL_HEALTH_FAILS", "3"))

CLOUDFLARED_CANDIDATES = [
    os.environ.get("CLOUDFLARED_BIN"),
    os.path.join(os.path.dirname(__file__), "cloudflared.exe"),
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "cloudflared.exe"),
    "cloudflared",
]

TUNNEL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def find_cloudflared() -> str | None:
    for cand in CLOUDFLARED_CANDIDATES:
        if not cand:
            continue
        if os.path.exists(cand):
            return cand
        found = shutil.which(cand)
        if found:
            return found
    return None


def start_cloudflared(bin_path: str):
    log.info("starting cloudflared tunnel -> http://localhost:%s", PORT)
    proc = subprocess.Popen(
        [bin_path, "tunnel", "--url", f"http://localhost:{PORT}",
         "--protocol", "http2", "--no-autoupdate"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    url = None
    started = time.time()
    try:
        for raw in proc.stdout:
            line = raw.rstrip()
            if line:
                log.info("[cfd] %s", line)
            m = TUNNEL_RE.search(line)
            if m:
                url = m.group(0)
                break
            if time.time() - started > 60:
                break
    except Exception as e:  # pragma: no cover - defensive
        log.warning("reading cloudflared output failed: %s", e)
    if not url:
        try:
            out, _ = proc.communicate(timeout=2)
            m = TUNNEL_RE.search(out or "")
            if m:
                url = m.group(0)
        except Exception:
            pass
    return proc, url


def _check(url: str, path: str = "/app", timeout: int = 8) -> bool:
    try:
        with urllib.request.urlopen(url + path, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def main() -> int:
    py = sys.executable
    provided = os.environ.get("MINI_APP_URL")
    bin_path = find_cloudflared() if not (provided and provided.startswith("http")) else None

    if provided and provided.startswith("http"):
        url = provided.rstrip("/")
        log.info("using provided MINI_APP_URL=%s (skipping tunnel)", url)
        cfd = None
    else:
        if not bin_path:
            log.error("cloudflared not found; set MINI_APP_URL to your public https URL")
            return 1
        cfd, url = start_cloudflared(bin_path)
        if not url:
            log.error("could not determine cloudflared tunnel URL")
            if cfd:
                cfd.terminate()
            return 1
        log.info("tunnel ready: %s", url)
        os.environ["MINI_APP_URL"] = url

    web_log = open("web.log", "a")
    web_err = open("web.err", "a")
    bot_log = open("bot.log", "a")
    bot_err = open("bot.err", "a")

    def start_children():
        web = subprocess.Popen(
            [py, "-m", "uvicorn", "web.server:app", "--host", HOST, "--port", str(PORT)],
            env=dict(os.environ), stdout=web_log, stderr=web_err,
        )
        bot = subprocess.Popen(
            [py, "-m", "bot.main"], env=dict(os.environ), stdout=bot_log, stderr=bot_err,
        )
        return web, bot

    web, bot = start_children()
    children = [web, bot]
    log.info("web pid=%s  bot pid=%s  Mini App -> %s/app", web.pid, bot.pid, url)

    def _restart_for_new_url():
        nonlocal web, bot, children
        for p in (web, bot):
            try:
                p.terminate()
            except Exception:
                pass
        time.sleep(2)
        web, bot = start_children()
        children = [web, bot]
        log.info("children restarted for new Mini App -> %s/app",
                 os.environ["MINI_APP_URL"])

    time.sleep(6)
    try:
        with urllib.request.urlopen(f"http://localhost:{PORT}/app", timeout=5) as r:
            log.info("local health /app -> %s (%s bytes)", r.status, len(r.read()))
    except Exception as e:
        log.warning("local health /app failed: %s", e)

    stop = {"v": False}

    def _handle(signum, _frame):
        log.info("signal %s - shutting down", signum)
        stop["v"] = True

    try:
        signal.signal(signal.SIGINT, _handle)
    except Exception:
        pass

    fails = 0
    while not stop["v"]:
        # Restart children that crashed on their own.
        if bot.poll() is not None:
            log.warning("bot exited with %s; restarting", bot.returncode)
            bot = subprocess.Popen([py, "-m", "bot.main"], env=dict(os.environ),
                                   stdout=bot_log, stderr=bot_err)
            children[1] = bot
        if web.poll() is not None:
            log.warning("web exited with %s; restarting", web.returncode)
            web = subprocess.Popen([py, "-m", "uvicorn", "web.server:app",
                                    "--host", HOST, "--port", str(PORT)],
                                   env=dict(os.environ), stdout=web_log, stderr=web_err)
            children[0] = web

        if cfd is not None:
            if cfd.poll() is not None:
                log.warning("cloudflared died; restarting tunnel")
                cfd, new_url = start_cloudflared(bin_path)
                if new_url:
                    url = new_url
                    os.environ["MINI_APP_URL"] = url
                    _restart_for_new_url()
            elif _check(url):
                fails = 0
            else:
                fails += 1
                log.warning("tunnel health check failed (%s/%s)",
                            fails, HEALTH_FAILS_BEFORE_RESTART)
                if fails >= HEALTH_FAILS_BEFORE_RESTART:
                    log.warning("tunnel unhealthy; restarting cloudflared")
                    try:
                        cfd.terminate()
                    except Exception:
                        pass
                    time.sleep(2)
                    cfd, new_url = start_cloudflared(bin_path)
                    if new_url:
                        url = new_url
                        os.environ["MINI_APP_URL"] = url
                        _restart_for_new_url()
                    fails = 0

        time.sleep(HEALTH_INTERVAL)

    for p in children + ([cfd] if cfd else []):
        try:
            p.terminate()
        except Exception:
            pass
    log.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
