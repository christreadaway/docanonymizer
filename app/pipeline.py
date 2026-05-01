"""Anonymize pipeline orchestrator.

Wires extract -> detect -> preview -> confirm -> scrub -> verify -> output
into a session object that the Flask routes drive step-by-step. Sessions
live in memory only - this is a single-user local tool.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import endpoints as endpoints_mod
from .config import KEYS_DIR, OUTPUT_DIR, UPLOADS_DIR
from .detector import detect_pii
from .extractors import ExtractResult, extract
from .key_files import new_session_id, save_key_file
from .logging_setup import get_logger
from .mapper import EntityRegistry
from .scrubber import scrub_csv, scrub_docx, scrub_text, scrub_xlsx
from .verifier import verify_output

log = get_logger("pipeline")


_SESSIONS: dict[str, "Session"] = {}
_SESSIONS_LOCK = threading.Lock()


@dataclass
class Session:
    id: str
    upload_path: Path
    original_filename: str
    endpoint: dict
    allowed_tags: list[str] = field(default_factory=list)
    extract: Optional[ExtractResult] = None
    registry: Optional[EntityRegistry] = None
    detection_progress: list[dict] = field(default_factory=list)
    detection_complete: bool = False
    scrub_steps: list[dict] = field(default_factory=list)
    output_path: Optional[Path] = None
    key_path: Optional[Path] = None
    verify_result: Optional[dict] = None
    formula_warnings: list[str] = field(default_factory=list)
    error: Optional[str] = None
    created_at: float = field(default_factory=time.monotonic)


def new_session(upload_path: Path, original_filename: str, allowed_tags: list[str],
                endpoint: Optional[dict] = None) -> Session:
    sid = new_session_id()
    ep = endpoint or endpoints_mod.get_active() or {}
    sess = Session(
        id=sid,
        upload_path=upload_path,
        original_filename=original_filename,
        endpoint=ep,
        allowed_tags=allowed_tags,
    )
    with _SESSIONS_LOCK:
        _SESSIONS[sid] = sess
    log.info("session created: id=%s file=%s", sid, original_filename)
    return sess


def get_session(sid: str) -> Optional[Session]:
    with _SESSIONS_LOCK:
        return _SESSIONS.get(sid)


def discard_session(sid: str) -> None:
    with _SESSIONS_LOCK:
        sess = _SESSIONS.pop(sid, None)
    if not sess:
        return
    # Best-effort cleanup of the temp upload.
    try:
        if sess.upload_path.exists() and UPLOADS_DIR in sess.upload_path.parents:
            sess.upload_path.unlink()
    except OSError as exc:
        log.warning("upload cleanup failed: %s", exc)
    log.info("session discarded: id=%s", sid)


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def run_extract_and_detect(sess: Session) -> Session:
    """Extract text from the upload, then run LLM detection across chunks."""
    try:
        sess.extract = extract(sess.upload_path)
    except Exception as exc:  # bubble up clean error to UI
        sess.error = f"extraction failed: {exc}"
        log.error("session %s extract failed: %s", sess.id, exc)
        return sess

    def on_chunk(info: dict) -> None:
        sess.detection_progress.append(info)

    try:
        sess.registry = detect_pii(
            sess.extract.text,
            allowed_tags=sess.allowed_tags or None,
            on_chunk=on_chunk,
            endpoint=sess.endpoint,
        )
    except Exception as exc:
        sess.error = f"detection failed: {exc}"
        log.error("session %s detect failed: %s", sess.id, exc)
        return sess

    sess.detection_complete = True
    counts = sess.registry.counts_per_type()
    log.info(
        "session %s detect complete: entities=%d replacements=%d %s",
        sess.id,
        sess.registry.total_entities(),
        sess.registry.total_replacements(),
        " ".join(f"{k}={v}" for k, v in sorted(counts.items())),
    )
    return sess


def confirm_and_scrub(sess: Session, deselected: Optional[list[str]] = None) -> Session:
    """Apply replacements + deep scrub + verification.

    `deselected` is a list of original PII strings the operator removed in
    the preview panel.
    """
    if sess.registry is None or sess.extract is None:
        sess.error = "session not detected yet"
        return sess

    for original in deselected or []:
        sess.registry.drop(original)

    rmap = sess.registry.as_replacement_map()
    if not rmap:
        sess.error = "no replacements to apply (registry empty)"
        return sess

    suffix = sess.extract.original_suffix
    out_ext = sess.extract.output_ext
    out_name = f"{Path(sess.original_filename).stem}_anon_{sess.id}{out_ext}"
    out_path = OUTPUT_DIR / out_name

    sess.scrub_steps.append({"step": "applying replacements", "status": "active"})
    log.info("session %s scrub start: out=%s", sess.id, out_name)

    try:
        if suffix in ("xlsx", "xls", "ods"):
            result = scrub_xlsx(sess.extract.working_path, rmap, out_path)
        elif suffix in ("docx", "doc", "odt"):
            result = scrub_docx(sess.extract.working_path, rmap, out_path)
        elif suffix == "csv":
            result = scrub_csv(sess.extract, rmap, out_path)
        else:
            result = scrub_text(sess.extract, rmap, out_path)
    except Exception as exc:
        sess.error = f"scrub failed: {exc}"
        log.error("session %s scrub failed: %s", sess.id, exc)
        return sess

    sess.scrub_steps[-1]["status"] = "done"
    for layer in result.layers_cleaned:
        sess.scrub_steps.append({"step": f"deep scrub: {layer}", "status": "done"})
    sess.formula_warnings = result.formula_warnings
    sess.output_path = result.output_path

    sess.scrub_steps.append({"step": "verification scan running", "status": "active"})
    verify = verify_output(result.output_path, rmap)
    sess.scrub_steps[-1]["status"] = "done" if verify.passed else "err"
    sess.verify_result = {
        "passed": verify.passed,
        "map_match_types": verify.map_match_types,
        "regex_match_types": verify.regex_match_types,
        "total_matches": verify.total_matches,
    }

    if verify.passed:
        sess.scrub_steps.append({"step": "output ready", "status": "done"})
        sess.key_path = save_key_file(
            session_id=sess.id,
            original_filename=sess.original_filename,
            endpoint=sess.endpoint,
            pii_types_scrubbed=sorted(sess.registry.counts_per_type().keys()),
            registry=sess.registry,
        )
    else:
        sess.scrub_steps.append({"step": "output blocked - verification failed", "status": "err"})

    log.info(
        "session %s scrub complete: verified=%s out=%s",
        sess.id, verify.passed, result.output_path.name,
    )
    return sess


def cleanup_upload(sess: Session) -> None:
    """Delete the temp upload after successful processing."""
    try:
        if sess.upload_path.exists() and UPLOADS_DIR in sess.upload_path.parents:
            sess.upload_path.unlink()
            log.info("upload deleted: %s", sess.upload_path.name)
    except OSError as exc:
        log.warning("upload delete failed: %s", exc)
