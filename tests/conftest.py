"""Shared fixtures.

Settings are faked through environment variables so no `.env` is needed to run the unit tests.
Database tests use DATABASE_URL (default: the local Docker instance from the README) and are
skipped when that database is unreachable or does not have the schema applied.
"""

import asyncio
import os
import selectors
import sys

import psycopg
import pytest
from psycopg.rows import dict_row

FAKE_ENV = {
    "WA_VERIFY_TOKEN": "verify-token",
    "WA_APP_SECRET": "app-secret",
    "WA_ACCESS_TOKEN": "access-token",
    "WA_PHONE_NUMBER_ID": "123456",
    "WA_API_VERSION": "v23.0",
    "PII_HMAC_KEY": "test-hmac-key-0123456789abcdef0123456789abcdef",
}
TEST_DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/abundancia"
)


def pytest_configure(config):
    for key, value in FAKE_ENV.items():
        os.environ.setdefault(key, value)
    os.environ.setdefault("DATABASE_URL", TEST_DATABASE_URL)


@pytest.fixture
def anyio_backend():
    if sys.platform == "win32":
        # psycopg async cannot run on Windows' default ProactorEventLoop.
        factory = lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())  # noqa: E731
        return "asyncio", {"loop_factory": factory}
    return "asyncio"


async def _connect() -> psycopg.AsyncConnection:
    try:
        connection = await psycopg.AsyncConnection.connect(
            TEST_DATABASE_URL, row_factory=dict_row, connect_timeout=3
        )
    except psycopg.OperationalError as e:
        pytest.skip(f"database not reachable at {TEST_DATABASE_URL}: {e}")
    cur = await connection.execute("select to_regclass('conversations') as t")
    if (await cur.fetchone())["t"] is None:
        await connection.close()
        pytest.skip("schema not applied; run db/migrations/001_initial.sql")
    return connection


@pytest.fixture
async def conn():
    """One connection per test. Whatever is left uncommitted at the end is rolled back."""
    connection = await _connect()
    try:
        yield connection
    finally:
        await connection.rollback()
        await connection.close()


@pytest.fixture
async def other_conn():
    """A second, independent connection for tests that exercise concurrency."""
    connection = await _connect()
    try:
        yield connection
    finally:
        await connection.rollback()
        await connection.close()
