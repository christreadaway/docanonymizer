"""Apply replacements + deep scrub for DOCX/XLSX (PRD 5.9).

Two-stage strategy:

1. **Surface replacement**: walk the document model with the format-native
   library and replace text where it lives (paragraph runs, table cells,
   workbook cells, slide shapes). This preserves formatting.

2. **Deep scrub**: unzip the resulting DOCX/XLSX, do a raw-XML string
   substitution pass across every part, strip tracked changes /
   comments / author metadata. Repack as a fresh archive. The output is
   never a modified copy of the original binary.

Key scrub layers per format:

  DOCX
    - Paragraph runs, table cells, headers, footers (python-docx pass)
    - Walk every part in the .docx zip; raw map substitution
    - Strip <w:ins>/<w:del> revision elements (keep final text)
    - Clear word/comments.xml
    - Zero docProps/core.xml: <dc:creator>, <cp:lastModifiedBy>
    - Strip alt text on images (wp:docPr/@descr, pic:cNvPr/@descr)

  XLSX
    - Cell values via openpyxl (preserves formatting/formulas)
    - Walk every part in the .xlsx zip; raw map substitution
    - Clear xl/comments*.xml
    - Zero docProps/core.xml: <dc:creator>, <cp:lastModifiedBy>
    - Flag formula strings that contain a matched PII value (do not silently scrub)
"""

from __future__ import annotations

import csv
import io
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .extractors import ExtractResult
from .logging_setup import get_logger

log = get_logger("scrubber")


@dataclass
class ScrubResult:
    output_path: Path
    layers_cleaned: list[str] = field(default_factory=list)
    formula_warnings: list[str] = field(default_factory=list)
    bytes_written: int = 0


def apply_replacements_text(text: str, replacement_map: dict[str, str]) -> str:
    """Plain-string substitution. Map MUST be sorted longest-first by caller."""
    out = text
    for original, placeholder in replacement_map.items():
        if not original:
            continue
        out = out.replace(original, placeholder)
    return out


# ---------------------------------------------------------------------------
# Run-aware XML replacement (CODE_REVIEW C3)
#
# Word routinely splits a single string like "Jane Smith" across multiple
# <w:r>/<w:t> runs after edits, so neither a per-run pass nor a raw string
# substitution can see it. These helpers join the text runs of each paragraph
# (or shared-string item), find matches in the joined text, and write the
# placeholder back into the run where the match starts - removing the matched
# characters from the following runs. Formatting outside the match survives.
# ---------------------------------------------------------------------------

_WP_BLOCK_RE = re.compile(r"<w:p[ >].*?</w:p>", re.DOTALL)
_WT_RE = re.compile(r"<w:t((?:\s[^>]*)?)/>|<w:t((?:\s[^>]*)?)>(.*?)</w:t>", re.DOTALL)
_SI_BLOCK_RE = re.compile(r"<si>.*?</si>", re.DOTALL)
_IS_BLOCK_RE = re.compile(r"<is>.*?</is>", re.DOTALL)
_T_RE = re.compile(r"<t((?:\s[^>]*)?)/>|<t((?:\s[^>]*)?)>(.*?)</t>", re.DOTALL)

_DOCX_RUN_PARTS = re.compile(r"word/(document|header\d*|footer\d*|footnotes|endnotes|comments)\.xml")
_XLSX_SHEET_PARTS = re.compile(r"xl/worksheets/sheet\d*\.xml")

_ENTITY_RE = re.compile(r"&(amp|lt|gt|quot|apos|#x?[0-9a-fA-F]+);")


def _xml_unescape(s: str) -> str:
    def repl(m: re.Match) -> str:
        e = m.group(1)
        table = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'"}
        if e in table:
            return table[e]
        try:
            code = int(e[2:], 16) if e[1] in "xX" else int(e[1:])
            return chr(code)
        except (ValueError, OverflowError):
            return m.group(0)
    return _ENTITY_RE.sub(repl, s)


def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&apos;"))


def _replace_in_blocks(xml: str, block_re: re.Pattern, t_re: re.Pattern,
                       t_name: str, replacement_map: dict[str, str]) -> str:
    """Apply the map inside each text-run block, spanning split runs."""

    def process_block(bm: re.Match) -> str:
        block = bm.group(0)
        nodes = []  # (start, end, attrs, text)
        for m in t_re.finditer(block):
            attrs = m.group(1) if m.group(1) is not None else (m.group(2) or "")
            inner = m.group(3) if m.group(3) is not None else ""
            nodes.append((m.start(), m.end(), attrs, _xml_unescape(inner)))
        if not nodes:
            return block
        joined = "".join(n[3] for n in nodes)
        if not joined:
            return block

        # Claim non-overlapping ranges, longest originals first (map order).
        ranges: list[tuple[int, int, str]] = []
        for original, placeholder in replacement_map.items():
            if not original:
                continue
            idx = 0
            while True:
                f = joined.find(original, idx)
                if f == -1:
                    break
                end = f + len(original)
                if not any(f < r_end and end > r_start for r_start, r_end, _ in ranges):
                    ranges.append((f, end, placeholder))
                    idx = end
                else:
                    idx = f + 1
        if not ranges:
            return block
        ranges.sort()

        # Map each joined-text character back to its owning node.
        owner: list[int] = []
        for i, n in enumerate(nodes):
            owner.extend([i] * len(n[3]))

        new_texts = [""] * len(nodes)
        i = 0
        r = 0
        while i < len(joined):
            if r < len(ranges) and ranges[r][0] == i:
                new_texts[owner[i]] += ranges[r][2]
                i = ranges[r][1]
                r += 1
            else:
                new_texts[owner[i]] += joined[i]
                i += 1

        # Rebuild the block back-to-front so offsets stay valid.
        rebuilt = block
        for k in range(len(nodes) - 1, -1, -1):
            start, end, attrs, _ = nodes[k]
            if "xml:space" not in attrs:
                attrs += ' xml:space="preserve"'
            node_xml = f"<{t_name}{attrs}>{_xml_escape(new_texts[k])}</{t_name}>"
            rebuilt = rebuilt[:start] + node_xml + rebuilt[end:]
        return rebuilt

    return block_re.sub(process_block, xml)


def _structured_replace_docx(name: str, xml: str, replacement_map: dict[str, str]) -> str:
    if _DOCX_RUN_PARTS.fullmatch(name):
        return _replace_in_blocks(xml, _WP_BLOCK_RE, _WT_RE, "w:t", replacement_map)
    return xml


def _structured_replace_xlsx(name: str, xml: str, replacement_map: dict[str, str]) -> str:
    if name == "xl/sharedStrings.xml":
        return _replace_in_blocks(xml, _SI_BLOCK_RE, _T_RE, "t", replacement_map)
    if _XLSX_SHEET_PARTS.fullmatch(name):
        return _replace_in_blocks(xml, _IS_BLOCK_RE, _T_RE, "t", replacement_map)
    return xml


# ---------------------------------------------------------------------------
# Format-specific writers
# ---------------------------------------------------------------------------

def scrub_text(extract: ExtractResult, replacement_map: dict[str, str], out_path: Path) -> ScrubResult:
    new_text = apply_replacements_text(extract.text, replacement_map)
    out_path.write_text(new_text, encoding="utf-8")
    return ScrubResult(
        output_path=out_path,
        layers_cleaned=["text"],
        bytes_written=out_path.stat().st_size,
    )


def scrub_csv(extract: ExtractResult, replacement_map: dict[str, str], out_path: Path) -> ScrubResult:
    rows = extract.payload.get("rows") or []
    new_rows: list[list[str]] = []
    for row in rows:
        new_rows.append([apply_replacements_text(cell, replacement_map) for cell in row])
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(new_rows)
    return ScrubResult(
        output_path=out_path,
        layers_cleaned=["cells"],
        bytes_written=out_path.stat().st_size,
    )


def scrub_docx(working_path: Path, replacement_map: dict[str, str], out_path: Path) -> ScrubResult:
    """DOCX scrub: surface replace via python-docx, then ZIP-level deep scrub."""
    from docx import Document

    # Stage 1 - format-aware surface replacement on a temp copy.
    staged = out_path.with_suffix(".staged.docx")
    shutil.copyfile(working_path, staged)
    doc = Document(str(staged))

    def _replace_runs(runs):
        for run in runs:
            if run.text:
                new = apply_replacements_text(run.text, replacement_map)
                if new != run.text:
                    run.text = new

    for section in doc.sections:
        for box in (section.header, section.footer):
            for p in box.paragraphs:
                _replace_runs(p.runs)
    for p in doc.paragraphs:
        _replace_runs(p.runs)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    _replace_runs(p.runs)
    doc.save(str(staged))

    # Stage 2 - ZIP-level deep scrub (run-aware pass handles split runs).
    layers = _deep_scrub_zip(
        staged,
        out_path,
        replacement_map=replacement_map,
        structured_replace=_structured_replace_docx,
        per_part_handlers={
            "word/document.xml":   _strip_revision_marks,
            "word/header*.xml":    _strip_revision_marks,
            "word/footer*.xml":    _strip_revision_marks,
            "word/comments.xml":   _empty_comments_xml,
            "word/commentsExtended.xml": _empty_comments_ex_xml,
            "docProps/core.xml":   _zero_core_authors,
            "docProps/app.xml":    _zero_app_company,
        },
    )
    staged.unlink(missing_ok=True)

    return ScrubResult(
        output_path=out_path,
        layers_cleaned=["docx_runs"] + layers,
        bytes_written=out_path.stat().st_size,
    )


def scrub_xlsx(working_path: Path, replacement_map: dict[str, str], out_path: Path) -> ScrubResult:
    """XLSX scrub: surface replace via openpyxl, ZIP-level deep scrub, formula warnings."""
    from openpyxl import load_workbook

    staged = out_path.with_suffix(".staged.xlsx")
    shutil.copyfile(working_path, staged)
    wb = load_workbook(str(staged))

    formula_warnings: list[str] = []
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                v = cell.value
                if v is None:
                    continue
                if isinstance(v, str):
                    if v.startswith("="):
                        # Formula - flag if a PII string is embedded; don't silently rewrite.
                        for original in replacement_map:
                            if original and original in v:
                                formula_warnings.append(
                                    f"sheet={ws.title!r} cell={cell.coordinate} formula references PII"
                                )
                                break
                    else:
                        new = apply_replacements_text(v, replacement_map)
                        if new != v:
                            cell.value = new
    wb.save(str(staged))

    layers = _deep_scrub_zip(
        staged,
        out_path,
        replacement_map=replacement_map,
        structured_replace=_structured_replace_xlsx,
        per_part_handlers={
            re.compile(r"xl/comments\d*\.xml"): _empty_xlsx_comments_xml,
            "docProps/core.xml": _zero_core_authors,
            "docProps/app.xml":  _zero_app_company,
        },
    )
    staged.unlink(missing_ok=True)

    if formula_warnings:
        log.warning("xlsx formula warnings: %d", len(formula_warnings))

    return ScrubResult(
        output_path=out_path,
        layers_cleaned=["xlsx_cells"] + layers,
        formula_warnings=formula_warnings,
        bytes_written=out_path.stat().st_size,
    )


# ---------------------------------------------------------------------------
# Deep scrub primitives
# ---------------------------------------------------------------------------

def _augment_map_with_escapes(replacement_map: dict[str, str]) -> dict[str, str]:
    """Add XML-escaped variants so originals containing & < > " ' are caught
    in their encoded form inside XML parts. Order (longest-first) preserved."""
    out: dict[str, str] = {}
    for original, placeholder in replacement_map.items():
        out[original] = placeholder
        esc = _xml_escape(original)
        if esc != original:
            out[esc] = _xml_escape(placeholder)
    return dict(sorted(out.items(), key=lambda kv: -len(kv[0])))


def _deep_scrub_zip(
    src: Path,
    dst: Path,
    replacement_map: dict[str, str],
    per_part_handlers: dict | None = None,
    structured_replace=None,
) -> list[str]:
    """Walk every part in `src`, run handlers and the raw map substitution, repack to `dst`.

    `structured_replace(name, xml, map) -> xml` runs first on text parts - it
    is the run-aware pass that catches strings split across XML runs.
    Returns a list of layer labels for logging.
    """
    per_part_handlers = per_part_handlers or {}
    layers_touched: set[str] = set()
    raw_map = _augment_map_with_escapes(replacement_map)

    with zipfile.ZipFile(src, "r") as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info)
            handler = _find_handler(info.filename, per_part_handlers)

            # Drop sentinel - skip writing this part entirely.
            if handler is _drop_part:
                layers_touched.add(f"drop:{info.filename}")
                continue

            # 1. Structured (run-aware) replacement, then raw map substitution
            #    for any text / XML part. Apply before the metadata-zero
            #    handlers so any placeholder that lands in author/company
            #    fields is then cleared by the explicit handler.
            if _is_text_part(info.filename):
                try:
                    text = data.decode("utf-8")
                    new_text = text
                    if structured_replace is not None:
                        new_text = structured_replace(info.filename, new_text, replacement_map)
                        if new_text != text:
                            layers_touched.add("run_aware_substitution")
                    substituted = apply_replacements_text(new_text, raw_map)
                    if substituted != new_text:
                        layers_touched.add("raw_xml_substitution")
                    if substituted != text:
                        data = substituted.encode("utf-8")
                except UnicodeDecodeError:
                    pass

            # 2. Targeted handler - tracked-change strip, metadata zero, etc.
            if handler is not None:
                try:
                    new_data, label = handler(info.filename, data)
                    if new_data is _DROP:
                        layers_touched.add(f"drop:{info.filename}")
                        continue
                    data = new_data
                    if label:
                        layers_touched.add(label)
                except Exception as exc:  # never let a malformed part take down the run
                    log.warning("handler failed on %s: %s", info.filename, exc)

            zout.writestr(info, data)

    return sorted(layers_touched)


_DROP = object()  # sentinel for handlers that signal "drop this part entirely"


def _find_handler(name: str, table: dict):
    for key, handler in table.items():
        if isinstance(key, str) and "*" in key:
            # cheap glob: replace * with non-slash run
            pat = re.compile(re.escape(key).replace(r"\*", r"[^/]*"))
            if pat.fullmatch(name):
                return handler
        elif isinstance(key, str):
            if key == name:
                return handler
        else:  # compiled pattern
            if key.fullmatch(name) or key.match(name):
                return handler
    return None


def _drop_part(name: str, data: bytes) -> tuple:  # pragma: no cover - sentinel target
    return _DROP, f"drop:{name}"


def _empty_comments_xml(name: str, data: bytes) -> tuple[bytes, str]:
    empty = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>'
    return empty, "comments_cleared"


def _empty_comments_ex_xml(name: str, data: bytes) -> tuple[bytes, str]:
    # Emptied rather than dropped: dropping leaves stale [Content_Types].xml
    # and .rels references that can trigger Office repair prompts (M4).
    empty = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w15:commentsEx xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml"/>'
    return empty, "comments_extended_cleared"


def _empty_xlsx_comments_xml(name: str, data: bytes) -> tuple[bytes, str]:
    empty = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<comments xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><authors/><commentList/></comments>'
    return empty, "xlsx_comments_cleared"


def _strip_revision_marks(name: str, data: bytes) -> tuple[bytes, str]:
    """Remove <w:ins>/<w:del> elements while keeping the final text.

    Strategy: drop opening/closing tags so the inner runs survive in place
    (this is a conservative XML-aware regex). Tracked-change deletions are
    fully removed (their contents shouldn't survive in the final document).
    """
    text = data.decode("utf-8", errors="ignore")

    # Remove <w:del>...</w:del> entirely (deleted content)
    text = re.sub(r"<w:del\b[^>]*>.*?</w:del>", "", text, flags=re.DOTALL)
    # Unwrap <w:ins>...</w:ins> -> keep inner content
    text = re.sub(r"<w:ins\b[^>]*>", "", text)
    text = re.sub(r"</w:ins>", "", text)
    # Strip alt text descriptions that may carry names ("Photo of John")
    text = re.sub(r' descr="[^"]*"', '', text)
    # Remove comment anchors - their comments part is emptied, so dangling
    # references would otherwise risk repair prompts (M4).
    text = re.sub(r"<w:commentRangeStart[^>]*/>", "", text)
    text = re.sub(r"<w:commentRangeEnd[^>]*/>", "", text)
    text = re.sub(r"<w:commentReference[^>]*/>", "", text)

    return text.encode("utf-8"), "tracked_changes_stripped"


def _zero_core_authors(name: str, data: bytes) -> tuple[bytes, str]:
    text = data.decode("utf-8", errors="ignore")
    # Empty the inner text. Preserve the open tag (with any xmlns/etc attributes)
    # so the document remains a valid OOXML core-properties file.
    text = re.sub(
        r"(<dc:creator(?:\s[^>]*)?>)[^<]*(</dc:creator>)",
        r"\1\2", text,
    )
    text = re.sub(
        r"(<cp:lastModifiedBy(?:\s[^>]*)?>)[^<]*(</cp:lastModifiedBy>)",
        r"\1\2", text,
    )
    return text.encode("utf-8"), "metadata_zeroed"


def _zero_app_company(name: str, data: bytes) -> tuple[bytes, str]:
    text = data.decode("utf-8", errors="ignore")
    text = re.sub(r"(<Company(?:\s[^>]*)?>)[^<]*(</Company>)", r"\1\2", text)
    text = re.sub(r"(<Manager(?:\s[^>]*)?>)[^<]*(</Manager>)", r"\1\2", text)
    return text.encode("utf-8"), "app_metadata_zeroed"


_TEXT_PART_SUFFIXES = (".xml", ".rels", ".txt", ".vml")


def _is_text_part(name: str) -> bool:
    n = name.lower()
    return any(n.endswith(s) for s in _TEXT_PART_SUFFIXES)
