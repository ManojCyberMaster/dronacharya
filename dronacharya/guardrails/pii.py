"""Basic PII/secret filter applied at ingest.

Local single-user builds permit general personal information; what we stop from
entering the knowledge base: payment card numbers, government ID numbers, and
credentials/secrets. Modes: redact (default), block, off.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_CARD_CANDIDATE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_AADHAAR = re.compile(r"(?<!\d[ -])(?<![\d-])\d{4}[ -]\d{4}[ -]\d{4}(?![ -]?\d)")
_PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
_SECRETS = [
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----")),
    ("api_key", re.compile(r"\b(?:sk|rk)-[A-Za-z0-9_-]{20,}\b")),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("generic_secret", re.compile(r"(?i)\b(?:api[_-]?key|secret|password|token)\s*[:=]\s*['\"]?[A-Za-z0-9_\-./+]{16,}")),
]


@dataclass
class PiiFinding:
    kind: str
    match: str


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def scan_pii(text: str) -> list[PiiFinding]:
    findings: list[PiiFinding] = []
    for m in _CARD_CANDIDATE.finditer(text):
        digits = re.sub(r"[ -]", "", m.group())
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            findings.append(PiiFinding("payment_card", m.group()))
    for kind, pattern in (("ssn", _SSN), ("aadhaar", _AADHAAR), ("pan", _PAN)):
        for m in pattern.finditer(text):
            findings.append(PiiFinding(kind, m.group()))
    for kind, pattern in _SECRETS:
        for m in pattern.finditer(text):
            findings.append(PiiFinding(kind, m.group()))
    return findings


def apply_pii_policy(text: str, mode: str) -> tuple[str, list[PiiFinding], bool]:
    """Returns (possibly-redacted text, findings, blocked)."""
    if mode == "off":
        return text, [], False
    findings = scan_pii(text)
    if not findings:
        return text, [], False
    if mode == "block":
        return text, findings, True
    redacted = text
    for f in findings:
        redacted = redacted.replace(f.match, f"[REDACTED:{f.kind}]")
    return redacted, findings, False
