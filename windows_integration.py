"""Windows sign-in startup, tray lifecycle, and existing-window activation."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import logging
import os
import subprocess
import sys
import threading
import time
from typing import Any, Callable


_LOG = logging.getLogger(__name__)
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE_NAME = "TapTap"
_RUN_COMMAND_LIMIT = 260


def _startup_executable(command: str) -> str | None:
    """Extract the executable from TapTap's narrowly defined Run command."""
    value = command.strip()
    suffix = "--startup"
    if not value.lower().endswith(suffix):
        return None
    executable = value[: -len(suffix)].rstrip()
    if executable.startswith('"'):
        if len(executable) < 2 or not executable.endswith('"'):
            return None
        executable = executable[1:-1]
    elif any(character.isspace() for character in executable):
        # Windows requires executable paths containing spaces to be quoted.
        return None
    return executable or None


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
        path_exists: Callable[[str], bool] | None = None,
    ) -> None:
        self.platform_name = platform_name or sys.platform
        self.frozen = (
            bool(getattr(sys, "frozen", False)) if frozen is None else bool(frozen)
        )
        self.executable = executable or sys.executable
        self.value_name = value_name
        self._registry_override = registry
        self._path_exists = path_exists or os.path.isfile

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
        registered_executable = (
            _startup_executable(current) if current is not None else None
        )
        target_exists = bool(
            registered_executable and self._path_exists(registered_executable)
        )
        reason = None
        if registered and not matches:
            reason = (
                "Start with Windows points to another existing TapTap copy; "
                "TapTap left it unchanged."
                if target_exists
                else "The registered TapTap copy could not be found."
            )
        return AutostartStatus(
            supported=True,
            enabled=registered,
            registered=registered,
            needs_repair=registered and not matches and not target_exists,
            reason=reason,
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
        """Repair a stale opt-in entry without taking over from a valid copy."""
        if not self.supported:
            return False
        current = self._read_registered_command()
        if current is None or current == self.command:
            return False
        registered_executable = _startup_executable(current)
        if registered_executable and self._path_exists(registered_executable):
            _LOG.warning(
                "Preserving Windows startup entry owned by existing executable: %s",
                registered_executable,
            )
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


class PywebviewWinFormsAdapter:
    """Contain the small private pywebview surface needed by TapTap's tray."""

    EXPECTED_VERSION = "6.2.x"

    def __init__(self, window: Any, *, backend: Any | None = None) -> None:
        if backend is None:
            from webview.platforms import winforms as backend

        self.backend = backend
        try:
            forms = backend.WinForms
            form = backend.BrowserView.instances.get(window.uid)
            required_controls = (
                "CloseReason",
                "ContextMenuStrip",
                "FormWindowState",
                "MouseButtons",
                "NotifyIcon",
                "ToolStripMenuItem",
                "ToolStripSeparator",
                "ToolTipIcon",
            )
            missing = [name for name in required_controls if not hasattr(forms, name)]
            if missing:
                raise AttributeError(", ".join(missing))
            if not hasattr(backend, "Icon") or not hasattr(backend, "Func"):
                raise AttributeError("Icon/Func")
        except (AttributeError, TypeError) as exc:
            raise RuntimeError(
                "TapTap requires pywebview's Windows backend layout from "
                f"pywebview {self.EXPECTED_VERSION}; the installed backend is incompatible."
            ) from exc
        if form is None:
            raise RuntimeError("TapTap's WinForms window is unavailable")
        self.form = form

    def invoke(self, callback: Callable[[], None]) -> None:
        if self.form.InvokeRequired:
            self.form.Invoke(self.backend.Func[self.backend.Type](callback))
        else:
            callback()

    def create_tray(
        self,
        icon_path: str,
        *,
        tray_text: str,
        on_open: Callable[..., None],
        on_quit: Callable[..., None],
        on_mouse_click: Callable[..., None],
        on_form_closing: Callable[..., None],
        on_form_closed: Callable[..., None],
    ) -> tuple[Any, Any, Any]:
        forms = self.backend.WinForms
        tray = forms.NotifyIcon()
        icon = self.backend.Icon(icon_path)
        menu = forms.ContextMenuStrip()
        open_item = forms.ToolStripMenuItem("Open TapTap")
        quit_item = forms.ToolStripMenuItem("Quit TapTap")
        open_item.Click += on_open
        quit_item.Click += on_quit
        menu.Items.Add(open_item)
        menu.Items.Add(forms.ToolStripSeparator())
        menu.Items.Add(quit_item)
        tray.Icon = icon
        tray.Text = tray_text
        tray.ContextMenuStrip = menu
        tray.MouseClick += on_mouse_click
        tray.Visible = True

        # pywebview's default handler exits on a user close. TapTap replaces it
        # so only that close reason hides the window; system termination remains.
        self.form.FormClosing -= self.form.on_closing
        self.form.FormClosing += on_form_closing
        self.form.FormClosed += on_form_closed
        return tray, menu, icon

    def show_form(self) -> None:
        forms = self.backend.WinForms
        self.form.Show()
        if self.form.WindowState == forms.FormWindowState.Minimized:
            self.form.WindowState = forms.FormWindowState.Normal
        self.form.Activate()
        self.form.BringToFront()

    def is_left_click(self, args: Any) -> bool:
        return args.Button == self.backend.WinForms.MouseButtons.Left

    def is_user_close(self, args: Any) -> bool:
        return args.CloseReason == self.backend.WinForms.CloseReason.UserClosing

    def close_form(self, if_disposed: Callable[[], None]) -> None:
        if not self.form.IsDisposed:
            self.form.Close()
        else:
            if_disposed()

    def show_background_hint(self, tray: Any) -> None:
        tray.ShowBalloonTip(
            3000,
            "TapTap is still running",
            "Reminders will continue in the background. Use the tray icon to open or quit TapTap.",
            self.backend.WinForms.ToolTipIcon.Info,
        )

    @staticmethod
    def dispose_controls(tray: Any, menu: Any, icon: Any) -> None:
        if tray is not None:
            tray.Visible = False
            tray.Dispose()
        if menu is not None:
            menu.Dispose()
        if icon is not None:
            icon.Dispose()


class WindowsDesktopLifecycle:
    """Keep a pywebview WinForms window alive behind a native tray icon."""

    def __init__(
        self,
        window: Any,
        icon_path: str,
        *,
        started_hidden: bool,
        tray_text: str = "TapTap",
        adapter_factory: Callable[[Any], PywebviewWinFormsAdapter] = PywebviewWinFormsAdapter,
    ) -> None:
        self.window = window
        self.icon_path = icon_path
        self.started_hidden = started_hidden
        self.tray_text = tray_text
        self._adapter_factory = adapter_factory
        self._quitting = threading.Event()
        self._ready = threading.Event()
        self._tray = None
        self._menu = None
        self._icon = None
        self._adapter = None
        self._close_hint_shown = False

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    def start(self) -> None:
        """Attach native controls after pywebview has created its WinForms form."""
        try:
            if not self.window.events.shown.wait(15):
                raise RuntimeError("TapTap's native window was not created")

            self._adapter = self._adapter_factory(self.window)

            def configure() -> None:
                assert self._adapter is not None
                self._tray, self._menu, self._icon = self._adapter.create_tray(
                    self.icon_path,
                    tray_text=self.tray_text,
                    on_open=self._on_open,
                    on_quit=self._on_quit,
                    on_mouse_click=self._on_tray_mouse_click,
                    on_form_closing=self._on_form_closing,
                    on_form_closed=self._on_form_closed,
                )

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
        if self._adapter is None:
            return
        self._adapter.invoke(callback)

    def _show_form(self) -> None:
        if self._adapter is None:
            return
        self._adapter.show_form()

    def _on_open(self, *_args) -> None:
        try:
            self._invoke(self._show_form)
        except Exception:
            _LOG.exception("Could not reopen TapTap from the tray")

    def _on_tray_mouse_click(self, _sender, args) -> None:
        """Open on one left-click while reserving right-click for the menu."""
        # COMPATIBILITY: Filter the button here instead of using a double-click
        # event; WinForms still needs right-click untouched for ContextMenuStrip.
        if self._adapter is not None and self._adapter.is_left_click(args):
            self._on_open()

    def _on_quit(self, *_args) -> None:
        self._quitting.set()

        def close() -> None:
            if self._adapter is not None:
                self._adapter.close_form(self._dispose_native)

        try:
            self._invoke(close)
        except Exception:
            _LOG.exception("Could not quit TapTap from the tray")

    def _on_form_closing(self, sender, args) -> None:
        _LOG.info(
            "Windows form closing (reason=%s, quitting=%s)",
            args.CloseReason,
            self._quitting.is_set(),
        )
        if (
            self._adapter is not None
            and not self._quitting.is_set()
            and self._adapter.is_user_close(args)
        ):
            args.Cancel = True
            sender.Hide()
            if self._tray is not None and not self._close_hint_shown:
                self._close_hint_shown = True
                self._adapter.show_background_hint(self._tray)
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
        if self._adapter is not None:
            self._adapter.dispose_controls(tray, menu, icon)

    def stop(self) -> None:
        self._quitting.set()
        try:
            self._invoke(self._dispose_native)
        except Exception:
            # The WinForms form may already be disposed during application exit.
            _LOG.debug("Tray controls were already disposed", exc_info=True)
