"""Database layer — SQLite storage for reminder events."""

from __future__ import annotations

import calendar
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator, Optional

# COMPATIBILITY: Keep this legacy location unless an explicit data migration is added.
DB_PATH = Path(
    os.environ.get("TAPTAP_DB_PATH") or Path.home() / ".reminder_app.db"
).expanduser()


class EventDB:
    """SQLite storage for events."""

    def __init__(self, db_path: Path | str | None = None):
        if db_path is None:
            db_path = os.environ.get("TAPTAP_DB_PATH") or DB_PATH
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Yield one transaction-scoped connection and always release it."""
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        try:
            conn.row_factory = sqlite3.Row
            # ROBUSTNESS: Busy waiting plus WAL lets the API and worker share SQLite.
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            # PORTABILITY: sqlite3.Connection.__exit__ commits or rolls back but
            # does not close; an explicit finally prevents Windows file locks.
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self):
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    name          TEXT    NOT NULL,
                    description   TEXT    DEFAULT '',
                    event_date    TEXT    NOT NULL,  -- YYYY-MM-DD
                    event_time    TEXT    NOT NULL,  -- HH:MM
                    reminder_min  INTEGER DEFAULT 15,  -- minutes before event
                    recurrence    TEXT    DEFAULT 'none',
                    category      TEXT    DEFAULT '',
                    active        INTEGER DEFAULT 1,
                    last_reminded TEXT,  -- claimed offsets for the current occurrence
                    snooze_until  TEXT,  -- ISO datetime; ignore until this time
                    schedule_revision INTEGER NOT NULL DEFAULT 0,
                    created_at    TEXT    DEFAULT (datetime('now','localtime'))
                )
                """
            )
            # COMPATIBILITY: Add category without replacing an existing database.
            # Inspect the schema first so unrelated SQLite errors are never hidden.
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(events)").fetchall()
            }
            if "category" not in columns:
                try:
                    conn.execute(
                        "ALTER TABLE events ADD COLUMN category TEXT DEFAULT ''"
                    )
                except sqlite3.OperationalError as exc:
                    # A simultaneous first launch can complete this exact
                    # migration between the schema check and ALTER statement.
                    if "duplicate column name" not in str(exc).lower():
                        raise
            if "schedule_revision" not in columns:
                try:
                    conn.execute(
                        "ALTER TABLE events ADD COLUMN "
                        "schedule_revision INTEGER NOT NULL DEFAULT 0"
                    )
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc).lower():
                        raise
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reminder_outbox (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    delivery_key      TEXT NOT NULL UNIQUE,
                    event_id          INTEGER NOT NULL,
                    occurrence_at     TEXT NOT NULL,
                    offsets           TEXT NOT NULL,
                    title             TEXT NOT NULL,
                    message           TEXT NOT NULL,
                    state             TEXT NOT NULL DEFAULT 'pending',
                    attempts          INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at    TEXT NOT NULL DEFAULT '1970-01-01 00:00:00',
                    last_attempt_at    TEXT,
                    delivered_at      TEXT,
                    last_error         TEXT,
                    fallback_at       TEXT,
                    popup_consumed_at TEXT,
                    created_at        TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_reminder_outbox_due "
                "ON reminder_outbox(state, next_attempt_at, id)"
            )
            conn.commit()

    # ── CRUD ──────────────────────────────────────────────────────────────

    def add_event(
        self,
        name: str,
        event_date: str,
        event_time: str,
        description: str = "",
        reminder_min: int = 15,
        recurrence: str = "none",
        category: str = "",
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO events (name, description, event_date, event_time,
                   reminder_min, recurrence, category)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (name, description, event_date, event_time, reminder_min, recurrence, category),
            )
            conn.commit()
            return cur.lastrowid

    def update_event(
        self,
        event_id: int,
        name: str,
        event_date: str,
        event_time: str,
        description: str = "",
        reminder_min: int = 15,
        recurrence: str = "none",
        category: str = "",
    ):
        # INVARIANT: Only schedule changes clear reminder/snooze state.
        with self._connect() as conn:
            conn.execute(
                """UPDATE events SET name=?, description=?, event_date=?,
                   event_time=?, reminder_min=?, recurrence=?, category=?,
                   last_reminded=CASE WHEN
                       COALESCE(event_date, '')<>? OR
                       COALESCE(event_time, '')<>? OR
                       COALESCE(CAST(reminder_min AS TEXT), '')<>? OR
                       COALESCE(recurrence, 'none')<>?
                     THEN NULL ELSE last_reminded END,
                   snooze_until=CASE WHEN
                       COALESCE(event_date, '')<>? OR
                       COALESCE(event_time, '')<>? OR
                       COALESCE(CAST(reminder_min AS TEXT), '')<>? OR
                       COALESCE(recurrence, 'none')<>?
                     THEN NULL ELSE snooze_until END,
                   schedule_revision=CASE WHEN
                       COALESCE(event_date, '')<>? OR
                       COALESCE(event_time, '')<>? OR
                       COALESCE(CAST(reminder_min AS TEXT), '')<>? OR
                       COALESCE(recurrence, 'none')<>?
                     THEN schedule_revision + 1 ELSE schedule_revision END
                   WHERE id=?""",
                (name, description, event_date, event_time,
                 reminder_min, recurrence, category,
                 event_date, event_time, str(reminder_min), recurrence,
                 event_date, event_time, str(reminder_min), recurrence,
                 event_date, event_time, str(reminder_min), recurrence,
                 event_id),
            )
            conn.commit()

    def delete_event(self, event_id: int):
        with self._connect() as conn:
            conn.execute("DELETE FROM events WHERE id=?", (event_id,))
            conn.commit()

    def get_all_active(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE active=1 ORDER BY event_date, event_time"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_event(self, event_id: int) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM events WHERE id=?", (event_id,)
            ).fetchone()
            return dict(row) if row else None

    # ── Reminder lifecycle ────────────────────────────────────────────────

    def mark_reminded(self, event_id: int, offset: int):
        """Record that a specific reminder offset has fired for this occurrence."""
        # INVARIANT: last_reminded is the claimed-offset set for this occurrence.
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_reminded FROM events WHERE id=?", (event_id,)
            ).fetchone()
            if not row:
                return
            current = row["last_reminded"] or ""
            offsets = set()
            if current:
                try:
                    offsets = {int(x) for x in current.split(",") if x.strip()}
                except ValueError:
                    offsets = set()
            offsets.add(offset)
            conn.execute(
                "UPDATE events SET last_reminded=? WHERE id=?",
                (",".join(str(o) for o in sorted(offsets)), event_id),
            )
            conn.commit()

    def claim_delivery(
        self,
        event: dict,
        offsets: list[int],
        title: str,
        message: str,
        *,
        claimed_at: datetime,
    ) -> int | None:
        """Atomically claim due offsets and persist their delivery payload."""
        normalized = sorted({int(offset) for offset in offsets})
        if not normalized:
            return None
        offsets_text = ",".join(str(offset) for offset in normalized)
        occurrence_at = f"{event['event_date']} {event['event_time']}"
        delivery_key = "|".join(
            (
                str(event["id"]),
                str(event.get("schedule_revision") or 0),
                occurrence_at,
                offsets_text,
            )
        )
        timestamp = claimed_at.strftime("%Y-%m-%d %H:%M:%S")

        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_reminded FROM events WHERE id=?",
                (event["id"],),
            ).fetchone()
            if not row:
                return None
            reminded = set()
            current = row["last_reminded"] or ""
            if current:
                try:
                    reminded = {int(value) for value in current.split(",") if value.strip()}
                except ValueError:
                    reminded = set()
            reminded.update(normalized)

            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO reminder_outbox
                    (delivery_key, event_id, occurrence_at, offsets, title,
                     message, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    delivery_key,
                    event["id"],
                    occurrence_at,
                    offsets_text,
                    title,
                    message,
                    timestamp,
                ),
            )
            conn.execute(
                "UPDATE events SET last_reminded=? WHERE id=?",
                (
                    ",".join(str(offset) for offset in sorted(reminded)),
                    event["id"],
                ),
            )
            conn.commit()
            return int(cursor.lastrowid) if cursor.rowcount else None

    def get_due_deliveries(
        self,
        *,
        now: datetime | None = None,
        limit: int = 50,
    ) -> list[dict]:
        timestamp = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM reminder_outbox "
                "WHERE state='pending' AND next_attempt_at<=? "
                "ORDER BY created_at, id LIMIT ?",
                (timestamp, max(1, int(limit))),
            ).fetchall()
            return [dict(row) for row in rows]

    def mark_delivery_delivered(
        self,
        delivery_id: int,
        *,
        delivered_at: datetime | None = None,
    ) -> None:
        timestamp = (delivered_at or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            conn.execute(
                "UPDATE reminder_outbox SET state='delivered', "
                "attempts=attempts+1, last_attempt_at=?, delivered_at=?, "
                "last_error=NULL WHERE id=?",
                (timestamp, timestamp, delivery_id),
            )
            conn.commit()

    def mark_delivery_failed(
        self,
        delivery_id: int,
        error: str,
        *,
        retry_at: datetime,
        attempted_at: datetime | None = None,
    ) -> None:
        attempted = (attempted_at or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
        retry = retry_at.strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            conn.execute(
                "UPDATE reminder_outbox SET state='pending', attempts=attempts+1, "
                "last_attempt_at=?, next_attempt_at=?, last_error=? WHERE id=?",
                (attempted, retry, str(error)[:1000], delivery_id),
            )
            conn.commit()

    def mark_delivery_fallback(self, delivery_id: int, *, shown_at: datetime) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE reminder_outbox SET fallback_at=? "
                "WHERE id=? AND fallback_at IS NULL",
                (shown_at.strftime("%Y-%m-%d %H:%M:%S"), delivery_id),
            )
            conn.commit()

    def consume_delivery_popups(self, limit: int = 100) -> list[dict]:
        """Return durable in-app fallbacks once, without a bounded memory queue."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM reminder_outbox WHERE popup_consumed_at IS NULL "
                "ORDER BY created_at, id LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
            if rows:
                placeholders = ",".join("?" for _row in rows)
                conn.execute(
                    f"UPDATE reminder_outbox SET popup_consumed_at=? "
                    f"WHERE id IN ({placeholders})",
                    (timestamp, *(row["id"] for row in rows)),
                )
                conn.commit()
            return [dict(row) for row in rows]

    def pending_delivery_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM reminder_outbox WHERE state='pending'"
            ).fetchone()
            return int(row["count"])

    def snooze(self, event_id: int, minutes: int = 5):
        with self._connect() as conn:
            conn.execute(
                "UPDATE events SET snooze_until=datetime('now','localtime', "
                "? || ' minutes'), last_reminded=NULL, active=1, "
                "schedule_revision=schedule_revision+1 WHERE id=?",
                (str(minutes), event_id),
            )
            conn.commit()

    def advance_recurring(
        self,
        event_id: int,
        current_date_str: str,
        recurrence: str,
        *,
        after: datetime | None = None,
    ):
        """Move a recurring event to its next (optionally future) occurrence."""
        current = datetime.strptime(current_date_str, "%Y-%m-%d").date()
        nxt = _next_date(current, recurrence)
        if nxt is None:
            return
        with self._connect() as conn:
            row = conn.execute(
                "SELECT event_time FROM events WHERE id=?", (event_id,)
            ).fetchone()
            if not row:
                return

            if after is not None:
                try:
                    event_time = datetime.strptime(row["event_time"], "%H:%M").time()
                except (TypeError, ValueError):
                    event_time = None

                # ROBUSTNESS: Preserve cadence while skipping missed occurrences
                # in one pass so reopening after a long absence cannot emit one
                # notification per second while the database catches up.
                while event_time is not None and datetime.combine(nxt, event_time) <= after:
                    following = _next_date(nxt, recurrence)
                    if following is None or following <= nxt:
                        return
                    nxt = following

            conn.execute(
                "UPDATE events SET event_date=?, last_reminded=NULL, "
                "snooze_until=NULL, schedule_revision=schedule_revision+1 WHERE id=?",
                (nxt.strftime("%Y-%m-%d"), event_id),
            )
            conn.commit()

    def deactivate(self, event_id: int):
        """Mark a non-recurring past event as inactive."""
        with self._connect() as conn:
            conn.execute("UPDATE events SET active=0 WHERE id=?", (event_id,))
            conn.commit()

    def purge_history(self, days: int = 90):
        """Delete inactive events older than the requested retention period."""
        days = max(1, int(days))
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM events WHERE active=0 AND "
                "event_date < date('now','localtime', ?)",
                (f"-{days} days",),
            )
            conn.execute(
                "DELETE FROM reminder_outbox WHERE state='delivered' AND "
                "delivered_at < datetime('now','localtime', ?)",
                (f"-{days} days",),
            )
            conn.commit()


# ── Recurrence math ────────────────────────────────────────────────────────

def _next_date(current: date, recurrence: str) -> Optional[date]:
    """Compute the next occurrence date given a recurrence rule.
    Supports standard rules (daily, weekly, monthly, yearly) and
    custom rules like '3:days', '2:weeks', '6:months', '1:years'."""
    if not isinstance(recurrence, str):
        return None

    # Custom recurrence: "N:unit"
    if ":" in recurrence:
        parts = recurrence.split(":")
        try:
            n = int(parts[0]) if len(parts) > 0 else 1
        except (ValueError, TypeError):
            return None
        if n < 1:
            return None
        unit = parts[1] if len(parts) > 1 else "days"
        try:
            if unit == "days":
                return current + timedelta(days=n)
            if unit == "weeks":
                return current + timedelta(weeks=n)
            if unit == "months":
                month_index = (current.year - 1) * 12 + current.month - 1 + n
                year, zero_based_month = divmod(month_index, 12)
                year += 1
                if year > date.max.year:
                    return None
                month = zero_based_month + 1
                last_day = calendar.monthrange(year, month)[1]
                return date(year, month, min(current.day, last_day))
            if unit == "years":
                target_year = current.year + n
                if target_year > date.max.year:
                    return None
                last_day = calendar.monthrange(target_year, current.month)[1]
                return date(target_year, current.month, min(current.day, last_day))
        except (OverflowError, ValueError):
            return None
        return None

    # Standard recurrence
    try:
        if recurrence == "daily":
            return current + timedelta(days=1)
        if recurrence == "weekly":
            return current + timedelta(days=7)
        if recurrence == "monthly":
            month = current.month + 1
            year = current.year
            if month > 12:
                month = 1
                year += 1
            last_day = calendar.monthrange(year, month)[1]
            return date(year, month, min(current.day, last_day))
        if recurrence == "yearly":
            target_year = current.year + 1
            last_day = calendar.monthrange(target_year, current.month)[1]
            return date(target_year, current.month, min(current.day, last_day))
    except (OverflowError, ValueError):
        return None
    return None
