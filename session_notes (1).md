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
- File names from the upload (`CLAUDE (1).md`, `business_spec (1).md`, `session_notes (1).md`, `doc-anonymizer-prd.md`) carry the `(1)` artifact from the original upload. Worth normalizing in a future cleanup commit.

### Next Steps

- Operator review of `screens.html` to confirm the screen flow and visual direction before any backend work begins.
- Once approved, scaffold the Flask skeleton from PRD 11 and wire the screens into Jinja templates (or keep them as a static reference and rebuild equivalent markup behind the Flask routes).
- Decide the `[ DARK ]` toggle placement and reconcile the prototype/PRD divergence.
