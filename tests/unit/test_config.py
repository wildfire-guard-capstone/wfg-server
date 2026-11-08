"""DB URL: DATABASE_URL first, else built from POSTGRES_* (EC2 db.env), else local dev."""

import pytest

from app.core.config import LOCAL_DATABASE_URL, Settings

POSTGRES = {"POSTGRES_USER": "wfg", "POSTGRES_PASSWORD": "p@ss!w/rd", "POSTGRES_DB": "wfg"}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ["DATABASE_URL", *POSTGRES, "DB_HOST", "DB_PORT"]:
        monkeypatch.delenv(key, raising=False)


def settings() -> Settings:
    return Settings(_env_file=None)


def test_local_default():
    assert settings().database_url == LOCAL_DATABASE_URL


def test_built_from_postgres_env_and_quoted(monkeypatch):
    for key, value in POSTGRES.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DB_HOST", "wfg-db")
    assert settings().database_url == "postgresql+psycopg://wfg:p%40ss%21w%2Frd@wfg-db:5432/wfg"


def test_database_url_wins(monkeypatch):
    for key, value in POSTGRES.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://a:b@h:1/d")
    assert settings().database_url == "postgresql+psycopg://a:b@h:1/d"
