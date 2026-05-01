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
