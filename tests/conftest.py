"""Shared pytest fixtures.

Each test runs against an isolated temp ROOT so endpoints.json / github.json
/ uploads / output / keys don't leak between cases.
"""

from __future__ import annotations

import importlib
import sys

import pytest


@pytest.fixture(autouse=True)
def isolated_root(tmp_path, monkeypatch):
    """Point the app at a fresh ROOT for every test."""
    monkeypatch.setenv("DOCANON_ROOT", str(tmp_path))
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    # Drop any cached app modules so they pick up the new env on reimport.
    for mod in list(sys.modules):
        if mod == "app" or mod.startswith("app."):
            del sys.modules[mod]

    yield tmp_path
