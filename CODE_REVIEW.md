# Code Review - Doc Anonymizer v1.3

Date: 2026-07-17
Scope: full backend (`app/`), frontend (`static/`, `templates/`), tests, config.
Method: line-by-line read of all modules, full test run (61/61 passing), plus
targeted repro scripts for every finding marked CONFIRMED below.

Overall verdict: the architecture is solid. Clean module separation, a real test
suite, the verification gate is enforced on all four release paths (file
download, key download, on-screen text, GitHub push), `github.json` is correctly
gitignored, and logging discipline is good. The findings below are ranked by how
much they threaten the two things this product promises: privacy and
reversibility.

---

## Critical

### C1. Verifier false positives permanently block clean output (CONFIRMED)
`app/verifier.py:26-32`. The PHONE regex matches any 10-digit number and the IP
regex matches any dotted-quad, so a clean document containing an invoice number
like `5124403981` or a version string like `1.2.3.4` fails verification. Repro:
a file with only those two strings fails with `regex_types=['IP','PHONE']`.
Because rule 9 gates every release path on verification, the user gets a dead
end with no override and no way to ship a perfectly clean document.

Fix: keep the map scan (actual detected PII residue) as a hard gate. Downgrade
generic regex hits to a warning that requires explicit operator acknowledgment,
and tighten the patterns (require separators in PHONE, validate IP octets 0-255,
Luhn-check credit cards).

### C2. LLM chunk failures are silently swallowed - PII goes unscanned
`app/detector.py:169-175`. When a chunk's LLM call fails, the loop logs the
error and `continue`s. Detection then reports complete, the preview renders, and
the user confirms - with an entire chunk of the document never scanned. Names
and addresses in that chunk sail through the verifier too (its regexes only
catch emails/phones/SSNs/IPs/cards). This is the single most likely way real PII
leaves the building.

Fix: a failed chunk must fail the run, or at minimum surface a blocking
"chunk 3/10 was NOT scanned - retry or acknowledge" decision before preview.

Note: `business_spec.md` Operational Decision 3 documents "log and continue"
and calls the verifier the safety net. The verifier is not a safety net for
names, addresses, or any PII without a rigid format - the spec's assumption
does not hold and should be revised alongside the fix.

### C3. PII split across DOCX runs survives scrubbing (CONFIRMED)
`app/scrubber.py:96-146`. Word routinely splits a name like "Jane Smith" across
two `<w:r>` runs after edits (`Ja` + `ne Smith`). The python-docx pass replaces
per-run and the deep-scrub pass replaces on raw XML, so neither sees the full
string. Repro: a two-run paragraph keeps "Jane Smith" verbatim in the output.
The verifier does catch it (the name is in the map), but that just lands the
user in the C1 dead end - the pipeline has no way to actually fix the file.

Fix: replace at paragraph level - join each paragraph's runs, find matches in
the joined text, and redistribute the replaced text across the original runs.
(The new web app in `web/` implements exactly this algorithm; port it.)

---

## High

### H1. Temp-file hygiene violations (CLAUDE.md non-negotiable)
The mandate says uploads are deleted immediately after processing completes
*or fails*. Today:
- Failure paths never delete: if extract, detect, or scrub throws, the upload
  stays in `uploads/` forever (`app/pipeline.py:94-127`).
- Success paths only sometimes delete: `cleanup_upload` runs only on
  file-download (`app/server.py:314`) or cancel. A user who uses "view text on
  screen" or downloads only the key leaves the original PII document on disk.
- LibreOffice conversions (`.doc`/`.odt`/etc.) are written to
  `/tmp/docanon-conv-*` (`app/extractors.py:301`) and never deleted - full
  PII copies accumulate in the system temp dir.
- A verification-failed output file stays in `output/` even though it still
  contains PII by definition.

Fix: a single `finally`-style cleanup in the pipeline that removes the upload,
conversion dirs, and failed outputs on every terminal state.

Note: `business_spec.md` Operational Decision 1 states uploads are "purged
immediately after each session ends (success, failure, or cancel)". The code
does not currently do what the spec says - this is spec drift, not just a bug.

### H2. Endpoint locality is enforced only in the browser
`POST /api/endpoints` accepts any URL; the "non-local URL" warning is a
client-side `confirm()` (`static/app.js:806-813`). `app/llm.py` will POST
document chunks to whatever `endpoints.json` says, with no server-side check or
even a log line. Anything that edits that file, or any direct API call, silently
defeats the privacy mandate.

Fix: validate in `endpoints.add_or_update` - reject non-local URLs unless the
payload carries an explicit `allow_nonlocal: true`, and log a WARNING at every
LLM call to a non-local endpoint.

### H3. XLSX formula results are invisible to both scrubber and verifier
`scrub_xlsx` only warns when a formula's *source text* contains PII, and after
openpyxl re-saves the workbook the cached formula values are gone, so the
verifier's `data_only=True` extraction reads formula cells as `None`
(`app/scrubber.py:164-172`, `app/extractors.py:199`). A cell like
`=A1&" "&B1` that concatenates a name is neither scrubbed nor verifiable, and
the sheet ships.

Fix: fail verification (not just warn) when any formula references a mapped PII
string, and document that formula-heavy sheets need manual review.

---

## Medium

### M1. Same text under two tags flips the placeholder (CONFIRMED)
`app/mapper.py:57-94`. If the LLM tags "St. Theresa" as ORG in chunk 1 and
PERSON in chunk 3, the replacement map ends up with only the later placeholder
(`[PERSON_88A3]`), the registry holds both, and `counts_per_type` undercounts.
Reversibility survives, but the output's tag semantics depend on LLM ordering.
Fix: first tag wins, or keep per-tag placeholders keyed by (text, tag).

### M2. Preview spans can overlap and garble the preview
`app/server.py:451-467` builds spans per original independently. When one
detected string is a substring of another (e.g. "Jane" and "Jane Smith"), the
span list overlaps and `renderPreview`'s cursor walk (`static/app.js:505-529`)
duplicates text. Fix: build spans in one longest-first pass over the text,
skipping regions already claimed.

### M3. Prompt grows unboundedly with the entity registry
`app/detector.py:74-89` serializes the whole registry into every chunk prompt.
On a long, name-dense document the prompt blows past `chunk_tokens` and the
model's context, and detection quality degrades silently. Fix: cap the registry
listing (most recent N entities) or drop the example values.

### M4. Dropped ZIP parts leave stale references
`scrub_docx`/`scrub_xlsx` drop `word/commentsExtended.xml` and
`xl/comments*.xml` without touching `[Content_Types].xml` or the `.rels` files,
and `word/document.xml` keeps its `w:commentReference` marks against an emptied
comments part. Office generally tolerates this but it risks "repair" prompts.
Fix: also strip the references and content-type entries.

### M5. Coverage gaps in DOCX surface pass
Text boxes (`w:txbxContent`), SmartArt, footnotes/endnotes are not visited by
python-docx; they're only covered by the raw-XML substitution pass, which has
the C3 split-run blindness. Worth an explicit extractor + scrub pass.

### M6. Image-only PDFs pass silently
A scanned PDF extracts zero characters, detection finds nothing, and an empty
"anonymized" .txt is released as verified. Warn when `char_count == 0` (or is
tiny relative to page count).

---

## Low

- `static/app.js` `rebindDropzones()` re-adds listeners to the same elements on
  every file selection - listener leak, multiplied click handling.
- `create(..., {html: ...})` interpolates endpoint nickname/model into innerHTML
  (`static/app.js:134`) - self-XSS in a local single-user app, but the escape
  helper already exists; use it.
- `app/github_mgr.py:203` - `resp.json()` on a non-JSON error body raises inside
  the error handler.
- Restored files always write to `output/{stem}_restored{ext}` - a second
  restore silently overwrites the first.
- `GET /api/unanonymize/download/<name>` serves any file in `output/` by name,
  untied to a session.
- Sessions live forever in `_SESSIONS`; a refresh mid-run orphans the session
  and its upload.
- Exception messages are logged raw (`log.error("... %s", exc)`); most are safe,
  but third-party parser exceptions can embed document content. Consider
  logging `type(exc).__name__` plus a sanitized message on paths that touch
  document text.

## What's in good shape

- Verification gate is consistently applied on all four release paths.
- Key file schema matches the PRD and round-trips (unanonymize tests pass).
- `github.json` gitignored (rule 11 verified), PATs redacted in every API
  response, key files are never pushed to GitHub.
- Longest-first replacement ordering is correct in both directions.
- Logging format matches spec; no PII values in any log call I traced.
- 61 tests, sensible coverage of mapper/detector/scrubber/verifier edges.

## Suggested order of attack

1. C1 + C2 (they gate everything else and are both privacy-critical)
2. H1 (mandate violation, cheap to fix)
3. C3 (port the paragraph-aware replacement from `web/index.html`)
4. H2, H3, then the mediums opportunistically.
