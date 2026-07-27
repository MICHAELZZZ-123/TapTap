# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for TapTap — builds a standalone executable."""

import os

_base = SPECPATH

# Collect all data files to bundle
added_files = [
    (os.path.join(_base, "templates", "index.html"), "templates"),
    (os.path.join(_base, "static", "style.css"), "static"),
    (os.path.join(_base, "static", "app.js"), "static"),
    (os.path.join(_base, "static", "history.js"), "static"),
]
# Add every SVG icon
icons_dir = os.path.join(_base, "static", "icons")
for f in os.listdir(icons_dir):
    if f.endswith(".svg"):
        added_files.append((os.path.join(icons_dir, f), os.path.join("static", "icons")))

a = Analysis(
    [os.path.join(_base, "app.py")],
    pathex=[_base],
    binaries=[],
    datas=added_files,
    hiddenimports=["flask", "werkzeug", "jinja2", "sqlite3", "database", "utils"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
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
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
