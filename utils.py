"""Utility helpers — formatting, notifications, and constants."""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta

# ── Constants ──────────────────────────────────────────────────────────────

RECURRENCE_OPTIONS = [
    ("None", "none"),
    ("Daily", "daily"),
    ("Weekly", "weekly"),
    ("Monthly", "monthly"),
    ("Yearly", "yearly"),
]

# ── Date / time helpers ────────────────────────────────────────────────────

def fmt_datetime(d: str, t: str) -> str:
    """Format date + time for display, e.g. 'Sat Jul 26 14:30'."""
    try:
        dt = datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M")
        return dt.strftime("%a %b %d %H:%M")
    except ValueError:
        return f"{d} {t}"


def combine_datetime(d: str, t: str) -> datetime:
    """Parse date + time strings into a datetime object."""
    return datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M")


def human_duration(td: timedelta) -> str:
    """Convert timedelta to a human-readable string like '2h 15m'."""
    total_minutes = int(td.total_seconds() / 60)
    if total_minutes < 1:
        return "less than a minute"
    hours, minutes = divmod(total_minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    return " ".join(parts)


# ── Reminder offset parsing ────────────────────────────────────────────────

def parse_offsets(raw) -> list[int]:
    """Parse comma-separated reminder offsets. Accepts both ',' and '，'."""
    try:
        s = str(raw).replace("，", ",")
        return [int(x.strip()) for x in s.split(",") if x.strip()]
    except (ValueError, TypeError):
        return []


def parse_reminded(raw) -> set[int]:
    """Parse which offsets have been reminded.
    Legacy format (ISO datetime string) → treat as all reminded.
    New format (comma-separated ints) → parse."""
    if not raw:
        return set()
    s = str(raw)
    if ":" in s:  # legacy datetime format — all offsets already fired
        return {99999}  # sentinel: block all offsets
    try:
        s = s.replace("，", ",")
        return {int(x.strip()) for x in s.split(",") if x.strip()}
    except (ValueError, TypeError):
        return set()


# ── Native notification (best-effort) ──────────────────────────────────────

def try_native_notify(title: str, message: str):
    """Attempt a native desktop notification via notify-send (Linux).
    Fails silently — the TUI toast is the primary notification channel."""
    try:
        subprocess.run(
            ["notify-send", title, message, "--icon=appointment"],
            timeout=3,
            capture_output=True,
        )
    except Exception:
        pass
