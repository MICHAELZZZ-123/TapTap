from __future__ import annotations

from pathlib import Path
import sys
import threading
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import windows_integration

from windows_integration import (
    AutostartError,
    AutostartManager,
    PywebviewWinFormsAdapter,
    WindowsDesktopLifecycle,
    _startup_executable,
    activate_existing_window,
)


class _FakeKey:
    def __init__(self, registry: "_FakeRegistry") -> None:
        self.registry = registry

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None


class _FakeRegistry:
    HKEY_CURRENT_USER = object()
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self) -> None:
        self.key_exists = False
        self.values: dict[str, str] = {}

    def OpenKey(self, *_args):
        if not self.key_exists:
            raise FileNotFoundError
        return _FakeKey(self)

    def CreateKeyEx(self, *_args):
        self.key_exists = True
        return _FakeKey(self)

    def QueryValueEx(self, _key, name: str):
        if name not in self.values:
            raise FileNotFoundError
        return self.values[name], self.REG_SZ

    def SetValueEx(self, _key, name: str, _reserved, _kind, value: str) -> None:
        self.values[name] = value

    def DeleteValue(self, _key, name: str) -> None:
        if name not in self.values:
            raise FileNotFoundError
        del self.values[name]


class _FakeUser32:
    def __init__(self, *, handle: int = 42, iconic: bool = False) -> None:
        self.handle = handle
        self.iconic = iconic
        self.calls: list[tuple] = []

    def FindWindowW(self, class_name, title):
        self.calls.append(("find", class_name, title))
        return self.handle

    def IsIconic(self, handle):
        self.calls.append(("iconic", handle))
        return self.iconic

    def ShowWindow(self, handle, command):
        self.calls.append(("show", handle, command))
        return True

    def SetForegroundWindow(self, handle):
        self.calls.append(("foreground", handle))
        return True


class _FakeClosingEvent:
    def __init__(self, *, cancel: bool = False) -> None:
        self.cancel = cancel
        self.calls = 0

    def set(self) -> bool:
        self.calls += 1
        return self.cancel


class _FakeWindow:
    def __init__(self, closing: _FakeClosingEvent) -> None:
        self.events = SimpleNamespace(closing=closing)


class _FakeForm:
    def __init__(self) -> None:
        self.hide_calls = 0

    def Hide(self) -> None:
        self.hide_calls += 1


class WindowsIntegrationTests(unittest.TestCase):
    def test_autostart_round_trip_and_moved_executable_repair(self) -> None:
        registry = _FakeRegistry()
        original = AutostartManager(
            platform_name="win32",
            frozen=True,
            executable=r"C:\Program Files\TapTap\TapTap.exe",
            registry=registry,
        )

        self.assertFalse(original.status().enabled)
        enabled = original.set_enabled(True)
        self.assertTrue(enabled.enabled)
        self.assertEqual(
            registry.values["TapTap"],
            r'"C:\Program Files\TapTap\TapTap.exe" --startup',
        )

        moved = AutostartManager(
            platform_name="win32",
            frozen=True,
            executable=r"D:\Apps\TapTap.exe",
            registry=registry,
            path_exists=lambda _path: False,
        )
        stale = moved.status()
        self.assertTrue(stale.enabled)
        self.assertTrue(stale.needs_repair)
        self.assertTrue(moved.repair_if_registered())
        self.assertEqual(registry.values["TapTap"], r"D:\Apps\TapTap.exe --startup")
        self.assertFalse(moved.status().needs_repair)

        disabled = moved.set_enabled(False)
        self.assertFalse(disabled.enabled)
        self.assertNotIn("TapTap", registry.values)

    def test_autostart_preserves_an_existing_alternate_copy(self) -> None:
        registry = _FakeRegistry()
        registry.key_exists = True
        registry.values["TapTap"] = (
            r'"C:\Program Files\TapTap\TapTap.exe" --startup'
        )
        manager = AutostartManager(
            platform_name="win32",
            frozen=True,
            executable=r"D:\Downloads\TapTap.exe",
            registry=registry,
            path_exists=lambda path: path == r"C:\Program Files\TapTap\TapTap.exe",
        )

        status = manager.status()
        self.assertTrue(status.enabled)
        self.assertFalse(status.needs_repair)
        self.assertIn("another existing TapTap copy", status.reason or "")
        self.assertFalse(manager.repair_if_registered())
        self.assertEqual(
            registry.values["TapTap"],
            r'"C:\Program Files\TapTap\TapTap.exe" --startup',
        )

        # Turning the setting on is an explicit user action and may transfer it.
        manager.set_enabled(True)
        self.assertEqual(
            registry.values["TapTap"], r"D:\Downloads\TapTap.exe --startup"
        )

    def test_autostart_repairs_missing_and_invalid_targets_only(self) -> None:
        self.assertEqual(
            _startup_executable(
                r'"C:\Program Files\TapTap\TapTap.exe" --startup'
            ),
            r"C:\Program Files\TapTap\TapTap.exe",
        )
        self.assertEqual(
            _startup_executable(r"D:\Apps\TapTap.exe --startup"),
            r"D:\Apps\TapTap.exe",
        )
        self.assertIsNone(
            _startup_executable(r"C:\Program Files\TapTap\TapTap.exe --startup")
        )
        self.assertIsNone(_startup_executable(r"D:\Apps\TapTap.exe --other"))

    def test_autostart_rejects_an_overlong_run_command(self) -> None:
        registry = _FakeRegistry()
        manager = AutostartManager(
            platform_name="win32",
            frozen=True,
            executable="C:\\" + ("deep\\" * 60) + "TapTap.exe",
            registry=registry,
        )

        with self.assertRaisesRegex(AutostartError, "path is too long"):
            manager.set_enabled(True)

        registry.key_exists = True
        registry.values["TapTap"] = r"C:\Old\TapTap.exe --startup"
        self.assertFalse(manager.set_enabled(False).enabled)

    def test_autostart_is_unavailable_outside_frozen_windows_app(self) -> None:
        manager = AutostartManager(
            platform_name="linux",
            frozen=False,
            executable="/tmp/TapTap",
            registry=_FakeRegistry(),
        )

        status = manager.status()
        self.assertFalse(status.supported)
        self.assertFalse(status.enabled)
        with self.assertRaises(AutostartError):
            manager.set_enabled(True)

    def test_existing_window_is_shown_and_focused(self) -> None:
        user32 = _FakeUser32()

        self.assertTrue(
            activate_existing_window(
                platform_name="win32",
                user32=user32,
                timeout=0,
            )
        )
        self.assertIn(("show", 42, 5), user32.calls)
        self.assertIn(("foreground", 42), user32.calls)

    def test_existing_minimized_window_is_restored(self) -> None:
        user32 = _FakeUser32(iconic=True)

        self.assertTrue(
            activate_existing_window(
                platform_name="win32",
                user32=user32,
                timeout=0,
            )
        )
        self.assertIn(("show", 42, 9), user32.calls)

    def test_user_close_hides_but_windows_shutdown_can_terminate(self) -> None:
        closing = _FakeClosingEvent()
        lifecycle = WindowsDesktopLifecycle(
            _FakeWindow(closing),
            "unused.ico",
            started_hidden=False,
        )
        lifecycle._adapter = SimpleNamespace(
            is_user_close=lambda args: args.CloseReason == "user",
            show_background_hint=Mock(),
        )
        lifecycle._quitting = threading.Event()
        form = _FakeForm()

        user_args = SimpleNamespace(CloseReason="user", Cancel=False)
        lifecycle._on_form_closing(form, user_args)
        self.assertTrue(user_args.Cancel)
        self.assertEqual(form.hide_calls, 1)
        self.assertEqual(closing.calls, 0)

        shutdown_args = SimpleNamespace(CloseReason="windows-shutdown", Cancel=False)
        lifecycle._on_form_closing(form, shutdown_args)
        self.assertFalse(shutdown_args.Cancel)
        self.assertEqual(closing.calls, 1)

    def test_tray_single_left_click_opens_and_right_click_keeps_menu(self) -> None:
        lifecycle = WindowsDesktopLifecycle(
            _FakeWindow(_FakeClosingEvent()),
            "unused.ico",
            started_hidden=True,
        )
        lifecycle._adapter = SimpleNamespace(
            is_left_click=lambda args: args.Button == "left"
        )
        lifecycle._on_open = Mock()

        lifecycle._on_tray_mouse_click(None, SimpleNamespace(Button="right"))
        lifecycle._on_open.assert_not_called()

        lifecycle._on_tray_mouse_click(None, SimpleNamespace(Button="left"))
        lifecycle._on_open.assert_called_once_with()

        source = Path(windows_integration.__file__).read_text(encoding="utf-8")
        self.assertIn("tray.MouseClick += on_mouse_click", source)
        self.assertNotIn("tray.DoubleClick += self._on_open", source)

    def test_incompatible_pywebview_backend_fails_with_a_clear_message(self) -> None:
        backend = SimpleNamespace(
            BrowserView=SimpleNamespace(instances={"window-1": object()}),
            WinForms=SimpleNamespace(),
        )
        window = SimpleNamespace(uid="window-1")

        with self.assertRaisesRegex(RuntimeError, "pywebview 6.2.x"):
            PywebviewWinFormsAdapter(window, backend=backend)

    @unittest.skipUnless(sys.platform == "win32", "Windows registry test")
    def test_real_hkcu_registration_round_trip(self) -> None:
        value_name = f"TapTapTest-{uuid.uuid4()}"
        manager = AutostartManager(
            platform_name="win32",
            frozen=True,
            executable=sys.executable,
            value_name=value_name,
        )
        try:
            self.assertTrue(manager.set_enabled(True).enabled)
            self.assertTrue(manager.status().enabled)
        finally:
            manager.set_enabled(False)
        self.assertFalse(manager.status().enabled)


if __name__ == "__main__":
    unittest.main()
