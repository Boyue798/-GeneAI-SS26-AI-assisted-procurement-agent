from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api import conversations
from api.auth import AuthUser
from api.conversations import ConversationRestore


class _FakeCursor:
    def __init__(self) -> None:
        self.executions: list[tuple[str, tuple[object, ...] | None]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def execute(self, statement: str, parameters: tuple[object, ...] | None = None) -> None:
        self.executions.append((statement, parameters))


class _FakeConnection:
    def __init__(self) -> None:
        self.cursor_instance = _FakeCursor()

    def cursor(self, *args, **kwargs) -> _FakeCursor:
        return self.cursor_instance

    def commit(self) -> None:
        return None

    def close(self) -> None:
        return None


class ConversationCriteriaTest(unittest.TestCase):
    def test_custom_criteria_survive_restore_serialization(self):
        restore = ConversationRestore(
            query="Find sustainable packaging suppliers in Poland",
            model="PKG-42",
            standards="ISO 14001",
            sourcingCriteria=[{"key": "environmental", "label": "Environmental standard", "weight": 70}],
        )
        snapshot = restore.model_dump()
        self.assertEqual(snapshot["model"], "PKG-42")
        self.assertEqual(snapshot["sourcingCriteria"][0]["key"], "environmental")

    def test_create_conversation_strips_nested_control_characters_before_jsonb_write(self):
        connection = _FakeConnection()
        request = conversations.NewConversation(
            module="sourcing",
            query="Industrial\x00 pump",
            filters={"target\x01Region": "Ger\x02many"},
            requestSnapshot={
                "evidence": {
                    "pdf\x03Text": "Flow rate\x00 100 L/min\x1f",
                },
            },
            resultsSnapshot=[
                {
                    "name": "Acme\x7f Pumps",
                    "evidence": [{"source\x04": "catalogue\x85 excerpt"}],
                },
            ],
        )
        user = AuthUser(
            email="buyer@example.com",
            name="Buyer",
            company="Example Co.",
            role="Procurement",
        )

        with (
            patch.object(conversations, "_db_conn", return_value=connection),
            patch.object(conversations, "_ensure_table"),
            patch.object(conversations, "Json", side_effect=lambda value: value),
        ):
            record = asyncio.run(conversations.create_conversation(request, user))

        statement, parameters = connection.cursor_instance.executions[-1]
        self.assertIn("INSERT INTO conversation_history", statement)
        assert parameters is not None
        self._assert_no_control_characters(record.model_dump())
        self._assert_no_control_characters(parameters[4])  # filters
        self._assert_no_control_characters(parameters[6])  # request_snapshot
        self._assert_no_control_characters(parameters[7])  # results_snapshot
        self.assertEqual(parameters[4], {"targetRegion": "Germany"})
        self.assertEqual(parameters[6]["evidence"]["pdfText"], "Flow rate 100 L/min")
        self.assertEqual(parameters[7][0]["name"], "Acme Pumps")

    def test_database_connection_failure_uses_local_history_fallback(self):
        with (
            patch.dict("os.environ", {"DATABASE_URL": "postgresql://unreachable"}, clear=False),
            patch.object(conversations.psycopg2, "connect", side_effect=RuntimeError("offline")) as connect,
        ):
            self.assertIsNone(conversations._db_conn())

        connect.assert_called_once_with("postgresql://unreachable", connect_timeout=3)

    def _assert_no_control_characters(self, value: object) -> None:
        if isinstance(value, str):
            self.assertIsNone(conversations._JSON_CONTROL_CHARS.search(value))
        elif isinstance(value, dict):
            for key, child in value.items():
                self._assert_no_control_characters(key)
                self._assert_no_control_characters(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                self._assert_no_control_characters(child)


if __name__ == "__main__":
    unittest.main()
