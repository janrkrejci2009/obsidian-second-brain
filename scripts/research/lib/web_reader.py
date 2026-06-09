"""Tavily Extract - pull full page content from URLs.

Isolated as a reusable utility (not buried in research_deep) so a future
`/read [url]` command, or any other caller, can reuse high-quality extraction.
Reads its key from the environment so it stays importable without a vault path,
like the free source clients.

Credit cost (as of 2026-06, https://tavily.com/pricing): 1 credit per 5
successful URLs (basic extract); failed extractions are not charged. We cap the
URL count and truncate each page so a deep-research synthesis prompt cannot blow
past the model's context window or trigger lost-in-the-middle.
"""

from __future__ import annotations

import os
import sys

from . import http

ENDPOINT = "https://api.tavily.com/extract"
MAX_EXTRACT_URLS = 3  # cap URLs per call (credits + context budget)
MAX_EXTRACT_CHARS = 8000  # truncate each page so synthesis context stays bounded


class WebReader:
    def __init__(self, retries: int = 1) -> None:
        self._session = http.get_session(retries=retries, backoff=1.0)

    def read(self, urls: list[str]) -> dict[str, str]:
        """Return {url: truncated_raw_content} for the URLs that extracted
        successfully. Never raises: failures (HTTP error, exception, or per-URL
        failed_results) are logged to stderr and simply omitted from the map."""
        urls = [u for u in urls if u][:MAX_EXTRACT_URLS]
        if not urls:
            return {}

        api_key = os.environ.get("TAVILY_API_KEY", "").strip()
        if not api_key:
            return {}

        try:
            r = self._session.post(
                ENDPOINT,
                json={"urls": urls},
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=http.DEFAULT_TIMEOUT,
            )
            if r.status_code != 200:
                print(f"[web_reader] Tavily extract HTTP {r.status_code}", file=sys.stderr)
                return {}
            data = r.json()
        except Exception as e:
            print(f"[web_reader] extract failed: {e}", file=sys.stderr)
            return {}

        out: dict[str, str] = {}
        for item in data.get("results", []):
            url = item.get("url")
            content = item.get("raw_content") or ""
            if url and content:
                out[url] = content[:MAX_EXTRACT_CHARS]

        for fail in data.get("failed_results", []):
            failed_url = fail.get("url") if isinstance(fail, dict) else fail
            print(f"[web_reader] extract failed for {failed_url}", file=sys.stderr)

        _log_credits("tavily-extract", data)
        return out


def _log_credits(command: str, data: dict) -> None:
    """Mirror tavily.py: log credit spend only when a vault is configured, since
    importing lib.usage pulls in lib.config (which requires OBSIDIAN_VAULT_PATH)."""
    credits = (data.get("usage") or {}).get("credits")
    if not credits or not os.environ.get("OBSIDIAN_VAULT_PATH", "").strip():
        return
    try:
        from . import usage

        usage.log_tavily(command, int(credits))
    except Exception as e:  # logging is never fatal
        print(f"[web_reader] credit logging skipped: {e}", file=sys.stderr)


__all__ = ["WebReader", "MAX_EXTRACT_URLS", "MAX_EXTRACT_CHARS"]
