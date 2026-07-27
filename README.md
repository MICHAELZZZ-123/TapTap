# TapTap

Desktop event reminder with on-screen notifications. Built with Flask + vanilla HTML/CSS/JS.

## Quick Start — Binary

Download `TapTap` from [Releases](../../releases), then in a terminal:

```bash
./TapTap
```

Open **http://127.0.0.1:5000** in your browser. Keep the terminal open (Ctrl+C to stop).

## Quick Start — Source

```bash
# Requirements: Python 3.14 (not 3.12)
python3.14 -m pip install flask
python3.14 app.py
```

## Notification Permission

On first launch your browser will ask *"127.0.0.1:5000 wants to show notifications"* — click **Allow**. This is how reminders appear on your desktop, outside the browser.

If blocked: click the lock/lock-icon in the address bar → Notifications → Allow.

## Features

| Feature | Detail |
|---|---|
| Reminders | Single (e.g. `15`) or multiple comma-separated offsets (e.g. `60, 30, 10`). Accepts both `,` and `，` |
| Custom recurrence | Every N days/weeks/months/years — choose "Custom…" in the dropdown |
| Desktop popup | Bottom-right, stays until you click ✕, with quick-snooze button |
| Sound | Two-tone chirp on reminder |
| Undo delete | 8-second toast with "Undo" to restore a deleted event |
| Countdown | Header shows minutes until your next upcoming reminder |
| History | Past events saved as templates — click "Reuse" to recreate |
| Dark/light | Mode switch in header, remembers your choice |
| Gone toast | Adding an event in the past shows "Gone is gone, my friend" — rejected |

## Project Files

| File | Purpose |
|---|---|
| `app.py` | Flask server, REST API, scheduler |
| `database.py` | SQLite storage (`~/.reminder_app.db`) |
| `utils.py` | Date helpers, reminder parsing |
| `templates/index.html` | HTML structure |
| `static/style.css` | All styling (light/dark themes) |
| `static/app.js` | All client logic |
| `static/history.js` | History panel, select/delete/reuse |
| `static/icons/` | 20 SVG icons (colored, locally served) |

## Build Standalone Binary

```bash
python3.14 -m pip install flask pyinstaller
python3.14 -m PyInstaller taptap.spec
# Output: dist/TapTap (Linux ELF, ~14 MB)
```
