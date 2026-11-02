"""Desktop and sign-in shortcuts.

A ``.lnk`` is a COM object, not a file format worth hand-rolling, so these are
written through ``WScript.Shell`` — the scripting host that ships with
Windows. Nothing is installed to make this work.

Whether a shortcut exists is read off disk rather than kept in settings, so
deleting one by hand doesn't leave the Setup tab claiming it's still there.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

from . import paths

NAME = "Timesheet Logger"

#: KNOWNFOLDERID values, from ``KnownFolders.h``. The Desktop is looked up
#: rather than assumed to be ``~/Desktop``, because OneDrive moves it.
FOLDERID_DESKTOP = "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}"
FOLDERID_STARTUP = "{B97D20BB-F46A-4C97-BA10-5E3608430854}"

CREATE_NO_WINDOW = 0x08000000


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_byte * 8),
    ]


def known_folder(folder_id: str) -> Path | None:
    guid = GUID()
    if ctypes.windll.ole32.CLSIDFromString(folder_id, ctypes.byref(guid)) != 0:
        return None
    buffer = ctypes.c_wchar_p()
    result = ctypes.windll.shell32.SHGetKnownFolderPath(
        ctypes.byref(guid), 0, None, ctypes.byref(buffer)
    )
    if result != 0:
        return None
    try:
        return Path(buffer.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(buffer)


def launcher() -> Path:
    """The ``.pyw`` that starts the app without a console window."""
    return paths.PROJECT_ROOT / "run.pyw"


def interpreter() -> Path:
    """``pythonw.exe`` beside whichever Python is running us.

    The console build would leave a black window on screen for as long as the
    overlay is up, which rather defeats an overlay.
    """
    current = Path(sys.executable)
    quiet = current.with_name("pythonw.exe")
    return quiet if quiet.exists() else current


def icon() -> Path:
    return paths.PROJECT_ROOT / "timesheet" / "ui" / "icons" / "app.ico"


def shortcut_path(location: str) -> Path | None:
    folder = known_folder(FOLDERID_DESKTOP if location == "desktop" else FOLDERID_STARTUP)
    return folder / f"{NAME}.lnk" if folder else None


def exists(location: str) -> bool:
    link = shortcut_path(location)
    return bool(link and link.exists())


def create(location: str) -> str:
    """Writes the shortcut. Returns "" on success, or why it failed."""
    link = shortcut_path(location)
    if link is None:
        return f"Windows did not report where the {location} folder is"
    if not launcher().exists():
        return f"{launcher().name} is missing from the project folder"

    art = icon()
    script = "; ".join(
        [
            "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:TS_LINK)",
            "$s.TargetPath = $env:TS_EXE",
            '$s.Arguments = \'"\' + $env:TS_ARG + \'"\'',
            "$s.WorkingDirectory = $env:TS_DIR",
            "$s.Description = 'Timesheet overlay'",
            *(["$s.IconLocation = $env:TS_ICON"] if art.exists() else []),
            "$s.Save()",
        ]
    )
    # Passed through the environment rather than interpolated into the script,
    # so a path containing a quote can't change what PowerShell runs.
    environment = {
        "TS_LINK": str(link),
        "TS_EXE": str(interpreter()),
        "TS_ARG": str(launcher()),
        "TS_DIR": str(paths.PROJECT_ROOT),
        "TS_ICON": str(art),
    }
    return _run(script, environment) or ("" if link.exists() else "the shortcut was not written")


def remove(location: str) -> str:
    link = shortcut_path(location)
    if link is None or not link.exists():
        return ""
    try:
        link.unlink()
    except OSError as error:
        return str(error)
    return ""


def set_enabled(location: str, wanted: bool) -> str:
    return create(location) if wanted else remove(location)


def _run(script: str, environment: dict[str, str]) -> str:
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=CREATE_NO_WINDOW,
            env={**os.environ, **environment},
        )
    except (OSError, subprocess.SubprocessError) as error:
        return str(error)
    return "" if result.returncode == 0 else (result.stderr.strip() or "PowerShell failed")
