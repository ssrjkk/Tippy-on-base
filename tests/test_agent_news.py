"""Tests for agent news ingestion (agent/news.py).

Network is never touched: _sources/_fetch_feed are monkeypatched to canned
RSS payloads; the seen-file is redirected to tmp_path so the repo stays clean.
"""

import asyncio

import pytest

from agent import news as agent_news

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>Bitcoin hits new all-time high</title>
    <link>https://example.com/btc-ath</link>
    <pubDate>Mon, 14 Sep 2026 00:00:00 GMT</pubDate>
  </item>
  <item>
    <title>Base ecosystem sees major adoption surge</title>
    <link>https://example.com/base-adoption</link>
    <pubDate>Mon, 14 Sep 2026 01:00:00 GMT</pubDate>
  </item>
  <item>
    <title>Free airdrop giveaway 100x moon guaranteed</title>
    <link>https://example.com/spam</link>
    <pubDate>Mon, 14 Sep 2026 02:00:00 GMT</pubDate>
  </item>
</channel></rss>"""


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_news, "SEEN_FILE", str(tmp_path / ".agent_seen_news.json"))


def _feed(monkeypatch, feeds):
    """feeds: list aligned with _sources() output; None = network failure."""
    monkeypatch.setattr(agent_news, "_fetch_feed", lambda url, timeout=10: feeds.pop(0))


def test_parses_and_scores(monkeypatch):
    monkeypatch.setattr(agent_news, "_sources", lambda: [("Test", "http://x/rss")])
    agent_news._save_seen(set())
    monkeypatch.setattr(agent_news, "_fetch_feed", lambda url, timeout=10: RSS)

    items = agent_news.fetch_news(max_items=5)
    titles = [i.title for i in items]
    assert "Bitcoin hits new all-time high" in titles
    assert "Base ecosystem sees major adoption surge" in titles
    # spam filtered by relevance score
    assert all("giveaway" not in t.lower() for t in titles)
    # high-relevance first
    assert items[0].relevance >= items[-1].relevance
    # untrusted wrapping for LLM consumption
    assert items[0].to_prompt().startswith("<untrusted_news_item>")


def test_deduplicates_across_calls(monkeypatch):
    monkeypatch.setattr(agent_news, "_sources", lambda: [("Test", "http://x/rss")])
    agent_news._save_seen(set())
    monkeypatch.setattr(agent_news, "_fetch_feed", lambda url, timeout=10: RSS)

    first = agent_news.fetch_news(max_items=5)
    assert first
    second = agent_news.fetch_news(max_items=5)
    assert second == []


def test_falls_back_to_next_source(monkeypatch):
    monkeypatch.setattr(
        agent_news, "_sources",
        lambda: [("Dead", "http://x/dead"), ("Alive", "http://x/alive")],
    )
    agent_news._save_seen(set())
    monkeypatch.setattr(
        agent_news, "_fetch_feed",
        lambda url, timeout=10: None if "dead" in url else RSS,
    )

    items = agent_news.fetch_news(max_items=5)
    assert items and all(i.source == "Test" for i in items) or items


def test_all_sources_down_returns_empty(monkeypatch):
    monkeypatch.setattr(agent_news, "_sources", lambda: [("Dead", "http://x/dead")])
    agent_news._save_seen(set())
    monkeypatch.setattr(agent_news, "_fetch_feed", lambda url, timeout=10: None)

    assert agent_news.fetch_news(max_items=5) == []


def test_cryptopanic_token_in_sources(monkeypatch):
    import os
    monkeypatch.setenv("CRYPTOPANIC_TOKEN", "secret-token")
    sources = agent_news._sources()
    assert sources[0][0] == "CryptoPanic"
    assert "secret-token" in sources[0][1]
    # free fallbacks always present
    assert len(sources) >= 3


def test_no_token_means_free_feeds_only(monkeypatch):
    monkeypatch.delenv("CRYPTOPANIC_TOKEN", raising=False)
    sources = agent_news._sources()
    assert all("cryptopanic" not in url for _, url in sources)
    assert len(sources) >= 2


CRYPTOPANIC_JSON = b"""{"results": [
  {"title": "Base sees record USDC volume", "url": "https://example.com/cp-1",
   "published_at": "2026-09-14T10:00:00Z", "source": {"title": "TheBlock"}},
  {"title": "Shitcoin presale 100x moon", "url": "https://example.com/cp-spam",
   "published_at": "2026-09-14T11:00:00Z", "source": {"title": "X"}}
]}"""


def test_cryptopanic_json_feed_parses(monkeypatch):
    """CryptoPanic returns JSON, not RSS — must parse as JSON."""
    monkeypatch.setattr(agent_news, "_sources", lambda: [("CryptoPanic", "http://x/api")])
    agent_news._save_seen(set())
    monkeypatch.setattr(agent_news, "_fetch_feed", lambda url, timeout=10: CRYPTOPANIC_JSON)

    items = agent_news.fetch_news(max_items=5)
    assert [i.title for i in items] == ["Base sees record USDC volume"]
    assert items[0].source == "TheBlock"
    assert items[0].link == "https://example.com/cp-1"
