# -*- coding: utf-8 -*-
import os
import unittest
from unittest.mock import patch

import call_status_store


class FakeCursor:
    rowcount = 1

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class FakeConnection:
    def __init__(self):
        self.statements = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        return FakeCursor()

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class CallStatusStoreTestCase(unittest.TestCase):
    def test_database_url_requires_postgres_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(call_status_store.database_url())
            self.assertEqual(call_status_store.database_backend(), "postgres")
            with self.assertRaisesRegex(RuntimeError, "PostgreSQL persistence is required"):
                call_status_store.require_database_url()

    def test_explicit_postgres_url_is_used(self):
        with patch.dict(
            os.environ,
            {"CALL_API_DATABASE_URL": "postgresql://test_user:test_password@localhost:5432/test_db"},
            clear=True,
        ):
            self.assertEqual(
                call_status_store.database_url(),
                "postgresql://test_user:test_password@localhost:5432/test_db",
            )

    def test_connect_uses_psycopg_and_initializes_postgres_schema(self):
        fake_conn = FakeConnection()
        with patch.dict(
            os.environ,
            {
                "CALL_API_DATABASE_URL": "postgresql://test_user:test_password@localhost:5432/test_db",
                "CALL_API_DB_CONNECT_TIMEOUT": "3",
            },
            clear=True,
        ), patch("psycopg.connect", return_value=fake_conn) as mock_connect:
            conn = call_status_store._connect()

        self.assertIs(conn, fake_conn)
        mock_connect.assert_called_once()
        _, kwargs = mock_connect.call_args
        self.assertEqual(kwargs["connect_timeout"], 3)
        self.assertGreaterEqual(len(fake_conn.statements), 3)
        self.assertIn("CREATE TABLE IF NOT EXISTS calls", fake_conn.statements[0][0])
        self.assertTrue(
            any("CREATE TABLE IF NOT EXISTS call_events" in sql for sql, _ in fake_conn.statements)
        )


if __name__ == "__main__":
    unittest.main()
