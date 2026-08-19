"""Windows sign-in startup, tray lifecycle, and existing-window activation."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import logging
import subprocess
import sys
import threading
import time
from typing import Any


_LOG = logging.getLogger(__name__)
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE_NAME = "TapTap"
_RUN_COMMAND_LIMIT = 260


class AutostartError(RuntimeError):
    """Raised when TapTap cannot read or update its Windows startup entry."""


class AutostartUnavailable(AutostartError):
    """Raised when sign-in startup is requested outside a frozen Windows build."""


@dataclass(frozen=True)
class AutostartStatus:
    supported: bool
    enabled: bool
    registered: bool
    needs_repair: bool = False
    reason: str | None = None

    def as_dict(self) -> dict[str, bool | str | None]:
        return {
            "supported": self.supported,
            "enabled": self.enabled,
            "registered": self.registered,
            "needs_repair": self.needs_repair,
            "reason": self.reason,
        }


class AutostartManager:
    """Own TapTap's per-user ``Run`` value without requiring elevation."""

    def __init__(
        self,
        *,
        platform_name: str | None = None,
        frozen: bool | None = None,
        executable: str | None = None,
        value_name: str = _RUN_VALUE_NAME,
        registry: Any | None = None,
    ) -> None:
        self.platform_name = platform_name or sys.platform
        self.frozen = (
            bool(getattr(sys, "frozen", False)) if frozen is None else bool(frozen)
        )
        self.executable = executable or sys.executable
        self.value_name = value_name
        self._registry_override = registry

    @property
    def supported(self) -> bool:
        return self.platform_name == "win32" and self.frozen

    @property
    def command(self) -> str:
        # list2cmdline follows Windows command-line quoting rules and keeps paths
        # containing spaces from turning into a different executable plus arguments.
        return subprocess.list2cmdline([self.executable, "--startup"])

    def _registry(self):
        if self._registry_override is not None:
            return self._registry_override
        if self.platform_name != "win32":
            raise AutostartUnavailable(
                "Start with Windows is available only in the packaged Windows app."
            )
        import winreg

        return winreg

    def _require_supported(self) -> None:
        if not self.supported:
            raise AutostartUnavailable(
                "Start with Windows is available only in the packaged Windows app."
            )

    def _validate_command(self) -> None:
        if len(self.command) > _RUN_COMMAND_LIMIT:
            raise AutostartError(
                "TapTap's executable path is too long for Windows startup. "
                "Move TapTap.exe to a shorter path and try again."
            )

    def _read_registered_command(self) -> str | None:
        registry = self._registry()
        try:
            with registry.OpenKey(
                registry.HKEY_CURRENT_USER,
                _RUN_KEY,
                0,
                registry.KEY_READ,
            ) as key:
                command, _value_type = registry.QueryValueEx(key, self.value_name)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise AutostartError(f"Could not read Windows startup settings: {exc}") from exc
        return str(command)

    def status(self) -> AutostartStatus:
        if not self.supported:
            return AutostartStatus(
                supported=False,
                enabled=False,
                registered=False,
                reason="Available in the packaged Windows app.",
            )
        # INVARIANT: A registry value is still user-enabled when it points at an
        # older portable copy; expose repair separately so the UI does not
        # silently turn an opt-in setting off.
        current = self._read_registered_command()
        registered = current is not None
        matches = current == self.command
        return AutostartStatus(
            supported=True,
            enabled=registered,
            registered=registered,
            needs_repair=registered and not matches,
        )

    def _write_current_command(self) -> None:
        self._require_supported()
        self._validate_command()
        registry = self._registry()
        try:
            with registry.CreateKeyEx(
                registry.HKEY_CURRENT_USER,
                _RUN_KEY,
                0,
                registry.KEY_SET_VALUE,
            ) as key:
                registry.SetValueEx(
                    key,
                    self.value_name,
                    0,
                    registry.REG_SZ,
                    self.command,
                )
        except OSError as exc:
            raise AutostartError(f"Could not enable Windows startup: {exc}") from exc

    def set_enabled(self, enabled: bool) -> AutostartStatus:
        self._require_supported()
        if enabled:
            self._write_current_command()
        else:
            registry = self._registry()
            try:
                with registry.OpenKey(
                    registry.HKEY_CURRENT_USER,
                    _RUN_KEY,
                    0,
                    registry.KEY_SET_VALUE,
                ) as key:
                    registry.DeleteValue(key, self.value_name)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise AutostartError(
                    f"Could not disable Windows startup: {exc}"
                ) from exc
        return self.status()

    def repair_if_registered(self) -> bool:
        """Point an existing opt-in entry at a manually moved executable."""
        if not self.supported:
            return False
        current = self._read_registered_command()
        if current is None or current == self.command:
            return False
        self._write_current_command()
        return True


def activate_existing_window(
    title: str = "TapTap",
    *,
    timeout: float = 3.0,
    platform_name: str | None = None,
    user32: Any | None = None,
) -> bool:
    """Show and focus an existing hidden/minimized TapTap window on Windows."""
    if (platform_name or sys.platform) != "win32":
        return False

    api = user32 or ctypes.windll.user32
    if user32 is None:
        api.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        api.FindWindowW.restype = ctypes.c_void_p
        api.IsIconic.argtypes = [ctypes.c_void_p]
        api.IsIconic.restype = ctypes.c_bool
        api.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        api.ShowWindow.restype = ctypes.c_bool
        api.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        api.SetForegroundWindow.restype = ctypes.c_bool
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        handle = api.FindWindowW(None, title)
        if handle:
            # Preserve a maximized hidden window, but restore a minimized one.
            if api.IsIconic(handle):
                api.ShowWindow(handle, 9)  # SW_RESTORE
            else:
                api.ShowWindow(handle, 5)  # SW_SHOW
            api.SetForegroundWindow(handle)
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


class WindowsDesktopLifecycle:
    """Keep a pywebview WinForms window alive behind a native tray icon."""

    def __init__(self, window: Any, icon_path: str, *, started_hidden: bool) -> None:
        self.window = window
        self.icon_path = icon_path
        self.started_hidden = started_hidden
        self._quitting = threading.Event()
        self._ready = threading.Event()
        self._form = None
        self._tray = None
        self._menu = None
        self._icon = None
        self._backend = None
        self._close_hint_shown = False

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    def start(self) -> None:
        """Attach native controls after pywebview has created its WinForms form."""
        try:
            if not self.window.events.shown.wait(15):
                raise RuntimeError("TapTap's native window was not created")

            from webview.platforms import winforms as backend

            self._backend = backend
            form = backend.BrowserView.instances.get(self.window.uid)
            if form is None:
                raise RuntimeError("TapTap's WinForms window is unavailable")
            self._form = form

            def configure() -> None:
                tray = backend.WinForms.NotifyIcon()
                icon = backend.Icon(self.icon_path)
                menu = backend.WinForms.ContextMenuStrip()
                open_item = backend.WinForms.ToolStripMenuItem("Open TapTap")
                quit_item = backend.WinForms.ToolStripMenuItem("Quit TapTap")
                open_item.Click += self._on_open
                quit_item.Click += self._on_quit
                menu.Items.Add(open_item)
                menu.Items.Add(backend.WinForms.ToolStripSeparator())
                menu.Items.Add(quit_item)
                tray.Icon = icon
                tray.Text = "TapTap"
                tray.ContextMenuStrip = menu
                tray.MouseClick += self._on_tray_mouse_click
                tray.Visible = True

                # INVARIANT: Replace pywebview's generic close handler so user close hides the
                # form, while sign-out, shutdown, Task Manager, and explicit Quit
                # retain their native close semantics.
                form.FormClosing -= form.on_closing
                form.FormClosing += self._on_form_closing
                form.FormClosed += self._on_form_closed

                self._tray = tray
                self._menu = menu
                self._icon = icon

            self._invoke(configure)
            self._ready.set()
            _LOG.info(
                "Windows tray lifecycle ready (started_hidden=%s)",
                self.started_hidden,
            )
        except Exception:
            _LOG.exception("Could not initialize the Windows tray lifecycle")
            # A failed hidden launch must never strand an invisible process.
            if self.started_hidden:
                try:
                    self.window.show()
                except Exception:
                    _LOG.exception("Could not reveal TapTap after tray startup failed")

    def _invoke(self, callback) -> None:
        if self._form is None or self._backend is None:
            return
        if self._form.InvokeRequired:
            self._form.Invoke(self._backend.Func[self._backend.Type](callback))
        else:
            callback()

    def _show_form(self) -> None:
        if self._form is None or self._backend is None:
            return
        self._form.Show()
        if self._form.WindowState == self._backend.WinForms.FormWindowState.Minimized:
            self._form.WindowState = self._backend.WinForms.FormWindowState.Normal
        self._form.Activate()
        self._form.BringToFront()

    def _on_open(self, *_args) -> None:
        try:
            self._invoke(self._show_form)
        except Exception:
            _LOG.exception("Could not reopen TapTap from the tray")

    def _on_tray_mouse_click(self, _sender, args) -> None:
        """Open on one left-click while reserving right-click for the menu."""
        backend = self._backend
        # COMPATIBILITY: Filter the button here instead of using a double-click
        # event; WinForms still needs right-click untouched for ContextMenuStrip.
        if (
            backend is not None
            and args.Button == backend.WinForms.MouseButtons.Left
        ):
            self._on_open()

    def _on_quit(self, *_args) -> None:
        self._quitting.set()

        def close() -> None:
            if self._form is not None and not self._form.IsDisposed:
                self._form.Close()
            else:
                self._dispose_native()

        try:
            self._invoke(close)
        except Exception:
            _LOG.exception("Could not quit TapTap from the tray")

    def _on_form_closing(self, sender, args) -> None:
        backend = self._backend
        _LOG.info(
            "Windows form closing (reason=%s, quitting=%s)",
            args.CloseReason,
            self._quitting.is_set(),
        )
        if (
            backend is not None
            and not self._quitting.is_set()
            and args.CloseReason == backend.WinForms.CloseReason.UserClosing
        ):
            args.Cancel = True
            sender.Hide()
            if self._tray is not None and not self._close_hint_shown:
                self._close_hint_shown = True
                self._tray.ShowBalloonTip(
                    3000,
                    "TapTap is still running",
                    "Reminders will continue in the background. Use the tray icon to open or quit TapTap.",
                    backend.WinForms.ToolTipIcon.Info,
                )
            _LOG.info("TapTap window hidden to the notification area")
            return

        # Preserve pywebview closing callbacks for real process termination.
        if self.window.events.closing.set():
            args.Cancel = True

    def _on_form_closed(self, *_args) -> None:
        self._quitting.set()
        self._dispose_native()

    def _dispose_native(self) -> None:
        # ROBUSTNESS: Clear references before Dispose because form-close and tray
        # callbacks can both arrive during application shutdown.
        tray, menu, icon = self._tray, self._menu, self._icon
        self._tray = None
        self._menu = None
        self._icon = None
        if tray is not None:
            tray.Visible = False
            tray.Dispose()
        if menu is not None:
            menu.Dispose()
        if icon is not None:
            icon.Dispose()

    def stop(self) -> None:
        self._quitting.set()
        try:
            self._invoke(self._dispose_native)
        except Exception:
            # The WinForms form may already be disposed during application exit.
            _LOG.debug("Tray controls were already disposed", exc_info=True)
