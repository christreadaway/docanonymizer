"""Entity registry + replacement map.

Implements PRD 5.1-5.3:
  - 4-char uppercase hex IDs, range 0000-FFFF, unique across all tag types
  - Same hex suffix shared across tag types for the same real-world entity
  - Replacement order: sort by string length descending before applying

Public surface:
    EntityRegistry()         - in-memory accumulator
    .add(text, tag, linked_to=None)  -> placeholder string
    .as_replacement_map()    -> dict[original -> placeholder]
    .as_serializable()       -> dict for the key file payload
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Optional

from .logging_setup import get_logger

log = get_logger("mapper")


# 17 PII tags from PRD 4.2, in display order (Standard tier first, Sensitive last).
TAG_ORDER = [
    "PERSON", "EMAIL", "PHONE", "ADDRESS", "ID", "ORG",
    "FINANCIAL", "DOB", "SID", "IP", "USERNAME",
    "GRADE", "MEDICAL", "IMMIGRATION", "DEMO", "RELIGION", "GENDER",
]
VALID_TAGS = set(TAG_ORDER)


def _new_hex(used: set[str]) -> str:
    """Generate a 4-char uppercase hex id not in `used`. 65,536 slots."""
    if len(used) >= 0xFFFF + 1:
        raise RuntimeError("hex id space exhausted (>65535 entities)")
    while True:
        candidate = f"{secrets.randbelow(0x10000):04X}"
        if candidate not in used:
            return candidate


@dataclass
class EntityRegistry:
    # hex_id -> {tag: original_text}
    entities: dict[str, dict[str, str]] = field(default_factory=dict)
    # original_text -> placeholder (what the scrubber applies)
    replacements: dict[str, str] = field(default_factory=dict)
    # original_text -> hex_id (so we never re-issue ids for repeats)
    text_to_hex: dict[str, str] = field(default_factory=dict)

    def _used_ids(self) -> set[str]:
        return set(self.entities.keys())

    def add(self, text: str, tag: str, linked_to: Optional[str] = None) -> str:
        """Register a PII span. Returns the placeholder string (e.g. `[PERSON_3A4F]`).

        `linked_to` may be either:
          - a hex id (`"3A4F"`) to bind to an existing entity, or
          - a previously-seen text value to look up.
        """
        if tag not in VALID_TAGS:
            raise ValueError(f"unknown tag: {tag}")
        text = text.strip()
        if not text:
            raise ValueError("empty PII text")

        # Already registered as the same tag - nothing to do.
        if text in self.replacements and self.replacements[text].startswith(f"[{tag}_"):
            return self.replacements[text]

        # Resolve a hex id.
        hex_id: Optional[str] = None
        if linked_to:
            link = linked_to.strip().upper()
            if len(link) == 4 and all(c in "0123456789ABCDEF" for c in link):
                if link in self.entities:
                    hex_id = link
            elif linked_to in self.text_to_hex:
                hex_id = self.text_to_hex[linked_to]

        if hex_id is None and text in self.text_to_hex:
            # Same exact text already seen under a different tag.
            hex_id = self.text_to_hex[text]
        if hex_id is None:
            hex_id = _new_hex(self._used_ids())

        placeholder = f"[{tag}_{hex_id}]"
        self.entities.setdefault(hex_id, {})[tag] = text
        self.replacements[text] = placeholder
        self.text_to_hex[text] = hex_id
        return placeholder

    def as_replacement_map(self) -> dict[str, str]:
        """Sorted longest-first to prevent partial-match collisions in the scrubber."""
        return dict(sorted(self.replacements.items(), key=lambda kv: -len(kv[0])))

    def counts_per_type(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for placeholder in self.replacements.values():
            tag = placeholder.split("_", 1)[0].lstrip("[")
            out[tag] = out.get(tag, 0) + 1
        return out

    def total_entities(self) -> int:
        return len(self.entities)

    def total_replacements(self) -> int:
        return len(self.replacements)

    def as_serializable(self) -> dict:
        """Build the entity_registry block used in the key file (PRD 5.7)."""
        out: dict[str, dict] = {}
        for hex_id, by_tag in self.entities.items():
            out[hex_id] = {
                "types": sorted(by_tag.keys()),
                "values": dict(by_tag),
            }
        return out

    def merge_chunks(self, raw_items: list[dict]) -> None:
        """Ingest a list of LLM-returned spans across chunks.

        Each item: {"text": str, "type": str, "linked_to": str | None}
        """
        for item in raw_items:
            text = (item.get("text") or "").strip()
            tag = (item.get("type") or "").strip().upper()
            link = item.get("linked_to")
            if not text or tag not in VALID_TAGS:
                continue
            try:
                self.add(text, tag, link if isinstance(link, str) else None)
            except (ValueError, RuntimeError) as exc:
                log.warning("skipped span: tag=%s reason=%s", tag, exc)

    def drop(self, original_text: str) -> bool:
        """Remove a span (used when the operator deselects a false positive)."""
        if original_text not in self.replacements:
            return False
        hex_id = self.text_to_hex.pop(original_text, None)
        self.replacements.pop(original_text, None)
        if hex_id and hex_id in self.entities:
            # Strip whichever tags pointed at this exact text.
            self.entities[hex_id] = {
                t: v for t, v in self.entities[hex_id].items() if v != original_text
            }
            if not self.entities[hex_id]:
                del self.entities[hex_id]
        return True
