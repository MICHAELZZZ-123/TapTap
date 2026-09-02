# TapTap

Native desktop event reminders built with Python, Flask, pywebview, and a local
SQLite database. The interface is HTML/CSS/JavaScript, hosted inside a normal
desktop window; no browser tab or terminal needs to stay open.

Current source release: **0.3.1**. See [CHANGELOG.md](CHANGELOG.md) for the
release description.

## Run a packaged build

- **Windows:** double-click `TapTap.exe`.
- **Linux:** make the file executable once with `chmod +x TapTap`, then launch
  it normally or run `./TapTap`.
- **macOS:** open `TapTap.app`. Publicly distributed builds must be signed for
  native notifications to work.

Minimizing the window keeps reminders active. On Windows, closing the window
hides TapTap to the notification area while reminder delivery continues; use
the tray icon's single left-click or **Open TapTap** action to reopen it, and
**Quit TapTap** to fully stop it. Pending deliveries survive an application
restart. If Windows blocks a native notification, TapTap retains it for retry
and uses a system sound plus the visible in-app alert as a background fallback.
Closing the window still quits TapTap on Linux and macOS. The operating system
may ask for notification permission on first use.

### What's new in the current build

- Windows can stay active in the notification area after its window is closed,
  with a one-click restore action and an explicit Quit command.
- **Start with Windows** can launch the packaged app hidden for the current
  Windows user, without administrator access.
- Reminder checks align to wall-clock seconds, with delayed startup and
  sleep/wake catch-up coalesced into one alert per event.
- Claimed reminders are stored in a durable delivery outbox until notification
  dispatch succeeds; failed native delivery is retried with bounded backoff.
- The refreshed keyboard shortcuts, category picker, live clock, and
  long-range countdown make regular scheduling faster to operate.

### Windows background and sign-in startup

The packaged Windows application shows an opt-in **Start with Windows** checkbox
in the header. When enabled, TapTap registers only the current user and launches
hidden in the notification area at sign-in; it never requires administrator
access. Opening `TapTap.exe` while that background instance is running restores
the existing window instead of starting another reminder worker.

Disabling the checkbox removes TapTap's startup entry. A different TapTap copy
does not take over an existing valid startup registration merely by being
opened. To transfer startup ownership, disable the setting in the registered
copy and enable it in the intended copy. TapTap repairs the entry automatically
only when the registered target is missing or invalid. Turn the setting off
before deleting the portable executable: without an installer, a deleted
program cannot remove its own Windows startup entry. Windows Startup Apps can
also disable the entry independently.

## Run from source

Python 3.14 is recommended.

```bash
python3.14 -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python app.py
```

For WSL, a headless machine, or frontend debugging in a regular browser:

```bash
python app.py --browser --debug
```

On Debian/Ubuntu, the Qt webview also needs its normal desktop runtime
libraries. Install any missing ones with:

```bash
sudo apt install libnss3 libxkbfile1 libxcb-cursor0 libxcb-icccm4 \
  libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-shape0 \
  libxcb-util1 libxcb-xkb1 libxkbcommon-x11-0
```

## Features

| Feature | Detail |
|---|---|
| Native desktop window | Lightweight OS webview instead of an external browser |
| Reliable reminders | Python worker checks on wall-clock second boundaries and stores delivery work durably until dispatched |
| Native notifications | Linux, macOS, and Windows notifications with retry and a permission-independent Windows fallback |
| Windows background mode | Close-to-tray with explicit Open and Quit actions while reminders continue |
| Windows sign-in startup | Optional per-user launch directly into the notification area |
| Reminders | Single or comma-separated offsets, such as `60, 30, 10` |
| Categories | Work, Personal, Health, Other, or custom colored labels |
| Keyboard shortcuts | Fixed Windows/macOS-aware bindings with a built-in Shortcuts reference |
| Snooze | Presets from 1–30 minutes plus a custom duration |
| Recurrence | Daily, weekly, monthly, yearly, or every N units |
| History | Reuse or permanently remove completed events |
| In-window alerts | Persistent bottom-right popup with dismiss and quick-snooze controls |
| Sound | Original two-tone reminder chirp |
| Undo delete | Three-second undo toast that pauses while hovered and supports `Ctrl+Z` |
| Countdown | Live seconds through years until the next reminder |
| Adaptive UI polling | Five seconds normally, one second near a reminder, paused while hidden |
| Past-event guard | Keeps the original “Gone is gone, my friend” rejection toast |
| Dark/light mode | Saved between desktop sessions |
| Single instance | A second Windows launch restores the running window instead of duplicating reminders |
| Local API protection | Every internal API call uses a random per-launch token |

## User data

Events remain in `~/.reminder_app.db`, preserving data from earlier TapTap
versions. Webview state and the single-instance lock use the platform's TapTap
application-data directory. Rotating logs are written to the platform's TapTap
log directory.

## Build a standalone desktop executable

PyInstaller must run on the operating system being targeted. A Linux/WSL build
cannot produce a Windows executable.

```bash
python -m pip install -r requirements.txt
python scripts/build_desktop.py
```

Outputs:

- Windows: `dist/TapTap.exe`
- Linux: `dist/TapTap`
- macOS: `dist/TapTap.app`

If you only have WSL but need a Windows `.exe`, push the source to GitHub and
run **Actions → Build desktop apps → Run workflow**. The included workflow
builds downloadable Windows and Linux artifacts on their native runners.

The spec bundles the complete `templates/` and `static/` trees, the native
webview runtime, notification support, application icons, and Windows version
metadata. The build helper also locates non-standard Linux `libpython`
installations automatically. On Linux it explicitly bundles Qt's NSS, XKB, and
XCB runtime libraries; if they are absent on a Debian/Ubuntu build machine, the
helper downloads and extracts the packages into `build/` without sudo.
Regenerate the icon files after changing the logo with:

```bash
python scripts/generate_icons.py
```

## Release checklist

1. Run `python -m unittest discover -s tests -v`.
2. Build on every target OS with a clean virtual environment.
3. Test add/edit/delete, notification permission, minimize, sleep/wake, snooze,
   recurrence, close-to-tray, sign-in startup, and a second launch.
4. Install and run on a clean standard-user machine.
5. Code-sign public Windows and macOS releases; notarize macOS builds.

Windows release builds must pass both native checks:

```powershell
./scripts/smoke_test_windows_window.ps1 -Executable ./dist/TapTap.exe
./scripts/smoke_test_windows_background.ps1 -Executable ./dist/TapTap.exe
```

Then perform the disruptive checks that automation must not simulate:

1. Enable **Start with Windows**, sign out, sign back in, and confirm that the
   registered executable starts hidden with one reminder worker.
2. Put Windows to sleep across a due reminder, wake it, and confirm that TapTap
   catches up once without duplicating the alert.
3. Deny Windows notification permission, trigger a reminder, and confirm that
   TapTap reports native delivery as unavailable, retains the delivery for
   retry, sounds the fallback, reveals its hidden window, and displays the
   in-app reminder; restore permission and verify a later native delivery.
4. Move the TapTap icon into the notification-area overflow and verify a real
   left-click, **Open TapTap**, and **Quit TapTap** from that location.
5. Shut down while TapTap is hidden, restart Windows, and confirm that startup
   is clean and the HKCU registration still points to the intended executable.

Record these as manual observations. Mocked close reasons or process-existence
checks are not evidence of a real sign-out, shutdown, sleep, or permission
transition.

## Project files

| File | Purpose |
|---|---|
| `app.py` | Flask API, authenticated local host, and desktop entry point |
| `reminders.py` | Background scheduler and native notification delivery |
| `windows_integration.py` | Windows autostart, notification-area lifecycle, and existing-window activation |
| `database.py` | SQLite storage and recurrence lifecycle |
| `utils.py` | Date and reminder parsing helpers |
| `templates/index.html` | HTML structure |
| `static/` | JavaScript, styles, icons, and application artwork |
| `taptap.spec` | Cross-platform PyInstaller configuration |
| `version_info.txt` | Windows executable metadata |
| `tests/` | API/UI compatibility, database, and reminder lifecycle tests |
| `FILE_NOTES.md` | File ownership, invariants, generated artifacts, and rebuild rules |

## Third-party artwork

The SVG interface icons are derived from
[Feather Icons](https://feathericons.com/) and used under the MIT License. See
[`static/icons/LICENSE-FEATHER.txt`](static/icons/LICENSE-FEATHER.txt) for the
copyright and complete license terms. TapTap itself is not offered under that
license.
