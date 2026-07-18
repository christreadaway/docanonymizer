"""Detector tests with a mocked LLM call."""

from __future__ import annotations

import json
from unittest.mock import patch


def test_extract_json_array_direct():
    from app.detector import _extract_json_array
    assert _extract_json_array('[{"text":"x","type":"PERSON"}]') == [{"text": "x", "type": "PERSON"}]


def test_extract_json_array_in_fence():
    from app.detector import _extract_json_array
    raw = "Sure, here:\n```json\n[{\"text\":\"x\",\"type\":\"PERSON\"}]\n```"
    out = _extract_json_array(raw)
    assert out == [{"text": "x", "type": "PERSON"}]


def test_extract_json_array_with_prose():
    from app.detector import _extract_json_array
    raw = "Some preamble: [{\"text\":\"x\",\"type\":\"EMAIL\"}] then more"
    assert _extract_json_array(raw) == [{"text": "x", "type": "EMAIL"}]


def test_extract_json_array_empty():
    from app.detector import _extract_json_array
    assert _extract_json_array("") == []
    assert _extract_json_array("nothing here") == []


def test_detect_pii_uses_llm_call_and_builds_registry():
    """End-to-end with a stubbed `llm_call`. No network."""
    from app import detector

    fake_response = json.dumps([
        {"text": "Jane Smith", "type": "PERSON", "linked_to": None},
        {"text": "jane@x.org", "type": "EMAIL", "linked_to": "Jane Smith"},
    ])

    with patch.object(detector.llm, "llm_call", return_value=fake_response):
        # Fake an active endpoint so detect_pii can be called.
        with patch.object(detector.endpoints_mod, "get_active",
                          return_value={"chunk_tokens": 2000, "api_style": "ollama",
                                        "base_url": "http://localhost:1", "model": "m",
                                        "nickname": "test"}):
            r = detector.detect_pii("Jane Smith met jane@x.org")
    assert r.total_replacements() == 2
    # entity linking applied: same hex_id for both PERSON and EMAIL
    hex_ids = {ph.split("_", 1)[1].rstrip("]") for ph in r.replacements.values()}
    assert len(hex_ids) == 1


def test_detector_retries_transient_llm_error():
    """A transient chunk failure is retried and the run completes."""
    from unittest.mock import patch as _patch
    from app import detector

    call_count = {"n": 0}

    def flaky(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise detector.llm.LLMError("boom")
        return json.dumps([{"text": "Jane Smith", "type": "PERSON"}])

    with patch.object(detector.llm, "llm_call", side_effect=flaky), \
         _patch.object(detector.time, "sleep"):
        with patch.object(detector.endpoints_mod, "get_active",
                          return_value={"chunk_tokens": 200, "api_style": "ollama",
                                        "base_url": "http://localhost:1", "model": "m",
                                        "nickname": "test"}):
            r = detector.detect_pii("Jane Smith. " * 1000)
    assert r.total_replacements() >= 1
    assert call_count["n"] >= 2  # the failed attempt was retried


def test_detector_aborts_when_chunk_permanently_fails():
    """CODE_REVIEW C2: an unscannable chunk must fail the run, never skip.

    Silently skipping a chunk means PII in it ships unscrubbed - names and
    addresses have no regex safety net.
    """
    import pytest
    from unittest.mock import patch as _patch
    from app import detector

    def always_fail(*args, **kwargs):
        raise detector.llm.LLMError("endpoint down")

    progress = []
    with patch.object(detector.llm, "llm_call", side_effect=always_fail), \
         _patch.object(detector.time, "sleep"):
        with patch.object(detector.endpoints_mod, "get_active",
                          return_value={"chunk_tokens": 2000, "api_style": "ollama",
                                        "base_url": "http://localhost:1", "model": "m",
                                        "nickname": "test"}):
            with pytest.raises(detector.llm.LLMError, match="could not be scanned"):
                detector.detect_pii("Jane Smith met Bob.", on_chunk=progress.append)
    # The chunk error was surfaced to the progress stream before aborting.
    assert any(p.get("error") for p in progress)
