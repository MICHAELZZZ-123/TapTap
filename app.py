#!/usr/bin/env python3.14
"""TapTap — desktop event reminders powered by Flask and pywebview."""

from __future__ import annotations

import argparse
import hmac
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import secrets
import sys
import webbrowser
from datetime import datetime

from filelock import FileLock, Timeout
from flask import Flask, jsonify, render_template, request, send_from_directory
from platformdirs import user_data_dir, user_log_dir

from database import EventDB
from reminders import NativeNotifier, ReminderEngine, ReminderWorker
from windows_integration import (
    AutostartError,
    AutostartManager,
    WindowsDesktopLifecycle,
    activate_existing_window,
)

# PyInstaller's no-console mode sets these to None on Windows and macOS.
# Supplying a sink keeps argparse and defensive print calls well-behaved.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

# Silence routine request logging; errors are still written to the app log.
logging.getLogger("werkzeug").setLevel(logging.ERROR)

# PACKAGING: PyInstaller extracts bundled files to sys._MEIPASS at runtime.
_BASE_DIR = (
    sys._MEIPASS if getattr(sys, "frozen", False)
    else os.path.dirname(__file__)
)
_DATA_DIR = Path(
    os.environ.get("TAPTAP_DATA_DIR") or user_data_dir("TapTap", appauthor=False)
).expanduser()
_LOG_DIR = Path(
    os.environ.get("TAPTAP_LOG_DIR") or user_log_dir("TapTap", appauthor=False)
).expanduser()
# SECURITY: This token is process-local; never persist it or replace it with a
# constant.
_API_TOKEN = secrets.token_urlsafe(32)
_DESKTOP_MODE = False
# INVARIANT (cache): Increment this whenever templates, JS, CSS, or icons change.
_ASSET_VERSION = "20260902-1"
autostart_manager = AutostartManager()

app = Flask(
    __name__,
    static_folder=None,
    template_folder=os.path.join(_BASE_DIR, "templates"),
)
# ROBUSTNESS: Keep services lazy so --help and guarded startup do not touch SQLite.
db: EventDB | None = None
reminder_engine: ReminderEngine | None = None
reminder_worker: ReminderWorker | None = None


def _initialize_runtime() -> None:
    """Create persistent runtime services only when the app actually starts."""
    global db, reminder_engine, reminder_worker

    if db is None:
        db = EventDB()
    if reminder_engine is None:
        reminder_engine = ReminderEngine(db)
    if reminder_worker is None:
        reminder_worker = ReminderWorker(
            reminder_engine,
            notifier=NativeNotifier(
                os.path.join(_BASE_DIR, "static", "app-icon.png")
            ),
        )

# ── Static files ───────────────────────────────────────────────────────────

@app.route("/static/<path:filename>")
def static_files(filename):
    resp = send_from_directory(os.path.join(_BASE_DIR, "static"), filename)
    resp.cache_control.max_age = 3600  # cache static assets for 1 hour
    return resp

# ── Routes ─────────────────────────────────────────────────────────────────

@app.before_request
def protect_local_api():
    """Require an unguessable per-process token for every local API call."""
    if request.path.startswith("/api/"):
        # SECURITY: Authenticate before initializing data services, even on loopback.
        supplied = request.headers.get("X-TapTap-Token", "")
        if not hmac.compare_digest(supplied, _API_TOKEN):
            return jsonify({"error": "invalid API token"}), 403
        try:
            _initialize_runtime()
        except Exception:
            logging.exception("Could not initialize TapTap's runtime services")
            return jsonify({"error": "TapTap data is unavailable"}), 503


@app.after_request
def prevent_stale_dynamic_responses(response):
    """Tokens and API data are process-local and must never be cached."""
    if request.path == "/" or request.path.startswith("/api/"):
        response.cache_control.no_store = True
    return response


@app.route("/")
def index():
    return render_template(
        "index.html",
        api_token=_API_TOKEN,
        desktop_mode=_DESKTOP_MODE,
        asset_version=_ASSET_VERSION,
    )


@app.route("/favicon.ico")
def favicon():
    """Serve the same centered vector artwork used by the desktop package."""
    return send_from_directory(
        os.path.join(_BASE_DIR, "static"),
        "app-icon.svg",
        mimetype="image/svg+xml",
        max_age=3600,
    )


def _event_payload_error(data, *, require_name: bool) -> str | None:
    """Reject malformed JSON types while retaining the original field defaults."""
    if not isinstance(data, dict):
        return "JSON object required"
    if require_name and (not data.get("name") or not isinstance(data["name"], str)):
        return "name required"
    for field in (
        "name",
        "event_date",
        "event_time",
        "description",
        "recurrence",
        "category",
    ):
        if field in data and not isinstance(data[field], str):
            return f"{field} must be text"
    if "reminder_min" in data and (
        isinstance(data["reminder_min"], bool)
        or not isinstance(data["reminder_min"], (str, int))
    ):
        return "reminder_min must be text or an integer"
    return None


@app.route("/api/events")
def api_list_events():
    events = db.get_all_active()
    return jsonify(events)

@app.route("/api/events", methods=["POST"])
def api_add_event():
    data = request.get_json(silent=True)
    if error := _event_payload_error(data, require_name=True):
        return jsonify({"error": error}), 400
    eid = db.add_event(
        name=data["name"],
        event_date=data.get("event_date", ""),
        event_time=data.get("event_time", ""),
        description=data.get("description", ""),
        reminder_min=data.get("reminder_min", 15),
        recurrence=data.get("recurrence", "none"),
        category=data.get("category", ""),
    )
    return jsonify({"id": eid}), 201

@app.route("/api/events/<int:event_id>", methods=["PUT"])
def api_update_event(event_id):
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "body required"}), 400
    if error := _event_payload_error(data, require_name=False):
        return jsonify({"error": error}), 400
    db.update_event(
        event_id=event_id,
        name=data.get("name", ""),
        event_date=data.get("event_date", ""),
        event_time=data.get("event_time", ""),
        description=data.get("description", ""),
        reminder_min=data.get("reminder_min", 15),
        recurrence=data.get("recurrence", "none"),
        category=data.get("category", ""),
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
    minutes = max(1, min(1440, request.args.get("minutes", 5, type=int) or 5))
    db.snooze(event_id, minutes=minutes)
    return jsonify({"ok": True})

@app.route("/api/pending")
def api_pending():
    """Return UI status and popups already claimed by the background worker."""
    return jsonify(
        {
            **reminder_engine.snapshot(),
            "notification": reminder_worker.notification_status(),
            "reminders": reminder_worker.drain_pending(),
        }
    )

@app.route("/api/time")
def api_time():
    """Return the server's current time so the frontend can calibrate."""
    now = datetime.now()
    return jsonify({
        "iso": now.isoformat(),
        "timestamp": now.timestamp(),
    })


@app.route("/api/settings/autostart", methods=["GET", "PUT"])
def api_autostart():
    """Read or update TapTap's per-user Windows sign-in registration."""
    try:
        if request.method == "PUT":
            data = request.get_json(silent=True)
            if not isinstance(data, dict) or not isinstance(data.get("enabled"), bool):
                return jsonify({"error": "enabled must be true or false"}), 400
            status = autostart_manager.set_enabled(data["enabled"])
        else:
            status = autostart_manager.status()
        return jsonify(status.as_dict())
    except AutostartError as exc:
        logging.warning("Could not update TapTap autostart: %s", exc)
        return jsonify({"error": str(exc)}), 500

# ── Entry point ────────────────────────────────────────────────────────────

def _configure_logging() -> None:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = _LOG_DIR / "taptap.log"
    handler = RotatingFileHandler(
        log_path,
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(
        isinstance(existing, RotatingFileHandler)
        and Path(existing.baseFilename) == log_path
        for existing in root.handlers
    ):
        root.addHandler(handler)
    else:
        handler.close()


def _show_message(title: str, message: str) -> None:
    """Show a useful error even in a Windows no-console build."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)
            return
        except Exception:
            pass
    print(f"{title}: {message}", file=sys.stderr)


def _desktop_icon_path(platform_name: str | None = None) -> str:
    """Return artwork in the format accepted by the native window backend."""
    target = platform_name or sys.platform
    # PACKAGING: WinForms System.Drawing.Icon requires ICO; passing PNG raises
    # an uncatchable CLR exception on pywebview's UI thread.
    filename = "app-icon.ico" if target == "win32" else "app-icon.png"
    return os.path.join(_BASE_DIR, "static", filename)


def _run_desktop(debug: bool = False, start_hidden: bool = False) -> None:
    global _DESKTOP_MODE

    import webview

    _DESKTOP_MODE = True
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    storage_path = _DATA_DIR / "webview"
    storage_path.mkdir(parents=True, exist_ok=True)
    icon_path = _desktop_icon_path()
    window_title = os.environ.get("TAPTAP_SMOKE_WINDOW_TITLE", "TapTap")

    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    window = webview.create_window(
        window_title,
        app,
        width=1100,
        height=760,
        min_size=(800, 600),
        background_color="#f4f7f8",
        text_select=True,
        hidden=start_hidden,
        focus=not start_hidden,
    )
    lifecycle = (
        WindowsDesktopLifecycle(
            window,
            icon_path,
            started_hidden=start_hidden,
            tray_text=os.environ.get("TAPTAP_SMOKE_TRAY_TEXT", "TapTap"),
        )
        if sys.platform == "win32"
        else None
    )
    try:
        webview.start(
            func=lifecycle.start if lifecycle is not None else None,
            debug=debug,
            gui="qt" if sys.platform.startswith("linux") else None,
            private_mode=False,
            storage_path=str(storage_path),
            icon=icon_path if os.path.exists(icon_path) else None,
        )
    finally:
        if lifecycle is not None:
            lifecycle.stop()


def _run_browser_mode(debug: bool = False, open_window: bool = True) -> None:
    """Developer fallback for headless/WSL environments."""
    global _DESKTOP_MODE

    import subprocess
    import threading
    from werkzeug.serving import make_server

    _DESKTOP_MODE = False
    try:
        # Preserve the original stable browser URL so saved notification
        # permission and bookmarks continue to work.
        server = make_server("127.0.0.1", 5000, app, threaded=True)
    except OSError:
        logging.warning("Port 5000 is unavailable; using a temporary port")
        server = make_server("127.0.0.1", 0, app, threaded=True)
    url = f"http://127.0.0.1:{server.server_port}"

    def open_browser() -> None:
        methods = []
        if os.path.exists("/mnt/c/Windows/System32/cmd.exe"):
            methods.append(
                lambda: subprocess.run(
                    ["/mnt/c/Windows/System32/cmd.exe", "/c", "start", url],
                    timeout=5,
                    capture_output=True,
                    check=False,
                )
            )
        methods.extend(
            [
                lambda: subprocess.run(
                    ["xdg-open", url], timeout=5, capture_output=True, check=False
                ),
                lambda: webbrowser.open(url),
            ]
        )
        for method in methods:
            try:
                method()
                return
            except Exception:
                continue

    if open_window:
        threading.Thread(target=open_browser, name="TapTapBrowser", daemon=True).start()
    if debug:
        print(f"TapTap browser mode running at {url}")
    server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    """Launch TapTap as a single-instance native desktop application."""
    # ROBUSTNESS: Parse first so informational flags work even if user data is broken.
    parser = argparse.ArgumentParser(description="TapTap desktop event reminders")
    parser.add_argument(
        "--browser",
        action="store_true",
        help="open the UI in a browser instead of a native window",
    )
    parser.add_argument("--debug", action="store_true", help="enable webview debugging")
    parser.add_argument(
        "--startup",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)

    try:
        _configure_logging()
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        # INVARIANT: Acquire this before the worker to prevent duplicate alerts.
        instance_lock = FileLock(str(_DATA_DIR / "TapTap.lock"))
        instance_lock.acquire(timeout=0)
    except Timeout:
        if args.startup:
            logging.info("Ignoring duplicate Windows startup launch")
            return 0
        if activate_existing_window(
            os.environ.get("TAPTAP_SMOKE_WINDOW_TITLE", "TapTap")
        ):
            logging.info("Activated the existing TapTap window")
            return 0
        _show_message("TapTap", "TapTap is already running.")
        return 0
    except Exception as exc:
        _show_message(
            "TapTap could not start",
            f"Could not prepare TapTap's data files:\n{exc}",
        )
        return 1

    worker_started = False
    try:
        try:
            # COMPATIBILITY: Repair only an existing opt-in registration, so a
            # normal launch never creates a Windows startup entry by itself.
            if autostart_manager.repair_if_registered():
                logging.info("Updated the Windows startup entry for this executable")
        except AutostartError:
            # A stale startup entry must not stop reminders from running. The UI
            # exposes the registration error when the user next changes the toggle.
            logging.exception("Could not repair the Windows startup entry")

        _initialize_runtime()
        assert reminder_worker is not None
        reminder_worker.start()
        worker_started = True
        if args.browser:
            _run_browser_mode(debug=args.debug, open_window=not args.no_open)
        else:
            _run_desktop(
                debug=args.debug,
                start_hidden=args.startup and sys.platform == "win32",
            )
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        logging.exception("TapTap failed to start")
        _show_message(
            "TapTap could not start",
            f"{exc}\n\nDetails were written to {_LOG_DIR / 'taptap.log'}.",
        )
        return 1
    finally:
        if worker_started:
            reminder_worker.stop()
        try:
            instance_lock.release()
        except Exception:
            logging.exception("Could not release the single-instance lock")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
