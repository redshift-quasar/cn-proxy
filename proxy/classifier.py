"""Content-based sensitivity detection (a small DLP engine).

scan()    -> list of findings (label, severity, span)
inspect() -> verdict: ALLOW / BLOCK / REDACT for a piece of text

Matched values are never returned in logs/messages, only the labels.
"""
import re
from dataclasses import dataclass

HIGH = "high"
LOW = "low"

LOW_THRESHOLD = 3      # this many LOW findings together count as sensitive


@dataclass(frozen=True)
class Finding:
    label: str
    severity: str
    start: int
    end: int


def _luhn_ok(number: str) -> bool:
    digits = [int(c) for c in number if c.isdigit()]

    if not 13 <= len(digits) <= 19:
        return False

    total = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit

    return total % 10 == 0


# Verhoeff checksum, used by Aadhaar numbers.
_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6], [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2], [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]


def _verhoeff_ok(number: str) -> bool:
    digits = [int(c) for c in number if c.isdigit()]

    if len(digits) != 12:
        return False

    c = 0
    for i, digit in enumerate(reversed(digits)):
        c = _D[c][_P[i % 8][digit]]

    return c == 0


# (label, severity, compiled regex, optional validator)
PATTERNS = [
    ("credit_card", HIGH,
     re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)"), _luhn_ok),

    ("us_ssn", HIGH,
     re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"), None),

    ("aadhaar", HIGH,
     re.compile(
         r"(?<!\d)(?<!\d[ -])[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}(?![ -]?\d)"
     ), _verhoeff_ok),

    ("private_key", HIGH,
     re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"), None),

    ("aws_access_key", HIGH,
     re.compile(r"\bAKIA[0-9A-Z]{16}\b"), None),

    ("credential", HIGH,
     re.compile(
         r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|token)"
         r"\s*[=:]\s*\S+"), None),

    ("email", LOW,
     re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), None),

    ("phone", LOW,
     re.compile(r"(?<!\d)(?:\+?91[- ]?)?[6-9]\d{9}(?!\d)"), None),
]


def scan(text: str) -> list:
    findings = []

    for label, severity, regex, validator in PATTERNS:
        for match in regex.finditer(text):
            if validator and not validator(match.group()):
                continue
            findings.append(
                Finding(label, severity, match.start(), match.end())
            )

    return findings


def is_sensitive(findings) -> bool:
    if any(f.severity == HIGH for f in findings):
        return True
    return sum(1 for f in findings if f.severity == LOW) >= LOW_THRESHOLD


def labels(findings) -> list:
    return sorted({f.label for f in findings})


def redact(text: str, findings) -> str:
    """Replace every finding span with [REDACTED] (overlaps are merged)."""

    spans = sorted((f.start, f.end) for f in findings)
    merged = []

    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    for start, end in reversed(merged):
        text = text[:start] + "[REDACTED]" + text[end:]

    return text


@dataclass
class Verdict:
    action: str          # "ALLOW", "BLOCK" or "REDACT"
    labels: list
    text: str            # text to send on (redacted if REDACT)


def inspect(text: str, on_sensitive: str = "block") -> Verdict:
    findings = scan(text)

    if not is_sensitive(findings):
        return Verdict("ALLOW", [], text)

    if on_sensitive == "redact":
        return Verdict("REDACT", labels(findings), redact(text, findings))

    return Verdict("BLOCK", labels(findings), "")
