"""Unanonymize pipeline (PRD 5.8).

Inverts the replacement map (placeholder -> original), sorts by placeholder
length descending (collision prevention - same as forward pass), and applies
to the document using the same writers.

Output filename: `{original_name}_restored.{ext}`
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .config import OUTPUT_DIR
from .extractors import extract
from .logging_setup import get_logger
from .scrubber import scrub_csv, scrub_docx, scrub_text, scrub_xlsx

log = get_logger("unanon")


def _unique_path(base: Path) -> Path:
    """Never silently overwrite an earlier restore."""
    if not base.exists():
        return base
    i = 1
    while True:
        candidate = base.with_name(f"{base.stem}_{i}{base.suffix}")
        if not candidate.exists():
            return candidate
        i += 1


def reverse_map(replacement_map: dict[str, str]) -> dict[str, str]:
    """Invert and sort longest-placeholder-first."""
    inverted = {placeholder: original for original, placeholder in replacement_map.items()}
    return dict(sorted(inverted.items(), key=lambda kv: -len(kv[0])))


def unanonymize_file(input_path: Path, key_payload: dict) -> Path:
    """Restore an anonymized file using the loaded key payload."""
    rmap = reverse_map(key_payload.get("replacement_map") or {})
    if not rmap:
        raise ValueError("key file has empty replacement_map")

    extracted = extract(input_path)
    suffix = extracted.original_suffix
    out_ext = extracted.output_ext

    base = key_payload.get("original_filename") or input_path.name
    out_path = _unique_path(OUTPUT_DIR / f"{Path(base).stem}_restored{out_ext}")

    log.info(
        "unanonymize start: input_suffix=%s output=%s entities=%d",
        suffix, out_path.name, len(key_payload.get("entity_registry") or {}),
    )

    try:
        if suffix in ("xlsx", "xls", "ods"):
            result = scrub_xlsx(extracted.working_path, rmap, out_path)
        elif suffix in ("docx", "doc", "odt"):
            result = scrub_docx(extracted.working_path, rmap, out_path)
        elif suffix == "csv":
            result = scrub_csv(extracted, rmap, out_path)
        else:  # txt / rtf / html / pdf / pptx / etc - text output
            # If the input is a previously-anonymized .txt that we need to invert,
            # treat it as text directly even if its source was PDF/PPTX.
            result = scrub_text(extracted, rmap, out_path)
    finally:
        # LibreOffice conversion dirs must not outlive the run (temp hygiene).
        wp = extracted.working_path
        if wp and wp != input_path and Path(wp).parent.name.startswith("docanon-conv-"):
            shutil.rmtree(Path(wp).parent, ignore_errors=True)

    log.info("unanonymize complete: out=%s bytes=%d", out_path.name, result.bytes_written)
    return result.output_path
