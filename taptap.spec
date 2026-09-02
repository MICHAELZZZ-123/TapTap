# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for TapTap — builds a standalone executable."""

import os
import sys
from glob import glob

from PyInstaller.utils.hooks import collect_data_files

_base = SPECPATH

# PACKAGING: Collect complete UI trees so future assets are not silently omitted.
added_files = [
    (os.path.join(_base, "templates"), "templates"),
    (os.path.join(_base, "static"), "static"),
    # desktop-notifier loads its default icon through importlib.resources while
    # importing. PyInstaller cannot infer that package/data dependency.
    *collect_data_files("desktop_notifier", includes=["resources/*"]),
]
bundled_binaries = []

# PACKAGING: PySide ships Qt itself, but Linux wheels rely on a small set of
# NSS/XKB/XCB system libraries. The build helper stages them here so the final
# one-file executable also works on a minimal desktop installation.
linux_runtime_dir = os.environ.get("TAPTAP_LINUX_RUNTIME_DIR")
if sys.platform.startswith("linux") and linux_runtime_dir:
    bundled_binaries.extend(
        (path, ".") for path in glob(os.path.join(linux_runtime_dir, "*.so*"))
    )
    added_files.extend(
        (path, ".") for path in glob(os.path.join(linux_runtime_dir, "*.chk"))
    )

hidden_imports = [
    "database",
    "reminders",
    "utils",
    "flask",
    "werkzeug",
    "jinja2",
    "sqlite3",
    "filelock",
    "platformdirs",
    "desktop_notifier.resources",
]

# PACKAGING: SSL and Bottle's optional adapters are unused by this loopback app.
excluded_modules = ["cryptography", "OpenSSL", "twisted", "zope"]
if sys.platform.startswith("linux"):
    hidden_imports += ["desktop_notifier.backends.dbus", "webview.platforms.qt"]
    excluded_modules += [
        "gi",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "cefpython3",
        "desktop_notifier.backends.macos",
        "desktop_notifier.backends.macos_support",
        "desktop_notifier.backends.winrt",
        "webview.platforms.android",
        "webview.platforms.cef",
        "webview.platforms.cocoa",
        "webview.platforms.edgechromium",
        "webview.platforms.gtk",
        "webview.platforms.mshtml",
        "webview.platforms.winforms",
    ]
elif sys.platform == "win32":
    hidden_imports += [
        "desktop_notifier.backends.winrt",
        "webview.platforms.edgechromium",
        "webview.platforms.winforms",
    ]
    excluded_modules += [
        "desktop_notifier.backends.dbus",
        "desktop_notifier.backends.macos",
        "desktop_notifier.backends.macos_support",
        "webview.platforms.android",
        "webview.platforms.cef",
        "webview.platforms.cocoa",
        "webview.platforms.gtk",
        "webview.platforms.qt",
    ]
elif sys.platform == "darwin":
    hidden_imports += ["desktop_notifier.backends.macos", "webview.platforms.cocoa"]
    excluded_modules += [
        "desktop_notifier.backends.dbus",
        "desktop_notifier.backends.winrt",
        "webview.platforms.android",
        "webview.platforms.cef",
        "webview.platforms.edgechromium",
        "webview.platforms.gtk",
        "webview.platforms.mshtml",
        "webview.platforms.qt",
        "webview.platforms.winforms",
    ]

a = Analysis(
    [os.path.join(_base, "app.py")],
    pathex=[_base],
    binaries=bundled_binaries,
    datas=added_files,
    hiddenimports=hidden_imports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excluded_modules,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="TapTap",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    icon=(
        os.path.join(_base, "static", "app-icon.ico")
        if sys.platform == "win32"
        else None
    ),
    version=(
        os.path.join(_base, "version_info.txt")
        if sys.platform == "win32"
        else None
    ),
    codesign_identity=None,
    entitlements_file=None,
)

if sys.platform == "darwin":
    app_bundle = BUNDLE(
        exe,
        name="TapTap.app",
        icon=os.path.join(_base, "static", "app-icon.png"),
        bundle_identifier="com.taptap.reminders",
        info_plist={
            "CFBundleShortVersionString": "0.3.1",
            "NSHighResolutionCapable": True,
        },
    )
