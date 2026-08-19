"""Routes a bare question to the right engine, so `dc "<question>"` (and
`dc ask`) reach for the right tool automatically instead of the user having
to know the difference between a lookup, a terse answer, a full explanation,
and an exhaustive scan.

Four destinations, matching the CLI's own commands:
- search — locate a known document/note (no LLM, just results)
- ask    — short factual/how-to answer (the previous default behavior)
- query  — an explanation, comparison, or synthesis over the knowledge base
- find   — "every X" / "all my X" — an exhaustive scan (search_codegen.find_all)

The "find" destination is protected by a keyword safety net checked BEFORE
the LLM call: a question that reads as an enumeration request is routed to
find_all() unconditionally, without waiting on (or trusting) a classifier
call. Top-k retrieval structurally cannot answer "list every X" — routing
that question anywhere else silently reintroduces the exact accuracy bug
find_all was built to fix, so this path must never depend on an LLM call
that could be down, slow, or simply wrong.
"""

from __future__ import annotations

import re

ROUTES = ("search", "ask", "query", "find")

# Deliberately biased toward over-matching: a false positive here just means
# an exhaustive scan runs when a short answer would have done (a few extra
# seconds); a false negative means a "list all X" question silently gets a
# top-k answer again — the exact failure this router exists to prevent.
_ENUMERATE_HINTS = re.compile(
    r"\b(list|find|show|give\s+me|get|collect)\s+(me\s+)?(all|every)\b"
    r"|\ball\s+(my|the|of\s+my)\b"
    r"|\bevery\s+(single\s+)?\S+\s+(i|that|which|on|in|across|from)\b"
    r"|\beach\s+(of\s+my|one\s+of)\b"
    r"|\bhow\s+many\b.*\band\s+where\b",
    re.I,
)

ROUTER_SYSTEM = """\
Classify a personal-knowledge-base question into exactly one word:

search — the user wants to LOCATE a known document/note/file, not have it
  explained. "find my notes on X", "where is the router config", "show me
  the PDF about Y", "open my TDL for the kitchen project".
ask — a short factual or how-to question with one clear answer. "how do I
  revert a git commit", "what's the command to list docker containers",
  "when did I save this".
query — the user wants an explanation, comparison, or synthesis drawing on
  several saved items. "explain how X works", "why did I choose A over B",
  "summarize what I know about Y", "compare my notes on X and Z".
find — the user wants EVERY matching item, not the best one. "list all my
  passwords", "every item on my wish lists", "find all mentions of Y across
  my notes", "how many times have I written about X, and where".

Reply with exactly one word: search, ask, query, or find. Nothing else.
"""


def route_question(config, question: str, *, chain=None) -> str:
    """One of ROUTES. Never raises — falls back to "ask" (the previous,
    safe default) on any classification failure."""
    if _ENUMERATE_HINTS.search(question):
        return "find"

    from .llm import get_provider_chain, run_complete

    chain = chain if chain is not None else get_provider_chain(config, task="answer")
    try:
        # NOT 10: reasoning-model providers (e.g. gpt-oss at low reasoning
        # effort) spend part of the budget on hidden reasoning tokens before
        # the actual word — too small a cap returns empty text and silently
        # falls back to "ask" every time (observed directly against a real
        # local model while building this).
        text, _provider = run_complete(chain, ROUTER_SYSTEM, question, max_tokens=60)
    except Exception:  # noqa: BLE001 — routing must never block answering
        return "ask"
    label = re.sub(r"[^a-z]", "", text.strip().lower())
    return label if label in ROUTES else "ask"
