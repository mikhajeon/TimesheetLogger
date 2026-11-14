"""Dev tool: rasterise the design-team SVGs into themed PNGs for the overlay.

Tk cannot read SVG and nothing that converts it is installed, so this drives
headless Chrome — already on the machine — to render each icon at the exact
pixel size and palette colour it is used at. The PNGs are committed, so running
the app needs neither Chrome nor access to the design folder.

Re-run after changing the icon map or a palette colour:

    python scripts/build_icons.py

Source icons are Font Awesome Pro, supplied through the Datacom design folder.
Point ICON_SOURCE at that folder if yours lives somewhere else.
"""

from __future__ import annotations

import os
import re
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from timesheet.ui.theme import PALETTES  # noqa: E402

# No machine-specific default: set ICON_SOURCE to your copy of the design folder.
DESIGN = Path(os.environ.get("ICON_SOURCE") or ".")
if not os.environ.get("ICON_SOURCE") and __name__ == "__main__":
    raise SystemExit(
        "Set ICON_SOURCE to your design icons folder first, e.g.\n"
        "  $env:ICON_SOURCE = 'C:\\path\\to\\Icons'"
    )
#: Single-path line icons, recoloured per palette.
SOURCE = DESIGN / "Functional Icons"
#: Full-colour illustrations, used as drawn.
HIFI_SOURCE = DESIGN / "Hi-fi Icons"
TARGET = ROOT / "timesheet" / "ui" / "icons"

BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]

#: app name -> (source svg stem, pixel size, palette token to paint it)
#:
#: Sizes are the rendered pixel size, so they match the place each icon is
#: used; a glyph scaled by Tk afterwards would be mushy.
ICONS: dict[str, tuple[str, int, str]] = {
    # Window chrome. A flat bar rather than "minimize"'s four inward-pointing
    # arrows (that glyph reads as "exit fullscreen"), matching the hyphen
    # Windows' own minimize button draws.
    "collapse": ("minus", 16, "MUTED"),
    "wide": ("arrows-left-right", 16, "MUTED"),
    "narrow": ("compress-wide", 16, "MUTED"),
    "undo": ("arrow-rotate-left", 14, "MUTED"),
    "redo": ("arrow-rotate-right", 14, "MUTED"),
    # Date navigation
    "prev": ("chevron-left", 13, "TEXT"),
    "next": ("chevron-right", 13, "TEXT"),
    # The Day tab's per-row expand/collapse affordance (see ``today.py``'s
    # ``_day_row``) — "up" is just "down" turned the other way, same as the
    # source icon set draws the two.
    "row-expand": ("chevron-down", 14, "MUTED"),
    "row-collapse": ("chevron-up", 14, "MUTED"),
    # Task rows
    "play": ("circle-play", 16, "TEXT"),
    "stop": ("circle-stop", 16, "ON_BILLABLE"),
    # The Day tab's +/- nudge chips (see ``timesheet/ui/today.py``'s
    # NUDGE_SIZE) — smaller than the chip's own circle now that chip has
    # shrunk too, rather than the glyph crowding the ring around it.
    "plus": ("circle-plus", 15, "MUTED"),
    "minus": ("circle-minus", 15, "MUTED"),
    # "delete" is the Day tab's own entry-delete chip in its inert state — a
    # trash can rather than an X, same glyph as the Setup/Recurring tabs'
    # two-click delete button ("trash" below) already use for the same
    # destructive idea.
    "delete": ("circle-trash", 18, "MUTED"),
    # Where a time entry came from
    "from-timer": ("clock", 12, "MUTED"),
    "from-manual": ("pencil", 12, "MUTED"),
    # The Day tab's own "this row repeats" marker, next to a row's note
    # field — same glyph "redo" above already uses, reused rather than
    # duplicated since a repeat is itself a kind of redo; sized and tinted
    # like "from-timer"/"from-manual" just above, the row's other small
    # inert indicator of how it came to be.
    "recurring": ("arrow-rotate-right", 12, "MUTED"),
    # The confirm/cancel chip pair, and the trash icon for a permanent delete.
    # "circle-check"/"circle-xmark" rather than plain "check"/"xmark" so the
    # glyph itself carries its own ring — these sit directly on the page
    # background rather than a solid chip behind them (see ``icons.chip``'s
    # callers, which all pass a ``fill`` matching whatever's already behind
    # the chip), so they're tinted plain BILLABLE/DANGER, the same as
    # "trash" below, rather than a contrast colour meant for a coloured fill.
    "confirm": ("circle-check", 14, "BILLABLE"),
    "deny": ("circle-xmark", 14, "DANGER"),
    "trash": ("circle-trash", 14, "DANGER"),
    # The Export tab's week grid: a note's copy chip, muted when there's
    # nothing on it to copy and accented once there is — two tinted variants
    # of the same glyph, swapped by state.
    "note": ("comment-lines", 14, "MUTED"),
    "note-filled": ("comment-lines", 14, "ACCENT"),
}

#: app name -> (source svg stem, pixel height)
#:
#: These are full-colour illustrations with their own gradients, so they're
#: rendered as drawn — no palette token — and the same PNG serves both
#: palettes. Only the height is given: they aren't square, so the width
#: follows from the viewBox.
HIFI: dict[str, tuple[str, int]] = {
    "badge": ("time_scale_hi-fi", 34),
}

#: The .ico Windows uses for the tray, the taskbar and the shortcuts.
#:
#: Windows picks a size from the file rather than scaling one, so the sizes it
#: asks for are all rendered. The illustration isn't square and an icon must
#: be, so each is drawn centred on a square transparent canvas.
TRAY_SOURCE = "time_scale_hi-fi"
TRAY_SIZES = (16, 20, 24, 32, 48, 64, 128, 256)
TRAY_FILE = TARGET / "app.ico"

SQUARE_PAGE = """<!doctype html><meta charset="utf-8"><style>
html, body {{ margin: 0; padding: 0; width: {size}px; height: {size}px;
  display: flex; align-items: center; justify-content: center;
  background: transparent; }}
svg {{ display: block; }}
</style>
{svg}"""


def write_ico(frames: list[tuple[int, bytes]], out: Path) -> None:
    """Packs rendered PNGs into one .ico.

    The entries hold the PNGs as-is, which Windows has read since Vista and
    which keeps the alpha channel the illustration needs.
    """
    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = len(header) + 16 * len(frames)
    entries, blobs = b"", b""
    for size, data in frames:
        # 256 is written as 0: the field is one byte wide.
        entries += struct.pack(
            "<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset
        )
        offset += len(data)
        blobs += data
    out.write_bytes(header + entries + blobs)


def browser() -> str:
    for candidate in BROWSERS:
        if Path(candidate).exists():
            return candidate
    raise SystemExit(
        "Neither Chrome nor Edge was found; one of them does the rasterising.\n"
        "Looked in:\n  " + "\n  ".join(BROWSERS)
    )


def proportions(svg: str, height: int) -> tuple[int, int]:
    """``(width, height)`` at ``height``, keeping the viewBox's aspect ratio."""
    box = re.search(r'viewBox="([\d.\s-]+)"', svg)
    if box is None:
        return height, height
    _, _, view_w, view_h = (float(n) for n in box.group(1).split())
    return round(height * view_w / view_h), height


def paint(svg: str, size: int, colour: str) -> str:
    """Sizes the SVG and sets its colour.

    ``fill`` goes on the root element rather than each path: these icons leave
    their paths unfilled, so they inherit it, and one substitution covers an
    icon however many subpaths it is drawn with.
    """
    svg = re.sub(r'\s(?:width|height|fill)="[^"]*"', "", svg, count=0)
    return svg.replace("<svg ", f'<svg width="{size}" height="{size}" fill="{colour}" ', 1)


def resize(svg: str, width: int, height: int) -> str:
    """Sizes an illustration without touching how it's coloured.

    Only the root element's own width and height are dropped — a hi-fi icon
    is built from gradients and embedded bitmaps that carry their own.
    """
    svg = re.sub(r'\s(?:width|height)="[^"]*"', "", svg, count=1)
    return svg.replace("<svg ", f'<svg width="{width}" height="{height}" ', 1)


def render(
    exe: str,
    out: Path,
    size: tuple[int, int],
    scratch: Path,
    markup: str,
    suffix: str = ".svg",
) -> None:
    width, height = size
    source = scratch / f"{out.stem}-{out.parent.name}{suffix}"
    source.write_text(markup, "utf-8")

    out.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            exe,
            "--headless",
            "--disable-gpu",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            # Transparent, so one PNG works on every background the icon sits
            # on — raised bars, hover states, the green running badge.
            "--default-background-color=00000000",
            f"--screenshot={out}",
            f"--window-size={width},{height}",
            source.as_uri(),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if not out.exists():
        raise SystemExit(f"{exe} produced nothing for {out.name}:\n{result.stderr}")


def source_svg(folder: Path, stem: str) -> str:
    svg_file = folder / f"{stem}.svg"
    if not svg_file.exists():
        raise SystemExit(f"No {stem}.svg in {folder}")
    return svg_file.read_text("utf-8")


def main() -> int:
    for folder in (SOURCE, HIFI_SOURCE):
        if not folder.is_dir():
            raise SystemExit(f"Icon source folder not found: {folder}")

    exe = browser()
    scratch = Path(tempfile.mkdtemp(prefix="tslog-icons-"))
    print(f"rasterising with {Path(exe).name}")

    for theme_name, palette in PALETTES.items():
        for name, (stem, size, token) in ICONS.items():
            svg = source_svg(SOURCE, stem)
            out = TARGET / theme_name / f"{name}.png"
            render(exe, out, (size, size), scratch, paint(svg, size, palette[token]))
            print(f"  {theme_name}/{name}.png  {size}px  {palette[token]}  ({stem})")

    for name, (stem, height) in HIFI.items():
        svg = source_svg(HIFI_SOURCE, stem)
        size = proportions(svg, height)
        markup = resize(svg, *size)
        # Written into both palette folders rather than shared, so the loader
        # stays a straight "icons/<palette>/<name>.png" lookup.
        for theme_name in PALETTES:
            out = TARGET / theme_name / f"{name}.png"
            render(exe, out, size, scratch, markup)
            print(f"  {theme_name}/{name}.png  {size[0]}x{size[1]}  full colour  ({stem})")

    svg = source_svg(HIFI_SOURCE, TRAY_SOURCE)
    frames = []
    for size in TRAY_SIZES:
        out = scratch / f"tray-{size}.png"
        width, height = proportions(svg, size)
        page = SQUARE_PAGE.format(size=size, svg=resize(svg, width, height))
        render(exe, out, (size, size), scratch, page, suffix=".html")
        frames.append((size, out.read_bytes()))
    write_ico(frames, TRAY_FILE)
    print(f"  app.ico  {', '.join(str(s) for s in TRAY_SIZES)}px  ({TRAY_SOURCE})")

    print(f"wrote {(len(ICONS) + len(HIFI)) * len(PALETTES)} icons and app.ico to {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
