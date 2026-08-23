"""API rate limiting middleware for FastAPI.

Protects /api/* endpoints from DDoS and abuse separate from Telegram limits.
Uses in-memory token bucket per IP. Returns 429 Too Many Requests when exceeded.

For production, consider Redis-backed rate limiting (e.g. slowapi).
"""

import time
from collections import defaultdict
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP token bucket rate limiter for API endpoints."""

    def __init__(self, app, requests_per_minute: int = 60, burst: int = 20):
        super().__init__(app)
        self.rpm = requests_per_minute
        self.burst = burst
        self._buckets: dict[str, tuple[float, float]] = {}  # ip -> (tokens, last_refill)
        self._last_cleanup = time.time()

    def _get_client_ip(self, request: Request) -> str:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _refill(self, ip: str) -> tuple[float, float]:
        now = time.time()
        tokens, last = self._buckets.get(ip, (float(self.burst), now))
        elapsed = now - last
        tokens = min(self.burst, tokens + elapsed * (self.rpm / 60.0))
        self._buckets[ip] = (tokens, now)
        return tokens, now

    async def dispatch(self, request: Request, call_next):
        # Only rate-limit /api/* and /metrics
        path = request.url.path
        if not (path.startswith("/api/") or path == "/metrics" or path == "/tos"):
            return await call_next(request)

        ip = self._get_client_ip(request)
        tokens, _ = self._refill(ip)

        if tokens < 1:
            return JSONResponse(
                status_code=429,
                content={"error": "rate limit exceeded", "retry_after_seconds": 60},
                headers={"Retry-After": "60"},
            )

        self._buckets[ip] = (tokens - 1, self._buckets[ip][1])

        # Periodic cleanup of stale entries (every 5 min)
        now = time.time()
        if now - self._last_cleanup > 300:
            self._cleanup(now)
            self._last_cleanup = now

        response = await call_next(request)
        response.headers["X-RateLimit-Remaining"] = str(int(tokens - 1))
        response.headers["X-RateLimit-Limit"] = str(self.burst)
        return response

    def _cleanup(self, now: float) -> None:
        stale = [ip for ip, (_, last) in self._buckets.items() if now - last > 600]
        for ip in stale:
            del self._buckets[ip]
