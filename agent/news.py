"""News ingestion — CryptoPanic RSS (token) + free public RSS fallbacks.

Filters: crypto-related, excludes spam/shitcoins, deduplicates.
All content is treated as untrusted data (wrapped in delimiters for LLM).

Sources are tried in order until one yields items:
  1. CryptoPanic RSS with a user token (CRYPTOPANIC_TOKEN) — higher quality;
  2. Free public RSS feeds that need no token (CoinDesk, CoinTelegraph).
"""

import hashlib
import html
import os
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from . import config

SEEN_FILE = os.path.join(config.STATE_DIR, ".agent_seen_news.json")


@dataclass
class NewsItem:
    title: str
    link: str
    published: str
    source: str
    relevance: float  # 0.0-1.0

    def to_prompt(self) -> str:
        """Wrap in delimiters for safe LLM consumption."""
        return (
            f"<untrusted_news_item>\n"
            f"Title: {self.title}\n"
            f"Source: {self.source}\n"
            f"Link: {self.link}\n"
            f"</untrusted_news_item>"
        )


# Keywords that indicate prediction-market-worthy news
HIGH_RELEVANCE = [
    "bitcoin", "btc", "ethereum", "eth", "solana", "sol",
    "base", "coinbase", "x402", "ai", "agent", "hack",
    "sec", "etf", "ban", "crash", "all-time high", "ath",
    "regulation", "partnership", "adoption", "launch",
]
LOW_RELEVANCE = [
    "airdrop", "giveaway", "free tokens", "presale",
    "shitcoin", "moon", "100x", "guaranteed",
]


def _score_relevance(title: str, summary: str) -> float:
    text = (title + " " + summary).lower()
    score = 0.3  # base
    for kw in HIGH_RELEVANCE:
        if kw in text:
            score += 0.15
    for kw in LOW_RELEVANCE:
        if kw in text:
            score -= 0.3
    return max(0.0, min(1.0, score))


def _load_seen() -> set:
    import json
    from pathlib import Path
    p = Path(SEEN_FILE)
    if p.exists():
        try:
            return set(json.loads(p.read_text()))
        except (ValueError, OSError):
            return set()
    return set()


def _save_seen(seen: set) -> None:
    import json
    from pathlib import Path
    # Cap the file: without pruning it grows (and is re-read) forever.
    if len(seen) > 5000:
        seen = set(sorted(seen)[-5000:])
    try:
        Path(SEEN_FILE).parent.mkdir(parents=True, exist_ok=True)
        Path(SEEN_FILE).write_text(json.dumps(sorted(seen)))
    except OSError:
        pass  # read-only FS: dedupe simply does not survive restarts


def _sources() -> list[tuple[str, str]]:
    """Ordered (source_label, url) list, CryptoPanic first when a token is set."""
    token = os.environ.get("CRYPTOPANIC_TOKEN", "").strip()
    sources: list[tuple[str, str]] = []
    if token:
        sources.append((
            "CryptoPanic",
            f"https://cryptopanic.com/api/free/v1/posts/?auth_token={token}&public=true",
        ))
    sources.extend([
        ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
        ("CoinTelegraph", "https://cointelegraph.com/rss"),
    ])
    return sources


def _fetch_feed(url: str, timeout: int = 10) -> bytes | None:
    """Fetch an RSS feed body; None on any network failure."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "TippyAgent/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception:
        return None


def _parse_items(data: bytes, source_label: str, seen: set) -> list[NewsItem]:
    # CryptoPanic's free API returns JSON, not RSS — detect and route.
    stripped = data.lstrip()
    if stripped.startswith(b"{"):
        return _parse_json_items(data, source_label, seen)
    return _parse_rss_items(data, source_label, seen)


def _parse_json_items(data: bytes, source_label: str, seen: set) -> list[NewsItem]:
    """CryptoPanic v1 JSON: {"results": [{"title", "url", "published_at",
    "source": {"title": ...}}, ...]}."""
    import json as _json

    try:
        payload = _json.loads(data)
    except ValueError:
        return []
    results = payload.get("results") or []
    if not isinstance(results, list):
        return []

    items = []
    for entry in results:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()
        link = str(entry.get("url") or "").strip()
        if not title or not link:
            continue
        published = str(entry.get("published_at") or "")
        source_obj = entry.get("source") or {}
        source = (
            source_obj.get("title", source_label)
            if isinstance(source_obj, dict)
            else source_label
        )

        uid = hashlib.md5(link.encode()).hexdigest()
        if uid in seen:
            continue
        seen.add(uid)

        relevance = _score_relevance(title, "")
        if relevance < 0.3:
            continue
        items.append(NewsItem(
            title=title,
            link=link,
            published=published,
            source=source,
            relevance=relevance,
        ))
    return items


def _parse_rss_items(data: bytes, source_label: str, seen: set) -> list[NewsItem]:
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []

    items = []
    for item in root.iter("item"):
        title_el = item.find("title")
        link_el = item.find("link")
        pub_el = item.find("pubDate")
        source_el = item.find("source")

        if title_el is None or link_el is None:
            continue

        title = html.unescape(title_el.text or "")
        link = (link_el.text or "").strip()
        published = pub_el.text if pub_el is not None else ""
        source = source_el.text if source_el is not None else source_label

        # Deduplicate
        uid = hashlib.md5(link.encode()).hexdigest()
        if uid in seen:
            continue
        seen.add(uid)

        relevance = _score_relevance(title, "")
        if relevance < 0.3:
            continue

        items.append(NewsItem(
            title=title,
            link=link,
            published=published,
            source=source,
            relevance=relevance,
        ))
    return items


def fetch_news(max_items: int = 5) -> list[NewsItem]:
    """Fetch latest news from the first working source. Deduplicated, scored.

    Returns [] when every source fails (network down / empty feeds) — the
    agent treats that as "nothing to do", never as an error.
    """
    seen = _load_seen()
    collected: list[NewsItem] = []

    for label, url in _sources():
        data = _fetch_feed(url)
        if data is None:
            continue
        collected.extend(_parse_items(data, label, seen))
        if collected:
            break  # first source that yields relevant items wins

    _save_seen(seen)
    collected.sort(key=lambda x: x.relevance, reverse=True)
    return collected[:max_items]
