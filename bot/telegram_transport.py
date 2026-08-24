"""Telegram Bot API transport hardening.

On some networks DNS resolves api.telegram.org to an unreachable IP
(regional censorship), while other Telegram datacenter IPs on the same
hostname serve the Bot API perfectly. Setting TELEGRAM_API_IP pins the
hostname to a reachable IP for aiogram's HTTP session only; TLS SNI and
certificate validation still use the real hostname, so this is safe.
"""
from __future__ import annotations

import asyncio
import logging
import socket

from aiogram.client.session.aiohttp import AiohttpSession

log = logging.getLogger("tipbot.telegram_transport")

API_HOST = "api.telegram.org"


class _PinnedHostResolver(asyncio.AbstractResolver):
    """Resolve API_HOST to a fixed IP; everything else via normal DNS.

    The fallback ThreadedResolver is created lazily inside the event loop
    (aiohttp >= 3.12 requires a running loop in its constructor).
    """

    def __init__(self, ip: str) -> None:
        self._ip = ip
        self._fallback: asyncio.AbstractResolver | None = None

    async def _get_fallback(self) -> asyncio.AbstractResolver:
        if self._fallback is None:
            from aiohttp.resolver import ThreadedResolver

            self._fallback = ThreadedResolver()
        return self._fallback

    async def resolve(self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_UNSPEC):
        if host == API_HOST:
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    6,
                    "",
                    (self._ip, port),
                )
            ]
        fallback = await self._get_fallback()
        return await fallback.resolve(host, port, family)

    async def close(self) -> None:
        if self._fallback is not None:
            await self._fallback.close()


def make_session(pinned_ip: str | None) -> AiohttpSession | None:
    """Build an aiogram session with a pinned resolver, or None if unused."""
    if not pinned_ip:
        return None
    session = AiohttpSession()
    session._connector_init["resolver"] = _PinnedHostResolver(pinned_ip)
    session._should_reset_connector = True
    log.warning("api.telegram.org pinned to %s (TELEGRAM_API_IP)", pinned_ip)
    return session
