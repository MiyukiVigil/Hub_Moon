"""Make the repo importable without installing it, and keep tests off the network.

The GUI package imports slint, which is an optional extra — anything that needs it
is skipped rather than failed, so `pytest` is useful on a machine with only the CLI
dependencies (which is what the CI matrix runs on for the older Pythons).
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """No test may reach the internet. A suite that quietly depends on a live
    endpoint is a suite that goes red when somebody else's server does."""
    import urllib.request

    def refuse(*a, **k):
        raise AssertionError("a test tried to open a network connection")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)


@pytest.fixture
def sample_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
