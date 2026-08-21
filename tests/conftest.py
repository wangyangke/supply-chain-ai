"""Shared fixtures for the test suite.

The tests run against the *committed* dataset snapshot (data/), which is
exactly what a reviewer would see in the repo. `SCR_DATA_DIR` is pinned to
the absolute data path so tests are immune to the process cwd.
"""

import os
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"

os.environ["SCR_DATA_DIR"] = str(DATA_DIR)


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return DATA_DIR


@pytest.fixture(scope="session")
def store():
    from src.store import Store

    return Store.load(str(DATA_DIR))


@pytest.fixture(scope="session")
def dataset(store):
    return store.dataset


@pytest.fixture(scope="session")
def client():
    """FastAPI TestClient bound to the committed dataset."""
    from fastapi.testclient import TestClient

    from src.api import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def runner():
    from typer.testing import CliRunner

    return CliRunner()
