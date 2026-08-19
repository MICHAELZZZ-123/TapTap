"""Reminder evaluation and background delivery for the desktop runtime."""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Protocol

from utils import combine_datetime, fmt_datetime, human_duration, parse_offsets, parse_reminded

_LOG = logging.getLogger(__name__)


class ReminderDatabase(Protocol):
    """The database operations used by :class:`ReminderEngine`."""

    def get_all_active(self) -> list[dict]: ...

    def mark_reminded(self, event_id: int, offset: int) -> None: ...

    def advance_recurring(
        self,
        event_id: int,
        current_date: str,
        recurrence: str,
        *,
        after: datetime | None = None,
    ) -> None: ...

    def deactivate(self, event_id: int) -> None: ...

    def purge_history(self, days: int = 90) -> None: ...


def _snoozed(event: dict, now: datetime) -> bool:
    raw = event.get("snooze_until")
    if not raw:
        return False
    try:
        return now < datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return False


class ReminderEngine:
    """Find due reminders independently of whether the UI is visible."""

    def __init__(
        self,
        database: ReminderDatabase,
        *,
        now: Callable[[], datetime] = datetime.now,
        cleanup_interval: timedelta = timedelta(hours=1),
    ) -> None:
        self.database = database
        self._now = now
        self._cleanup_interval = cleanup_interval
        self._last_cleanup: datetime | None = None
        self._lock = threading.RLock()

    def process_due(self, now: datetime | None = None) -> list[dict]:
        """Persist and return every reminder which became due."""
        current = now or self._now()
        fired: list[dict] = []

        # INVARIANT: Only one caller may claim reminder offsets at a time. This keeps a
        # future manual refresh or wake handler from delivering duplicates.
        with self._lock:
            events = self.database.get_all_active()
            for event in events:
                if _snoozed(event, current):
                    continue

                try:
                    event_at = combine_datetime(event["event_date"], event["event_time"])
                except (KeyError, TypeError, ValueError):
                    continue

                offsets = parse_offsets(event.get("reminder_min"))
                reminded_raw = event.get("last_reminded")
                # COMPATIBILITY: Versions before 0.1 stored an ISO timestamp here. It means
                # the entire occurrence was handled, not an offset of 99999.
                already_reminded = (
                    set(offsets)
                    if reminded_raw and ":" in str(reminded_raw)
                    else parse_reminded(reminded_raw)
                )

                due_offsets = [
                    offset
                    for offset in offsets
                    if offset not in already_reminded
                    and current >= event_at - timedelta(minutes=offset)
                ]
                if due_offsets:
                    time_until = event_at - current
                    if time_until.total_seconds() < 0:
                        when = f"Started {human_duration(-time_until)} ago"
                    elif time_until.total_seconds() < 60:
                        when = "Starting now!"
                    else:
                        when = f"In {human_duration(time_until)}"

                    message = (
                        f"{fmt_datetime(event['event_date'], event['event_time'])} — {when}"
                    )
                    if event.get("description"):
                        message += f"\n{event['description']}"

                    fired.append(
                        {
                            "title": event["name"],
                            "message": message,
                            "id": event["id"],
                        }
                    )
                    # A delayed launch or wake can make several offsets due at
                    # once. Claim every offset, but emit one notification for the
                    # event so startup never creates a duplicate reminder storm.
                    for offset in due_offsets:
                        self.database.mark_reminded(event["id"], offset)
                        already_reminded.add(offset)

                if current > event_at:
                    recurrence = event.get("recurrence") or "none"
                    if recurrence != "none":
                        self.database.advance_recurring(
                            event["id"],
                            event["event_date"],
                            recurrence,
                            after=current,
                        )
                    else:
                        self.database.deactivate(event["id"])

            if (
                self._last_cleanup is None
                or current - self._last_cleanup >= self._cleanup_interval
            ):
                self.database.purge_history(days=90)
                self._last_cleanup = current

        return fired

    def snapshot(self, now: datetime | None = None) -> dict:
        """Return current UI status without mutating reminder state."""
        current = now or self._now()
        events = self.database.get_all_active()
        next_at: datetime | None = None

        for event in events:
            if _snoozed(event, current):
                continue
            try:
                event_at = combine_datetime(event["event_date"], event["event_time"])
            except (KeyError, TypeError, ValueError):
                continue

            for offset in parse_offsets(event.get("reminder_min")):
                reminder_at = event_at - timedelta(minutes=offset)
                if reminder_at > current and (next_at is None or reminder_at < next_at):
                    next_at = reminder_at
            if event_at > current and (next_at is None or event_at < next_at):
                next_at = event_at

        return {
            "status": f"{len(events)} event(s)",
            "next_at": next_at.isoformat() if next_at else None,
        }


class NativeNotifier:
    """Small synchronous adapter around desktop-notifier's async API."""

    def __init__(
        self,
        app_icon: str | Path | None = None,
        *,
        send_timeout: float = 5.0,
    ) -> None:
        self._app_icon = Path(app_icon) if app_icon else None
        self._send_timeout = max(0.1, float(send_timeout))
        self._loop: asyncio.AbstractEventLoop | None = None
        self._notifier = None
        self._sound = None
        self._disabled = False

    def _initialize(self) -> bool:
        if self._disabled:
            return False
        if self._notifier is not None:
            return True
        try:
            from desktop_notifier import DEFAULT_SOUND, DesktopNotifier, Icon

            self._loop = asyncio.new_event_loop()
            icon = (
                Icon(path=self._app_icon)
                if self._app_icon is not None and self._app_icon.is_file()
                else None
            )
            self._notifier = DesktopNotifier(app_name="TapTap", app_icon=icon)
            self._sound = DEFAULT_SOUND
            return True
        except Exception:
            if self._loop is not None and not self._loop.is_closed():
                self._loop.close()
            self._loop = None
            self._notifier = None
            self._sound = None
            self._disabled = True
            _LOG.exception("Native desktop notifications are unavailable")
            return False

    def send(self, title: str, message: str) -> bool:
        """Deliver a notification and report whether dispatch succeeded."""
        if not self._initialize() or self._loop is None:
            return False
        try:
            # ROBUSTNESS: OS notification services must not block the worker forever.
            self._loop.run_until_complete(
                asyncio.wait_for(
                    self._notifier.send(
                        title=title,
                        message=message,
                        sound=self._sound,
                    ),
                    timeout=self._send_timeout,
                )
            )
            return True
        except Exception:
            _LOG.exception("Could not send native desktop notification")
            return False

    def close(self) -> None:
        if self._loop is not None and not self._loop.is_closed():
            self._loop.close()
        self._loop = None
        self._notifier = None
        self._sound = None


class ReminderWorker:
    """Claim reminders on wall-clock ticks and deliver them off-scheduler."""

    def __init__(
        self,
        engine: ReminderEngine,
        *,
        notifier: NativeNotifier | None = None,
        interval: float = 1.0,
        wall_time: Callable[[], float] = time.time,
    ) -> None:
        self.engine = engine
        self.notifier = notifier or NativeNotifier()
        self.interval = max(0.25, interval)
        self._wall_time = wall_time
        # ROBUSTNESS: Bound queued window popups if the UI is hidden for a long time.
        self._pending: deque[dict] = deque(maxlen=100)
        self._pending_lock = threading.Lock()
        # INVARIANT: The scheduler is the sole producer; platform notification
        # calls run on the delivery thread and never delay the next due check.
        self._delivery_queue: queue.Queue[list[dict]] = queue.Queue()
        self._stop = threading.Event()
        self._delivery_stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._delivery_thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._delivery_stop.clear()
        self._delivery_thread = threading.Thread(
            target=self._run_delivery,
            name="TapTapNotificationDelivery",
            daemon=True,
        )
        self._delivery_thread.start()
        self._thread = threading.Thread(
            target=self._run,
            name="TapTapReminderWorker",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        deadline = time.monotonic() + max(0.0, timeout)
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=max(0.0, deadline - time.monotonic()))
        if (
            self._delivery_thread is not None
            and self._delivery_thread is not threading.current_thread()
        ):
            self._delivery_thread.join(timeout=max(0.0, deadline - time.monotonic()))

    def process_once(self, now: datetime | None = None) -> list[dict]:
        reminders = self.engine.process_due(now=now)
        return self._deliver(reminders)

    def _deliver(self, reminders: list[dict]) -> list[dict]:
        queued: list[dict] = []
        for reminder in reminders:
            try:
                delivered = self.notifier.send(reminder["title"], reminder["message"])
            except Exception:
                delivered = False
                _LOG.exception("Could not deliver a claimed reminder")
            item = {
                **reminder,
                "native_notified": delivered,
                "delivered_at": datetime.now().astimezone().isoformat(),
            }
            if delivered:
                _LOG.info(
                    "Native desktop notification dispatched for event %s",
                    reminder.get("id"),
                )
            queued.append(item)
        if queued:
            with self._pending_lock:
                self._pending.extend(queued)
        return queued

    def drain_pending(self) -> list[dict]:
        with self._pending_lock:
            items = list(self._pending)
            self._pending.clear()
        return items

    def _seconds_until_next_tick(self, now: float | None = None) -> float:
        """Return a delay which ends on the next wall-clock interval boundary."""
        current = self._wall_time() if now is None else now
        elapsed = current % self.interval
        return self.interval if elapsed < 1e-9 else self.interval - elapsed

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    reminders = self.engine.process_due()
                    if reminders:
                        # TIMING: Claim the whole due batch on the aligned scheduler
                        # tick. Native delivery happens separately and cannot shift
                        # the next full-second check.
                        self._delivery_queue.put(reminders)
                except Exception:
                    _LOG.exception("Reminder background check failed")
                self._stop.wait(self._seconds_until_next_tick())
        finally:
            # The scheduler is the only producer. Once it exits, delivery can drain
            # everything already claimed and close the platform notifier safely.
            self._delivery_stop.set()

    def _run_delivery(self) -> None:
        try:
            while not self._delivery_stop.is_set() or not self._delivery_queue.empty():
                try:
                    reminders = self._delivery_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                try:
                    self._deliver(reminders)
                except Exception:
                    _LOG.exception("Reminder delivery worker failed")
                finally:
                    self._delivery_queue.task_done()
        finally:
            # ROBUSTNESS: Closing happens only after the scheduler has stopped
            # producing and this worker has drained every queued reminder batch.
            self.notifier.close()
