"""Unit test fixtures — all external dependencies mocked."""

from unittest.mock import MagicMock, AsyncMock

import pytest


@pytest.fixture
def mock_conn():
    """Mock psycopg.Connection with cursor context manager."""
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    cursor.fetchall.return_value = []
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    conn._cursor = cursor  # easy access in tests
    return conn
