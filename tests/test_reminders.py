from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
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


class ImmediateAsyncNotifier:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, **kwargs) -> None:
        self.sent.append(kwargs)
        callback = kwargs.get("on_dispatched")
        if callback:
            callback()


class DeniedAsyncNotifier(ImmediateAsyncNotifier):
    async def request_authorisation(self) -> bool:
        return False

    async def has_authorisation(self) -> bool:
        return False


class SilentAsyncNotifier:
    def __init__(self) -> None:
        self.sent = 0

    async def request_authorisation(self) -> bool:
        return True

    async def has_authorisation(self) -> bool:
        return True

    async def send(self, **kwargs) -> None:
        del kwargs
        self.sent += 1


class FlakyNativeNotifier(NativeNotifier):
    def __init__(self, failures: int, **kwargs) -> None:
        super().__init__(**kwargs)
        self.failures = failures
        self.attempts = 0
        self.backend = ImmediateAsyncNotifier()

    def _create_backend(self):
        self.attempts += 1
        if self.attempts <= self.failures:
            raise RuntimeError("temporary backend failure")
        return self.backend, None


class BlockingNotifier:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.closed = False

    def send(self, title: str, message: str) -> bool:
        del title, message
        self.started.set()
        self.release.wait(timeout=5)
        return True

    def close(self) -> None:
        self.closed = True


class RecordingEngine:
    def __init__(self, delegate: ReminderEngine) -> None:
        self.delegate = delegate
        self.database = delegate.database
        self.calls = 0
        self.second_check = threading.Event()
        self._lock = threading.Lock()

    def process_due(self, now: datetime | None = None) -> list[dict]:
        with self._lock:
            self.calls += 1
            if self.calls >= 2:
                self.second_check.set()
        return self.delegate.process_due(now)


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

    def test_offset_becomes_due_at_the_exact_full_minute(self) -> None:
        self.add_event(reminder_min="15")

        before = self.engine.process_due(datetime(2030, 1, 2, 11, 44, 59, 999999))
        at_boundary = self.engine.process_due(datetime(2030, 1, 2, 11, 45, 0))

        self.assertEqual(before, [])
        self.assertEqual(len(at_boundary), 1)
        # A configured offset does not silently add an event-time (zero) reminder.
        self.assertEqual(self.engine.process_due(datetime(2030, 1, 2, 12, 0, 0)), [])

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
        self.assertEqual(event["schedule_revision"], 1)
        self.assertEqual(self.db.pending_delivery_count(), 1)

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

    def test_delayed_start_coalesces_overdue_offsets_per_event(self) -> None:
        event_id = self.add_event(reminder_min="60,30,10")

        fired = self.engine.process_due(datetime(2030, 1, 2, 11, 35))

        self.assertEqual([item["id"] for item in fired], [event_id])
        self.assertEqual(self.db.get_event(event_id)["last_reminded"], "30,60")

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
        self.assertIsNotNone(datetime.fromisoformat(queued[0]["delivered_at"]))
        self.assertEqual(worker.drain_pending(), queued)
        self.assertEqual(worker.drain_pending(), [])

    def test_failed_delivery_survives_restart_and_retries(self) -> None:
        self.add_event(reminder_min="30")
        fallback_calls: list[str] = []
        first_worker = ReminderWorker(
            self.engine,
            notifier=FakeNotifier(result=False),
            fallback_alert=lambda: fallback_calls.append("alerted"),
        )

        failed = first_worker.process_once(datetime(2030, 1, 2, 11, 35))

        self.assertFalse(failed[0]["native_notified"])
        self.assertEqual(self.db.pending_delivery_count(), 1)
        self.assertEqual(fallback_calls, ["alerted"])
        pending = self.db.get_due_deliveries(now=datetime.now())
        self.assertEqual(pending, [])  # The first retry respects its cooldown.

        second_worker = ReminderWorker(
            ReminderEngine(EventDB(self.db.db_path)),
            notifier=FakeNotifier(result=True),
            fallback_alert=lambda: fallback_calls.append("duplicate"),
        )
        delivered = second_worker._deliver_due(
            now=datetime.now() + timedelta(seconds=6)
        )

        self.assertTrue(delivered[0]["native_notified"])
        self.assertEqual(self.db.pending_delivery_count(), 0)
        self.assertEqual(fallback_calls, ["alerted"])

    def test_shutdown_leaves_blocked_delivery_durable(self) -> None:
        now = datetime.now()
        self.add_event(
            name="Shutdown",
            event_date=now.strftime("%Y-%m-%d"),
            event_time=now.strftime("%H:%M"),
            reminder_min="0",
        )
        notifier = BlockingNotifier()
        worker = ReminderWorker(
            self.engine,
            notifier=notifier,
            interval=0.25,
            fallback_alert=lambda: None,
        )

        worker.start()
        self.assertTrue(notifier.started.wait(timeout=2))
        worker.stop(timeout=0.1)

        self.assertTrue(worker._delivery_thread.is_alive())
        self.assertEqual(self.db.pending_delivery_count(), 1)
        notifier.release.set()
        worker._delivery_thread.join(timeout=2)
        self.assertFalse(worker._delivery_thread.is_alive())

    def test_worker_wait_is_aligned_to_wall_clock_boundaries(self) -> None:
        worker = ReminderWorker(
            self.engine,
            notifier=FakeNotifier(),
            wall_time=lambda: 100.25,
        )

        self.assertAlmostEqual(worker._seconds_until_next_tick(), 0.75)
        self.assertAlmostEqual(worker._seconds_until_next_tick(101.0), 1.0)
        self.assertAlmostEqual(worker._seconds_until_next_tick(101.999), 0.001)

    def test_slow_notification_does_not_block_timing_checks(self) -> None:
        now = datetime.now()
        self.add_event(
            name="Boundary",
            event_date=now.strftime("%Y-%m-%d"),
            event_time=now.strftime("%H:%M"),
            reminder_min="0",
        )
        engine = RecordingEngine(self.engine)
        notifier = BlockingNotifier()
        worker = ReminderWorker(engine, notifier=notifier, interval=0.25)

        worker.start()
        try:
            self.assertTrue(notifier.started.wait(timeout=1.5))
            self.assertTrue(engine.second_check.wait(timeout=1.5))
        finally:
            notifier.release.set()
            worker.stop(timeout=2)

        queued = worker.drain_pending()
        self.assertEqual([item["id"] for item in queued], [1])
        self.assertTrue(queued[0]["native_notified"])
        self.assertTrue(notifier.closed)

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

    def test_native_notification_permission_denial_is_a_failure(self) -> None:
        backend = DeniedAsyncNotifier()
        notifier = NativeNotifier()
        notifier._loop = asyncio.new_event_loop()
        notifier._notifier = backend

        with self.assertLogs("reminders", level="ERROR"):
            delivered = notifier.send("Denied", "Use the fallback")

        self.assertFalse(delivered)
        self.assertEqual(backend.sent, [])
        self.assertEqual(notifier.snapshot()["state"], "retrying")

    def test_native_notification_requires_dispatch_confirmation(self) -> None:
        backend = SilentAsyncNotifier()
        notifier = NativeNotifier()
        notifier._loop = asyncio.new_event_loop()
        notifier._notifier = backend

        with self.assertLogs("reminders", level="ERROR"):
            delivered = notifier.send("Silent failure", "Retry this")

        self.assertFalse(delivered)
        self.assertEqual(backend.sent, 1)
        self.assertEqual(notifier.snapshot()["state"], "retrying")

    def test_native_notification_initialization_recovers_after_cooldown(self) -> None:
        clock = [100.0]
        notifier = FlakyNativeNotifier(
            failures=1,
            initial_retry_delay=5,
            max_retry_delay=20,
            monotonic=lambda: clock[0],
        )

        with self.assertLogs("reminders", level="ERROR"):
            self.assertFalse(notifier.send("First", "Fallback"))
        self.assertEqual(notifier.attempts, 1)
        self.assertEqual(notifier.snapshot()["state"], "retrying")
        self.assertEqual(notifier.snapshot()["retry_in_seconds"], 5)

        clock[0] = 104.9
        self.assertFalse(notifier.send("Too early", "Fallback"))
        self.assertEqual(notifier.attempts, 1)

        clock[0] = 105.0
        self.assertTrue(notifier.send("Recovered", "Delivered"))
        self.assertEqual(notifier.attempts, 2)
        self.assertEqual(notifier.snapshot()["state"], "ready")
        self.assertEqual(len(notifier.backend.sent), 1)
        notifier.close()

    def test_native_notification_initialization_uses_bounded_backoff(self) -> None:
        clock = [0.0]
        notifier = FlakyNativeNotifier(
            failures=3,
            initial_retry_delay=2,
            max_retry_delay=4,
            monotonic=lambda: clock[0],
        )

        with self.assertLogs("reminders", level="ERROR"):
            self.assertFalse(notifier.send("One", "Fallback"))
            clock[0] = 1.0
            self.assertFalse(notifier.send("Suppressed", "Fallback"))
            clock[0] = 2.0
            self.assertFalse(notifier.send("Two", "Fallback"))
            clock[0] = 5.9
            self.assertFalse(notifier.send("Still suppressed", "Fallback"))
            clock[0] = 6.0
            self.assertFalse(notifier.send("Three", "Fallback"))

        self.assertEqual(notifier.attempts, 3)
        self.assertEqual(notifier.snapshot()["retry_in_seconds"], 4)
        notifier.close()


if __name__ == "__main__":
    unittest.main()
