"""Flask server. Single-page app + JSON API.

Routes:
  GET  /                              - the single-page UI
  GET  /api/health                    - server liveness
  GET  /api/endpoints                 - list configured LLM endpoints
  POST /api/endpoints                 - add or update endpoint
  DEL  /api/endpoints/<id>            - delete endpoint
  POST /api/endpoints/<id>/select     - set as active
  POST /api/endpoints/health          - health-check all (or one)
  POST /api/endpoints/check-local     - dry-run is_local_url for an entered URL

  GET  /api/github                    - list GitHub connections (token redacted)
  POST /api/github                    - add or update connection
  DEL  /api/github/<id>               - delete connection
  POST /api/github/<id>/test          - test PAT/repo access
  POST /api/github/push               - push a session output

  POST /api/anonymize/upload          - multipart upload, kicks off detection
  GET  /api/anonymize/<sid>/status    - poll detection progress + preview data
  POST /api/anonymize/<sid>/confirm   - confirm preview, run scrub + verify
  GET  /api/anonymize/<sid>/results   - get final results / verification status
  GET  /api/anonymize/<sid>/download/file  - download scrubbed file
  GET  /api/anonymize/<sid>/download/key   - download key.json
  POST /api/anonymize/<sid>/cancel    - discard session, delete upload

  POST /api/unanonymize               - upload anon file + key, run inverse
  GET  /api/unanonymize/<sid>/download - download restored file

  GET  /api/keys                       - list keys for the unanonymize picker
  GET  /api/log/tail                   - tail the log file (last N lines)
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional

from flask import Flask, abort, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename

from . import endpoints as endpoints_mod
from . import github_mgr
from . import pipeline
from . import unanonymize as unan
from .config import (
    KEYS_DIR,
    LOG_FILE,
    MAX_UPLOAD_BYTES,
    OUTPUT_DIR,
    PORT,
    STATIC_DIR,
    TEMPLATES_DIR,
    UPLOADS_DIR,
)
from .extractors import OUTPUT_NOTES, SUPPORTED, libreoffice_available
from .key_files import list_recent, load_key_file
from .logging_setup import get_logger
from .mapper import TAG_ORDER, VALID_TAGS

log = get_logger("server")


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(TEMPLATES_DIR),
        static_folder=str(STATIC_DIR),
        static_url_path="/static",
    )
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

    endpoints_mod.ensure_initialized()
    _log_startup()

    # -----------------------------------------------------------------------
    # UI
    # -----------------------------------------------------------------------
    @app.get("/")
    def index():
        return render_template(
            "index.html",
            supported_extensions=sorted(SUPPORTED),
            output_notes=OUTPUT_NOTES,
            valid_tags=TAG_ORDER,
            libreoffice=libreoffice_available(),
        )

    # -----------------------------------------------------------------------
    # Health
    # -----------------------------------------------------------------------
    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok"})

    # -----------------------------------------------------------------------
    # Endpoint manager
    # -----------------------------------------------------------------------
    @app.get("/api/endpoints")
    def ep_list():
        return jsonify({
            "endpoints": endpoints_mod.list_endpoints(),
            "active": (endpoints_mod.get_active() or {}).get("id"),
        })

    @app.post("/api/endpoints")
    def ep_save():
        payload = request.get_json(force=True, silent=True) or {}
        try:
            saved = endpoints_mod.add_or_update(payload)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"endpoint": saved})

    @app.delete("/api/endpoints/<eid>")
    def ep_delete(eid: str):
        ok = endpoints_mod.delete(eid)
        return ("", 204) if ok else (jsonify({"error": "not found"}), 404)

    @app.post("/api/endpoints/<eid>/select")
    def ep_select(eid: str):
        if not endpoints_mod.set_last_used(eid):
            return jsonify({"error": "not found"}), 404
        return jsonify({"active": eid})

    @app.post("/api/endpoints/health")
    def ep_health():
        payload = request.get_json(silent=True) or {}
        eid = payload.get("id")
        if eid:
            ep = endpoints_mod.get(eid)
            if not ep:
                return jsonify({"error": "not found"}), 404
            return jsonify({"results": [endpoints_mod.health_check(ep)]})
        return jsonify({"results": endpoints_mod.health_check_all()})

    @app.post("/api/endpoints/check-local")
    def ep_check_local():
        payload = request.get_json(force=True, silent=True) or {}
        url = payload.get("base_url", "")
        return jsonify({"local": endpoints_mod.is_local_url(url)})

    # -----------------------------------------------------------------------
    # GitHub manager
    # -----------------------------------------------------------------------
    @app.get("/api/github")
    def gh_list():
        return jsonify({"connections": github_mgr.list_connections()})

    @app.post("/api/github")
    def gh_save():
        payload = request.get_json(force=True, silent=True) or {}
        try:
            saved = github_mgr.add_or_update(payload)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"connection": saved})

    @app.delete("/api/github/<cid>")
    def gh_delete(cid: str):
        ok = github_mgr.delete(cid)
        return ("", 204) if ok else (jsonify({"error": "not found"}), 404)

    @app.post("/api/github/<cid>/test")
    def gh_test(cid: str):
        conn = github_mgr.get(cid)
        if not conn:
            return jsonify({"error": "not found"}), 404
        return jsonify(github_mgr.test_connection(conn))

    @app.post("/api/github/push")
    def gh_push():
        payload = request.get_json(force=True, silent=True) or {}
        sid = payload.get("session_id")
        cid = payload.get("connection_id")
        sess = pipeline.get_session(sid) if sid else None
        if not sess or not sess.output_path:
            return jsonify({"error": "session not found or has no output"}), 404
        if not sess.verify_result or not sess.verify_result.get("passed"):
            return jsonify({"error": "verification not passed - push blocked"}), 400
        conn = github_mgr.get(cid) if cid else None
        if not conn:
            return jsonify({"error": "github connection not found"}), 404
        result = github_mgr.push_file(
            conn,
            sess.output_path,
            dest_path=payload.get("path"),
            branch=payload.get("branch"),
            commit_message=payload.get("commit_message"),
        )
        if result.get("status") != "ok":
            return jsonify(result), 502
        return jsonify(result)

    # -----------------------------------------------------------------------
    # Anonymize pipeline
    # -----------------------------------------------------------------------
    @app.post("/api/anonymize/upload")
    def anon_upload():
        if "file" not in request.files:
            return jsonify({"error": "no file"}), 400
        f = request.files["file"]
        if not f.filename:
            return jsonify({"error": "empty filename"}), 400
        safe = secure_filename(f.filename)
        suffix = Path(safe).suffix.lower().lstrip(".")
        if suffix not in SUPPORTED:
            return jsonify({"error": f"unsupported file type: .{suffix}"}), 400

        # Tags selection - JSON body field is a string; falls back to "ALL".
        tags_raw = request.form.get("tags", "")
        if tags_raw and tags_raw != "ALL":
            allowed = [t.strip().upper() for t in tags_raw.split(",") if t.strip()]
            allowed = [t for t in allowed if t in VALID_TAGS]
            if not allowed:
                return jsonify({"error": "no valid tags supplied"}), 400
        else:
            allowed = sorted(VALID_TAGS)

        ep_id = request.form.get("endpoint_id")
        endpoint = endpoints_mod.get(ep_id) if ep_id else None

        # Save upload to /uploads with a session-prefixed name.
        sid_holder: dict = {}
        upload_path = UPLOADS_DIR / safe
        # If the user uploads two files in a row with the same name, suffix.
        if upload_path.exists():
            stem = upload_path.stem
            ext = upload_path.suffix
            i = 1
            while upload_path.exists():
                upload_path = UPLOADS_DIR / f"{stem}_{i}{ext}"
                i += 1
        f.save(upload_path)
        log.info("upload received: name=%s size=%d type=%s",
                 upload_path.name, upload_path.stat().st_size, suffix)

        sess = pipeline.new_session(upload_path, safe, allowed, endpoint=endpoint)
        sid_holder["sid"] = sess.id

        # Run extract + detect in a worker so the HTTP call returns immediately.
        threading.Thread(
            target=pipeline.run_extract_and_detect, args=(sess,), daemon=True,
        ).start()

        return jsonify({
            "session_id": sess.id,
            "filename": safe,
            "suffix": suffix,
            "output_ext": OUTPUT_NOTES[suffix][0],
            "output_note": OUTPUT_NOTES[suffix][1],
            "size": upload_path.stat().st_size,
        })

    @app.get("/api/anonymize/<sid>/status")
    def anon_status(sid: str):
        sess = pipeline.get_session(sid)
        if not sess:
            return jsonify({"error": "session not found"}), 404
        body = {
            "session_id": sess.id,
            "detection_complete": sess.detection_complete,
            "progress": sess.detection_progress,
            "error": sess.error,
        }
        if sess.detection_complete and sess.registry is not None:
            body["preview"] = _preview_payload(sess)
        return jsonify(body)

    @app.post("/api/anonymize/<sid>/confirm")
    def anon_confirm(sid: str):
        sess = pipeline.get_session(sid)
        if not sess:
            return jsonify({"error": "session not found"}), 404
        if not sess.detection_complete:
            return jsonify({"error": "detection not complete"}), 409
        payload = request.get_json(silent=True) or {}
        deselected = payload.get("deselected") or []
        threading.Thread(
            target=pipeline.confirm_and_scrub, args=(sess, deselected), daemon=True,
        ).start()
        return jsonify({"session_id": sid, "started": True})

    @app.get("/api/anonymize/<sid>/results")
    def anon_results(sid: str):
        sess = pipeline.get_session(sid)
        if not sess:
            return jsonify({"error": "session not found"}), 404
        return jsonify({
            "session_id": sid,
            "scrub_steps": sess.scrub_steps,
            "verify_result": sess.verify_result,
            "formula_warnings": sess.formula_warnings,
            "output_filename": sess.output_path.name if sess.output_path else None,
            "key_filename": sess.key_path.name if sess.key_path else None,
            "counts": sess.registry.counts_per_type() if sess.registry else {},
            "totals": {
                "entities": sess.registry.total_entities() if sess.registry else 0,
                "replacements": sess.registry.total_replacements() if sess.registry else 0,
            },
            "error": sess.error,
        })

    @app.get("/api/anonymize/<sid>/download/file")
    def anon_dl_file(sid: str):
        sess = pipeline.get_session(sid)
        if not sess or not sess.output_path or not sess.output_path.exists():
            abort(404)
        if not sess.verify_result or not sess.verify_result.get("passed"):
            abort(403)
        # After download, clean up the temp upload (output stays in /output).
        pipeline.cleanup_upload(sess)
        return send_file(sess.output_path, as_attachment=True)

    @app.get("/api/anonymize/<sid>/text")
    def anon_text(sid: str):
        """Return the scrubbed text for on-screen copy/paste.

        Verification gate applies here too - text is not released until
        the post-scrub verification pass succeeds (PRD 5.10).
        """
        sess = pipeline.get_session(sid)
        if not sess or not sess.output_path or not sess.output_path.exists():
            abort(404)
        if not sess.verify_result or not sess.verify_result.get("passed"):
            abort(403)
        try:
            text = pipeline.anonymized_text(sess)
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 409
        return jsonify({
            "text": text,
            "char_count": len(text),
            "filename": sess.output_path.name,
        })

    @app.get("/api/anonymize/<sid>/download/key")
    def anon_dl_key(sid: str):
        sess = pipeline.get_session(sid)
        if not sess or not sess.key_path or not sess.key_path.exists():
            abort(404)
        if not sess.verify_result or not sess.verify_result.get("passed"):
            abort(403)
        return send_file(sess.key_path, as_attachment=True)

    @app.post("/api/anonymize/<sid>/cancel")
    def anon_cancel(sid: str):
        pipeline.discard_session(sid)
        return jsonify({"discarded": sid})

    # -----------------------------------------------------------------------
    # Unanonymize
    # -----------------------------------------------------------------------
    @app.post("/api/unanonymize")
    def unanon():
        if "file" not in request.files or "key" not in request.files:
            return jsonify({"error": "need file and key"}), 400
        in_f = request.files["file"]
        in_k = request.files["key"]
        if not in_f.filename or not in_k.filename:
            return jsonify({"error": "empty filename"}), 400

        safe_in = secure_filename(in_f.filename)
        safe_key = secure_filename(in_k.filename)
        upload_path = UPLOADS_DIR / safe_in
        if upload_path.exists():
            stem, ext = upload_path.stem, upload_path.suffix
            i = 1
            while upload_path.exists():
                upload_path = UPLOADS_DIR / f"{stem}_{i}{ext}"
                i += 1
        in_f.save(upload_path)
        key_path = UPLOADS_DIR / safe_key
        if key_path.exists():
            stem, ext = key_path.stem, key_path.suffix
            i = 1
            while key_path.exists():
                key_path = UPLOADS_DIR / f"{stem}_{i}{ext}"
                i += 1
        in_k.save(key_path)

        try:
            payload = load_key_file(key_path)
            log.info(
                "unanonymize start: file=%s key=%s entities=%d",
                upload_path.name, key_path.name,
                len(payload.get("entity_registry") or {}),
            )
            out_path = unan.unanonymize_file(upload_path, payload)
        except Exception as exc:
            log.error("unanonymize failed: %s", exc)
            return jsonify({"error": str(exc)}), 500
        finally:
            for tmp in (upload_path, key_path):
                try:
                    if tmp.exists():
                        tmp.unlink()
                except OSError:
                    pass

        log.info("unanonymize complete: out=%s", out_path.name)
        return jsonify({
            "output_filename": out_path.name,
            "download_url": f"/api/unanonymize/download/{out_path.name}",
        })

    @app.get("/api/unanonymize/download/<name>")
    def unanon_download(name: str):
        safe = secure_filename(name)
        target = OUTPUT_DIR / safe
        if not target.exists() or OUTPUT_DIR not in target.parents:
            abort(404)
        return send_file(target, as_attachment=True)

    # -----------------------------------------------------------------------
    # Keys + log tail
    # -----------------------------------------------------------------------
    @app.get("/api/keys")
    def keys_index():
        return jsonify({"keys": list_recent(50)})

    @app.get("/api/log/tail")
    def log_tail():
        n = int(request.args.get("n", "100"))
        n = max(1, min(n, 500))
        if not LOG_FILE.exists():
            return jsonify({"lines": []})
        # Tail the file. For local logs this is fast enough.
        with LOG_FILE.open("r", encoding="utf-8", errors="replace") as f:
            tail = f.readlines()[-n:]
        return jsonify({"lines": [ln.rstrip("\n") for ln in tail]})

    return app


def _preview_payload(sess) -> dict:
    """Build the preview body for the UI - first 10,000 chars + spans."""
    if sess.registry is None or sess.extract is None:
        return {}
    text = sess.extract.text
    truncated = False
    head = text
    if len(head) > 10_000:
        head = head[:10_000]
        truncated = True
    spans: list[dict] = []
    # Build span list using the longest-first ordered map so positions reflect
    # real replacements.
    rmap = sess.registry.as_replacement_map()
    for original, placeholder in rmap.items():
        if not original:
            continue
        idx = 0
        while True:
            found = head.find(original, idx)
            if found == -1:
                break
            spans.append({
                "start": found,
                "end": found + len(original),
                "original": original,
                "placeholder": placeholder,
            })
            idx = found + len(original)
    spans.sort(key=lambda s: s["start"])
    return {
        "text_head": head,
        "char_count": len(text),
        "truncated": truncated,
        "spans": spans,
        "counts": sess.registry.counts_per_type(),
        "entities": sess.registry.total_entities(),
        "replacements": sess.registry.total_replacements(),
    }


def _log_startup() -> None:
    eps = endpoints_mod.list_endpoints()
    active = endpoints_mod.get_active() or {}
    gh_count = len(github_mgr.list_connections())
    log.info(
        "startup: port=%d endpoints=%d active=%s github_connections=%d",
        PORT, len(eps), active.get("id"), gh_count,
    )


# Convenience entrypoint (`python -m app.server`).
app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=PORT, debug=False)
