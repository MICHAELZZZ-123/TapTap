# TapTap File Notes

This is the ownership map for the repository. Use it to decide where a change
belongs, which checks it needs, and whether a packaged application must be
rebuilt.

## Inline notation tags

- `SECURITY` marks authentication, escaping, or secret-handling boundaries.
- `COMPATIBILITY` marks behavior needed to preserve existing data or platforms.
- `INVARIANT` marks assumptions that must remain true across future edits.
- `ROBUSTNESS` marks bounded failure handling and concurrency safeguards.
- `PACKAGING` marks behavior that affects frozen Windows/Linux/macOS artifacts.

## Runtime source

| File | Responsibility | Important notes |
|---|---|---|
| `app.py` | Flask routes, local API authentication, lazy runtime initialization, logging, single-instance handling, autostart API, and native/browser launch | Keep the original route contract stable. Increment `_ASSET_VERSION` whenever cached frontend assets change. |
| `database.py` | SQLite schema, migrations, CRUD, snooze state, recurrence advancement, durable reminder outbox, and history cleanup | The default `~/.reminder_app.db` location is retained deliberately for upgrade compatibility. Claim an offset and create its unique outbox record in one transaction; migrations must preserve existing rows. |
| `reminders.py` | Background due-reminder evaluation, durable native delivery/retry, and in-window/fallback alerts | A delivery is complete only after confirmed native dispatch. Keep boundary-aligned timing separate from bounded delivery calls, and leave unfinished outbox rows restart-safe. |
| `utils.py` | Shared date formatting and reminder-offset parsing | Offset parsing accepts ASCII and Chinese commas and removes duplicates without reordering. |
| `windows_integration.py` | Per-user Windows startup registration, existing-window activation, and native notification-area lifecycle | User close hides the form; explicit Quit, sign-out, shutdown, and Task Manager close must still terminate cleanly. Preserve an existing valid startup owner, keep the command quoted, and contain private pywebview WinForms access in the compatibility adapter. |

## Frontend

| File or directory | Responsibility | Important notes |
|---|---|---|
| `templates/index.html` | Window structure, form controls, API/desktop metadata, and script/style loading | IDs are part of the UI compatibility contract in `tests/test_app_contract.py`. |
| `static/app.js` | Main UI state, API client, event form/list, keyboard shortcuts, Windows autostart control, clock, polling, notifications, categories, snooze, and delete/undo | Escape database-derived values before inserting HTML. All API calls require the per-process token header. |
| `static/history.js` | History rendering, selection, deletion, and reuse | It intentionally uses helpers from `app.js`; both scripts are loaded with `defer` in that order. |
| `static/style.css` | Light/dark themes and complete window styling | A style change requires an asset-version increment and rebuild. |
| `static/icons/` | Local interface and category SVGs | Feather-derived assets and their license note live together. New referenced assets must be covered by an availability test. |
| `static/app-icon.svg` | Editable application logo source | Run `scripts/generate_icons.py` after changing it. |
| `static/app-icon.png`, `static/app-icon.ico` | Generated Linux/macOS and Windows application artwork | These are source-controlled release assets, not disposable build output. |

## Packaging and automation

| File | Responsibility | Important notes |
|---|---|---|
| `requirements.txt` | Runtime and build dependencies | Platform markers keep PySide6 out of Windows builds. |
| `taptap.spec` | PyInstaller asset, backend, metadata, and executable configuration | It bundles the complete UI trees plus desktop-notifier's importlib resources, so a rebuild includes required runtime assets automatically. |
| `scripts/build_desktop.py` | Reproducible current-platform build entry point | On Linux it stages the NSS/XKB/XCB libraries Qt needs, downloading Debian/Ubuntu packages without sudo only when necessary. |
| `scripts/generate_icons.py` | Regenerates PNG/ICO artwork from the SVG source | Review generated image changes before committing them. |
| `scripts/smoke_test_windows_window.ps1` | Verifies that a frozen Windows build creates a responsive native window | Run only on Windows; it isolates test data and stops only processes that it started. |
| `scripts/smoke_test_windows_background.ps1` | Verifies hidden startup, native dispatch or permission-denied fallback, durable outbox state, single-instance activation, close-to-tray, real tray interaction, and graceful Quit | Run on the exact frozen Windows artifact after the normal responsive-window smoke test. It preserves and restores an existing TapTap autostart value. |
| `.github/workflows/build-desktop.yml` | Native Windows and Linux CI builds | A real Windows `.exe` must be produced by this Windows runner or on a Windows machine. |
| `version_info.txt` | Windows executable version metadata | Update it when publishing a new application version. |
| `TapTap.sh` | Convenience launcher for the Linux artifact | It expects `dist/TapTap` to exist. |

## Tests and documentation

| File | Coverage |
|---|---|
| `tests/test_app_contract.py` | Original routes, UI controls/actions, shortcut contracts, token protection, frontend assets, CRUD, history, restore, snooze, and pending-popup flow |
| `tests/test_database.py` | Recurrence math, migration preservation, schedule revisions, durable outbox/popups, runtime paths, and concurrent SQLite writes |
| `tests/test_reminders.py` | Due offsets, durable claims, dispatch confirmation, retry/restart recovery, shutdown durability, snooze, recurrence, fallback, and notification timeout |
| `tests/test_windows_integration.py` | Autostart quoting, enable/disable/repair behavior, real HKCU round-trip on Windows, existing-window activation, and single-left-click tray routing |
| `README.md` | User installation, operation, build, and release instructions |
| `CHANGELOG.md` | User-visible release history |

## Generated and persistent files

- `build/`, `dist/`, bytecode, test caches, logs, local databases, and virtual
  environments are ignored by Git.
- `dist/TapTap` is a generated Linux executable. It is not a Windows `.exe`.
- User events are persistent data and should not be deleted during an upgrade.
- The webview data directory contains window/browser state and the instance lock;
  rotating logs use the platform log directory.

## Change and release rules

1. Rebuild after changing runtime Python, templates, JavaScript, CSS, icons,
   dependencies, the PyInstaller spec, or Windows metadata.
2. Increment `app.py::_ASSET_VERSION` when JavaScript, CSS, templates, or cached
   icons change.
3. Run `python -m unittest discover -s tests -v` and `git diff --check` before a
   build.
4. Build using `python scripts/build_desktop.py` on every target operating
   system; do not reuse a Linux artifact for Windows.
5. Inspect the packaged archive or smoke-test its embedded server when adding
   assets, so a source-only success cannot hide a stale binary.
6. Test real notifications, minimize behavior, sleep/wake, and a second launch
   on each release target before distribution. Windows must additionally pass
   both native-window and background-lifecycle smoke tests.
