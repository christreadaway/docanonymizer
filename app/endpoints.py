"""LLM endpoint manager.

Persists configured endpoints to `endpoints.json`. Validates that base URLs
are localhost or RFC1918 private addresses (PRD 7.2 non-local guard).
Health-checks endpoints in parallel.

Public surface:
    list_endpoints() -> list[dict]
    save_endpoints(endpoints, last_used) -> None
    add_or_update(endpoint) -> dict
    delete(endpoint_id) -> bool
    get(endpoint_id) -> dict | None
    set_last_used(endpoint_id) -> None
    is_local_url(url) -> bool
    health_check(endpoint) -> {"status": "ok"|"err", "model_count": int|None, ...}
    health_check_all() -> list[dict]
"""

from __future__ import annotations

import ipaddress
import json
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable, Optional
from urllib.parse import urlparse

import requests

from .config import (
    CHUNK_TOKENS,
    DEFAULT_ENDPOINT_STYLE,
    DEFAULT_ENDPOINT_URL,
    DEFAULT_MODEL,
    ENDPOINTS_FILE,
)
from .logging_setup import get_logger

log = get_logger("endpoints")

_LOCK = threading.Lock()
_HEALTH_TIMEOUT = 2.5


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _empty_state() -> dict:
    """Default state: a single Ollama endpoint pointed at localhost."""
    eid = "default-ollama"
    return {
        "last_used": eid,
        "endpoints": [
            {
                "id": eid,
                "nickname": "Ollama (local)",
                "base_url": DEFAULT_ENDPOINT_URL,
                "api_style": DEFAULT_ENDPOINT_STYLE,
                "model": DEFAULT_MODEL,
                "chunk_tokens": CHUNK_TOKENS,
            }
        ],
    }


def _read() -> dict:
    if not ENDPOINTS_FILE.exists():
        return _empty_state()
    try:
        with ENDPOINTS_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        log.error("endpoints.json unreadable, falling back to defaults: %s", exc)
        return _empty_state()
    if not isinstance(data, dict) or "endpoints" not in data:
        return _empty_state()
    return data


def _write(state: dict) -> None:
    with _LOCK:
        tmp = ENDPOINTS_FILE.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        tmp.replace(ENDPOINTS_FILE)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def list_endpoints() -> list[dict]:
    return list(_read()["endpoints"])


def get(endpoint_id: str) -> Optional[dict]:
    for ep in list_endpoints():
        if ep.get("id") == endpoint_id:
            return ep
    return None


def get_active() -> Optional[dict]:
    state = _read()
    last = state.get("last_used")
    if last:
        for ep in state["endpoints"]:
            if ep.get("id") == last:
                return ep
    return state["endpoints"][0] if state["endpoints"] else None


def set_last_used(endpoint_id: str) -> bool:
    state = _read()
    if not any(e.get("id") == endpoint_id for e in state["endpoints"]):
        return False
    state["last_used"] = endpoint_id
    _write(state)
    log.info("active endpoint set: id=%s", endpoint_id)
    return True


def add_or_update(payload: dict) -> dict:
    """Insert or update an endpoint. Returns the saved record.

    Raises ValueError on invalid input.
    """
    _validate_payload(payload)

    state = _read()
    eid = payload.get("id") or _new_id(payload["nickname"], state["endpoints"])
    record = {
        "id": eid,
        "nickname": payload["nickname"].strip(),
        "base_url": payload["base_url"].strip().rstrip("/"),
        "api_style": payload["api_style"],
        "model": payload["model"].strip(),
        "chunk_tokens": int(payload.get("chunk_tokens") or CHUNK_TOKENS),
        "allow_nonlocal": bool(payload.get("allow_nonlocal")),
    }
    if record["allow_nonlocal"]:
        log.warning("NON-LOCAL endpoint saved: id=%s - document text will "
                    "leave this machine when it is used", eid)

    found = False
    for i, ep in enumerate(state["endpoints"]):
        if ep.get("id") == eid:
            state["endpoints"][i] = record
            found = True
            break
    if not found:
        state["endpoints"].append(record)

    if not state.get("last_used"):
        state["last_used"] = eid

    _write(state)
    log.info(
        "endpoint saved: id=%s style=%s model=%s",
        record["id"], record["api_style"], record["model"],
    )
    return record


def delete(endpoint_id: str) -> bool:
    state = _read()
    before = len(state["endpoints"])
    state["endpoints"] = [e for e in state["endpoints"] if e.get("id") != endpoint_id]
    if len(state["endpoints"]) == before:
        return False
    if state.get("last_used") == endpoint_id:
        state["last_used"] = state["endpoints"][0]["id"] if state["endpoints"] else None
    _write(state)
    log.info("endpoint deleted: id=%s", endpoint_id)
    return True


# ---------------------------------------------------------------------------
# Validation - local-only guard
# ---------------------------------------------------------------------------

def _validate_payload(p: dict) -> None:
    required = ("nickname", "base_url", "api_style", "model")
    for k in required:
        if not p.get(k):
            raise ValueError(f"missing field: {k}")
    if p["api_style"] not in ("ollama", "openai"):
        raise ValueError("api_style must be 'ollama' or 'openai'")
    parsed = urlparse(p["base_url"])
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("base_url must be a valid http(s) URL")
    # Server-side locality guard (CODE_REVIEW H2). The browser warning alone
    # is not enforcement - anything that hits this API directly must make the
    # same explicit choice.
    if not is_local_url(p["base_url"]) and not p.get("allow_nonlocal"):
        raise ValueError(
            "base_url is not a local address. This app only sends document "
            "text to local LLM endpoints. To save a non-local endpoint anyway, "
            "re-submit with allow_nonlocal=true - document text WILL leave "
            "this machine."
        )


def is_local_url(url: str) -> bool:
    """True if `url` resolves to localhost, link-local, or an RFC1918 range."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if not host:
        return False
    if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return True
    # Bare IP literal? Skip DNS.
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_loopback or ip.is_private or ip.is_link_local
    except ValueError:
        pass
    # Hostname - resolve. Best effort; on failure, treat as non-local.
    try:
        addrs = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for fam, _, _, _, sockaddr in addrs:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if not (ip.is_loopback or ip.is_private or ip.is_link_local):
            return False
    return True


# ---------------------------------------------------------------------------
# Health checks
# ---------------------------------------------------------------------------

def health_check(endpoint: dict) -> dict:
    """Reach the endpoint and report status."""
    started = time.monotonic()
    style = endpoint.get("api_style")
    base = endpoint.get("base_url", "").rstrip("/")
    if style == "ollama":
        url = f"{base}/api/tags"
    elif style == "openai":
        url = f"{base}/v1/models"
    else:
        return {"id": endpoint.get("id"), "status": "err", "error": "unknown api_style"}

    try:
        resp = requests.get(url, timeout=_HEALTH_TIMEOUT)
        elapsed = time.monotonic() - started
        if resp.status_code != 200:
            return {
                "id": endpoint.get("id"), "status": "err",
                "http_status": resp.status_code,
                "elapsed_ms": int(elapsed * 1000),
            }
        body = resp.json() if resp.content else {}
        if style == "ollama":
            count = len(body.get("models") or [])
        else:
            count = len(body.get("data") or [])
        return {
            "id": endpoint.get("id"), "status": "ok",
            "model_count": count, "elapsed_ms": int(elapsed * 1000),
        }
    except requests.RequestException as exc:
        return {"id": endpoint.get("id"), "status": "err", "error": type(exc).__name__}


def health_check_all() -> list[dict]:
    eps = list_endpoints()
    if not eps:
        return []
    out: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(len(eps), 4)) as ex:
        futs = {ex.submit(health_check, ep): ep for ep in eps}
        for fut in as_completed(futs):
            out.append(fut.result())
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new_id(nickname: str, existing: Iterable[dict]) -> str:
    seen = {e.get("id") for e in existing}
    base = "".join(c.lower() if c.isalnum() else "-" for c in nickname).strip("-")
    base = base or f"ep-{uuid.uuid4().hex[:6]}"
    candidate = base
    n = 2
    while candidate in seen:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def ensure_initialized() -> None:
    """Write the default endpoints file on first run if it does not exist."""
    if not ENDPOINTS_FILE.exists():
        _write(_empty_state())
