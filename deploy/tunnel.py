"""Start cloudflared tunnel + bot + web server. Outputs the public URL."""
import os
import re
import subprocess
import sys

CF = os.path.join(os.path.dirname(__file__), "cloudflared.exe")

def main():
    proc = subprocess.Popen(
        [CF, "tunnel", "--url", "http://localhost:8000"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    url = None
    lines = []
    for line in proc.stdout:
        line = line.rstrip()
        lines.append(line)
        m = re.search(r'(https://[a-z0-9-]+\.trycloudflare\.com)', line)
        if m:
            url = m.group(1)
            break
        if len(lines) > 50:
            break

    if not url:
        print("ERROR: could not find tunnel URL")
        print("Last lines:", lines[-10:])
        proc.kill()
        sys.exit(1)

    # Per-IP rate limiting collapses to one bucket while every client arrives
    # from the local cloudflared (peer 127.0.0.1). Tell the operator to flip
    # TRUST_PROXY_XFF=1 so the app uses Cloudflare's X-Forwarded-For REAL
    # client IPs instead — the app only honours XFF from trusted peers
    # (127.0.0.1 is trusted by default), so direct-socket spoofing stays dead.
    print("NOTE: set TRUST_PROXY_XFF=1 in .env so this tunnel's clients are",
          "rate-limited per real IP, and set METRICS_TOKEN to protect /metrics.")

    print(f"TUNNEL_URL={url}")
    print(f"MINI_APP={url}/app")
    print(f"Dashboard: {url}")
    print(f"API: {url}/api/health")
    print("Tunnel running in background. Press Ctrl+C to stop.")
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.kill()

if __name__ == "__main__":
    main()
