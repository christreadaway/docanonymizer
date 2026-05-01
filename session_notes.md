# Session Notes: Doc Anonymizer

This file is a running log of Claude Code sessions. Append after every significant session. Do not edit previous entries.

---

## Session 001 - 2026-05-01

**Type:** Requirements and planning (Claude Chat - not yet in Claude Code)

### What Was Done

Conceptualized and fully specced a local document anonymizer tool from scratch. The idea originated as a browser-based artifact using the public Anthropic API, but the privacy requirement was identified immediately - the original document cannot touch any public LLM. The design pivoted to a local Flask app using Ollama or any compatible local LLM.

**PRD written and iterated through three versions:**

v1.0 - Initial spec covering PDF/XLSX/DOCX/CSV, Ollama as sole LLM backend, basic PII scrubbing with per-type placeholders.

v1.1 - Expanded to support any local LLM endpoint, not just Ollama. Added multi-endpoint manager with nicknames, health checks, OpenAI-compatible API style (covering LM Studio, llama.cpp, Jan, Koboldcpp, LocalAI). Added non-local endpoint guard.

v1.2 - Expanded file type support to full matrix: PDF, XLSX/XLS, CSV, DOCX/DOC, PPTX/PPT, ODT/ODS/ODP, TXT/RTF/HTML. Tiered output (format-preserving vs text-only) documented per format. LibreOffice dependency added for legacy and ODF formats.

**Key design decisions made:**

- Identifier format: `[TAG_XXXX]` where XXXX is 4-character uppercase hex (0000-FFFF = 65,536 permutations per type)
- Entity linking: same hex suffix across all PII types for the same real-world person (`[PERSON_3A4F]`, `[EMAIL_3A4F]`, `[PHONE_3A4F]`)
- Replacement order: sort by string length descending to prevent partial match collisions
- Key file stores full replacement map and entity registry for reversibility
- Key file schema includes endpoint URL and api_style so the record is self-documenting
- No database, no auth, no external dependencies of any kind
- Temp files deleted immediately after processing, not on a schedule
- Logging must never capture raw document text or PII values - metadata only

**Companion files created:**
- `doc-anonymizer-prd.md` - full product requirements document (v1.2)
- `CLAUDE.md` - workstyle instructions and project rules for Claude Code
- `business_spec.md` - rationale and scope summary
- `session_notes.md` - this file

### Open Questions (Unresolved)

1. Auto-delete policy for `/uploads` and `/output` directories - leave to user or auto-purge after N hours?
2. XLSX formula cells containing PII - scrub, skip, or warn only?
3. Key file encryption at rest - AES password protection as optional feature?
4. LLM timeout mid-chunk - resume from last completed chunk or restart?
5. OpenAI-compatible endpoints vary on whether `model` field is required - Claude Code should make the model field configurable per endpoint (already in schema)

### Next Steps

- Stand up Flask skeleton with endpoint manager and health check
- Build file extraction layer (pdfplumber, python-docx, openpyxl, python-pptx)
- Build LLM adapter layer (ollama + openai styles behind single `llm_call()` function)
- Build entity registry and replacement map logic
- Build anonymize pipeline end-to-end with XLSX as first test format
- Build unanonymize pipeline
- UI: single HTML page per spec in PRD Section 12
- Integration test: full round-trip anonymize + unanonymize on a real donor list

---

<!-- Append new sessions below this line -->

---

## Session 001 Addendum - 2026-05-01 (continued)

**Design direction finalized:**

- Full light and dark mode support via CSS custom properties and `prefers-color-scheme`, with manual toggle persisted to `localStorage`
- Terminal-esque aesthetic throughout - monospace font for entire UI (not just log panel), black and white color system, ASCII-style progress indicators (`[ ]`, `[>]`, `[x]`, `[!]`), no rounded buttons, no decorative elements
- Hard rule: buttons must always contrast against their surface. Primary buttons use solid fill. Disabled states are explicitly muted. Secondary and destructive buttons follow defined border/text patterns.
- Full CSS token spec added to PRD Section 12.2 with exact hex values for both modes
- Button contract, layout, accessibility requirements, and WCAG AA contrast mandate documented in PRD Section 12.3-12.5 and CLAUDE.md

PRD bumped to v1.2 final. All four project files updated. Ready for Claude Code.


---

## Session 001 Addendum 2 - 2026-05-01 (continued)

**Scope additions finalized for v1.3:**

PII types expanded from 6 to 17. Added: FINANCIAL, DOB, SID, IP, USERNAME, GRADE, MEDICAL, IMMIGRATION, DEMO, RELIGION, GENDER. Organized into Standard and Sensitive tiers (visual grouping only - no functional difference). SANITIZE ALL master toggle added as the default on-load state.

Mandatory pre-scrub preview panel added. User reviews all detected PII spans highlighted inline with proposed placeholders. Can deselect false positives. Must click CONFIRM AND SCRUB to proceed. No skip path exists.

Deep scrub guarantee added (Section 5.9). The output file is always freshly constructed - never a modified copy of the original binary. DOCX: walks every XML file in the ZIP archive, strips tracked changes (w:ins/w:del), clears comments.xml, zeros author metadata in docProps. XLSX: same ZIP walk, clears cell comments, zeros author metadata. PDF: already clean .txt output in v1. Regex net added as secondary scanner: email, phone, SSN, IP, credit card patterns.

Post-scrub verification pass added (Section 5.10). Re-extracts all text from output file, runs replacement map in reverse plus regex patterns. Download and GitHub push are disabled until verification passes. Result logged as pass/fail with match types only - matched values never logged.

GitHub repository push added (Section 4.10). User configures named connections (owner/repo, branch, PAT). After a verified scrub, user can push anonymized file to any configured repo branch. github.json added to mandatory .gitignore. Only the anonymized output is ever transmitted.

External network policy clarified: LLM endpoints must be localhost or LAN. GitHub API (api.github.com) is the only permitted external call, and only on explicit user action.

Two open questions added: (5) verification false positive whitelist workflow; (6) manual span tagging in preview panel.

PRD is v1.3 final. All files updated. Spec is complete and ready for Claude Code.

---

## Session 002 - 2026-05-01 (Claude Code, branch `claude/implement-screens-design-Wvu9O`)

**Type:** Implementation - first code session. Materialized the screens design from the Claude Design handoff bundle.

### What Was Built

`screens.html` at the repo root. A single static page that renders all six artboards from the design handoff (Anonymize happy path: Empty -> File loaded -> Detecting -> Preview -> Scrubbing -> Verified) at 1100px artboard width with macOS browser chrome around each. The dark mode terminal aesthetic is applied throughout, drawing color tokens from PRD 12.2 and the button/typography rules from CLAUDE.md and PRD 12.3-12.4.

The original handoff prototype was React + UMD CDN. That stack violates CLAUDE.md's rule "Single HTML page, vanilla JS, no external CSS frameworks or CDNs," so it was rewritten in vanilla HTML/CSS/JS (no React, no Babel, no CDN). Visual output is reproduced; the prototype's pan/zoom design canvas chrome (rename, drag-reorder, focus mode) was intentionally omitted - those are design tool affordances, not part of the app.

### Decisions and Assumptions

- **Artboard sizing.** Prototype hardcoded each artboard to 1100x780 with internally scrolling content. For a static page where focus mode does not exist, content cropping is a real UX bug. Switched to `min-height: 780px; height: auto` so each card grows to fit its content. Width stays at 1100px to preserve the design intent.
- **Status bar layout.** The status bar's content (endpoint label + dropdown + 3 secondary buttons) does not fit in 760px even with shrunk paddings. Allowed the status bar to wrap to 2 rows via `flex-wrap: wrap`; flagged below as something to revisit.
- **`[DARK]` toggle placement.** Prototype puts it in the status bar; PRD 12.4 says it goes in the header bar. Followed the prototype since the user's instruction was to implement the design. The PRD-vs-prototype divergence is noted; pick a side when the real toggle is wired up.
- **Browser chrome inclusion.** Kept the chrome wrapper from the prototype because the design intentionally frames each screen as a localhost browser session. This is design language, not a real implementation detail; the eventual Flask app's HTML will not include chrome.

### Bugs Found and Fixed (this session)

Comprehensive headless test pass with Playwright revealed six bugs across the implementation. All fixed before commit.

1. **Critical** - status dot collapsed to 0 width. Default flex `min-width: auto` shrank the 10x10 dot to invisible. Fix: `flex-shrink: 0` on `.da-dot`.
2. **High** - "Ollama (local)" wrapped to two lines under flex squeeze. Fix: `white-space: nowrap` on status-bar text spans plus `flex-shrink: 0` on all status-bar children.
3. **High** - status bar buttons overflowed past the right edge of the 760px content area. Fix: reduced sm-button padding (14 -> 10) and font (13 -> 12), added `flex-wrap: wrap` so any remaining overflow falls to a second row inside the status bar background instead of overhanging.
4. **Medium** - all six artboards cropped content at the bottom because the card was a hard 780px and the chrome body had `overflow: hidden`. Fix: `min-height: 780px; height: auto` on `.dc-card`, `overflow: visible` and flex chain on the chrome body so the wrapper grows with its child.
5. **Low** - results panel had multi-line vertical gaps between rows. Cause: `white-space: pre` on `.da-results` rendered the template literal's formatting whitespace between sibling divs as visible blank lines. Fix: scoped `white-space: pre` to direct child rows only.
6. **Low** - missing focus states (PRD 12.3 mandates `outline: 2px solid var(--border-focus)`); buttons defaulted to `type=submit`. Fix: added `:focus-visible` rules; added `type="button"` to every button.

### Tests Run

- Playwright headless render of all six artboards in Chromium, full-page screenshots to `/tmp/screen-{1..6}.png`. Manual visual review against the prototype.
- Per-screen structural assertions (artboard count, status bar present, tabs present, primary/disabled button counts, progress steps, log lines, PII rows, placeholders, verified line text).
- Console error / page error capture: zero errors after fixes.
- WCAG AA contrast spot-check: all measured text/background pairs at 5.92:1 or higher; primary buttons and active tabs at 21:1 (well past AAA).
- Keyboard focus check: first Tab focuses the endpoint select with the spec'd 2px white outline.
- Disabled-button click test: disabled `[ DETECT PII ]` correctly blocks click events.

### Open Issues / Known Limitations

- Status bar wraps to a second row at 760px width. Acceptable for a design mock; if/when the real header is built, either trim the bar's content or move `[ DARK ]` to the header per PRD 12.4.
- The screens are static art: no upload, detection, scrub, or unanonymize behavior is wired. This is intentional - the design canvas is a visual deliverable for the operator to react to before implementation begins.
- Filename cleanup: the `(1)` artifact from the original upload was dropped from `CLAUDE.md`, `business_spec.md`, and `session_notes.md` later in this same session.

### Next Steps

- Operator review of `screens.html` to confirm the screen flow and visual direction before any backend work begins.
- Once approved, scaffold the Flask skeleton from PRD 11 and wire the screens into Jinja templates (or keep them as a static reference and rebuild equivalent markup behind the Flask routes).
- Decide the `[ DARK ]` toggle placement and reconcile the prototype/PRD divergence.

---

## Session 003 - 2026-05-01 (Claude Code, branch `claude/implement-screens-design-Wvu9O`)

**Type:** Implementation - full v1.3 build per PRD.

### What Was Built

Complete Flask app with vanilla-JS frontend implementing the entire PRD. 12 Python modules under `app/`, single-page UI under `templates/` and `static/`, runtime data dirs `uploads/`, `output/`, `keys/`. Top-level entrypoint `run.py`.

**Python modules (`app/`):**

| Module | Responsibility |
|---|---|
| `config.py`              | env-driven config; `DOCANON_ROOT` override for tests; runtime data paths separated from source-tree paths so Flask can always find templates/static |
| `logging_setup.py`       | structured ISO-Z / level / module logger writing to `anonymizer.log` and stdout |
| `endpoints.py`           | LLM endpoint manager: persisted to `endpoints.json`, RFC1918/loopback validation guard, parallel health checks |
| `llm.py`                 | adapter contract `llm_call(prompt, endpoint=...)` for `ollama` and `openai` API styles |
| `extractors.py`          | per-format text extraction (PDF, DOCX, XLSX, CSV, PPTX, ODT/ODS/ODP via LibreOffice, TXT/RTF/HTML); returns format-aware `ExtractResult` |
| `chunker.py`             | tiktoken-aware chunking with 200-token overlap; chars-fallback when tiktoken is missing |
| `mapper.py`              | `EntityRegistry`: 4-char hex IDs, entity-linking via `linked_to`, longest-first replacement map for collision-free substitution |
| `detector.py`            | LLM detection orchestrator; tolerant JSON-array parser handles fences and prose; per-chunk error recovery |
| `scrubber.py`            | surface-replace per format + ZIP-level deep scrub: tracked changes (`<w:ins>`/`<w:del>`) stripped, `word/comments.xml` cleared, `xl/comments*.xml` dropped, `dc:creator` / `cp:lastModifiedBy` zeroed, alt-text descr stripped, raw-XML map substitution across every part |
| `verifier.py`            | post-scrub re-extraction + map-reverse + regex net (EMAIL/PHONE/SSN/IP/CREDIT_CARD); strips placeholders before regex to avoid false positives |
| `key_files.py`           | `*.key.json` save/load per PRD 5.7 schema |
| `unanonymize.py`         | reverses the replacement map (longest-first) and routes through the same writers |
| `github_mgr.py`          | connection storage in `github.json` (PAT redacted in API), `repos/{owner}/{name}` test, `PUT contents` push with auto-fetched SHA |
| `pipeline.py`            | session orchestrator: extract -> detect -> preview -> confirm -> scrub -> verify; runs detection / scrub on background threads so HTTP returns immediately |
| `server.py`              | Flask routes per the API plan in the file's docstring |

**Frontend:**

- `templates/index.html` - single page, no CDN, no framework. Sections: header, status bar, endpoint manager panel, GitHub manager panel, mode tabs, file drop zone, PII grid, detect button, detection progress, preview, scrub progress, results, GitHub push panel, log panel.
- `static/styles.css` - dark + light tokens via `prefers-color-scheme` plus a manual `[data-theme]` override that persists to `localStorage`.
- `static/app.js` - vanilla JS: state object, fetch wrapper, theme toggle, endpoint CRUD + health, GitHub CRUD + test, drop-zone + change handler, PII grid renderer, detection polling, preview placeholder rendering with click-to-deselect, scrub polling, results table, GitHub push panel, log tail.

### Decisions and Assumptions

- **Per-process in-memory sessions.** Single-user local tool; no need for Redis or DB. `pipeline._SESSIONS` is a dict guarded by a lock. Sessions disappear on restart. Outputs stay in `output/`; key files in `keys/`.
- **Background threads for long ops.** Upload, detection, and scrub are launched on `threading.Thread` so the HTTP request returns instantly. The frontend polls `/status` and `/results` until done. This avoids long-held connections for 30s+ LLM calls.
- **Open Question 1 (auto-cleanup of `/uploads` and `/output`).** Decided: `/uploads/` is purged immediately after the session completes (or is cancelled). `/output/` is kept for the user to manage manually - it's their anonymized work product.
- **Open Question 2 (XLSX formulas containing PII).** Decided: flag and warn, do not silently rewrite. The scrubber surfaces `formula_warnings` to the UI so the operator can review.
- **Open Question 4 (LLM timeout mid-chunk).** Decided: log and skip the failed chunk, continue with the rest. The verifier catches anything missed. A more aggressive resume policy can come in v2.
- **Deep scrub ordering.** The deep-scrub ZIP walk now applies the raw map substitution **before** the targeted handlers (metadata-zero, comments-clear). This is defense-in-depth: even if a placeholder lands in `<dc:creator>` via the raw pass, the explicit handler still empties it.
- **Per-row `white-space: pre` for results.** Same lesson as the screens.html session: scoped to row divs to keep template-literal whitespace from rendering as blank lines.
- **Theme toggle.** Honors `prefers-color-scheme` until the user explicitly clicks `[ DARK ]` / `[ LIGHT ]`, at which point `data-theme` on `<html>` wins and persists to `localStorage`.
- **Module names.** `app.server` (not `app.app`) for the Flask app so the entrypoint reads `python run.py` cleanly without shadowing the package name.

### Bugs Found and Fixed (this session)

Discovered via the test suite + headless UI rendering:

1. **High - XLSX deep-scrub broke OOXML namespace.** The `<dc:creator>` zeroing handler self-closed the tag, dropping its `xmlns:dc` attribute, which made openpyxl reject the file on reload. Fix: keep the original opening tag (with all attributes) and empty only the inner text via a backref-replace.
2. **High - test isolation broke template loading.** `DOCANON_ROOT` was overriding `TEMPLATES_DIR`, so under tests Flask couldn't find `templates/index.html`. Fix: split `SOURCE_ROOT` (always anchored to `app/__init__.py`'s parent) from runtime `ROOT`. Templates and static now use `SOURCE_ROOT`.
3. **Medium - drop zone collapsed to inline width.** `<label>` is inline by default. Added `display: block` to `.dropzone`.
4. **Medium - DETECT button lost the file reference after innerHTML rewrite.** Captured the chosen file in `state.anonFile` so the button click path reads from state, not the (now destroyed) DOM input. Same pattern for unanon zones via `state.unanon`.
5. **Low - PII grid rendered alphabetically.** Server was passing `sorted(VALID_TAGS)`. Added `mapper.TAG_ORDER` (the spec's order) and routed the index template + detector prompt through it.
6. **Low - metadata-zero handler ran before raw substitution.** That left "Jane Smith" in `<dc:creator>` because the regex didn't tolerate `xmlns:dc`. Reordered: raw substitution first, then targeted handlers (defense in depth) - and the regex was made permissive.

### Tests Run

`pytest tests/` - **58/58 passing** in ~2 seconds. Suite mocks the LLM (no network, no local Ollama required) and isolates each test against a fresh `DOCANON_ROOT` temp directory.

Coverage areas:

- `test_mapper.py` (10) - hex uniqueness, entity linking by hex / by text, longest-first sort, drop, invalid tag rejection, merge_chunks input sanitization, 500-entity stress.
- `test_endpoints.py` (8) - default state, CRUD, validation, RFC1918 / loopback / link-local detection, atomic write semantics, unreachable-endpoint health check.
- `test_extractors.py` (7) - txt, csv, html (script + style + tags stripped), xlsx, docx (header/footer/tables walked), pptx (speaker notes captured), unsupported-suffix rejection.
- `test_scrubber_verifier.py` (10) - text / csv / xlsx / docx surface replace, deep-scrub raw-XML pass, metadata zeroing, tracked-changes (`<w:ins>`/`<w:del>`) stripping with manual XML injection, formula warnings, verifier pass / map-residue fail / regex-residue fail / placeholder-immunity.
- `test_unanonymize.py` (3) - text + xlsx round-trip; longest-first reversal collision test.
- `test_detector.py` (6) - JSON extraction (direct / fenced / prose / empty), full detection with mocked llm_call, error-recovery on failing chunk.
- `test_server.py` (12) - health, endpoint CRUD via API, invalid-payload rejection, local-URL check, index page render, unsupported-upload rejection, full upload -> detect -> confirm -> verify -> download flow with mocked LLM, unanonymize via API, GitHub PAT redaction, **download blocked when verification fails**.
- `test_privacy.py` (2) - **end-to-end pipeline run + log scan: zero PII values appear anywhere in `anonymizer.log`**; mapper logger-level scan.

Plus a Playwright headless run against the live Flask server: status bar populated, 17 PII rows in spec order, 6 sensitive flags, SANITIZE-ALL toggle locks/unlocks individual checkboxes, theme toggle round-trips, manage panels open/close, tabs switch, file upload enables DETECT button, endpoint add round-trips through the API. **Zero console errors.**

### Open Issues / Known Limitations

- The detector's prompt is well-instructed but model quality varies. Real-world testing against Llama 3.2, Mistral 7B, and Phi-3 should follow before the operator runs production data through it. Consider a small built-in prompt-tuning panel in v2.
- PDF and PPTX output are text-only in v1 (per PRD 8). DOCX / XLSX preserve formatting fully.
- Open Questions 3, 5, 6 from PRD remain open - key file encryption, regex-whitelist after a verify miss, and manual span tagging in the preview. None are blockers for v1.
- The frontend log panel polls only on user action (refresh / after each pipeline transition). A streaming server-sent events upgrade would be nicer in v2.
- Status bar still wraps to two rows under 760px. Same call as Session 002: acceptable for now, revisit when the visual design lands.

### Next Steps

- Operator runs the app against a real local LLM (Ollama/Llama 3.2) on a sample donor list and validates the detection quality.
- Add a small fixture corpus to `tests/` (sanitized DOCX with tracked changes, sample XLSX with formulas, PDF with text) so the test suite exercises real document shapes, not only synthesized ones.
- Wire up the `[ HOOKS ]` for resolving Open Questions 3 / 5 / 6 if the operator wants them in v2.
