"""Builds the two downloads attached to a GitHub release.

    python scripts/build_release.py            # both, into ./dist
    python scripts/build_release.py zip        # source zip only
    python scripts/build_release.py exe        # single-file .exe only (needs PyInstaller)

The zip is for machines that already have Python and won't run unknown ``.exe``
files: unzip, start ``run.pyw``, then tick "Start menu shortcut" in Preferences
and pin it from Start. The ``.exe`` needs nothing installed.
"""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
sys.path.insert(0, str(ROOT))

from timesheet import __version__  # noqa: E402

#: What the app needs to run, and nothing else: no tests, docs, scripts or tooling.
INCLUDE = ("timesheet", "run.py", "run.pyw", "README.md")


def build_zip() -> Path:
    name = f"TimesheetLogger-v{__version__}"
    target = DIST / f"{name}.zip"
    DIST.mkdir(exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for entry in INCLUDE:
            source = ROOT / entry
            files = sorted(source.rglob("*")) if source.is_dir() else [source]
            for file in files:
                if file.is_dir() or "__pycache__" in file.parts or file.suffix == ".pyc":
                    continue
                archive.write(file, f"{name}/{file.relative_to(ROOT).as_posix()}")
    return target


def build_exe() -> Path:
    icons = ROOT / "timesheet" / "ui" / "icons"
    subprocess.run(
        [
            sys.executable, "-m", "PyInstaller",
            "--noconfirm", "--clean", "--onefile", "--windowed",
            "--name", "TimesheetLogger",
            "--icon", str(icons / "app.ico"),
            "--add-data", f"{icons};timesheet/ui/icons",
            "--distpath", str(DIST),
            "--workpath", str(ROOT / "build"),
            "--specpath", str(ROOT / "build"),
            str(ROOT / "run.pyw"),
        ],
        check=True,
    )
    built = DIST / "TimesheetLogger.exe"
    target = DIST / f"TimesheetLogger-v{__version__}.exe"
    target.unlink(missing_ok=True)
    built.rename(target)
    return target


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("all", "zip"):
        print(build_zip())
    if which in ("all", "exe"):
        print(build_exe())
