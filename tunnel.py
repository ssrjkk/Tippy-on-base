"""Start cloudflared tunnel + bot + web server. Outputs the public URL."""
import subprocess
import re
import sys
import time
import os

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
