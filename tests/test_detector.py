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


def test_detector_recovers_from_llm_error():
    """A single failing chunk should not abort the whole pass."""
    from app import detector

    call_count = {"n": 0}

    def flaky(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise detector.llm.LLMError("boom")
        return json.dumps([{"text": "Jane Smith", "type": "PERSON"}])

    with patch.object(detector.llm, "llm_call", side_effect=flaky):
        with patch.object(detector.endpoints_mod, "get_active",
                          return_value={"chunk_tokens": 200, "api_style": "ollama",
                                        "base_url": "http://localhost:1", "model": "m",
                                        "nickname": "test"}):
            # Force chunking by handing in big text
            r = detector.detect_pii("Jane Smith. " * 1000)
    # After the first chunk fails, subsequent chunks still produce output.
    assert r.total_replacements() >= 1
