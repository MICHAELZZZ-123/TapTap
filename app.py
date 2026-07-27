#!/usr/bin/env python3.14
"""TapTap — Automatic Event Reminders.

A browser-based desktop reminder app with on-screen notifications.
"""

from __future__ import annotations

import logging as _logging
import os as _os
import sys
import webbrowser
from datetime import datetime, timedelta

from flask import Flask, Response, jsonify, render_template, request, send_from_directory

from database import EventDB
from utils import combine_datetime, fmt_datetime, human_duration, parse_offsets, parse_reminded

# Silence Flask dev-server banner
_logging.getLogger("werkzeug").setLevel(_logging.ERROR)

# PyInstaller support: files are extracted to sys._MEIPASS at runtime
_BASE_DIR = (
    sys._MEIPASS if getattr(sys, "frozen", False)
    else _os.path.dirname(__file__)
)

app = Flask(
    __name__,
    static_folder=None,
    template_folder=_os.path.join(_BASE_DIR, "templates"),
)
db = EventDB()

# Tiny inline favicon to stop 404 noise
_FAVICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<circle cx="16" cy="16" r="14" fill="#0097a7"/>'
    '<text x="16" y="22" text-anchor="middle" font-size="20">⏰</text>'
    "</svg>"
)

# ── Static files ───────────────────────────────────────────────────────────

@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(_os.path.join(_BASE_DIR, "static"), filename)

# ── Routes ─────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/favicon.ico")
def favicon():
    return Response(_FAVICON, mimetype="image/svg+xml")

@app.route("/api/events")
def api_list_events():
    events = db.get_all_active()
    return jsonify(events)

@app.route("/api/events", methods=["POST"])
def api_add_event():
    data = request.get_json()
    if not data or not data.get("name"):
        return jsonify({"error": "name required"}), 400
    eid = db.add_event(
        name=data["name"],
        event_date=data.get("event_date", ""),
        event_time=data.get("event_time", ""),
        description=data.get("description", ""),
        reminder_min=data.get("reminder_min", 15),
        recurrence=data.get("recurrence", "none"),
    )
    return jsonify({"id": eid}), 201

@app.route("/api/events/<int:event_id>", methods=["PUT"])
def api_update_event(event_id):
    data = request.get_json()
    if not data:
        return jsonify({"error": "body required"}), 400
    db.update_event(
        event_id=event_id,
        name=data.get("name", ""),
        event_date=data.get("event_date", ""),
        event_time=data.get("event_time", ""),
        description=data.get("description", ""),
        reminder_min=data.get("reminder_min", 15),
        recurrence=data.get("recurrence", "none"),
    )
    return jsonify({"ok": True})

@app.route("/api/events/<int:event_id>", methods=["DELETE"])
def api_delete_event(event_id):
    permanent = request.args.get("permanent") == "1"
    ev = db.get_event(event_id)
    if permanent:
        db.delete_event(event_id)
    else:
        db.deactivate(event_id)
    return jsonify({"ok": True, "name": ev["name"] if ev else "event"})


@app.route("/api/events/<int:event_id>/restore", methods=["POST"])
def api_restore_event(event_id):
    """Undo a recent deletion."""
    with db._connect() as conn:
        conn.execute("UPDATE events SET active=1 WHERE id=?", (event_id,))
        conn.commit()
    return jsonify({"ok": True})


@app.route("/api/history")
def api_history():
    """Return inactive events (deleted / completed) as templates."""
    with db._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE active=0 "
            "ORDER BY event_date DESC, event_time DESC LIMIT 30"
        ).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/events/<int:event_id>/snooze", methods=["POST"])
def api_snooze_event(event_id):
    db.snooze(event_id, minutes=5)
    return jsonify({"ok": True})

@app.route("/api/pending")
def api_pending():
    """Check for due reminders and return them. Runs inline so timing is
    always aligned with the frontend poll — no race conditions."""
    now = datetime.now()
    fired: list[dict] = []
    events = db.get_all_active()

    for ev in events:
        # Skip snoozed events
        if ev["snooze_until"]:
            try:
                until = datetime.strptime(ev["snooze_until"], "%Y-%m-%d %H:%M:%S")
                if now < until:
                    continue
            except ValueError:
                pass
        try:
            event_dt = combine_datetime(ev["event_date"], ev["event_time"])
        except ValueError:
            continue

        # Parse reminder offsets: single value like "15" or multiple "60,30,10"
        offsets = parse_offsets(ev["reminder_min"])
        # Which offsets have already fired for this occurrence?
        already_reminded: set[int] = parse_reminded(ev["last_reminded"])

        for offset in offsets:
            if offset in already_reminded:
                continue
            reminder_dt = event_dt - timedelta(minutes=offset)
            if now < reminder_dt:
                continue

            # Fire this reminder
            time_until = event_dt - now
            if time_until.total_seconds() < 0:
                when = f"Started {human_duration(-time_until)} ago"
            elif time_until.total_seconds() < 60:
                when = "Starting now!"
            else:
                when = f"In {human_duration(time_until)}"

            title = ev["name"]
            msg = f"{fmt_datetime(ev['event_date'], ev['event_time'])} — {when}"
            if ev["description"]:
                msg += f"\n{ev['description']}"

            fired.append({"title": title, "message": msg, "id": ev["id"]})
            db.mark_reminded(ev["id"], offset)

        if now > event_dt:
            if ev["recurrence"] != "none":
                db.advance_recurring(ev["id"], ev["event_date"], ev["recurrence"])
            else:
                db.deactivate(ev["id"])

    return jsonify({
        "reminders": fired,
        "status": f"{len(events)} event(s)  |  Polling every 1 s",
    })

@app.route("/api/time")
def api_time():
    """Return the server's current time so the frontend can calibrate."""
    now = datetime.now()
    return jsonify({
        "iso": now.isoformat(),
        "timestamp": now.timestamp(),
    })

# ── Entry point ────────────────────────────────────────────────────────────

def main():
    """Launch the TapTap — opens in your default browser."""

    import subprocess
    import threading

    # Purge history older than 90 days
    with db._connect() as conn:
        conn.execute(
            "DELETE FROM events WHERE active=0 AND "
            "event_date < date('now','localtime','-90 days')"
        )
        conn.commit()

    url = "http://127.0.0.1:5000"

    def open_browser():
        # Try Windows browser (WSL2), then Linux, then python fallback
        for method in [
            lambda: subprocess.run(
                ["/mnt/c/Windows/System32/cmd.exe", "/c", "start", url],
                timeout=5, capture_output=True,
            ),
            lambda: subprocess.run(
                ["xdg-open", url], timeout=5, capture_output=True,
            ),
            lambda: webbrowser.open(url),
        ]:
            try:
                method()
                return
            except Exception:
                continue

    threading.Thread(target=open_browser, daemon=True).start()

    print("\n  📅  TapTap running at " + url)
    print("  Press Ctrl+C to quit.\n")
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)

if __name__ == "__main__":
    main()
