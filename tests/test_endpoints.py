"""LLM endpoint manager tests."""

import json

import pytest


def test_default_state_when_no_file():
    from app import endpoints as ep
    eps = ep.list_endpoints()
    assert len(eps) == 1
    assert eps[0]["nickname"]


def test_add_update_delete():
    from app import endpoints as ep
    saved = ep.add_or_update({
        "nickname": "LM Studio",
        "base_url": "http://localhost:1234",
        "api_style": "openai",
        "model": "mistral",
        "chunk_tokens": 1500,
    })
    assert saved["id"]
    eps = ep.list_endpoints()
    assert any(e["id"] == saved["id"] for e in eps)

    # update
    updated = ep.add_or_update({**saved, "model": "mixtral"})
    assert updated["id"] == saved["id"]
    assert ep.get(saved["id"])["model"] == "mixtral"

    assert ep.delete(saved["id"]) is True
    assert ep.get(saved["id"]) is None
    assert ep.delete("does-not-exist") is False


def test_validation_rejects_bad_input():
    from app import endpoints as ep
    with pytest.raises(ValueError):
        ep.add_or_update({"nickname": "x"})  # missing fields
    with pytest.raises(ValueError):
        ep.add_or_update({
            "nickname": "x", "base_url": "http://localhost", "api_style": "weird", "model": "m"
        })
    with pytest.raises(ValueError):
        ep.add_or_update({
            "nickname": "x", "base_url": "not-a-url", "api_style": "ollama", "model": "m"
        })


def test_is_local_url_localhost():
    from app.endpoints import is_local_url
    assert is_local_url("http://localhost:11434")
    assert is_local_url("http://127.0.0.1")
    assert is_local_url("http://192.168.1.5:11434")
    assert is_local_url("http://10.0.0.5")
    assert is_local_url("http://172.16.5.5")


def test_is_local_url_public_ip_rejected():
    from app.endpoints import is_local_url
    assert not is_local_url("http://8.8.8.8")
    # A public hostname like example.com should resolve to non-local;
    # we accept "False" if DNS unavailable (offline test boxes).
    # We don't assert positively here to avoid network flake.


def test_set_last_used():
    from app import endpoints as ep
    s = ep.add_or_update({
        "nickname": "X", "base_url": "http://localhost:1", "api_style": "ollama", "model": "m"
    })
    assert ep.set_last_used(s["id"])
    active = ep.get_active()
    assert active["id"] == s["id"]
    assert ep.set_last_used("does-not-exist") is False


def test_health_check_handles_unreachable():
    from app import endpoints as ep
    result = ep.health_check({
        "id": "x",
        "api_style": "ollama",
        "base_url": "http://127.0.0.1:1",  # nothing listening
    })
    assert result["status"] == "err"


def test_atomic_write_no_partial_files(tmp_path):
    from app import endpoints as ep
    ep.add_or_update({
        "nickname": "Y", "base_url": "http://localhost:1", "api_style": "ollama", "model": "m"
    })
    # No .tmp file should remain after a clean save.
    assert not list(tmp_path.glob("*.tmp"))
    # The endpoints file is valid JSON.
    state = json.loads((tmp_path / "endpoints.json").read_text())
    assert "endpoints" in state
