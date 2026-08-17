"""Content-policy hook.

BasicRules is a deliberately narrow, permissive rule set — legitimate
professional/academic material (law, medicine, engineering, security studies)
must never be blocked. Richer policies can plug in at this same interface.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol


@dataclass
class PolicyDecision:
    action: str  # allow | flag | block
    reason: str = ""


class ContentPolicy(Protocol):
    def check(self, text: str, *, title: str = "") -> PolicyDecision: ...


class BasicRules:
    """Narrow patterns for unambiguous illegal-content markers only."""

    _BLOCK = [
        re.compile(r"(?i)\bchild (?:sexual abuse|porn)\w*"),
        re.compile(r"(?i)\bcsam\b"),
    ]
    _FLAG = [
        re.compile(r"(?i)\bhow to (?:build|make) (?:a )?(?:pipe bomb|ied)\b"),
    ]

    def check(self, text: str, *, title: str = "") -> PolicyDecision:
        haystack = f"{title}\n{text[:20000]}"
        for pattern in self._BLOCK:
            if pattern.search(haystack):
                return PolicyDecision("block", f"matched {pattern.pattern}")
        for pattern in self._FLAG:
            if pattern.search(haystack):
                return PolicyDecision("flag", f"matched {pattern.pattern}")
        return PolicyDecision("allow")


def get_policy(name: str) -> ContentPolicy:
    return BasicRules()
