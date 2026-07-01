"""`sslmode=` -> asyncpg `ssl=` translation in build_async_engine.

pg8000/psycopg2 (and every managed-Postgres) DATABASE_URI carries libpq's
`sslmode=`, which asyncpg rejects. `_asyncpg_ssl_connect_args` translates it so
those URIs keep booting after the async flip.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import make_url

from api.database import _asyncpg_ssl_connect_args


def test_sslmode_require_translates_to_ssl_string() -> None:
    url = make_url("postgresql+asyncpg://u:p@host:5432/db?sslmode=require")
    new_url, connect_args = _asyncpg_ssl_connect_args(url)
    # asyncpg accepts the bare mode string for the non-verifying modes.
    assert connect_args == {"ssl": "require"}
    # ...and the param is stripped so it isn't also forwarded as `sslmode=`.
    assert "sslmode" not in new_url.query


def test_sslmode_absent_is_noop() -> None:
    url = make_url("postgresql+asyncpg://u:p@host:5432/db")
    new_url, connect_args = _asyncpg_ssl_connect_args(url)
    assert connect_args == {}
    assert new_url == url


def test_sslmode_ignored_for_non_asyncpg_driver() -> None:
    # sqlite (and any non-asyncpg driver) is left untouched.
    url = make_url("sqlite+aiosqlite://")
    _, connect_args = _asyncpg_ssl_connect_args(url)
    assert connect_args == {}


def test_verify_full_without_sslrootcert_raises() -> None:
    url = make_url("postgresql+asyncpg://u:p@host:5432/db?sslmode=verify-full")
    with pytest.raises(RuntimeError, match="sslrootcert"):
        _asyncpg_ssl_connect_args(url)


def test_verify_ca_with_half_specified_client_cert_raises(tmp_path: Path) -> None:
    # sslrootcert present (so we pass that check), but a client cert without its
    # key must fail loudly — the cert/key check runs before the CA is loaded, so
    # the file contents don't matter here.
    ca = tmp_path / "ca.pem"
    ca.write_text("presence is checked before the CA is loaded")
    url = make_url(f"postgresql+asyncpg://u:p@host:5432/db?sslmode=verify-ca&sslrootcert={ca}&sslcert=/tmp/client.crt")
    with pytest.raises(RuntimeError, match="sslcert and sslkey"):
        _asyncpg_ssl_connect_args(url)
