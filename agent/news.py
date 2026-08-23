"""News ingestion — CryptoPanic RSS feed, filtered for relevance.

Filters: crypto-related, excludes spam/shitcoins, deduplicates.
All content is treated as untrusted data (wrapped in delimiters for LLM).
"""

import hashlib
import html
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass

SEEN_FILE = ".agent_seen_news.json"


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
        return set(json.loads(p.read_text()))
    return set()


def _save_seen(seen: set) -> None:
    import json
    from pathlib import Path
    Path(SEEN_FILE).write_text(json.dumps(list(seen)))


def fetch_news(max_items: int = 5) -> list[NewsItem]:
    """Fetch latest news from CryptoPanic RSS. Returns deduplicated, scored items."""
    url = "https://cryptopanic.com/api/free/v1/posts/?auth_token=&public=true"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "TippyAgent/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
    except Exception:
        return []

    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []

    seen = _load_seen()
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
        source = source_el.text if source_el is not None else ""

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

    _save_seen(seen)
    items.sort(key=lambda x: x.relevance, reverse=True)
    return items[:max_items]
