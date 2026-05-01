"""Flask route smoke tests using the test client.

These exercise wiring (status codes, JSON shapes) without a live LLM. The
detection routes are tested using a stubbed llm.llm_call.
"""

from __future__ import annotations

import io
import json
from unittest.mock import patch


def _client():
    from app.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_health():
    c = _client()
    r = c.get("/api/health")
    assert r.status_code == 200
    assert r.get_json() == {"status": "ok"}


def test_endpoints_crud_via_api():
    c = _client()
    # default state has one endpoint
    r = c.get("/api/endpoints")
    assert r.status_code == 200
    data = r.get_json()
    assert data["active"]
    assert len(data["endpoints"]) >= 1

    # add another
    r = c.post("/api/endpoints", json={
        "nickname": "LM Studio", "base_url": "http://localhost:1234",
        "api_style": "openai", "model": "mistral",
    })
    assert r.status_code == 200
    eid = r.get_json()["endpoint"]["id"]

    # select
    r = c.post(f"/api/endpoints/{eid}/select")
    assert r.status_code == 200

    # delete
    r = c.delete(f"/api/endpoints/{eid}")
    assert r.status_code == 204


def test_endpoints_invalid_payload():
    c = _client()
    r = c.post("/api/endpoints", json={"nickname": "x"})
    assert r.status_code == 400


def test_check_local_url():
    c = _client()
    r = c.post("/api/endpoints/check-local", json={"base_url": "http://localhost:11434"})
    assert r.get_json()["local"] is True
    r = c.post("/api/endpoints/check-local", json={"base_url": "http://8.8.8.8"})
    assert r.get_json()["local"] is False


def test_index_renders():
    c = _client()
    r = c.get("/")
    assert r.status_code == 200
    body = r.data.decode()
    assert "DOC-ANON" in body
    assert "[ ANONYMIZE ]" in body
    assert "[ UNANONYMIZE ]" in body
    assert "[ SANITIZE ALL ]" in body


def test_anonymize_upload_unsupported_type():
    c = _client()
    r = c.post("/api/anonymize/upload", data={
        "file": (io.BytesIO(b"x"), "x.exe"),
        "tags": "ALL",
    }, content_type="multipart/form-data")
    assert r.status_code == 400


def test_anonymize_upload_then_status_with_mocked_llm(tmp_path):
    """Full pipeline up to detection, with the LLM stubbed."""
    from app import detector

    fake = json.dumps([{"text": "Jane Smith", "type": "PERSON", "linked_to": None}])

    c = _client()
    with patch.object(detector.llm, "llm_call", return_value=fake):
        # Upload a tiny txt file.
        r = c.post("/api/anonymize/upload", data={
            "file": (io.BytesIO(b"Jane Smith hello"), "memo.txt"),
            "tags": "ALL",
        }, content_type="multipart/form-data")
        assert r.status_code == 200
        sid = r.get_json()["session_id"]

        # Spin briefly waiting for the worker thread.
        import time
        for _ in range(60):
            s = c.get(f"/api/anonymize/{sid}/status").get_json()
            if s.get("detection_complete"):
                break
            time.sleep(0.05)
        assert s["detection_complete"]
        assert s["preview"]["entities"] == 1
        assert s["preview"]["replacements"] == 1


def test_anonymize_full_flow_with_mocked_llm():
    """Upload -> detect -> confirm -> verify -> download."""
    import time
    from app import detector

    fake = json.dumps([{"text": "Jane Smith", "type": "PERSON", "linked_to": None}])

    c = _client()
    with patch.object(detector.llm, "llm_call", return_value=fake):
        r = c.post("/api/anonymize/upload", data={
            "file": (io.BytesIO(b"Hello Jane Smith"), "memo.txt"),
            "tags": "ALL",
        }, content_type="multipart/form-data")
        sid = r.get_json()["session_id"]

        for _ in range(60):
            s = c.get(f"/api/anonymize/{sid}/status").get_json()
            if s.get("detection_complete"):
                break
            time.sleep(0.05)

        c.post(f"/api/anonymize/{sid}/confirm", json={"deselected": []})

        for _ in range(60):
            r = c.get(f"/api/anonymize/{sid}/results").get_json()
            if r.get("verify_result"):
                break
            time.sleep(0.05)
        assert r["verify_result"]["passed"] is True
        assert r["output_filename"]
        assert r["key_filename"]
        # download path
        r2 = c.get(f"/api/anonymize/{sid}/download/file")
        assert r2.status_code == 200
        # the response body should contain the placeholder
        assert b"[PERSON_" in r2.data
        # key download
        r3 = c.get(f"/api/anonymize/{sid}/download/key")
        assert r3.status_code == 200
        key = json.loads(r3.data)
        assert "replacement_map" in key


def test_unanonymize_via_api():
    """Roundtrip: anonymize text via backend helpers, then unanonymize via the API."""
    import io as _io
    from app.extractors import extract
    from app.scrubber import scrub_text
    from app.config import OUTPUT_DIR

    # Manually anonymize a tiny text file using the underlying helpers.
    src = OUTPUT_DIR.parent / "uploads" / "memo.txt"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("Jane Smith hello")
    rmap = {"Jane Smith": "[PERSON_3A4F]"}
    anon = OUTPUT_DIR / "memo_anon.txt"
    scrub_text(extract(src), rmap, anon)

    key_payload = {
        "session_id": "abcd1234",
        "original_filename": "memo.txt",
        "replacement_map": rmap,
    }
    key_bytes = json.dumps(key_payload).encode("utf-8")

    c = _client()
    r = c.post("/api/unanonymize", data={
        "file": (_io.BytesIO(anon.read_bytes()), "memo_anon.txt"),
        "key": (_io.BytesIO(key_bytes), "memo_abcd1234.key.json"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["output_filename"]
    # download the restored file
    r2 = c.get(body["download_url"])
    assert r2.status_code == 200
    assert b"Jane Smith" in r2.data


def test_log_tail_empty_safe():
    c = _client()
    r = c.get("/api/log/tail")
    assert r.status_code == 200
    assert "lines" in r.get_json()


def test_github_redacts_token():
    c = _client()
    r = c.post("/api/github", json={
        "nickname": "demo",
        "repo": "octocat/hello-world",
        "branch": "main",
        "token": "ghp_secrettokenvalue",
    })
    assert r.status_code == 200
    saved = r.get_json()["connection"]
    assert saved["token"] == "***"
    assert saved["token_set"] is True

    # listing also redacts
    r2 = c.get("/api/github").get_json()
    for c_rec in r2["connections"]:
        assert c_rec.get("token") in (None, "***")


def test_anonymize_download_blocked_until_verified():
    """Force a verify failure and confirm the download endpoint refuses."""
    import time
    from app import detector, verifier
    from unittest.mock import patch as _patch

    fake = json.dumps([{"text": "Jane Smith", "type": "PERSON", "linked_to": None}])
    c = _client()

    # Make the verifier always fail.
    fail = verifier.VerifyResult(passed=False, total_matches=2, regex_match_types=["EMAIL"])
    with _patch.object(detector.llm, "llm_call", return_value=fake), \
         _patch("app.pipeline.verify_output", return_value=fail):
        r = c.post("/api/anonymize/upload", data={
            "file": (io.BytesIO(b"Hello Jane Smith"), "memo.txt"),
            "tags": "ALL",
        }, content_type="multipart/form-data")
        sid = r.get_json()["session_id"]
        for _ in range(60):
            s = c.get(f"/api/anonymize/{sid}/status").get_json()
            if s.get("detection_complete"):
                break
            time.sleep(0.05)
        c.post(f"/api/anonymize/{sid}/confirm", json={"deselected": []})
        for _ in range(60):
            res = c.get(f"/api/anonymize/{sid}/results").get_json()
            if res.get("verify_result"):
                break
            time.sleep(0.05)

        assert res["verify_result"]["passed"] is False
        # File download blocked
        dl = c.get(f"/api/anonymize/{sid}/download/file")
        assert dl.status_code == 403
        # Key download blocked too
        dlk = c.get(f"/api/anonymize/{sid}/download/key")
        assert dlk.status_code in (403, 404)
