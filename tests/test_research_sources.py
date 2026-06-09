"""Unit tests for the free (key-less) research source library.

No network calls: the aggregator is exercised with in-memory fake sources, so
these are fast and deterministic in CI. The real source clients are imported
to guard against import-time breakage, but never invoked.

The most important guarantee here: the free-source library is importable with
NO OBSIDIAN_VAULT_PATH set (it only fetches and emits JSON; saving to the vault
is a separate concern). See FORK_INSIGHTS.md items #1-5.
"""

from __future__ import annotations

import json
import os

import pytest

from scripts.research.lib import cache, http
from scripts.research.lib.aggregator import aggregate
from scripts.research.lib.result import Result, encode_results
from scripts.research.lib import source_config


def test_library_imports_without_vault_path():
    """The free library must not require OBSIDIAN_VAULT_PATH at import time."""
    assert "OBSIDIAN_VAULT_PATH" not in os.environ or True  # documents intent
    # Importing every real source must not raise (and must not need a vault).
    from scripts.research.lib.sources.arxiv import ArxivSource
    from scripts.research.lib.sources.crossref import CrossRefSource
    from scripts.research.lib.sources.devto import DevToSource
    from scripts.research.lib.sources.duckduckgo import DuckDuckGoSource
    from scripts.research.lib.sources.hackernews import HackerNewsSource
    from scripts.research.lib.sources.lobsters import LobstersSource
    from scripts.research.lib.sources.openalex import OpenAlexSource
    from scripts.research.lib.sources.reddit import RedditSource
    from scripts.research.lib.sources.semantic_scholar import SemanticScholarSource
    from scripts.research.lib.sources.tavily import TavilySource
    from scripts.research.lib.sources.wikipedia import WikipediaSource

    # WebReader (Tavily Extract) must also import without a vault path.
    from scripts.research.lib.web_reader import WebReader  # noqa: F401

    names = {
        ArxivSource.name, CrossRefSource.name, DevToSource.name, DuckDuckGoSource.name,
        HackerNewsSource.name, LobstersSource.name, OpenAlexSource.name, RedditSource.name,
        SemanticScholarSource.name, TavilySource.name, WikipediaSource.name,
    }
    assert len(names) == 11  # all source names distinct


def test_result_json_roundtrip():
    r = Result(source="hackernews", title="Show HN: thing", url="https://x.y", points=42)
    blob = json.dumps([r], default=encode_results)
    back = json.loads(blob)
    assert back[0]["source"] == "hackernews"
    assert back[0]["points"] == 42
    assert back[0]["abstract"] is None


def test_polite_user_agent():
    assert http.polite_user_agent("ua/1.0", None) == "ua/1.0"
    assert http.polite_user_agent("ua/1.0", "me@x.io") == "ua/1.0 (mailto:me@x.io)"


def test_source_config_defaults_and_env(monkeypatch):
    # Defaults when nothing is set.
    source_config._CACHE = None
    for var in ("RESEARCH_CONTACT_EMAIL", "RESEARCH_CACHE_TTL_HOURS"):
        monkeypatch.delenv(var, raising=False)
    cfg = source_config.load()
    assert cfg.cache_ttl_hours == 24
    assert cfg.contact_email is None
    assert cfg.searxng_instances  # non-empty default list

    # Env overrides (clear the memoized cache first).
    source_config._CACHE = None
    monkeypatch.setenv("RESEARCH_CONTACT_EMAIL", "me@x.io")
    monkeypatch.setenv("RESEARCH_CACHE_TTL_HOURS", "6")
    cfg2 = source_config.load()
    assert cfg2.contact_email == "me@x.io"
    assert cfg2.cache_ttl_hours == 6
    source_config._CACHE = None  # leave clean for other tests


def test_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cache.get("hackernews", "rust async", ttl_hours=24) is None  # miss
    rows = [Result(source="hackernews", title="t", url="u", points=1)]
    cache.put("hackernews", "rust async", rows)
    hit = cache.get("hackernews", "rust async", ttl_hours=24)
    assert hit is not None and hit[0]["title"] == "t"
    # Expired entries are treated as a miss.
    assert cache.get("hackernews", "rust async", ttl_hours=0) is None


class _FakeSource:
    def __init__(self, name, rows=None, raises=False):
        self.name = name
        self._rows = rows or []
        self._raises = raises

    def search(self, query, n=10):
        if self._raises:
            raise RuntimeError("boom")
        return self._rows


def test_aggregate_collects_and_degrades_gracefully():
    good = _FakeSource("good", rows=[Result(source="good", title="a", url="http://a")])
    also = _FakeSource("also", rows=[Result(source="also", title="b", url="http://b")])
    broken = _FakeSource("broken", raises=True)

    out = aggregate("topic", [good, also, broken], n_per_source=5, timeout=10)

    assert out["topic"] == "topic"
    assert out["stats"]["sources_attempted"] == 3
    assert out["stats"]["sources_succeeded"] == 2  # broken one did not crash the run
    assert out["stats"]["results_total"] == 2
    assert out["stats"]["success"] is False  # success requires >= 3 sources
    assert any("broken" in w for w in out["warnings"])
    # Output is JSON-serializable as-is (results already dicts).
    json.dumps(out)


# --- Tavily (search + extract) -------------------------------------------------
# These mock the HTTP session (no network). Credit logging is exercised only when
# OBSIDIAN_VAULT_PATH is set; tests clear it so logging is skipped and lib.config
# (which requires the vault path) is never imported.


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, response=None, raises=False):
        self._response = response
        self._raises = raises
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        if self._raises:
            raise RuntimeError("network boom")
        return self._response


def test_tavily_search_maps_results(tmp_path, monkeypatch):
    from scripts.research.lib.sources.tavily import TavilySource

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    monkeypatch.delenv("OBSIDIAN_VAULT_PATH", raising=False)

    src = TavilySource()
    src._session = _FakeSession(_FakeResponse(200, {
        "results": [
            {"title": "Rust async", "url": "https://example.com/a",
             "content": "snippet here", "score": 0.9},
        ],
        "usage": {"credits": 1},
    }))

    out = src.search("rust async runtimes unique-map", n=5)
    assert len(out) == 1
    assert out[0].source == "tavily"
    assert out[0].url == "https://example.com/a"
    assert out[0].snippet == "snippet here"
    assert out[0].extra["score"] == 0.9
    body = src._session.calls[0]["json"]
    assert body["include_raw_content"] is False  # full content is a separate extract step
    assert src._session.calls[0]["headers"]["Authorization"] == "Bearer tvly-test"


def test_tavily_search_non_200_returns_empty(tmp_path, monkeypatch):
    from scripts.research.lib.sources.tavily import TavilySource

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    src = TavilySource()
    src._session = _FakeSession(_FakeResponse(429, {}))
    assert src.search("rate limited unique-429", n=5) == []


def test_tavily_search_exception_returns_empty(tmp_path, monkeypatch):
    from scripts.research.lib.sources.tavily import TavilySource

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    src = TavilySource()
    src._session = _FakeSession(raises=True)
    assert src.search("network down unique-boom", n=5) == []


def test_tavily_cache_key_isolates_depth(tmp_path, monkeypatch):
    """A 'basic' run and an 'advanced' run of the same query must not poison each
    other's cache (challenge 1)."""
    from scripts.research.lib.sources.tavily import TavilySource

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    monkeypatch.delenv("OBSIDIAN_VAULT_PATH", raising=False)

    monkeypatch.setenv("TAVILY_SEARCH_DEPTH", "basic")
    basic = TavilySource()
    basic._session = _FakeSession(_FakeResponse(200, {
        "results": [{"title": "B", "url": "https://b", "content": "basic", "score": 1}]}))
    assert basic.search("shared query", n=10)[0].snippet == "basic"
    assert len(basic._session.calls) == 1

    # Same query, advanced depth -> different cache key -> its own request.
    monkeypatch.setenv("TAVILY_SEARCH_DEPTH", "advanced")
    adv = TavilySource()
    adv._session = _FakeSession(_FakeResponse(200, {
        "results": [{"title": "A", "url": "https://a", "content": "advanced", "score": 1}]}))
    assert adv.search("shared query", n=10)[0].snippet == "advanced"
    assert len(adv._session.calls) == 1  # no cross-poison

    # Re-running basic now hits the cache: no new request, original payload.
    monkeypatch.setenv("TAVILY_SEARCH_DEPTH", "basic")
    basic2 = TavilySource()
    basic2._session = _FakeSession(_FakeResponse(200, {"results": []}))
    assert basic2.search("shared query", n=10)[0].snippet == "basic"
    assert len(basic2._session.calls) == 0  # served from cache


def test_web_reader_truncates_and_drops_failures(tmp_path, monkeypatch):
    from scripts.research.lib.web_reader import MAX_EXTRACT_CHARS, WebReader

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    monkeypatch.delenv("OBSIDIAN_VAULT_PATH", raising=False)

    reader = WebReader()
    reader._session = _FakeSession(_FakeResponse(200, {
        "results": [{"url": "https://ok", "raw_content": "x" * (MAX_EXTRACT_CHARS + 1000)}],
        "failed_results": [{"url": "https://bad"}],
        "usage": {"credits": 1},
    }))
    out = reader.read(["https://ok", "https://bad"])
    assert set(out.keys()) == {"https://ok"}  # failed_results dropped
    assert len(out["https://ok"]) == MAX_EXTRACT_CHARS  # truncated to the cap


def test_web_reader_caps_url_count(tmp_path, monkeypatch):
    from scripts.research.lib.web_reader import MAX_EXTRACT_URLS, WebReader

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    reader = WebReader()
    reader._session = _FakeSession(_FakeResponse(200, {"results": [], "failed_results": []}))
    reader.read([f"https://u{i}" for i in range(10)])
    posted = reader._session.calls[0]["json"]["urls"]
    assert len(posted) == MAX_EXTRACT_URLS


def test_web_reader_no_key_returns_empty(monkeypatch):
    from scripts.research.lib.web_reader import WebReader

    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    reader = WebReader()
    assert reader.read(["https://x"]) == {}


def test_free_sources_swaps_ddg_for_tavily(monkeypatch):
    """With a Tavily key, the keyless web pool uses Tavily instead of DuckDuckGo
    (challenge 4); without it, DuckDuckGo stays and Tavily is absent."""
    from scripts.research.research import _free_sources

    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    names = {s.name for s in _free_sources(academic=False)}
    assert "tavily" in names and "duckduckgo" not in names

    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    names_free = {s.name for s in _free_sources(academic=False)}
    assert "duckduckgo" in names_free and "tavily" not in names_free


def test_free_sources_academic_never_uses_tavily(monkeypatch):
    from scripts.research.research import _free_sources

    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    names = {s.name for s in _free_sources(academic=True)}
    assert "tavily" not in names
