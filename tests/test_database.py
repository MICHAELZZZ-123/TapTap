from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

from database import EventDB, _next_date


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = EventDB(Path(self.temp_dir.name) / "events.db")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def add_event(self, **overrides) -> int:
        values = {
            "name": "Planning",
            "event_date": "2030-01-31",
            "event_time": "09:30",
            "description": "Initial",
            "reminder_min": "30,10",
            "recurrence": "monthly",
            "category": "work",
        }
        values.update(overrides)
        return self.db.add_event(**values)

    def test_connection_context_always_closes_connection(self) -> None:
        with self.db._connect() as connection:
            connection.execute("SELECT 1").fetchone()

        # Regression guard: Windows cannot remove a temporary database while a
        # SQLite connection still owns a file handle.
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")

    def test_recurrence_math_preserves_supported_rules(self) -> None:
        self.assertEqual(_next_date(date(2030, 1, 2), "daily"), date(2030, 1, 3))
        self.assertEqual(_next_date(date(2030, 1, 2), "weekly"), date(2030, 1, 9))
        self.assertEqual(_next_date(date(2030, 1, 31), "monthly"), date(2030, 2, 28))
        self.assertEqual(_next_date(date(2024, 2, 29), "yearly"), date(2025, 2, 28))
        self.assertEqual(_next_date(date(2030, 1, 2), "3:days"), date(2030, 1, 5))
        self.assertEqual(_next_date(date(2030, 1, 31), "2:months"), date(2030, 3, 31))

    def test_invalid_recurrence_steps_do_not_loop_or_overflow(self) -> None:
        self.assertIsNone(_next_date(date(2030, 1, 2), "0:days"))
        self.assertIsNone(_next_date(date(2030, 1, 2), "-1:weeks"))
        self.assertIsNone(_next_date(date.max, "daily"))
        self.assertIsNone(_next_date(date(2030, 1, 2), "999999999:years"))

    def test_editing_schedule_clears_stale_reminder_and_snooze_state(self) -> None:
        event_id = self.add_event()
        with self.db._connect() as connection:
            connection.execute(
                "UPDATE events SET last_reminded='30', snooze_until='2030-01-31 10:00:00' "
                "WHERE id=?",
                (event_id,),
            )
            connection.commit()

        self.db.update_event(
            event_id,
            "Planning",
            "2030-02-01",
            "09:30",
            "Initial",
            "30,10",
            "monthly",
            "work",
        )

        event = self.db.get_event(event_id)
        self.assertIsNone(event["last_reminded"])
        self.assertIsNone(event["snooze_until"])

    def test_editing_only_content_keeps_current_reminder_state(self) -> None:
        event_id = self.add_event()
        with self.db._connect() as connection:
            connection.execute(
                "UPDATE events SET last_reminded='30', snooze_until='2030-01-31 10:00:00' "
                "WHERE id=?",
                (event_id,),
            )
            connection.commit()

        self.db.update_event(
            event_id,
            "Renamed",
            "2030-01-31",
            "09:30",
            "Changed notes",
            "30,10",
            "monthly",
            "personal",
        )

        event = self.db.get_event(event_id)
        self.assertEqual(event["last_reminded"], "30")
        self.assertEqual(event["snooze_until"], "2030-01-31 10:00:00")

    def test_concurrent_writes_do_not_lose_events(self) -> None:
        def insert(index: int) -> int:
            return self.add_event(name=f"Event {index}")

        with ThreadPoolExecutor(max_workers=6) as executor:
            ids = list(executor.map(insert, range(30)))

        self.assertEqual(len(set(ids)), 30)
        self.assertEqual(len(self.db.get_all_active()), 30)

    def test_default_path_is_read_when_instance_is_created(self) -> None:
        dynamic_path = Path(self.temp_dir.name) / "dynamic.db"
        previous = os.environ.get("TAPTAP_DB_PATH")
        os.environ["TAPTAP_DB_PATH"] = str(dynamic_path)
        try:
            dynamic_db = EventDB()
        finally:
            if previous is None:
                os.environ.pop("TAPTAP_DB_PATH", None)
            else:
                os.environ["TAPTAP_DB_PATH"] = previous

        self.assertEqual(dynamic_db.db_path, dynamic_path)
        self.assertTrue(dynamic_path.is_file())

    def test_legacy_database_migration_preserves_existing_events(self) -> None:
        legacy_path = Path(self.temp_dir.name) / "legacy.db"
        with sqlite3.connect(legacy_path) as connection:
            connection.execute(
                """CREATE TABLE events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    event_date TEXT NOT NULL,
                    event_time TEXT NOT NULL,
                    reminder_min INTEGER DEFAULT 15,
                    recurrence TEXT DEFAULT 'none',
                    active INTEGER DEFAULT 1,
                    last_reminded TEXT,
                    snooze_until TEXT,
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                )"""
            )
            connection.execute(
                "INSERT INTO events (name, event_date, event_time) VALUES (?, ?, ?)",
                ("Existing event", "2030-06-01", "08:00"),
            )
            connection.commit()

        migrated = EventDB(legacy_path)

        events = migrated.get_all_active()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["name"], "Existing event")
        self.assertEqual(events[0]["category"], "")


if __name__ == "__main__":
    unittest.main()
