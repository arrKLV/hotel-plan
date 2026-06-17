"""Pytest-конфигурация: корень проекта в sys.path + временная БД для тестов storage."""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from app import storage  # noqa: E402


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Изолированная SQLite-БД на каждый тест (storage._conn читает config.DB_PATH динамически)."""
    db = tmp_path / "test.db"
    monkeypatch.setattr(config, "DB_PATH", str(db))
    storage.init_db()
    yield db
