"""Database layer — SQLite storage for reminder events."""

from __future__ import annotations

import calendar
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

DB_PATH = Path.home() / ".reminder_app.db"


class EventDB:
    """SQLite storage for events."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

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
                    active        INTEGER DEFAULT 1,
                    last_reminded TEXT,  -- ISO datetime when last reminder fired
                    snooze_until  TEXT,  -- ISO datetime; ignore until this time
                    created_at    TEXT    DEFAULT (datetime('now','localtime'))
                )
                """
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
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO events (name, description, event_date, event_time,
                   reminder_min, recurrence)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (name, description, event_date, event_time, reminder_min, recurrence),
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
    ):
        with self._connect() as conn:
            conn.execute(
                """UPDATE events SET name=?, description=?, event_date=?,
                   event_time=?, reminder_min=?, recurrence=?
                   WHERE id=?""",
                (name, description, event_date, event_time,
                 reminder_min, recurrence, event_id),
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

    def snooze(self, event_id: int, minutes: int = 5):
        with self._connect() as conn:
            conn.execute(
                "UPDATE events SET snooze_until=datetime('now','localtime', "
                "? || ' minutes'), last_reminded=NULL WHERE id=?",
                (str(minutes), event_id),
            )
            conn.commit()

    def advance_recurring(self, event_id: int, current_date_str: str, recurrence: str):
        """Move a recurring event to its next occurrence."""
        current = datetime.strptime(current_date_str, "%Y-%m-%d").date()
        nxt = _next_date(current, recurrence)
        if nxt is None:
            return
        with self._connect() as conn:
            conn.execute(
                "UPDATE events SET event_date=?, last_reminded=NULL, "
                "snooze_until=NULL WHERE id=?",
                (nxt.strftime("%Y-%m-%d"), event_id),
            )
            conn.commit()

    def deactivate(self, event_id: int):
        """Mark a non-recurring past event as inactive."""
        with self._connect() as conn:
            conn.execute("UPDATE events SET active=0 WHERE id=?", (event_id,))
            conn.commit()


# ── Recurrence math ────────────────────────────────────────────────────────

def _next_date(current: date, recurrence: str) -> Optional[date]:
    """Compute the next occurrence date given a recurrence rule.
    Supports standard rules (daily, weekly, monthly, yearly) and
    custom rules like '3:days', '2:weeks', '6:months', '1:years'."""
    # Custom recurrence: "N:unit"
    if ":" in recurrence:
        parts = recurrence.split(":")
        try:
            n = int(parts[0]) if len(parts) > 0 else 1
        except (ValueError, TypeError):
            return None
        unit = parts[1] if len(parts) > 1 else "days"
        if unit == "days":
            return current + timedelta(days=n)
        elif unit == "weeks":
            return current + timedelta(weeks=n)
        elif unit == "months":
            month = current.month + n
            year = current.year
            while month > 12:
                month -= 12
                year += 1
            last_day = calendar.monthrange(year, month)[1]
            day = min(current.day, last_day)
            return date(year, month, day)
        elif unit == "years":
            target_year = current.year + n
            last_day = calendar.monthrange(target_year, current.month)[1]
            day = min(current.day, last_day)
            return date(target_year, current.month, day)
        return None

    # Standard recurrence
    if recurrence == "daily":
        return current + timedelta(days=1)
    elif recurrence == "weekly":
        return current + timedelta(days=7)
    elif recurrence == "monthly":
        month = current.month + 1
        year = current.year
        if month > 12:
            month = 1
            year += 1
        last_day = calendar.monthrange(year, month)[1]
        day = min(current.day, last_day)
        return date(year, month, day)
    elif recurrence == "yearly":
        target_year = current.year + 1
        last_day = calendar.monthrange(target_year, current.month)[1]
        day = min(current.day, last_day)
        return date(target_year, current.month, day)
    return None
