"""Shared pytest fixtures.

Each test runs against an isolated temp ROOT so endpoints.json / github.json
/ uploads / output / keys don't leak between cases.
"""

from __future__ import annotations

import importlib
import sys
import threading
import time

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

    # Pipeline workers run in daemon threads. Let stragglers finish before the
    # next test tears down / reloads the app modules under them.
    main = threading.main_thread()
    deadline = time.monotonic() + 2.0
    for t in threading.enumerate():
        if t is not main and t.daemon and t.is_alive():
            t.join(timeout=max(0.0, deadline - time.monotonic()))
