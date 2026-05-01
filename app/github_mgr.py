"""GitHub connection manager + push (PRD 4.10).

Stores connections in `github.json` (which `.gitignore` keeps out of source
control). Each connection: nickname, repo, branch, destination path, PAT.

Public surface mirrors the endpoints module: list/get/add_or_update/delete.
Plus `test_connection` and `push_file`.
"""

from __future__ import annotations

import base64
import json
import threading
import uuid
from pathlib import Path
from typing import Optional

import requests

from .config import GITHUB_API_URL, GITHUB_FILE
from .logging_setup import get_logger

log = get_logger("github")

_LOCK = threading.Lock()
_TIMEOUT = 15


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _empty_state() -> dict:
    return {"connections": []}


def _read() -> dict:
    if not GITHUB_FILE.exists():
        return _empty_state()
    try:
        with GITHUB_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        log.error("github.json unreadable: %s", exc)
        return _empty_state()
    if not isinstance(data, dict) or "connections" not in data:
        return _empty_state()
    return data


def _write(state: dict) -> None:
    with _LOCK:
        tmp = GITHUB_FILE.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        tmp.replace(GITHUB_FILE)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def list_connections(redact: bool = True) -> list[dict]:
    """Return a copy. With `redact=True`, the PAT is masked - this is what
    the UI gets."""
    out = []
    for conn in _read()["connections"]:
        copy = dict(conn)
        if redact and "token" in copy and copy["token"]:
            copy["token"] = "***"
            copy["token_set"] = True
        out.append(copy)
    return out


def get(conn_id: str, redact: bool = False) -> Optional[dict]:
    for conn in _read()["connections"]:
        if conn.get("id") == conn_id:
            copy = dict(conn)
            if redact and copy.get("token"):
                copy["token"] = "***"
                copy["token_set"] = True
            return copy
    return None


def add_or_update(payload: dict) -> dict:
    _validate(payload)
    state = _read()
    cid = payload.get("id") or _new_id(payload["nickname"], state["connections"])
    existing = next((c for c in state["connections"] if c.get("id") == cid), None)

    record = {
        "id": cid,
        "nickname": payload["nickname"].strip(),
        "repo": payload["repo"].strip(),
        "branch": (payload.get("branch") or "main").strip(),
        "path": (payload.get("path") or "").strip(),
        "token": payload.get("token") or (existing.get("token") if existing else ""),
    }
    if existing:
        for i, c in enumerate(state["connections"]):
            if c.get("id") == cid:
                state["connections"][i] = record
                break
    else:
        state["connections"].append(record)

    _write(state)
    log.info(
        "github connection saved: id=%s repo=%s branch=%s",
        record["id"], record["repo"], record["branch"],
    )
    redacted = dict(record)
    redacted["token"] = "***" if redacted["token"] else ""
    redacted["token_set"] = bool(record["token"])
    return redacted


def delete(conn_id: str) -> bool:
    state = _read()
    before = len(state["connections"])
    state["connections"] = [c for c in state["connections"] if c.get("id") != conn_id]
    if len(state["connections"]) == before:
        return False
    _write(state)
    log.info("github connection deleted: id=%s", conn_id)
    return True


# ---------------------------------------------------------------------------
# Test + push
# ---------------------------------------------------------------------------

def test_connection(conn: dict) -> dict:
    """`GET /repos/{owner}/{repo}` to verify the PAT and repo access."""
    repo = conn.get("repo", "")
    token = conn.get("token", "")
    if "/" not in repo or not token:
        return {"status": "err", "error": "missing repo or token"}

    url = f"{GITHUB_API_URL}/repos/{repo}"
    headers = _auth_headers(token)
    try:
        resp = requests.get(url, headers=headers, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        return {"status": "err", "error": type(exc).__name__}
    if resp.status_code != 200:
        return {"status": "err", "http_status": resp.status_code}
    body = resp.json()
    return {
        "status": "ok",
        "repo": body.get("full_name"),
        "default_branch": body.get("default_branch"),
        "private": body.get("private"),
    }


def push_file(conn: dict, file_path: Path, dest_path: Optional[str] = None,
              branch: Optional[str] = None, commit_message: Optional[str] = None) -> dict:
    """`PUT /repos/{owner}/{repo}/contents/{path}` upload."""
    repo = conn.get("repo", "")
    token = conn.get("token", "")
    branch = branch or conn.get("branch") or "main"
    rel_path = (dest_path or conn.get("path") or "").strip("/")
    target = f"{rel_path}/{file_path.name}" if rel_path else file_path.name

    url = f"{GITHUB_API_URL}/repos/{repo}/contents/{target}"
    headers = _auth_headers(token)

    # Look up existing SHA so we can update rather than 422 on conflict.
    existing_sha = None
    try:
        head = requests.get(url, headers=headers, params={"ref": branch}, timeout=_TIMEOUT)
        if head.status_code == 200:
            existing_sha = head.json().get("sha")
    except requests.RequestException:
        pass

    content = file_path.read_bytes()
    body = {
        "message": commit_message or f"Anonymized upload: {file_path.name}",
        "content": base64.b64encode(content).decode("ascii"),
        "branch": branch,
    }
    if existing_sha:
        body["sha"] = existing_sha

    log.info(
        "github push initiated: repo=%s branch=%s path=%s size=%d",
        repo, branch, target, len(content),
    )
    try:
        resp = requests.put(url, headers=headers, json=body, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        log.error("github push failed: %s", type(exc).__name__)
        return {"status": "err", "error": type(exc).__name__}

    if resp.status_code not in (200, 201):
        log.error("github push http %s", resp.status_code)
        return {"status": "err", "http_status": resp.status_code,
                "message": (resp.json() or {}).get("message")}

    payload = resp.json()
    sha = (payload.get("content") or {}).get("sha")
    html_url = (payload.get("content") or {}).get("html_url")
    log.info("github push success: sha=%s", sha)
    return {"status": "ok", "sha": sha, "html_url": html_url, "path": target, "branch": branch}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate(p: dict) -> None:
    for k in ("nickname", "repo"):
        if not p.get(k):
            raise ValueError(f"missing field: {k}")
    if "/" not in p["repo"]:
        raise ValueError("repo must be 'owner/name' format")


def _new_id(nickname: str, existing) -> str:
    seen = {c.get("id") for c in existing}
    base = "".join(c.lower() if c.isalnum() else "-" for c in nickname).strip("-")
    base = base or f"gh-{uuid.uuid4().hex[:6]}"
    candidate = base
    n = 2
    while candidate in seen:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def _auth_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "doc-anonymizer",
        "X-GitHub-Api-Version": "2022-11-28",
    }
