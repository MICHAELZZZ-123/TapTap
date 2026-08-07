# TapTap

Native desktop event reminders built with Python, Flask, pywebview, and a local
SQLite database. The interface is HTML/CSS/JavaScript, hosted inside a normal
desktop window; no browser tab or terminal needs to stay open.

Current source release: **0.2.1**. See [CHANGELOG.md](CHANGELOG.md) for the
release description.

## Run a packaged build

- **Windows:** double-click `TapTap.exe`.
- **Linux:** make the file executable once with `chmod +x TapTap`, then launch
  it normally or run `./TapTap`.
- **macOS:** open `TapTap.app`. Publicly distributed builds must be signed for
  native notifications to work.

Minimizing the window keeps reminders active. Closing the window quits TapTap.
The operating system may ask for notification permission on first use.

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
| Reliable reminders | Python worker checks every second, including while minimized |
| Native notifications | Linux, macOS, and Windows notifications with browser fallback |
| Reminders | Single or comma-separated offsets, such as `60, 30, 10` |
| Categories | Work, Personal, Health, Other, or custom colored labels |
| Snooze | Presets from 1–30 minutes plus a custom duration |
| Recurrence | Daily, weekly, monthly, yearly, or every N units |
| History | Reuse or permanently remove completed events |
| In-window alerts | Persistent bottom-right popup with dismiss and quick-snooze controls |
| Sound | Original two-tone reminder chirp |
| Undo delete | Three-second undo toast that pauses while hovered |
| Countdown | Live seconds/minutes until the next reminder |
| Adaptive UI polling | Five seconds normally, one second near a reminder, paused while hidden |
| Past-event guard | Keeps the original “Gone is gone, my friend” rejection toast |
| Dark/light mode | Saved between desktop sessions |
| Single instance | A second launch exits instead of duplicating reminders |
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
   recurrence, and a second launch.
4. Install and run on a clean standard-user machine.
5. Code-sign public Windows and macOS releases; notarize macOS builds.

## Project files

| File | Purpose |
|---|---|
| `app.py` | Flask API, authenticated local host, and desktop entry point |
| `reminders.py` | Background scheduler and native notification delivery |
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
