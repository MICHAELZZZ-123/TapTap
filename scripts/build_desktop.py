"""Build TapTap for the current operating system with PyInstaller."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig

import PyInstaller.__main__


ROOT = Path(__file__).resolve().parents[1]

LINUX_RUNTIME_PACKAGES = (
    "libnss3",
    "libnspr4",
    "libxkbfile1",
    "libxkbcommon-x11-0",
    "libxcb-cursor0",
    "libxcb-icccm4",
    "libxcb-util1",
    "libxcb-image0",
    "libxcb-keysyms1",
    "libxcb-render-util0",
    "libxcb-shape0",
    "libxcb-xkb1",
)

# PACKAGING: Qt WebEngine links to the first group. Other NSS modules are loaded
# dynamically, so PyInstaller cannot discover them from ELF dependencies alone.
LINUX_RUNTIME_LIBRARIES = (
    "libnspr4.so",
    "libnss3.so",
    "libnssutil3.so",
    "libsmime3.so",
    "libxkbfile.so.1",
    "libxkbcommon-x11.so.0",
    "libxcb-cursor.so.0",
    "libxcb-icccm.so.4",
    "libxcb-util.so.1",
    "libxcb-image.so.0",
    "libxcb-keysyms.so.1",
    "libxcb-render-util.so.0",
    "libxcb-shape.so.0",
    "libxcb-xkb.so.1",
    "libplc4.so",
    "libplds4.so",
    "libssl3.so",
    "libsoftokn3.so",
    "libfreebl3.so",
    "libfreeblpriv3.so",
    "libnssdbm3.so",
    "libnssckbi.so",
)


def _linux_library_dirs(extracted_root: Path | None = None) -> list[Path]:
    """Return likely multiarch library directories in priority order."""
    multiarch = sysconfig.get_config_var("MULTIARCH") or "x86_64-linux-gnu"
    directories: list[Path] = []
    if extracted_root is not None:
        directories.extend(
            [
                extracted_root / "usr" / "lib" / multiarch,
                extracted_root / "lib" / multiarch,
            ]
        )
    directories.extend(
        Path(item)
        for item in os.environ.get("LD_LIBRARY_PATH", "").split(os.pathsep)
        if item
    )
    directories.extend(
        [
            Path("/usr/lib") / multiarch,
            Path("/lib") / multiarch,
            Path("/usr/lib64"),
            Path("/lib64"),
        ]
    )
    return list(dict.fromkeys(directories))


def _find_linux_library(name: str, directories: list[Path]) -> Path | None:
    for directory in directories:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def _extract_linux_runtime_packages(cache: Path) -> Path:
    """Download missing Ubuntu/Debian libraries without requiring root."""
    downloads = cache / "debs"
    extracted = cache / "root"
    downloads.mkdir(parents=True, exist_ok=True)
    extracted.mkdir(parents=True, exist_ok=True)

    def extract_archives() -> None:
        archives = sorted(downloads.glob("*.deb"))
        if archives and shutil.which("dpkg-deb") is None:
            packages = " ".join(LINUX_RUNTIME_PACKAGES)
            raise RuntimeError(
                "Missing Qt runtime libraries. Install these packages before "
                f"building: {packages}"
            )
        for archive in archives:
            subprocess.run(
                ["dpkg-deb", "-x", str(archive), str(extracted)],
                check=True,
            )

    extract_archives()
    extracted_dirs = _linux_library_dirs(extracted)
    required = LINUX_RUNTIME_LIBRARIES[:14]
    if any(_find_linux_library(name, extracted_dirs) is None for name in required):
        if shutil.which("apt-get") is None or shutil.which("dpkg-deb") is None:
            packages = " ".join(LINUX_RUNTIME_PACKAGES)
            raise RuntimeError(
                "Missing Qt runtime libraries. Install these packages before "
                f"building: {packages}"
            )
        subprocess.run(
            ["apt-get", "download", *LINUX_RUNTIME_PACKAGES],
            cwd=downloads,
            check=True,
        )
        extract_archives()
    return extracted


def _prepare_linux_runtime_bundle() -> None:
    """Stage Qt's non-wheel libraries for explicit PyInstaller collection."""
    if not sys.platform.startswith("linux"):
        return

    cache = ROOT / "build" / "linux-runtime"
    stage = cache / "stage"
    stage.mkdir(parents=True, exist_ok=True)

    directories = _linux_library_dirs()
    required = LINUX_RUNTIME_LIBRARIES[:14]
    if any(_find_linux_library(name, directories) is None for name in required):
        extracted = _extract_linux_runtime_packages(cache)
        directories = _linux_library_dirs(extracted)

    missing = [
        name
        for name in required
        if _find_linux_library(name, directories) is None
    ]
    if missing:
        raise RuntimeError(
            "Could not locate required Qt libraries: " + ", ".join(missing)
        )

    for name in LINUX_RUNTIME_LIBRARIES:
        source = _find_linux_library(name, directories)
        if source is not None:
            shutil.copy2(source.resolve(), stage / name)

    # PACKAGING: NSS validates dynamic provider modules with adjacent checksums.
    for directory in directories:
        if not directory.is_dir():
            continue
        for checksum in directory.glob("lib*.chk"):
            shutil.copy2(checksum, stage / checksum.name)

    os.environ["TAPTAP_LINUX_RUNTIME_DIR"] = str(stage)
    current = os.environ.get("LD_LIBRARY_PATH", "")
    paths = [str(stage), *(item for item in current.split(os.pathsep) if item)]
    os.environ["LD_LIBRARY_PATH"] = os.pathsep.join(dict.fromkeys(paths))


def _make_local_libpython_discoverable() -> None:
    """Handle Python installs whose shared library lives in ~/.local/lib."""
    if not sys.platform.startswith("linux"):
        return
    library_name = sysconfig.get_config_var("INSTSONAME")
    if not library_name:
        return

    candidates = [
        Path(sys.base_prefix) / "lib",
        Path.home() / ".local" / "lib",
    ]
    for directory in candidates:
        if (directory / library_name).exists():
            current = os.environ.get("LD_LIBRARY_PATH", "")
            paths = [str(directory), *(item for item in current.split(os.pathsep) if item)]
            os.environ["LD_LIBRARY_PATH"] = os.pathsep.join(dict.fromkeys(paths))
            return


def main() -> None:
    _prepare_linux_runtime_bundle()
    _make_local_libpython_discoverable()
    PyInstaller.__main__.run(
        [
            "--clean",
            "--noconfirm",
            str(ROOT / "taptap.spec"),
        ]
    )


if __name__ == "__main__":
    main()
