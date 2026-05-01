"""Round-trip anonymize -> unanonymize tests."""

from __future__ import annotations

import json
from pathlib import Path


def test_unanonymize_text_roundtrip(tmp_path):
    """Apply replacements forward, then reverse via unanonymize."""
    from app.extractors import extract
    from app.scrubber import scrub_text
    from app.unanonymize import unanonymize_file

    src = tmp_path / "memo.txt"
    src.write_text("Jane Smith met Bob Torres at jane@x.org")

    rmap = {
        "Jane Smith": "[PERSON_3A4F]",
        "Bob Torres": "[PERSON_11C2]",
        "jane@x.org": "[EMAIL_3A4F]",
    }
    anon_out = tmp_path / "memo_anon.txt"
    scrub_text(extract(src), rmap, anon_out)

    # Save a key file for unanonymize to load.
    key_payload = {
        "session_id": "abc12345",
        "original_filename": "memo.txt",
        "replacement_map": rmap,
        "entity_registry": {},
    }
    out_path = unanonymize_file(anon_out, key_payload)
    restored = out_path.read_text()
    assert "Jane Smith" in restored
    assert "Bob Torres" in restored
    assert "jane@x.org" in restored
    assert "[PERSON_" not in restored


def test_unanonymize_xlsx_roundtrip(tmp_path):
    from openpyxl import Workbook, load_workbook
    from app.scrubber import scrub_xlsx
    from app.unanonymize import unanonymize_file

    src = tmp_path / "donors.xlsx"
    wb = Workbook(); ws = wb.active
    ws.append(["name", "email"])
    ws.append(["Jane Smith", "jane@x.org"])
    wb.save(str(src))

    rmap = {"Jane Smith": "[PERSON_3A4F]", "jane@x.org": "[EMAIL_3A4F]"}
    anon_out = tmp_path / "donors_anon.xlsx"
    scrub_xlsx(src, rmap, anon_out)

    key_payload = {
        "session_id": "abc12345",
        "original_filename": "donors.xlsx",
        "replacement_map": rmap,
    }
    restored_path = unanonymize_file(anon_out, key_payload)
    wb2 = load_workbook(str(restored_path))
    cells = [c.value for row in wb2.active.iter_rows() for c in row]
    assert "Jane Smith" in cells
    assert "jane@x.org" in cells


def test_unanonymize_handles_long_first_correctly(tmp_path):
    """Reversal must sort placeholders by length to avoid PERSON_3A4F replacing inside PERSON_3A4FA."""
    from app.scrubber import scrub_text
    from app.extractors import extract
    from app.unanonymize import unanonymize_file

    # Construct a placeholder collision-prone case.
    src = tmp_path / "x.txt"
    src.write_text("Apple A and Apple AB")
    rmap = {"Apple AB": "[ORG_AAAA]", "Apple A": "[ORG_BBBB]"}
    out = tmp_path / "anon.txt"
    scrub_text(extract(src), rmap, out)
    # Ensure forward replacement was correct (no partial collision).
    text = out.read_text()
    assert text == "[ORG_BBBB] and [ORG_AAAA]"

    key_payload = {
        "session_id": "abc12345",
        "original_filename": "x.txt",
        "replacement_map": rmap,
    }
    restored_path = unanonymize_file(out, key_payload)
    assert restored_path.read_text() == "Apple A and Apple AB"
