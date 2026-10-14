"""pytest setup.

`pytest` runs the offline suite: free, no network, a few seconds per file.
`pytest --live` also runs the tests that call Groq or fetch live job boards
(they cost a fraction of a cent and need GROQ_API_KEY in .env).
"""
import os

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def pytest_addoption(parser):
    parser.addoption("--live", action="store_true",
                     help="also run tests that call the model or the network")


def pytest_configure(config):
    config.addinivalue_line("markers", "live: calls the model or the network")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="needs --live (calls the model or the network)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
