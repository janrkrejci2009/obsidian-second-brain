"""Full-page reader via the Tavily Extract API. Optional deep-research upgrade.

/research-deep's synthesis normally sees only citation snippets. When
TAVILY_API_KEY is set, the top sources are fetched as full page text and
injected into the synthesis prompt, so the synthesizer reads what the pages
actually say instead of guessing from snippets. Pattern from fork-insights
round 2 (the web-reader fork).

Contract: never raises. Any failure returns what was extracted so far (or {});
deep research must proceed snippet-only when extraction is unavailable. Paid:
Tavily Extract bills credits per URL batch - hence the hard URL cap. Credit
cost (as of 2026-06, https://tavily.com/pricing): 1 credit per 5 successful
URLs (basic extract); failed extractions are not charged.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from . import http

API_URL = "https://api.tavily.com/extract"
MAX_EXTRACT_URLS = 3      # hard cap: extraction is paid, synthesis has finite context
MAX_EXTRACT_CHARS = 8000  # per page; enough substance, bounded prompt growth


def available() -> bool:
    return bool(os.environ.get("TAVILY_API_KEY", "").strip())


def read(urls: list[str]) -> dict[str, str]:
    """Extract full text for up to MAX_EXTRACT_URLS unique URLs.
    Returns {url: text}; empty dict when unavailable or on total failure."""
    if not available() or not urls:
        return {}
    unique: list[str] = []
    for u in urls:
        if u and u.startswith("http") and u not in unique:
            unique.append(u)
        if len(unique) >= MAX_EXTRACT_URLS:
            break
    if not unique:
        return {}

    session = http.get_session(retries=1, backoff=1.0)
    try:
        resp = session.post(
            API_URL,
            json={"urls": unique},
            headers={"Authorization": f"Bearer {os.environ['TAVILY_API_KEY'].strip()}"},
            timeout=http.DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
    except Exception as e:  # noqa: BLE001 - extraction is additive, never fatal
        print(f"[web_reader] extract failed ({e}); synthesis proceeds snippet-only", file=sys.stderr)
        return {}

    out: dict[str, str] = {}
    for item in data.get("results", []):
        url = item.get("url") or ""
        text = (item.get("raw_content") or "").strip()
        if url and text:
            out[url] = text[:MAX_EXTRACT_CHARS]

    for fail in data.get("failed_results", []):
        failed_url = fail.get("url") if isinstance(fail, dict) else fail
        print(f"[web_reader] extract failed for {failed_url}", file=sys.stderr)

    _log_credits("tavily-extract", data, len(out))
    return out


def _log_credits(command: str, data: dict, urls_extracted: int) -> None:
    """Record real Tavily credit spend (not a flat $0) via the shared usage log,
    guarded behind a vault path since importing lib.usage pulls in lib.config
    (which requires OBSIDIAN_VAULT_PATH at import time)."""
    if not os.environ.get("OBSIDIAN_VAULT_PATH", "").strip():
        return
    try:
        from . import usage

        credits = (data.get("usage") or {}).get("credits")
        if credits:
            usage.log_tavily(command, int(credits))
        else:
            usage.log_call(command, "tavily", 0, 0, 0.0, extra={"urls_extracted": urls_extracted})
    except Exception as e:  # logging is never fatal
        print(f"[web_reader] credit logging skipped: {e}", file=sys.stderr)


__all__ = ["available", "read", "MAX_EXTRACT_URLS", "MAX_EXTRACT_CHARS"]
