"""Web content extraction.

HARD RULE (see ARCHITECTURE.md): only the single page the user explicitly saved is ever
fetched/extracted. No link following, no nested crawling — in any backend.
Backends: browser-html (extension sends rendered DOM), server-fetch
(trafilatura fetches the one URL), firecrawl (optional, single page scrape).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Extracted:
    title: str
    text: str
    lang: str | None = None


def _detect_lang(text: str) -> str | None:
    try:
        from langdetect import detect

        return detect(text[:4000])
    except Exception:
        return None


def from_html(html: str, url: str | None = None, title_hint: str = "") -> Extracted | None:
    import trafilatura

    text = trafilatura.extract(
        html, url=url, include_comments=False, include_tables=True,
        favor_precision=True,
    )
    if not text or not text.strip():
        return None
    title = title_hint
    if not title:
        try:
            meta = trafilatura.extract_metadata(html, default_url=url)
            title = (meta.title if meta else "") or ""
        except Exception:
            title = ""
    return Extracted(title=title or (url or "Untitled"), text=text.strip(),
                     lang=_detect_lang(text))


def from_url(url: str) -> Extracted | None:
    """Server-side fetch of exactly one URL (CLI saves). Never follows links.
    Uses the shared polite fetcher (browser headers, curl fallback, per-host
    throttle) instead of trafilatura's — same anti-bot behavior everywhere."""
    from .fetch import fetch_page

    downloaded, _err = fetch_page(url)
    if not downloaded:
        return None
    return from_html(downloaded, url=url)


def from_firecrawl(url: str, api_key: str) -> Extracted | None:
    """Optional Firecrawl backend — scrape endpoint only (single page, no crawl)."""
    import json
    import urllib.request

    req = urllib.request.Request(
        "https://api.firecrawl.dev/v1/scrape",
        data=json.dumps({"url": url, "formats": ["markdown"]}).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read())
    data = payload.get("data") or {}
    text = (data.get("markdown") or "").strip()
    if not text:
        return None
    title = (data.get("metadata") or {}).get("title") or url
    return Extracted(title=title, text=text, lang=_detect_lang(text))
