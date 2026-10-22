"""Shared pytest setup: settings from .env.example plus a fake key (the core needs one), and fixtures for the store
tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def _load_example_settings() -> None:
    """Set every setting from the repo's .env.example, so tests never depend on a personal .env."""
    example = Path(__file__).resolve().parents[2] / ".env.example"
    for line in example.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            os.environ[name.strip()] = value.strip()


_load_example_settings()
os.environ["MEANWHILE_KEY"] = "test-key-not-real"

from meanwhile.store import Store  # noqa: E402


@pytest.fixture
def store(tmp_path) -> Store:
    """A throwaway store in a temporary folder."""
    return Store(tmp_path / "data")
