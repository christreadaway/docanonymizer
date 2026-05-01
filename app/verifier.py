"""Post-scrub verification (PRD 5.10).

Re-extracts text from the output file and scans it twice:
  1. for any value that appears in the replacement map (any original PII string)
  2. for a small set of regex patterns that catch common PII shapes

If anything matches, verification FAILS, the output is not released, and
the failure is logged with match types only - never the matched values.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .extractors import extract
from .logging_setup import get_logger

log = get_logger("verify")


# Regex patterns from PRD 5.10. Conservative; the goal is "catch obvious
# residue" not "perfectly classify". A failure here is a hard stop.
PATTERNS: dict[str, re.Pattern] = {
    "EMAIL":       re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"),
    "PHONE":       re.compile(r"(?:\+?\d{1,3}[\s\-]?)?(?:\(?\d{3}\)?[\s\-]?)\d{3}[\s\-]?\d{4}"),
    "SSN":         re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "IP":          re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
    "CREDIT_CARD": re.compile(r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b"),
}


@dataclass
class VerifyResult:
    passed: bool
    map_match_types: list[str] = field(default_factory=list)
    regex_match_types: list[str] = field(default_factory=list)
    total_matches: int = 0


def _values_from_map(replacement_map: dict[str, str]) -> Iterable[tuple[str, str]]:
    """Yield (original_value, tag) so we can count types without leaking values."""
    for original, placeholder in replacement_map.items():
        # placeholder format: [TAG_XXXX]
        body = placeholder.strip("[]")
        tag = body.split("_", 1)[0] if "_" in body else "UNKNOWN"
        yield original, tag


def verify_output(output_path: Path, replacement_map: dict[str, str]) -> VerifyResult:
    """Run the verification pass against the freshly scrubbed file."""
    extracted = extract(output_path)
    text = extracted.text

    map_match_types: set[str] = set()
    map_total = 0
    for original, tag in _values_from_map(replacement_map):
        if not original:
            continue
        if original in text:
            map_match_types.add(tag)
            map_total += text.count(original)

    regex_match_types: set[str] = set()
    regex_total = 0
    for label, pat in PATTERNS.items():
        # Skip matches whose entire string IS a placeholder we just installed.
        # Placeholders look like [TAG_XXXX]; the regex IPs and SSNs would never
        # land inside one because of the brackets, but emails/phones could
        # superficially match digits inside placeholders. Strip placeholders
        # before regex scanning.
        scrubbed_for_regex = re.sub(r"\[[A-Z]+_[0-9A-F]{4}\]", "", text)
        for _ in pat.finditer(scrubbed_for_regex):
            regex_match_types.add(label)
            regex_total += 1
            break  # one hit is enough to flag the type

    total = map_total + regex_total
    passed = total == 0

    log.info(
        "verify result: passed=%s map_types=%s regex_types=%s total_matches=%d",
        passed,
        ",".join(sorted(map_match_types)) or "-",
        ",".join(sorted(regex_match_types)) or "-",
        total,
    )

    return VerifyResult(
        passed=passed,
        map_match_types=sorted(map_match_types),
        regex_match_types=sorted(regex_match_types),
        total_matches=total,
    )
