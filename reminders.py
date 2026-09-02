"""Reminder evaluation and background delivery for the desktop runtime."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Protocol

from utils import combine_datetime, fmt_datetime, human_duration, parse_offsets, parse_reminded

_LOG = logging.getLogger(__name__)


class ReminderDatabase(Protocol):
    """The database operations used by :class:`ReminderEngine`."""

    def get_all_active(self) -> list[dict]: ...

    def mark_reminded(self, event_id: int, offset: int) -> None: ...

    def claim_delivery(
        self,
        event: dict,
        offsets: list[int],
        title: str,
        message: str,
        *,
        claimed_at: datetime,
    ) -> int | None: ...

    def get_due_deliveries(
        self, *, now: datetime | None = None, limit: int = 50
    ) -> list[dict]: ...

    def mark_delivery_delivered(
        self, delivery_id: int, *, delivered_at: datetime | None = None
    ) -> None: ...

    def mark_delivery_failed(
        self,
        delivery_id: int,
        error: str,
        *,
        retry_at: datetime,
        attempted_at: datetime | None = None,
    ) -> None: ...

    def mark_delivery_fallback(self, delivery_id: int, *, shown_at: datetime) -> None: ...

    def consume_delivery_popups(self, limit: int = 100) -> list[dict]: ...

    def pending_delivery_count(self) -> int: ...

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

                    delivery_id = self.database.claim_delivery(
                        event,
                        due_offsets,
                        event["name"],
                        message,
                        claimed_at=current,
                    )
                    if delivery_id is not None:
                        fired.append(
                            {
                                "title": event["name"],
                                "message": message,
                                "id": event["id"],
                                "outbox_id": delivery_id,
                            }
                        )

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
        initial_retry_delay: float = 5.0,
        max_retry_delay: float = 300.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._app_icon = Path(app_icon) if app_icon else None
        self._send_timeout = max(0.1, float(send_timeout))
        self._initial_retry_delay = max(0.1, float(initial_retry_delay))
        self._max_retry_delay = max(
            self._initial_retry_delay, float(max_retry_delay)
        )
        self._monotonic = monotonic
        self._loop: asyncio.AbstractEventLoop | None = None
        self._notifier = None
        self._sound = None
        self._last_error: str | None = None
        self._next_init_attempt = 0.0
        self._retry_delay = self._initial_retry_delay
        self._state_lock = threading.Lock()

    def _create_backend(self):
        """Create the platform backend separately so recovery can be tested."""
        from desktop_notifier import DEFAULT_SOUND, DesktopNotifier, Icon

        icon = (
            Icon(path=self._app_icon)
            if self._app_icon is not None and self._app_icon.is_file()
            else None
        )
        return DesktopNotifier(app_name="TapTap", app_icon=icon), DEFAULT_SOUND

    def _close_backend(self) -> None:
        if self._loop is not None and not self._loop.is_closed():
            self._loop.close()
        self._loop = None
        self._notifier = None
        self._sound = None

    def _schedule_retry(self, error: Exception) -> float:
        """Reset a failed backend and return its bounded retry delay."""
        self._close_backend()
        with self._state_lock:
            delay = self._retry_delay
            self._last_error = str(error) or type(error).__name__
            self._next_init_attempt = self._monotonic() + delay
            self._retry_delay = min(delay * 2, self._max_retry_delay)
        return delay

    def _initialize(self) -> bool:
        if self._notifier is not None:
            return True
        with self._state_lock:
            if self._monotonic() < self._next_init_attempt:
                return False
        try:
            self._loop = asyncio.new_event_loop()
            self._notifier, self._sound = self._create_backend()
            with self._state_lock:
                self._last_error = None
                self._next_init_attempt = 0.0
                self._retry_delay = self._initial_retry_delay
            return True
        except Exception as exc:
            delay = self._schedule_retry(exc)
            _LOG.exception(
                "Native desktop notifications are unavailable; retrying after "
                "%.1f seconds",
                delay,
            )
            return False

    def send(self, title: str, message: str) -> bool:
        """Deliver a notification and report whether dispatch succeeded."""
        if not self._initialize() or self._loop is None:
            return False
        try:
            dispatched = threading.Event()

            async def send_confirmed() -> None:
                request_authorisation = getattr(
                    self._notifier, "request_authorisation", None
                )
                check_authorisation = getattr(self._notifier, "has_authorisation", None)
                if callable(request_authorisation):
                    authorised = await request_authorisation()
                elif callable(check_authorisation):
                    authorised = await check_authorisation()
                else:
                    authorised = True
                if callable(check_authorisation) and authorised:
                    authorised = await check_authorisation()
                if not authorised:
                    raise PermissionError(
                        "Windows notification permission is disabled for TapTap"
                    )
                await self._notifier.send(
                    title=title,
                    message=message,
                    sound=self._sound,
                    on_dispatched=dispatched.set,
                )
                if not dispatched.is_set():
                    raise RuntimeError(
                        "The notification backend did not confirm dispatch"
                    )

            # ROBUSTNESS: OS notification services must not block the worker forever.
            self._loop.run_until_complete(
                asyncio.wait_for(
                    send_confirmed(),
                    timeout=self._send_timeout,
                )
            )
            return True
        except Exception as exc:
            delay = self._schedule_retry(exc)
            _LOG.exception(
                "Could not send native desktop notification; retrying after "
                "%.1f seconds",
                delay,
            )
            return False

    def snapshot(self) -> dict:
        """Return user-facing backend health without forcing initialization."""
        with self._state_lock:
            if self._notifier is not None:
                return {"state": "ready", "available": True, "message": None}
            if self._last_error is None:
                return {"state": "idle", "available": None, "message": None}
            retry_in = max(0, int(self._next_init_attempt - self._monotonic() + 0.999))
            return {
                "state": "retrying",
                "available": False,
                "message": (
                    "Native notifications unavailable; TapTap will retry with "
                    "the next reminder."
                ),
                "retry_in_seconds": retry_in,
            }

    def close(self) -> None:
        self._close_backend()


class ReminderWorker:
    """Claim reminders on wall-clock ticks and drain their durable outbox."""

    def __init__(
        self,
        engine: ReminderEngine,
        *,
        notifier: NativeNotifier | None = None,
        interval: float = 1.0,
        wall_time: Callable[[], float] = time.time,
        fallback_alert: Callable[[], None] | None = None,
    ) -> None:
        self.engine = engine
        self.database = engine.database
        self.notifier = notifier or NativeNotifier()
        self.interval = max(0.25, interval)
        self._wall_time = wall_time
        self._fallback_alert = fallback_alert or self._default_fallback_alert
        self._stop = threading.Event()
        self._delivery_stop = threading.Event()
        self._delivery_wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._delivery_thread: threading.Thread | None = None

    @staticmethod
    def _default_fallback_alert() -> None:
        """Reveal TapTap and play a Windows sound when native toasts are blocked."""
        if sys.platform != "win32":
            return
        import winsound
        from windows_integration import activate_existing_window

        sound_error = None
        try:
            winsound.PlaySound(
                "SystemExclamation",
                winsound.SND_ALIAS | winsound.SND_ASYNC,
            )
        except RuntimeError as exc:
            sound_error = exc
        revealed = activate_existing_window(
            os.environ.get("TAPTAP_SMOKE_WINDOW_TITLE", "TapTap"),
            timeout=0,
        )
        if sound_error is not None and not revealed:
            raise RuntimeError(
                "Windows could neither play nor reveal TapTap's fallback alarm"
            ) from sound_error

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._delivery_stop.clear()
        self._delivery_wake.clear()
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
        self._delivery_stop.set()
        self._delivery_wake.set()
        deadline = time.monotonic() + max(0.0, timeout)
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=max(0.0, deadline - time.monotonic()))
        if (
            self._delivery_thread is not None
            and self._delivery_thread is not threading.current_thread()
        ):
            self._delivery_thread.join(timeout=max(0.0, deadline - time.monotonic()))

    def process_once(self, now: datetime | None = None) -> list[dict]:
        self.engine.process_due(now=now)
        return self._deliver_due(now=now)

    def _deliver_due(self, now: datetime | None = None) -> list[dict]:
        current = now or datetime.now()
        queued: list[dict] = []
        for reminder in self.database.get_due_deliveries(now=current):
            try:
                delivered = self.notifier.send(reminder["title"], reminder["message"])
            except Exception as exc:
                delivered = False
                error = str(exc) or type(exc).__name__
                _LOG.exception("Could not deliver a claimed reminder")
            else:
                status = getattr(self.notifier, "snapshot", lambda: {})()
                error = status.get("message") or "Native notification dispatch failed"
            attempted_at = datetime.now()
            if delivered:
                self.database.mark_delivery_delivered(
                    reminder["id"], delivered_at=attempted_at
                )
            else:
                delay = min(5 * (2 ** min(int(reminder["attempts"]), 6)), 300)
                self.database.mark_delivery_failed(
                    reminder["id"],
                    error,
                    attempted_at=attempted_at,
                    retry_at=attempted_at + timedelta(seconds=delay),
                )
                if reminder.get("fallback_at") is None:
                    try:
                        self._fallback_alert()
                    except Exception:
                        _LOG.exception("Could not play the background fallback alert")
                    else:
                        self.database.mark_delivery_fallback(
                            reminder["id"], shown_at=attempted_at
                        )
                        _LOG.info(
                            "Background fallback alert activated for event %s",
                            reminder.get("event_id"),
                        )
            item = {
                "title": reminder["title"],
                "message": reminder["message"],
                "id": reminder["event_id"],
                "outbox_id": reminder["id"],
                "native_notified": delivered,
                "delivered_at": attempted_at.strftime("%Y-%m-%d %H:%M:%S"),
            }
            if delivered:
                _LOG.info(
                    "Native desktop notification dispatched for event %s",
                    reminder.get("event_id"),
                )
            queued.append(item)
        return queued

    def drain_pending(self) -> list[dict]:
        return [
            {
                "title": item["title"],
                "message": item["message"],
                "id": item["event_id"],
                "outbox_id": item["id"],
                "native_notified": item["state"] == "delivered",
                "delivered_at": item["delivered_at"],
            }
            for item in self.database.consume_delivery_popups()
        ]

    def notification_status(self) -> dict:
        """Expose native delivery health when the notifier supports it."""
        snapshot = getattr(self.notifier, "snapshot", None)
        pending = self.database.pending_delivery_count()
        if callable(snapshot):
            status = snapshot()
        else:
            status = {"state": "unknown", "available": None, "message": None}
        status["pending_deliveries"] = pending
        if pending and not status.get("message"):
            status["message"] = (
                f"{pending} native notification(s) waiting for delivery; "
                "TapTap will retry."
            )
        return status

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
                        self._delivery_wake.set()
                except Exception:
                    _LOG.exception("Reminder background check failed")
                self._stop.wait(self._seconds_until_next_tick())
        finally:
            # The scheduler is the only producer. Once it exits, delivery can drain
            # or retain everything already claimed in SQLite for the next launch.
            self._delivery_stop.set()
            self._delivery_wake.set()

    def _run_delivery(self) -> None:
        try:
            while not self._delivery_stop.is_set():
                try:
                    self._deliver_due()
                except Exception:
                    _LOG.exception("Reminder delivery worker failed")
                self._delivery_wake.wait(1.0)
                self._delivery_wake.clear()
        finally:
            self.notifier.close()
