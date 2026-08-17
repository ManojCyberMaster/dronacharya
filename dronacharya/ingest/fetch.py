"""Polite single-page fetching, shared by every fetch path (CLI saves, seed
builds, bookmark imports).

Real documentation sites gate scripted requests three different ways, so one
identity is never enough:
- fingerprint checks reject a browser User-Agent that arrives without the
  rest of a real browser's headers (freedesktop.org answers 418) — so the
  primary profile sends a full, coherent header set;
- anti-bot challenges like Anubis (Arch Wiki, Fedora docs, Alpine wiki) pass
  an honest `curl` identity but block half-faked browsers — so the first
  retry switches to one;
- per-host rate limits reset connections on rapid-fire requests (gnu.org,
  GitHub 429) — so requests to the same host are spaced out and every retry
  backs off.

HARD RULE (see ARCHITECTURE.md): only the exact URL given is fetched — never links.
"""

from __future__ import annotations

import gzip
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

MAX_PAGE_BYTES = 5 * 1024 * 1024
HOST_MIN_INTERVAL = 1.0  # seconds between requests to the same host

_BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36 Edg/139.0.0.0"),
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}
_CURL_HEADERS = {"User-Agent": "curl/8.9.1", "Accept": "*/*"}
# Anti-bot interstitials arrive as HTTP 200 — detect by content and fall
# through to the next identity (Anubis intentionally passes plain curl).
_CHALLENGE_MARKERS = (
    "making sure you're not a bot",          # Anubis
    "just a moment",                          # Cloudflare
    "verifying you are human",
    "checking if the site connection is secure",
    "enable javascript and cookies to continue",
)
# 4xx that anti-bot layers use for "try harder", plus transient 5xx
_RETRYABLE_HTTP = {403, 406, 408, 418, 425, 429, 500, 502, 503, 504}

_last_fetch: dict[str, float] = {}


def _throttle(host: str) -> None:
    last = _last_fetch.get(host)
    if last is not None:
        wait = HOST_MIN_INTERVAL - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
    _last_fetch[host] = time.monotonic()


def _attempt(url: str, headers: dict[str, str], timeout: int) -> str:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(MAX_PAGE_BYTES)
        if resp.headers.get("Content-Encoding", "").lower() == "gzip":
            raw = gzip.decompress(raw)
        charset = resp.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")


def _attempt_system_curl(url: str, timeout: int) -> str:
    """Real curl as the last identity: some anti-bot layers (Anubis) fingerprint
    the TLS/HTTP2 client itself, so Python pretending to be curl still gets
    challenged while genuine curl passes. curl ships on Linux/macOS/Win10+."""
    import shutil
    import subprocess

    curl = shutil.which("curl")
    if curl is None:
        raise RuntimeError("curl not installed")
    proc = subprocess.run(
        [curl, "-sSL", "--compressed", "--max-time", str(timeout),
         "--max-filesize", str(MAX_PAGE_BYTES), "-A", "curl/8.9.1", "--", url],
        capture_output=True, timeout=timeout + 10,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode(errors="replace").strip().splitlines()
        raise RuntimeError(detail[-1][:120] if detail else f"curl exit {proc.returncode}")
    return proc.stdout[:MAX_PAGE_BYTES].decode("utf-8", errors="replace")


def fetch_page(url: str, timeout: int = 30) -> tuple[str | None, str | None]:
    """Fetch exactly one page. Returns (html, None) or (None, error_reason)."""
    if not url.startswith(("http://", "https://")):
        return None, "unsupported URL scheme"
    host = urlparse(url).netloc
    error: str = "unreachable"
    attempts = (
        lambda: _attempt(url, _BROWSER_HEADERS, timeout),
        lambda: _attempt(url, _CURL_HEADERS, timeout),
        lambda: _attempt_system_curl(url, timeout),
    )
    for i, attempt in enumerate(attempts):
        if i:
            time.sleep(2 * i)  # backoff: 2s, then 4s
        _throttle(host)
        try:
            html = attempt()
            import html as html_mod
            # entity/typography-agnostic: Anubis writes "you&#39;re not a bot"
            head = html_mod.unescape(html[:4000]).lower().replace("’", "'")
            if any(m in head for m in _CHALLENGE_MARKERS):
                error = "bot challenge page"
                continue  # retry with the next identity
            return html, None
        except urllib.error.HTTPError as e:
            error = f"HTTP {e.code}"
            if e.code not in _RETRYABLE_HTTP:
                return None, error
        except urllib.error.URLError as e:
            error = f"unreachable ({getattr(e, 'reason', e)})"
        except TimeoutError:
            error = "timed out"
        except Exception as e:  # noqa: BLE001 — bad certs, decode bombs, missing curl…
            error = f"error ({type(e).__name__}: {e})"
    return None, error
