"""LLM adapter layer.

All LLM I/O for the app goes through `llm_call(prompt, endpoint=...) -> str`.
Two API styles are supported per PRD 7.2:
  - "ollama":  POST {base_url}/api/generate
  - "openai":  POST {base_url}/v1/chat/completions
"""

from __future__ import annotations

import time
from typing import Optional

import requests

from . import endpoints as endpoints_mod
from .logging_setup import get_logger

log = get_logger("llm")

_DEFAULT_TIMEOUT = 120  # seconds; long-context calls can take a while


class LLMError(RuntimeError):
    """Raised when the active LLM endpoint returns an error or is unreachable."""


def llm_call(prompt: str, endpoint: Optional[dict] = None, timeout: int = _DEFAULT_TIMEOUT) -> str:
    """Send a prompt to the active (or supplied) endpoint and return the raw text response."""
    ep = endpoint or endpoints_mod.get_active()
    if ep is None:
        raise LLMError("no LLM endpoint configured")
    style = ep.get("api_style")
    base = (ep.get("base_url") or "").rstrip("/")
    model = ep.get("model")
    if not base or not model:
        raise LLMError("endpoint missing base_url or model")

    started = time.monotonic()
    if style == "ollama":
        text = _call_ollama(base, model, prompt, timeout)
    elif style == "openai":
        text = _call_openai(base, model, prompt, timeout)
    else:
        raise LLMError(f"unsupported api_style: {style}")

    elapsed = time.monotonic() - started
    log.debug(
        "llm_call complete: endpoint=%s style=%s tokens_in~%d t=%.2fs",
        ep.get("nickname"), style, len(prompt) // 4, elapsed,
    )
    return text


def _call_ollama(base: str, model: str, prompt: str, timeout: int) -> str:
    url = f"{base}/api/generate"
    body = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0},
    }
    try:
        resp = requests.post(url, json=body, timeout=timeout)
    except requests.RequestException as exc:
        raise LLMError(f"ollama request failed: {type(exc).__name__}") from exc
    if resp.status_code != 200:
        raise LLMError(f"ollama http {resp.status_code}")
    data = resp.json()
    out = data.get("response")
    if not isinstance(out, str):
        raise LLMError("ollama: missing 'response' field")
    return out


def _call_openai(base: str, model: str, prompt: str, timeout: int) -> str:
    url = f"{base}/v1/chat/completions"
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "stream": False,
    }
    try:
        resp = requests.post(url, json=body, timeout=timeout)
    except requests.RequestException as exc:
        raise LLMError(f"openai request failed: {type(exc).__name__}") from exc
    if resp.status_code != 200:
        raise LLMError(f"openai http {resp.status_code}")
    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("openai: malformed response") from exc
