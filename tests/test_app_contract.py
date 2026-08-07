from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path


class _IdParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()

    def handle_starttag(self, tag, attrs) -> None:
        del tag
        for name, value in attrs:
            if name == "id" and value:
                self.ids.add(value)


class FakeNotifier:
    def __init__(self, result: bool = False) -> None:
        self.result = result
        self.sent: list[tuple[str, str]] = []

    def send(self, title: str, message: str) -> bool:
        self.sent.append((title, message))
        return self.result

    def close(self) -> None:
        pass


class AppContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bootstrap_dir = tempfile.TemporaryDirectory()
        cls.previous_env = {
            name: os.environ.get(name)
            for name in ("TAPTAP_DB_PATH", "TAPTAP_DATA_DIR", "TAPTAP_LOG_DIR")
        }
        root = Path(cls.bootstrap_dir.name)
        os.environ["TAPTAP_DB_PATH"] = str(root / "bootstrap.db")
        os.environ["TAPTAP_DATA_DIR"] = str(root / "data")
        os.environ["TAPTAP_LOG_DIR"] = str(root / "logs")

        import app as app_module

        cls.module = app_module
        cls.flask_app = app_module.app
        cls.flask_app.config.update(TESTING=True)

    @classmethod
    def tearDownClass(cls) -> None:
        for name, value in cls.previous_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        cls.bootstrap_dir.cleanup()

    def setUp(self) -> None:
        from database import EventDB
        from reminders import ReminderEngine, ReminderWorker

        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = EventDB(Path(self.temp_dir.name) / "events.db")
        self.module.db = self.db
        self.module.reminder_engine = ReminderEngine(self.db)
        self.module.reminder_worker = ReminderWorker(
            self.module.reminder_engine,
            notifier=FakeNotifier(),
        )
        self.client = self.flask_app.test_client()
        self.headers = {"X-TapTap-Token": self.module._API_TOKEN}

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_original_route_contract_is_preserved(self) -> None:
        # COMPATIBILITY: Packaging work must not remove or rename original routes.
        actual = {
            (rule.rule, frozenset(rule.methods - {"HEAD", "OPTIONS"}))
            for rule in self.flask_app.url_map.iter_rules()
        }
        expected = {
            ("/", frozenset({"GET"})),
            ("/favicon.ico", frozenset({"GET"})),
            ("/static/<path:filename>", frozenset({"GET"})),
            ("/api/events", frozenset({"GET"})),
            ("/api/events", frozenset({"POST"})),
            ("/api/events/<int:event_id>", frozenset({"PUT"})),
            ("/api/events/<int:event_id>", frozenset({"DELETE"})),
            ("/api/events/<int:event_id>/restore", frozenset({"POST"})),
            ("/api/events/<int:event_id>/snooze", frozenset({"POST"})),
            ("/api/history", frozenset({"GET"})),
            ("/api/pending", frozenset({"GET"})),
            ("/api/time", frozenset({"GET"})),
        }
        self.assertEqual(actual, expected)

    def test_help_does_not_open_the_database(self) -> None:
        blocked_parent = Path(self.temp_dir.name) / "not-a-directory"
        blocked_parent.write_text("file", encoding="utf-8")
        env = os.environ.copy()
        env["TAPTAP_DB_PATH"] = str(blocked_parent / "events.db")

        result = subprocess.run(
            [sys.executable, str(Path(self.module.__file__)), "--help"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("TapTap desktop event reminders", result.stdout)

    def test_original_ui_controls_and_actions_are_preserved(self) -> None:
        # COMPATIBILITY: IDs and callable actions are the frontend's stable contract.
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        parser = _IdParser()
        parser.feed(response.get_data(as_text=True))
        expected_ids = {
            "btn-cancel", "btn-delete-sel", "btn-save", "btn-select-all",
            "btn-select-mode", "clock", "countdown", "custom-cat",
            "custom-cat-name", "custom-n", "custom-recur", "custom-unit",
            "edit-id", "empty", "ev-category", "ev-date", "ev-desc",
            "ev-name", "ev-recurrence", "ev-reminder", "ev-time",
            "event-list", "form-card", "form-title", "history-actions",
            "history-list", "history-panel", "history-toggle", "mode-dark",
            "mode-light", "mode-switch", "selected-count", "status",
        }
        self.assertEqual(parser.ids, expected_ids)

        source_root = Path(self.module._BASE_DIR) / "static"
        scripts = "\n".join(
            (source_root / name).read_text(encoding="utf-8")
            for name in ("app.js", "history.js")
        )
        actions = set(
            re.findall(r"^(?:async )?function ([A-Za-z0-9_]+)", scripts, re.MULTILINE)
        )
        expected_actions = {
            "setMode", "playBeep", "calibrateClock", "calibratedNow",
            "localDateValue", "recurrenceLabel",
            "_scheduleNextPoll", "updateClock", "updateCountdown", "showToast",
            "requestNotifyPermission", "fireReminder", "api", "loadEvents",
            "esc", "saveEvent", "startEdit", "toggleCustomRecur",
            "toggleCustomCat", "refreshCategoryOptions", "cancelEdit",
            "updateCategoryIcon", "deleteEvent", "snoozeEvent", "pollReminders",
            "_renderHistory",
            "toggleHistory", "updateSelected", "toggleSelectMode",
            "toggleSelectAll", "deleteSelected", "deleteOneHist", "reuseEvent",
        }
        self.assertTrue(expected_actions <= actions)

    def test_dynamic_page_is_not_cached_and_assets_are_versioned(self) -> None:
        response = self.client.get("/")
        html = response.get_data(as_text=True)

        self.assertIn("no-store", response.headers.get("Cache-Control", ""))
        self.assertIn(f'/static/app.js?v={self.module._ASSET_VERSION}', html)
        self.assertIn(f'/static/history.js?v={self.module._ASSET_VERSION}', html)
        self.assertIn(f'/static/style.css?v={self.module._ASSET_VERSION}', html)
        self.assertIn(self.module._API_TOKEN, html)

    def test_history_reuse_clears_edit_mode_before_loading_template(self) -> None:
        source = (
            Path(self.module._BASE_DIR) / "static" / "history.js"
        ).read_text(encoding="utf-8")
        reuse_body = source.split("async function reuseEvent", 1)[1]
        self.assertLess(
            reuse_body.index("cancelEdit();"),
            reuse_body.index("document.getElementById('ev-name').value"),
        )

    def test_release_metadata_is_synchronized(self) -> None:
        version = "0.2.1"
        root = Path(self.module.__file__).resolve().parent

        self.assertIn(
            f"## [{version}]",
            (root / "CHANGELOG.md").read_text(encoding="utf-8"),
        )
        self.assertIn(
            f"Current source release: **{version}**",
            (root / "README.md").read_text(encoding="utf-8"),
        )
        self.assertIn(
            f'"CFBundleShortVersionString": "{version}"',
            (root / "taptap.spec").read_text(encoding="utf-8"),
        )
        windows_metadata = (root / "version_info.txt").read_text(encoding="utf-8")
        self.assertIn(f"StringStruct('FileVersion', '{version}')", windows_metadata)
        self.assertIn(f"StringStruct('ProductVersion', '{version}')", windows_metadata)

    def test_category_icon_assets_are_available(self) -> None:
        icons = (
            "circle-outline.svg",
            "briefcase.svg",
            "user.svg",
            "heart.svg",
            "tag.svg",
            "star.svg",
        )
        for icon in icons:
            with self.subTest(icon=icon):
                with self.client.get(f"/static/icons/{icon}") as response:
                    self.assertEqual(response.status_code, 200)
                    self.assertIn(b"<svg", response.data)

    def test_desktop_icon_format_matches_native_backend(self) -> None:
        windows_icon = Path(self.module._desktop_icon_path("win32"))
        other_icon = Path(self.module._desktop_icon_path("linux"))

        # Regression guard: WinForms crashes on its UI thread if given a PNG.
        self.assertEqual(windows_icon.name, "app-icon.ico")
        self.assertEqual(other_icon.name, "app-icon.png")
        self.assertTrue(windows_icon.is_file())
        self.assertTrue(other_icon.is_file())

    def test_api_requires_the_process_token(self) -> None:
        self.assertEqual(self.client.get("/api/events").status_code, 403)
        response = self.client.get("/api/events", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), [])

    def test_malformed_event_payloads_return_json_errors(self) -> None:
        cases = (
            ["not", "an", "object"],
            {"name": ["not", "text"]},
            {"name": "Meeting", "event_date": None},
            {"name": "Meeting", "reminder_min": {"minutes": 5}},
        )
        for payload in cases:
            with self.subTest(payload=payload):
                response = self.client.post(
                    "/api/events", json=payload, headers=self.headers
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("error", response.get_json())

    def test_crud_history_restore_and_permanent_delete(self) -> None:
        payload = {
            "name": "Review <draft>",
            "event_date": "2030-04-05",
            "event_time": "14:30",
            "description": "Notes & links",
            "reminder_min": "60,30,10",
            "recurrence": "2:weeks",
            "category": "project-a",
        }
        created = self.client.post("/api/events", json=payload, headers=self.headers)
        self.assertEqual(created.status_code, 201)
        event_id = created.get_json()["id"]

        events = self.client.get("/api/events", headers=self.headers).get_json()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["name"], payload["name"])
        self.assertEqual(events[0]["reminder_min"], payload["reminder_min"])

        updated = {**payload, "name": "Final review", "event_time": "15:00"}
        response = self.client.put(
            f"/api/events/{event_id}", json=updated, headers=self.headers
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.db.get_event(event_id)["name"], "Final review")

        deleted = self.client.delete(f"/api/events/{event_id}", headers=self.headers)
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.get_json()["name"], "Final review")
        history = self.client.get("/api/history", headers=self.headers).get_json()
        self.assertEqual([event["id"] for event in history], [event_id])

        restored = self.client.post(
            f"/api/events/{event_id}/restore", headers=self.headers
        )
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(self.db.get_event(event_id)["active"], 1)

        self.client.delete(
            f"/api/events/{event_id}?permanent=1", headers=self.headers
        )
        self.assertIsNone(self.db.get_event(event_id))

    def test_snooze_and_pending_popup_flow(self) -> None:
        event_id = self.db.add_event(
            name="Stand-up",
            event_date="2030-01-02",
            event_time="12:00",
            reminder_min="30",
        )
        snoozed = self.client.post(
            f"/api/events/{event_id}/snooze?minutes=0", headers=self.headers
        )
        self.assertEqual(snoozed.status_code, 200)
        self.assertEqual(self.db.get_event(event_id)["active"], 1)

        with self.db._connect() as connection:
            connection.execute(
                "UPDATE events SET snooze_until=NULL, last_reminded=NULL WHERE id=?",
                (event_id,),
            )
            connection.commit()
        queued = self.module.reminder_worker.process_once(
            self.module.datetime(2030, 1, 2, 11, 35)
        )
        self.assertEqual(len(queued), 1)

        pending = self.client.get("/api/pending", headers=self.headers).get_json()
        self.assertEqual([item["id"] for item in pending["reminders"]], [event_id])
        drained = self.client.get("/api/pending", headers=self.headers).get_json()
        self.assertEqual(drained["reminders"], [])


if __name__ == "__main__":
    unittest.main()
