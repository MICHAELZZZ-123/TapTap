from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from database import EventDB
from reminders import NativeNotifier, ReminderEngine, ReminderWorker


class FakeNotifier:
    def __init__(self, result: bool = True) -> None:
        self.result = result
        self.sent: list[tuple[str, str]] = []

    def send(self, title: str, message: str) -> bool:
        self.sent.append((title, message))
        return self.result

    def close(self) -> None:
        pass


class SlowAsyncNotifier:
    async def send(self, **kwargs) -> None:
        del kwargs
        await asyncio.sleep(10)


class ReminderEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = EventDB(Path(self.temp_dir.name) / "events.db")
        self.engine = ReminderEngine(self.db)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def add_event(
        self,
        *,
        name: str = "Stand-up",
        event_date: str = "2030-01-02",
        event_time: str = "12:00",
        reminder_min: str = "30,10",
        recurrence: str = "none",
    ) -> int:
        return self.db.add_event(
            name=name,
            event_date=event_date,
            event_time=event_time,
            reminder_min=reminder_min,
            recurrence=recurrence,
        )

    def test_due_offsets_fire_once_and_one_off_event_completes(self) -> None:
        event_id = self.add_event()

        first = self.engine.process_due(datetime(2030, 1, 2, 11, 35))
        self.assertEqual([item["id"] for item in first], [event_id])
        self.assertEqual(self.db.get_event(event_id)["last_reminded"], "30")
        self.assertEqual(self.engine.process_due(datetime(2030, 1, 2, 11, 36)), [])

        second = self.engine.process_due(datetime(2030, 1, 2, 11, 51))
        self.assertEqual(len(second), 1)
        self.assertEqual(self.db.get_event(event_id)["last_reminded"], "10,30")

        self.assertEqual(self.engine.process_due(datetime(2030, 1, 2, 12, 1)), [])
        self.assertEqual(self.db.get_event(event_id)["active"], 0)

    def test_snoozed_event_is_not_claimed(self) -> None:
        event_id = self.add_event()
        with self.db._connect() as connection:
            connection.execute(
                "UPDATE events SET snooze_until=? WHERE id=?",
                ("2030-01-02 12:30:00", event_id),
            )
            connection.commit()

        self.assertEqual(self.engine.process_due(datetime(2030, 1, 2, 12, 5)), [])
        self.assertEqual(self.db.get_event(event_id)["active"], 1)

    def test_recurring_event_advances_and_resets_reminders(self) -> None:
        event_id = self.add_event(reminder_min="0", recurrence="daily")

        fired = self.engine.process_due(datetime(2030, 1, 2, 12, 1))

        self.assertEqual(len(fired), 1)
        event = self.db.get_event(event_id)
        self.assertEqual(event["event_date"], "2030-01-03")
        self.assertEqual(event["last_reminded"], None)
        self.assertEqual(event["active"], 1)

    def test_long_overdue_recurrence_skips_directly_to_the_future(self) -> None:
        event_id = self.add_event(
            event_date="2030-01-02",
            reminder_min="0",
            recurrence="daily",
        )

        fired = self.engine.process_due(datetime(2030, 1, 10, 12, 1))

        self.assertEqual([item["id"] for item in fired], [event_id])
        self.assertEqual(self.db.get_event(event_id)["event_date"], "2030-01-11")
        self.assertEqual(self.engine.process_due(datetime(2030, 1, 10, 12, 1)), [])

    def test_duplicate_offsets_only_fire_once(self) -> None:
        event_id = self.add_event(reminder_min="30, 30, 10")

        fired = self.engine.process_due(datetime(2030, 1, 2, 11, 35))

        self.assertEqual([item["id"] for item in fired], [event_id])
        self.assertEqual(self.db.get_event(event_id)["last_reminded"], "30")

    def test_legacy_timestamp_does_not_refire_an_occurrence(self) -> None:
        event_id = self.add_event(reminder_min="30")
        with self.db._connect() as connection:
            connection.execute(
                "UPDATE events SET last_reminded=? WHERE id=?",
                ("2029-12-30 09:00:00", event_id),
            )
            connection.commit()

        self.assertEqual(self.engine.process_due(datetime(2030, 1, 2, 11, 35)), [])

    def test_snapshot_selects_the_earliest_reminder(self) -> None:
        self.add_event(reminder_min="30,10")

        snapshot = self.engine.snapshot(datetime(2030, 1, 2, 11, 0))

        self.assertEqual(snapshot["status"], "1 event(s)")
        self.assertEqual(snapshot["next_at"], "2030-01-02T11:30:00")

    def test_worker_marks_native_delivery_and_buffers_popup(self) -> None:
        self.add_event(reminder_min="30")
        notifier = FakeNotifier(result=True)
        worker = ReminderWorker(self.engine, notifier=notifier)

        queued = worker.process_once(datetime(2030, 1, 2, 11, 35))

        self.assertEqual(len(notifier.sent), 1)
        self.assertTrue(queued[0]["native_notified"])
        self.assertEqual(worker.drain_pending(), queued)
        self.assertEqual(worker.drain_pending(), [])

    def test_snooze_reactivates_a_completed_event(self) -> None:
        event_id = self.add_event()
        self.db.deactivate(event_id)

        self.db.snooze(event_id, minutes=5)

        self.assertEqual(self.db.get_event(event_id)["active"], 1)

    def test_native_notification_timeout_fails_cleanly(self) -> None:
        notifier = NativeNotifier(send_timeout=0.1)
        notifier._loop = asyncio.new_event_loop()
        notifier._notifier = SlowAsyncNotifier()

        with self.assertLogs("reminders", level="ERROR"):
            delivered = notifier.send("Title", "Message")

        self.assertFalse(delivered)
        notifier.close()
        self.assertIsNone(notifier._loop)
        self.assertIsNone(notifier._notifier)


if __name__ == "__main__":
    unittest.main()
