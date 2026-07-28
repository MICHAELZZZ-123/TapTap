# Changelog

All notable changes to TapTap will be documented in this file.

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
