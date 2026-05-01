"""Privacy mandate tests (CLAUDE.md non-negotiable).

After running a full pipeline, the log file must contain NO PII values, NO
document text, and NO replacement-map values.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch


PII_VALUES = [
    "Jane Smith",
    "Bob Torres",
    "jane.smith@school.org",
    "555-234-9988",
    "123 Main St",
    "Austin TX 78701",
]


def test_no_pii_in_log_after_full_run(tmp_path):
    """End-to-end: upload, detect, scrub, verify - then grep the log."""
    import io
    from app import detector
    from app.server import create_app
    from app.config import LOG_FILE

    fake = json.dumps([
        {"text": "Jane Smith", "type": "PERSON", "linked_to": None},
        {"text": "Bob Torres", "type": "PERSON", "linked_to": None},
        {"text": "jane.smith@school.org", "type": "EMAIL", "linked_to": "Jane Smith"},
        {"text": "555-234-9988", "type": "PHONE", "linked_to": "Jane Smith"},
        {"text": "123 Main St", "type": "ADDRESS", "linked_to": None},
    ])

    body = (
        "From Jane Smith <jane.smith@school.org>\n"
        "To Bob Torres\n"
        "Phone: 555-234-9988\n"
        "Address: 123 Main St, Austin TX 78701\n"
    ).encode()

    app = create_app()
    c = app.test_client()
    with patch.object(detector.llm, "llm_call", return_value=fake):
        r = c.post("/api/anonymize/upload", data={
            "file": (io.BytesIO(body), "memo.txt"),
            "tags": "ALL",
        }, content_type="multipart/form-data")
        sid = r.get_json()["session_id"]
        for _ in range(80):
            s = c.get(f"/api/anonymize/{sid}/status").get_json()
            if s.get("detection_complete"):
                break
            time.sleep(0.05)
        c.post(f"/api/anonymize/{sid}/confirm", json={"deselected": []})
        for _ in range(80):
            res = c.get(f"/api/anonymize/{sid}/results").get_json()
            if res.get("verify_result"):
                break
            time.sleep(0.05)

    assert res["verify_result"]["passed"]

    # Now scan the log for PII values.
    log_text = LOG_FILE.read_text(encoding="utf-8") if LOG_FILE.exists() else ""
    for pii in PII_VALUES:
        assert pii not in log_text, f"PII leak in log: {pii!r}"


def test_mapper_log_messages_have_only_metadata():
    """The mapper log surface should never carry the PII values themselves."""
    import logging
    from io import StringIO

    from app.mapper import EntityRegistry

    # Capture logs from the mapper logger.
    handler = logging.StreamHandler(StringIO())
    logger = logging.getLogger("docanon.mapper")
    logger.addHandler(handler)

    r = EntityRegistry()
    r.merge_chunks([
        {"text": "Jane Smith", "type": "PERSON"},
        {"text": "jane.smith@school.org", "type": "EMAIL", "linked_to": "Jane Smith"},
        {"text": "x", "type": "BOGUS"},  # logged-skipped
    ])
    captured = handler.stream.getvalue()
    for pii in PII_VALUES[:2]:
        assert pii not in captured, f"PII in mapper log: {pii!r}"
