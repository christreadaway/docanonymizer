"""Key file save / load (PRD 5.7).

Schema:
{
  "session_id":       "a3f9c1b2",
  "original_filename": "donor_list.xlsx",
  "created_at":       "2026-05-01T14:32:00Z",
  "llm_endpoint":     "http://localhost:11434",
  "llm_api_style":    "ollama",
  "model_used":       "llama3.2",
  "pii_types_scrubbed": [...],
  "entity_registry":  {...},
  "replacement_map":  {...}
}
"""

from __future__ import annotations

import datetime as dt
import json
import secrets
from pathlib import Path
from typing import Optional

from .config import KEYS_DIR
from .logging_setup import get_logger
from .mapper import EntityRegistry

log = get_logger("key")


def new_session_id() -> str:
    return secrets.token_hex(4)  # 8 hex chars


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def save_key_file(
    session_id: str,
    original_filename: str,
    endpoint: dict,
    pii_types_scrubbed: list[str],
    registry: EntityRegistry,
) -> Path:
    payload = {
        "session_id": session_id,
        "original_filename": original_filename,
        "created_at": _now_iso(),
        "llm_endpoint": endpoint.get("base_url", ""),
        "llm_api_style": endpoint.get("api_style", ""),
        "model_used": endpoint.get("model", ""),
        "pii_types_scrubbed": sorted(set(pii_types_scrubbed)),
        "entity_registry": registry.as_serializable(),
        "replacement_map": registry.as_replacement_map(),
    }
    stem = Path(original_filename).stem or "document"
    name = f"{stem}_{session_id}.key.json"
    path = KEYS_DIR / name
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    log.info("key file saved: %s", path.name)
    return path


def load_key_file(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    required = ("session_id", "original_filename", "replacement_map")
    for k in required:
        if k not in payload:
            raise ValueError(f"key file missing required field: {k}")
    return payload


def list_recent(limit: int = 50) -> list[dict]:
    """Lightweight directory listing for the unanonymize key picker."""
    items: list[dict] = []
    if not KEYS_DIR.exists():
        return items
    for p in sorted(KEYS_DIR.glob("*.key.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            stat = p.stat()
            items.append({
                "name": p.name,
                "size": stat.st_size,
                "modified": _fmt_mtime(stat.st_mtime),
            })
        except OSError:
            continue
        if len(items) >= limit:
            break
    return items


def _fmt_mtime(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
