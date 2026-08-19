#!/usr/bin/env python3
"""One-off credential audit — answers "list all the passwords in my notes
and where they're used" directly, by scanning EVERY stored knowledge unit
for credential-shaped lines (keyword immediately followed by a value).

This deliberately bypasses RAG/embeddings/rerank entirely: "list all X"
is an exhaustive-enumeration query, not a "find the best-matching passage"
query — semantic top-k retrieval structurally can't answer it (a generic
doc titled "passwd(1) man page" will outscore a bare `pwd: abc123` line
in a TDL task every time). A full linear scan is the correct tool here;
the KB is small enough that this runs in well under a second.

Local, read-only, no network calls, opens the SAME database `dc` uses
(via dronacharya.config.load_config / dronacharya.storage.get_repo).

Usage:
    .venv/bin/python scripts/find_credentials.py [--out FILE]

The output contains PLAINTEXT credentials pulled straight from your notes.
Treat it accordingly: don't commit it, don't paste it anywhere, delete the
--out file once you've reviewed it.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# keyword immediately followed by ":" or "=" and a value — requires the
# key:value SHAPE, not just the word appearing in prose (excludes "the
# login process requires a password" while catching "pwd: abc123" or
# "Password=hunter2").
PATTERN = re.compile(
    r"""(?ix)
    \b(pass(?:word|phrase)?|pwd|passwd|secret|
       api[_ -]?key|access[_ -]?key|private[_ -]?key|
       token|credential|pin|login|auth)\b
    [ \t]*[:=][ \t]*
    (?P<value>\S+)
    """
)


def _line_for(text: str, pos: int) -> str:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return text[start: end if end != -1 else len(text)]


def scan() -> list[dict]:
    from dronacharya.config import load_config
    from dronacharya.storage import get_repo

    config = load_config()
    repo = get_repo(config)
    hits: list[dict] = []
    try:
        for doc, units in repo.iter_documents_with_units():
            for u in units:
                text = u.text or ""
                seen_lines = set()
                for m in PATTERN.finditer(text):
                    line = _line_for(text, m.start()).strip()
                    if line in seen_lines:
                        continue
                    seen_lines.add(line)
                    hits.append({
                        "document": doc.title or "(untitled)",
                        "where": u.heading_path or "",
                        "source": doc.url or doc.file_path or "",
                        "line": line,
                    })
    finally:
        repo.close()
    return hits


def render(hits: list[dict]) -> str:
    if not hits:
        return "No credential-shaped lines found (keyword immediately " \
               "followed by ':' or '=' and a value)."
    out = [f"Found {len(hits)} credential-shaped line(s):\n"]
    for h in hits:
        where = f" — {h['where']}" if h["where"] else ""
        src = f"  [{h['source']}]" if h["source"] else ""
        out.append(f"* {h['document']}{where}{src}\n    {h['line']}")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="also write the report to this file "
                                   "(contains plaintext secrets)")
    args = ap.parse_args()
    report = render(scan())
    print(report)
    if args.out:
        Path(args.out).write_text(report)
        print(f"\n(also written to {args.out} — delete it once reviewed)",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
