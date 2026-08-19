# Changelog

All notable changes to TapTap will be documented in this file.

## [0.3.0] — 2026-08-19

Desktop productivity update focused on dependable Windows background operation,
more precise reminder delivery, and faster keyboard-driven scheduling.

### Added
- Fixed keyboard shortcuts for starting, saving, refreshing, showing Past Events,
  and undoing the latest available deletion, plus a built-in `Shortcuts` panel.
- Windows notification-area support with **Open TapTap** and **Quit TapTap**
  actions, so reminders and native notifications continue after the main window
  is closed. A single left-click on the tray icon reopens the window.
- An opt-in **Start with Windows** control that registers the packaged executable
  per user and launches it hidden at sign-in without administrator privileges.

### Changed
- The next-reminder countdown now uses days, months, and years for distant events.
- Enter now behaves normally in form controls instead of saving from every
  field, while Escape and the new-event shortcut protect unfinished drafts with
  a discard confirmation.
- A normal Windows launch now restores an existing hidden or minimized TapTap
  window; duplicate sign-in launches exit silently and cannot create a second
  reminder worker.

### Fixed
- Category choices now open in a custom keyboard-accessible dropdown where
  every preset and saved custom category displays its icon before selection.
- The top-right clock now visibly ticks every second and immediately
  recalibrates after window restore, page visibility changes, or sleep/wake;
  ticks are aligned to full wall-clock seconds instead of page-load timing.
- Configured reminder offsets are claimed on full-second boundaries, while
  potentially slow native notification delivery runs outside the timing loop.
- Delayed startup and wake catch-up collapse simultaneously overdue offsets into
  one notification per event, and old successful native notifications are not
  replayed as a popup storm when the window is reopened.

### Upgrade notes
- Existing events, history, theme, and reminder settings are retained; this
  update does not migrate or replace the local SQLite database.
- On Windows, the title-bar close button now hides TapTap to the notification
  area. Choose **Quit TapTap** from the tray icon's right-click menu when you
  want to stop reminders completely.
- **Start with Windows** remains opt-in. Replacing a portable executable at the
  same path keeps the startup setting valid; after moving it, open the new copy
  once to repair the registered path before deleting the old copy.

## [0.2.1] — 2026-08-07

Maintenance release focused on complete packaged assets, safer upgrades, and
clearer repository ownership without changing TapTap's original feature set.

### Added
- Category-specific selector icons for Work, Personal, Health, Other, and
  custom categories, plus a neutral default icon.
- `FILE_NOTES.md` and tagged inline maintenance notes for security,
  compatibility, invariants, robustness, and packaging boundaries.

### Changed
- Frontend assets now use a renewed cache version, and the PyInstaller spec
  collects the complete `templates/` and `static/` trees.
- Windows and macOS release metadata now identify the application as version
  0.2.1.

### Fixed
- Windows builds now pass ICO artwork to WinForms instead of crashing silently
  when `System.Drawing.Icon` receives PNG artwork.
- Windows CI now requires the packaged executable to create a responsive native
  `TapTap` window instead of validating only the `--help` path.
- Fresh builds now contain all six category SVGs instead of relying on files
  added after an earlier executable was produced.
- Form defaults and reused history templates use the local calendar date rather
  than combining a UTC date with local time.
- Custom recurrence labels render naturally, such as `every 2 weeks`.
- Reusing a history item always creates a new event and cannot overwrite an
  event that was already being edited.
- History checkboxes remain visible when selection mode refreshes the panel.
- Runtime services initialize after argument parsing and within guarded startup,
  so help output and useful errors do not depend on SQLite being available.
- Linux one-file builds include the NSS, XKB, and XCB libraries required by Qt
  WebEngine.

## [0.2.0] — 2026-08-07

### Added
- Native pywebview application window with persistent local webview state.
- One-second Python reminder worker that continues while the window is minimized.
- Cross-platform native notifications with an in-window/browser fallback.
- Single-instance locking, rotating application logs, application artwork, and
  Windows executable version metadata.
- Per-launch authentication token for every internal API request.
- Automated API/UI compatibility, database, and reminder lifecycle tests, plus
  an icon generation script.

### Changed
- `/api/pending` is now read-only; reminder state changes happen in the worker.
- PyInstaller builds hide the console, bundle complete asset directories, and
  include the platform webview and notification dependencies.
- Snoozing a just-completed event reactivates it so quick snooze remains useful.
- Linux launcher now opens the native application directly.

### Fixed
- Reminders no longer depend on a visible browser tab polling the server.
- Legacy `last_reminded` timestamps no longer cause old reminders to fire again.
- A fixed port is no longer required for the packaged desktop application.
- Editing an event's schedule now clears stale reminder and snooze state.
- Long-overdue recurring events advance directly to their next future occurrence
  instead of emitting a notification storm while catching up.
- Duplicate reminder offsets fire once, native notification calls have a timeout,
  and SQLite waits safely for short-lived concurrent writes.
- Versioned frontend assets and non-cacheable API tokens prevent stale webview
  code after an upgrade; dynamic event labels are HTML-escaped.

## [0.1.1] — 2026-07-28

### Added
- **Event categories**: Tag events as Work, Personal, Health, Other, or a custom category. Categories appear as colored dots and left-border accents on event cards, making it easy to scan your list visually.
- **Configurable snooze**: Snooze reminders for any duration (1–1440 minutes). The event list has a dropdown (1m / 5m / 10m / 15m / 30m / custom), and popup notifications offer 2m / 5m / 15m quick-snooze buttons.
- **Adaptive polling**: The frontend polls the server every 5 seconds normally, switching to every 1 second when a reminder is within 15 seconds — second-accurate firing without wasting resources.
- **Tab visibility throttling**: Polling pauses when the browser tab is hidden and resumes instantly when visible, saving CPU and network when TapTap is in the background.
- **Static asset caching**: CSS, JS, and icon files now include a `Cache-Control: max-age=3600` header.
- **Second-level countdown**: The countdown displays seconds (`in 47s`) when the next reminder is under a minute away.
- **DB auto-cleanup**: History older than 90 days is periodically purged (checked once per hour during normal polling).

### Changed
- **Favicon**: Replaced emoji-based favicon with a clean SVG bell icon for consistent rendering across all platforms.
- **Notification icon**: Desktop notifications now use the app favicon instead of an emoji.
- **Clock refresh**: Header clock updates every 30 seconds instead of every second (the countdown is what matters).
- **Undo toast**: Auto-dismiss reduced from 8s to 3s; hovering over the toast pauses the timer so you have time to click Undo.
- **History refresh**: Bulk delete and single delete now refresh the history panel directly instead of toggling it closed and open.
- **History bulk delete**: Individual delete failures are silently skipped so one bad row doesn't block the rest.

### Removed
- Dropped the unused `try_native_notify()` function and its `subprocess` import from `utils.py`.

### Fixed
- Countdown now derives its data from the same poll response as reminders, eliminating a separate fetch per tick.
- Tab title flash on reminder uses a bell emoji instead of an alarm-clock emoji.
- Status bar text simplified to `N event(s)` and placeholder changed to "Ready" (the old "polling every 1 s" was misleading with adaptive polling).
- README updated with v0.1.1 features (categories, configurable snooze, adaptive polling, undo timing).

## [0.1.0] — 2026-07-27

Initial release.
- Add, edit, delete events with date, time, description, reminders, and recurring rules.
- On-screen popup notifications with sound alerts and desktop notifications.
- Multi-reminder offsets (e.g. "60, 30, 10" minutes before).
- Light / dark mode with system-aware defaults.
- History panel for past and deleted events with undo.
- Clock calibration against server time.
