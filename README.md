# Doc Anonymizer

Local document anonymizer. Strips PII from documents using any local LLM endpoint (Ollama, LM Studio, llama.cpp, or any OpenAI-compatible local server). The original document never leaves the machine.

See `business_spec.md` for the why and `doc-anonymizer-prd.md` for the full spec.

---

## Install (Mac)

```bash
cd ~/doc-anonymizer
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Optional - LibreOffice unlocks `.doc`, `.ppt`, `.odt`, `.ods`, `.odp`:

```bash
brew install --cask libreoffice
```

A local LLM is required. Easiest setup:

```bash
brew install ollama
ollama pull llama3.2
ollama serve
```

---

## Run

```bash
cd ~/doc-anonymizer
source .venv/bin/activate
python run.py
```

Then open `http://localhost:5000`.

The first time the app starts, it writes a default `endpoints.json` pointing at `http://localhost:11434` (Ollama). Use the `[ MANAGE ENDPOINTS ]` panel in the UI to add or change endpoints.

---

## Tests

```bash
cd ~/doc-anonymizer
source .venv/bin/activate
python -m pytest tests/ -v
```

The test suite mocks the LLM and runs against an isolated temp directory. No network or local LLM required.

---

## Project layout

```
~/doc-anonymizer/
  run.py                 # entrypoint
  app/
    server.py            # Flask routes
    config.py            # env config
    logging_setup.py     # structured ISO/level/module logging
    endpoints.py         # LLM endpoint manager
    llm.py               # adapter (ollama / openai-compat)
    extractors.py        # per-format text extraction
    chunker.py           # token-aware chunking
    detector.py          # LLM detection orchestrator
    mapper.py            # entity registry, hex IDs, replacement map
    scrubber.py          # surface replace + deep ZIP scrub
    verifier.py          # post-scrub verification (map + regex)
    key_files.py         # key file save/load
    unanonymize.py       # reverse pipeline
    github_mgr.py        # GitHub connections + push
    pipeline.py          # session orchestrator
  static/
    styles.css           # terminal-monochrome tokens (PRD 12.2)
    app.js               # vanilla frontend
  templates/
    index.html           # single-page app shell
  tests/                 # pytest suite (LLM mocked)
  uploads/  output/  keys/   # runtime data (gitignored)
  endpoints.json  github.json  anonymizer.log   # runtime config + log (gitignored)
```

---

## Privacy contract (non-negotiable)

- The only network traffic permitted is to local LLM endpoints (localhost / RFC1918 / link-local) and, on explicit user action, GitHub.
- Document text and PII values never appear in `anonymizer.log`. Only metadata (counts, types, timings).
- `/uploads/` is purged after each session.
- `github.json` holds the GitHub PAT and is in `.gitignore`. Never commit it.
- Download and GitHub push are blocked until the post-scrub verification pass returns zero residual matches.
