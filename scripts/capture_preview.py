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


def capture(hwnd: int, path: Path) -> None:
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32

    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
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
    from timesheet.store import Store

    store = Store(paths.data_file())
    store.load()

    acme = store.add_client("Acme Ltd")
    migration = store.add_project(acme.id, "Platform Migration", "K-10234", True)
    northwind = store.add_client("Northwind Energy")
    portal = store.add_project(northwind.id, "Customer Portal", "K-10871", True)
    admin = store.db.projects[0]

    runbook = store.add_task(migration.id, "Cutover runbook", True)
    tests = store.add_task(migration.id, "Fix staging integration tests", True)
    review = store.add_task(portal.id, "Design review with client", True)
    timesheets = store.add_task(admin.id, "Timesheets & internal admin", False)

    day = today()
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

    from timesheet.ui.overlay import Overlay

    app = Overlay()
    app.root.geometry("470x700+60+60")
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)

    handle = ctypes.windll.user32.GetAncestor(app.root.winfo_id(), 2)  # GA_ROOT

    for tab in ("Today", "Week", "Export", "Setup"):
        app.show_tab(tab)
        for _ in range(12):
            app.root.update()
        app.root.after(120, lambda: None)
        app.root.update()
        capture(handle, docs / f"preview-{tab.lower()}.png")

    app.quit()


if __name__ == "__main__":
    main()
