"""Dev tool: screenshot the overlay into docs/preview-<tab>.png.

Runs against throwaway sample data in a temp folder, so it never touches your
real timesheet. Uses GDI through ctypes and encodes the PNG with zlib, keeping
the project free of image dependencies.

    python scripts/capture_preview.py
"""

from __future__ import annotations

import ctypes
import os
import struct
import sys
import tempfile
import zlib
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SRCCOPY = 0x00CC0020
BI_RGB = 0


def write_png(path: Path, width: int, height: int, bgra: bytes) -> None:
    rows = []
    stride = width * 4
    for y in range(height):
        row = bgra[y * stride : (y + 1) * stride]
        # BGRA from GDI -> RGB for PNG colour type 2.
        rows.append(b"\x00" + bytes(b for i in range(0, len(row), 4) for b in (row[i + 2], row[i + 1], row[i])))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
        + chunk(b"IEND", b"")
    )


def capture(hwnd: int, path: Path, pad: int = 0) -> None:
    """Screenshots a window, optionally with a margin of whatever is behind it.

    The margin is for windows with rounded corners, where some of what shows
    through is part of the picture.
    """
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32

    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    rect.left, rect.top = rect.left - pad, rect.top - pad
    rect.right, rect.bottom = rect.right + pad, rect.bottom + pad
    width, height = rect.right - rect.left, rect.bottom - rect.top

    screen_dc = user32.GetDC(0)
    mem_dc = gdi32.CreateCompatibleDC(screen_dc)
    bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
    gdi32.SelectObject(mem_dc, bitmap)
    gdi32.BitBlt(mem_dc, 0, 0, width, height, screen_dc, rect.left, rect.top, SRCCOPY)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    info = BITMAPINFOHEADER()
    info.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    info.biWidth = width
    info.biHeight = -height  # negative: top-down rows
    info.biPlanes = 1
    info.biBitCount = 32
    info.biCompression = BI_RGB

    buffer = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(mem_dc, bitmap, 0, height, buffer, ctypes.byref(info), 0)

    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(0, screen_dc)

    write_png(path, width, height, buffer.raw)
    print(f"wrote {path}  ({width}x{height})")


def seed_sample_data() -> None:
    from timesheet import paths
    from timesheet.dates import add_days, today
    from timesheet.models import Recurrence
    from timesheet.store import Store

    store = Store(paths.data_file())
    store.load()

    acme = store.add_client("Acme Ltd")
    migration = store.add_project(acme.id, "Platform Migration", "K-10234", True)
    store.add_project(acme.id, "Data Warehouse", "K-10412", True)  # shows the nesting
    northwind = store.add_client("Northwind Energy")
    portal = store.add_project(northwind.id, "Customer Portal", "K-10871", True)
    admin = store.db.projects[0]

    runbook = store.add_task(migration.id, "Cutover runbook", True)
    tests = store.add_task(migration.id, "Fix staging integration tests", True)
    review = store.add_task(portal.id, "Design review with client", True)
    timesheets = store.add_task(admin.id, "Timesheets & internal admin", False)

    # A Phase, for the Setup tab shot: a task reads as one the moment it
    # picks up a child, shown nested underneath it rather than as a type.
    go_live = store.add_task(migration.id, "Go-live", True)
    store.add_subtask(go_live.id, "Final smoke test", True)

    day = today()

    # A recurring note, for both shots: its own schedule, sharing the task's
    # billing bucket rather than needing one of its own. Daily and unlogged,
    # so it shows up as its own pending row without touching the Day tab's
    # already-tuned totals.
    store.add_recurring_note(
        timesheets.id, "Daily stand-up", 15, Recurrence(frequency="daily", minutes=15, start=day)
    )

    store.set_plan(runbook.id, day, 180)
    store.set_plan(tests.id, day, 120)
    store.set_plan(review.id, day, 60)
    store.set_plan(timesheets.id, day, 30)
    store.set_plan(runbook.id, add_days(day, -1), 120)
    store.set_plan(review.id, add_days(day, 1), 90)

    store.add_entry(runbook.id, day, 145, "Walked the platform team through the cutover sequence")
    store.add_entry(tests.id, day, 95, "Resolved three failing integration tests blocking staging")
    store.add_entry(timesheets.id, day, 25, "Weekly admin")
    store.add_entry(runbook.id, add_days(day, -1), 130, "Drafted the rollback plan with their DBA")
    store.set_day_note(day, "Cutover rehearsal moved to Monday at the client's request.")
    store.start_timer(review.id)


def main() -> None:
    home = Path(tempfile.mkdtemp(prefix="tslog-preview-"))
    os.environ["TIMESHEET_HOME"] = str(home)
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # physical pixels for BitBlt

    seed_sample_data()

    from timesheet.ui.overlay import FULL_SIZE, TAB_LABELS, TABS, Overlay

    app = Overlay()
    app.root.geometry("{}x{}+60+60".format(*FULL_SIZE))
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)

    handle = ctypes.windll.user32.GetAncestor(app.root.winfo_id(), 2)  # GA_ROOT

    def settle() -> None:
        for _ in range(12):
            app.root.update()
        app.root.after(120, lambda: None)
        app.root.update()

    settle()
    # Becoming DPI-aware above scales up Tk's own font metrics to match
    # whatever this monitor's actually running at, which real launches never
    # opt into (see the module docstring) and ``FULL_SIZE`` was never sized
    # for. Pack silently clips whichever tab button runs out of room rather
    # than overflowing, so a shot taken at ``FULL_SIZE`` itself can crop the
    # last tab's label to nothing — widen just the window used for the
    # shots, never the app's own real default, to whatever the scaled tab
    # bar actually asks for.
    bar = app.tab_buttons["Today"].master.master
    width = max(FULL_SIZE[0], bar.winfo_reqwidth() + 10)
    if width != FULL_SIZE[0]:
        app.root.geometry(f"{width}x{FULL_SIZE[1]}+60+60")
        settle()

    # Named for what's on the tab button, not the internal key — the key
    # called "Setup" is the Preferences content these days.
    for tab in TABS:
        app.show_tab(tab)
        settle()
        capture(handle, docs / f"preview-{TAB_LABELS[tab].lower()}.png")

    # One light-mode shot, so the README can show both palettes.
    app.set_theme("light")
    app.show_tab("Today")
    settle()
    capture(handle, docs / "preview-light.png")

    capture_badges(app, handle, docs, settle)
    app.quit()


#: Shows through the badge's rounded corners in the strip below. Mid grey,
#: since neither palette's own background would show the other one's edges.
BACKDROP = "#8a8f99"


def capture_badges(app, handle: int, docs: Path, settle) -> None:
    """The collapsed badge in both palettes, timer running and stopped.

    Each shot takes in a margin around the window, because the corners are
    rounded and what shows through them is part of what's being shown. A
    plain window is parked behind to be that something.
    """
    import tkinter as tk

    size, gap, scale = 46, 10, 2
    backdrop = tk.Toplevel(app.root, bg=BACKDROP)
    backdrop.overrideredirect(True)
    # Topmost as well, or collapsing — which re-applies the overlay's own
    # always-on-top — leaves the backdrop stranded below the desktop.
    backdrop.attributes("-topmost", True)
    backdrop.geometry(f"{size + gap * 2}x{size + gap * 2}+{60 - gap}+{60 - gap}")

    shots = []
    for palette in ("dark", "light"):
        app.set_theme(palette)
        app.collapse(True)
        for running in (True, False):
            if running:
                app.store.start_timer(app.store.db.tasks[0].id)
            elif app.store.db.active_timer is not None:
                app.store.stop_timer()
            app.root.geometry(f"{size}x{size}+60+60")
            # Settle before ordering, not after: a pending theme rebuild
            # re-applies the overlay's always-on-top and undoes the lift.
            settle()
            # lift() doesn't reliably raise a borderless window on Windows;
            # re-asserting topmost does, and the last one asserted ends up on
            # top of the others.
            for window in (backdrop, app.root):
                window.attributes("-topmost", False)
                window.attributes("-topmost", True)
                settle()
            shot = docs / f"_badge-{palette}-{running}.png"
            capture(handle, shot, pad=gap)
            shots.append(shot)
        app.collapse(False)
        settle()
    backdrop.destroy()

    tiles = [tk.PhotoImage(file=str(shot)).zoom(scale) for shot in shots]
    strip = tk.PhotoImage(width=sum(t.width() for t in tiles), height=tiles[0].height())
    for index, tile in enumerate(tiles):
        strip.tk.call(strip, "copy", tile, "-to", index * tile.width(), 0)
    for shot in shots:
        shot.unlink()
    strip.write(str(docs / "preview-badge.png"), format="png")
    print(f"wrote {docs / 'preview-badge.png'}  ({strip.width()}x{strip.height()})")


if __name__ == "__main__":
    main()
