"""Tavily search source. Optional paid extra for the free-mode pool.

Joins the aggregation pool only when TAVILY_API_KEY is set (the caller checks;
see research._free_sources). Plain requests against the REST API - no
tavily-python SDK, so the dependency list stays unchanged and the test suite
never needs the vendor package installed.

Credit cost (as of 2026-06, https://tavily.com/pricing): basic search = 1
credit, advanced = 2. raw_content is intentionally NOT requested here - full
page content is pulled separately (and only for the best hits) via web_reader,
which is far cheaper than asking for raw_content on every result.
"""

from __future__ import annotations

import os
import sys

from .. import cache, http
from ..result import Result
from ..source_config import load

API_URL = "https://api.tavily.com/search"
MAX_API_RESULTS = 20  # Tavily hard cap on max_results


class TavilySource:
    name = "tavily"

    def __init__(self, retries: int = 1) -> None:
        key = os.environ.get("TAVILY_API_KEY", "").strip()
        if not key:
            raise RuntimeError("TavilySource requires TAVILY_API_KEY")
        self._key = key
        self._session = http.get_session(retries=retries, backoff=1.0)
        self._ttl = load().cache_ttl_hours
        self._depth = os.environ.get("TAVILY_SEARCH_DEPTH", "basic").strip() or "basic"
        try:
            self._max = int(os.environ.get("TAVILY_MAX_RESULTS", "10").strip() or "10")
        except ValueError:
            self._max = 10

    def search(self, query: str, n: int = 10) -> list[Result]:
        depth = self._depth
        max_results = max(1, min(n, self._max, MAX_API_RESULTS))

        # Cache key folds in the params that change results, so a "basic" run and
        # an "advanced" run of the same query do not poison each other. cache.py
        # keys on sha1(normalize(query)) and ignores these params on its own.
        cache_query = f"{query.lower().strip()}::depth={depth}::n={max_results}"
        cached = cache.get(self.name, cache_query, ttl_hours=self._ttl)
        if cached is not None:
            return [Result(**r) for r in cached]

        resp = self._session.post(
            API_URL,
            json={
                "query": query,
                "search_depth": depth,
                "max_results": max_results,
                "include_raw_content": False,
            },
            headers={"Authorization": f"Bearer {self._key}"},
            timeout=http.DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        results = [
            Result(
                source=self.name,
                title=item.get("title") or "",
                url=item.get("url") or "",
                snippet=item.get("content") or None,
                extra={"score": item.get("score")},
            )
            for item in data.get("results", [])
            if item.get("url")
        ]
        if results:
            cache.put(self.name, cache_query, results)
        _log_credits("tavily-search", data)
        return results


def _log_credits(command: str, data: dict) -> None:
    """Record Tavily credit spend via the shared usage log. Guarded behind a vault
    path because importing lib.usage transitively imports lib.config, which
    requires OBSIDIAN_VAULT_PATH at import time. In the bare free-import path
    (CI/ephemeral, no vault) there is nothing meaningful to track, so skip."""
    credits = (data.get("usage") or {}).get("credits")
    if not credits or not os.environ.get("OBSIDIAN_VAULT_PATH", "").strip():
        return
    try:
        from .. import usage

        usage.log_tavily(command, int(credits))
    except Exception as e:  # logging is never fatal to a search
        print(f"[tavily] credit logging skipped: {e}", file=sys.stderr)


__all__ = ["TavilySource"]
