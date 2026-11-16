"""Window snapshots and a "ghost" window to animate them — ctypes + GDI only.

Animating the real Tk window means Tk repaints dozens of widgets on every
frame, which is what made the expand/collapse stutter. Instead:

1. :func:`capture` takes a bitmap of the window as it looks (``PrintWindow``).
2. :class:`Ghost` is a click-through layered window that shows those bitmaps
   scaled and cross-faded at whatever size/position it's told to, via
   ``UpdateLayeredWindow`` — no Tk, no widget layout, just a few blits.

Everything here raises on failure so the caller can fall back to the live
animation; nothing is installed, only Windows' own user32/gdi32/msimg32.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
msimg32 = ctypes.WinDLL("msimg32", use_last_error=True)

H = ctypes.c_void_p


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte),
    ]


def _sig(fn, restype, *argtypes):
    fn.restype = restype
    fn.argtypes = list(argtypes)


_sig(user32.GetDC, H, H)
_sig(user32.ReleaseDC, ctypes.c_int, H, H)
_sig(user32.PrintWindow, wintypes.BOOL, H, H, wintypes.UINT)
_sig(
    user32.CreateWindowExW, H, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
    wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, H, H, H, H,
)
_sig(user32.DestroyWindow, wintypes.BOOL, H)
_sig(user32.ShowWindow, wintypes.BOOL, H, ctypes.c_int)
_sig(
    user32.UpdateLayeredWindow, wintypes.BOOL, H, H, ctypes.POINTER(wintypes.POINT),
    ctypes.POINTER(wintypes.SIZE), H, ctypes.POINTER(wintypes.POINT), wintypes.DWORD,
    ctypes.POINTER(_BLENDFUNCTION), wintypes.DWORD,
)
_sig(gdi32.CreateCompatibleDC, H, H)
_sig(gdi32.CreateDIBSection, H, H, ctypes.POINTER(_BITMAPINFO), wintypes.UINT,
     ctypes.POINTER(ctypes.c_void_p), H, wintypes.DWORD)
_sig(gdi32.SelectObject, H, H, H)
_sig(gdi32.DeleteObject, wintypes.BOOL, H)
_sig(gdi32.DeleteDC, wintypes.BOOL, H)
_sig(gdi32.SetStretchBltMode, ctypes.c_int, H, ctypes.c_int)
_sig(
    gdi32.StretchBlt, wintypes.BOOL, H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.DWORD,
)
_sig(
    msimg32.AlphaBlend, wintypes.BOOL, H, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, _BLENDFUNCTION,
)

_sig(gdi32.CreateRoundRectRgn, H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
     ctypes.c_int, ctypes.c_int)
_sig(gdi32.CombineRgn, ctypes.c_int, H, H, H, ctypes.c_int)
_sig(gdi32.CreatePen, H, ctypes.c_int, ctypes.c_int, wintypes.DWORD)
_sig(gdi32.GetStockObject, H, ctypes.c_int)
_sig(gdi32.RoundRect, wintypes.BOOL, H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
     ctypes.c_int, ctypes.c_int)
_sig(user32.SetWindowRgn, ctypes.c_int, H, H, wintypes.BOOL)
_sig(user32.GetAncestor, H, H, wintypes.UINT)

_PW_RENDERFULLCONTENT = 2
_HALFTONE = 4
_SRCCOPY = 0x00CC0020
_ULW_ALPHA = 2
_SW_SHOWNOACTIVATE = 4
_WS_POPUP = 0x80000000
# layered | topmost | toolwindow (no taskbar button) | no-activate | click-through
_GHOST_EXSTYLE = 0x80000 | 0x8 | 0x80 | 0x08000000 | 0x20


class _Surface:
    """A top-down 32-bit DIB with its own memory DC."""

    def __init__(self, width: int, height: int) -> None:
        self.width, self.height = max(1, width), max(1, height)
        screen = user32.GetDC(None)
        try:
            self.dc = gdi32.CreateCompatibleDC(screen)
            info = _BITMAPINFO()
            info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
            info.bmiHeader.biWidth = self.width
            info.bmiHeader.biHeight = -self.height  # top-down
            info.bmiHeader.biPlanes = 1
            info.bmiHeader.biBitCount = 32
            self.bits = ctypes.c_void_p()
            self.bitmap = gdi32.CreateDIBSection(
                screen, ctypes.byref(info), 0, ctypes.byref(self.bits), None, 0
            )
        finally:
            user32.ReleaseDC(None, screen)
        if not self.dc or not self.bitmap:
            self.close()
            raise OSError("could not create a GDI surface")
        self._previous = gdi32.SelectObject(self.dc, self.bitmap)

    def has_content(self) -> bool:
        """False for an all-black grab (a window that refused to render)."""
        size = self.width * self.height * 4
        return any(ctypes.string_at(self.bits, size))

    def close(self) -> None:
        dc, bitmap = getattr(self, "dc", None), getattr(self, "bitmap", None)
        if dc and getattr(self, "_previous", None):
            gdi32.SelectObject(dc, self._previous)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if dc:
            gdi32.DeleteDC(dc)
        self.dc = self.bitmap = None


def capture(hwnd: int, width: int, height: int) -> _Surface:
    """A bitmap of the window as it's currently drawn, even if it is fully
    transparent right now (``PrintWindow`` asks it to paint itself)."""
    surface = _Surface(width, height)
    if not user32.PrintWindow(hwnd, surface.dc, _PW_RENDERFULLCONTENT) or not surface.has_content():
        surface.close()
        raise OSError("PrintWindow produced nothing")
    return surface

def capture_crop(hwnd: int, width: int, height: int, rect: tuple) -> _Surface:
    """Just ``rect`` (``x, y, w, h`` in the window's own pixels) of the window
    as it is drawn right now — a snapshot a ghost can show."""
    x, y, w, h = rect
    shot = capture(hwnd, width, height)
    try:
        out = _Surface(w, h)
        gdi32.StretchBlt(out.dc, 0, 0, w, h, shot.dc, x, y, w, h, _SRCCOPY)
        return out
    finally:
        shot.close()


def capture_rgb(hwnd: int, width: int, height: int) -> bytes:
    """The window as it is drawn right now, as top-down 8-bit RGB bytes
    (``width * height * 3``) — for dimming or otherwise re-drawing a view
    as an image. Raises ``OSError`` if Windows won't render it."""
    surface = capture(hwnd, width, height)
    try:
        raw = ctypes.string_at(surface.bits, width * height * 4)  # BGRA
    finally:
        surface.close()
    rgb = bytearray(width * height * 3)
    rgb[0::3] = raw[2::4]
    rgb[1::3] = raw[1::4]
    rgb[2::3] = raw[0::4]
    return bytes(rgb)


def from_rgb(rgb: bytes, width: int, height: int) -> _Surface:
    """A surface showing a picture given as top-down 8-bit RGB bytes."""
    surface = _Surface(width, height)
    bgra = bytearray(width * height * 4)
    bgra[0::4] = rgb[2::3]
    bgra[1::4] = rgb[1::3]
    bgra[2::4] = rgb[0::3]
    ctypes.memmove(surface.bits, bytes(bgra), len(bgra))
    return surface


def solid(colour: str) -> _Surface:
    """A 1x1 surface of one ``#rrggbb`` colour; the ghost stretches it to
    whatever size it's drawing, so it costs nothing to make or scale."""
    surface = _Surface(1, 1)
    red, green, blue = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    ctypes.memmove(surface.bits, bytes((blue, green, red, 0)), 4)
    return surface

def new_surface(width: int, height: int) -> _Surface:
    """An empty surface a caller can compose a frame on with :func:`blit`."""
    return _Surface(width, height)


def blit(
    dst: _Surface, src: _Surface, x: int, y: int, sx: int, sy: int, w: int, h: int,
    alpha: float = 1.0, clip: tuple | None = None,
) -> None:
    """Copies the ``w x h`` part of ``src`` at ``(sx, sy)`` onto ``dst`` at
    ``(x, y)`` 1:1, at ``alpha`` opacity, cut off outside ``clip``
    (``x, y, w, h`` on ``dst``; the whole of ``dst`` by default)."""
    cx, cy, cw, ch = clip if clip is not None else (0, 0, dst.width, dst.height)
    # Trim the copy to the clip rectangle (and to what ``src`` actually has).
    left = max(0, cx - x)
    top = max(0, cy - y)
    right = min(w, cx + cw - x, src.width - sx)
    bottom = min(h, cy + ch - y, src.height - sy)
    if right - left <= 0 or bottom - top <= 0 or alpha <= 0:
        return
    args = (
        x + left, y + top, right - left, bottom - top,
        src.dc, sx + left, sy + top, right - left, bottom - top,
    )
    if alpha >= 1:
        gdi32.StretchBlt(dst.dc, *args, _SRCCOPY)
    else:
        msimg32.AlphaBlend(dst.dc, *args, _BLENDFUNCTION(0, 0, round(255 * alpha), 0))


def fill(dst: _Surface, rect: tuple, colour: str) -> None:
    """Paints ``rect`` (``x, y, w, h``) of ``dst`` one ``#rrggbb`` colour."""
    swatch = solid(colour)
    try:
        x, y, w, h = rect
        gdi32.StretchBlt(dst.dc, x, y, w, h, swatch.dc, 0, 0, 1, 1, _SRCCOPY)
    finally:
        swatch.close()


class Ghost:
    """A click-through layered window that displays two snapshots, scaled to
    the same rect and cross-faded by ``mix`` (0 = all ``a``, 1 = all ``b``)."""

    def __init__(self, max_width: int, max_height: int, click_through: bool = True) -> None:
        """``click_through=False`` makes the ghost swallow clicks instead:
        for an animation that covers the app, a press on the part of the
        screen it is showing must not fall through to the window behind
        (nor reach the invisible real window under it)."""
        self._frame = _Surface(max_width, max_height)
        self._screen = user32.GetDC(None)
        self._shown = False
        self.hwnd = user32.CreateWindowExW(
            _GHOST_EXSTYLE if click_through else _GHOST_EXSTYLE & ~0x20,
            # A "Static" window answers hit-tests with HTTRANSPARENT unless it
            # has SS_NOTIFY; with it, it takes the click without ever taking
            # focus (a "Button" would, and so would steal the foreground).
            "Static", None, _WS_POPUP | (0 if click_through else 0x100),
            0, 0, 1, 1, None, None, None, None,
        )
        if not self.hwnd:
            self.close()
            raise OSError("could not create the ghost window")
        self._proc = self._old_proc = None
        if not click_through:
            self._refuse_activation()
        gdi32.SetStretchBltMode(self._frame.dc, _HALFTONE)

    def _refuse_activation(self) -> None:
        """A press on a window normally makes it the foreground window —
        and when this one is then destroyed, Windows hands the foreground to
        some other app. Answering WM_MOUSEACTIVATE with MA_NOACTIVATE keeps
        the app's own window in front while the click is still swallowed."""
        proc_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, wintypes.HWND, ctypes.c_uint,
            ctypes.c_size_t, ctypes.c_ssize_t,
        )
        get_long = user32.GetWindowLongPtrW
        get_long.restype = ctypes.c_void_p
        get_long.argtypes = [wintypes.HWND, ctypes.c_int]
        set_long = user32.SetWindowLongPtrW
        set_long.restype = ctypes.c_void_p
        set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        call = user32.CallWindowProcW
        call.restype = ctypes.c_ssize_t
        call.argtypes = [
            ctypes.c_void_p, wintypes.HWND, ctypes.c_uint,
            ctypes.c_size_t, ctypes.c_ssize_t,
        ]
        old = get_long(self.hwnd, -4)

        def proc(hwnd, message, wparam, lparam):
            if message == 0x21:  # WM_MOUSEACTIVATE
                return 3  # MA_NOACTIVATE
            return call(old, hwnd, message, wparam, lparam)

        self._proc, self._old_proc = proc_type(proc), old
        set_long(self.hwnd, -4, ctypes.cast(self._proc, ctypes.c_void_p))

    def set_click_through(self, value: bool) -> None:
        """Lets clicks pass through to whatever is underneath (or not)."""
        style = user32.GetWindowLongW(self.hwnd, -20)
        style = (style | 0x20) if value else (style & ~0x20)
        user32.SetWindowLongW(self.hwnd, -20, style)

    def draw(
        self, rect: tuple, a: _Surface, b: _Surface, mix: float, alpha: float,
        border: tuple | None = None,
    ) -> None:
        """``border`` is ``((r, g, b), thickness, radius)``: the rectangle
        is then clipped to that corner radius and edged with a stroke of
        that thickness and colour, drawn inside its bounds — so a caller
        can animate the outline's colour, width and curvature along with
        the rectangle itself."""
        x, y, w, h = rect
        w = max(1, min(w, self._frame.width))
        h = max(1, min(h, self._frame.height))
        dc = self._frame.dc
        gdi32.StretchBlt(dc, 0, 0, w, h, a.dc, 0, 0, a.width, a.height, _SRCCOPY)
        if mix > 0:
            blend = _BLENDFUNCTION(0, 0, round(255 * min(mix, 1.0)), 0)
            msimg32.AlphaBlend(dc, 0, 0, w, h, b.dc, 0, 0, b.width, b.height, blend)
        if border is not None:
            (red, green, blue), thickness, radius = border
            pen = gdi32.CreatePen(
                6, max(1, round(thickness)),  # PS_INSIDEFRAME
                int(red) | (int(green) << 8) | (int(blue) << 16),
            )
            old_pen = gdi32.SelectObject(dc, pen)
            old_brush = gdi32.SelectObject(dc, gdi32.GetStockObject(5))  # NULL_BRUSH
            gdi32.RoundRect(dc, 0, 0, w, h, round(radius * 2), round(radius * 2))
            gdi32.SelectObject(dc, old_pen)
            gdi32.SelectObject(dc, old_brush)
            gdi32.DeleteObject(pen)
        overall = _BLENDFUNCTION(0, 0, round(255 * min(max(alpha, 0.0), 1.0)), 0)
        ok = user32.UpdateLayeredWindow(
            self.hwnd, self._screen, ctypes.byref(wintypes.POINT(x, y)),
            ctypes.byref(wintypes.SIZE(w, h)), dc, ctypes.byref(wintypes.POINT(0, 0)),
            0, ctypes.byref(overall), _ULW_ALPHA,
        )
        if not ok:
            raise OSError("UpdateLayeredWindow failed")
        if border is not None:
            try:
                round_hwnd(self.hwnd, w, h, max(0, round(border[2])))
            except OSError:
                pass  # square corners are a fine fallback
        if not self._shown:
            user32.ShowWindow(self.hwnd, _SW_SHOWNOACTIVATE)
            self._shown = True

    def close(self) -> None:
        if getattr(self, "hwnd", None):
            user32.DestroyWindow(self.hwnd)
            self.hwnd = None
        if getattr(self, "_screen", None):
            user32.ReleaseDC(None, self._screen)
            self._screen = None
        self._frame.close()


def round_hwnd(hwnd: int, width: int, height: int, radius: int, inset: int = 0) -> None:
    """Clips a window (a top-level, or any Tk widget that has its own
    window, which on Windows includes every Frame) to a rounded rectangle.
    Works on every Windows version, unlike DWM's own corner rounding;
    the edge isn't anti-aliased, so keep radii modest.

    ``inset`` pulls the clip in from every side (and the radius with it): for
    a window whose real outline is drawn by an anti-aliased layer on top, so
    the hard clip edge sits hidden under that layer's opaque band instead of
    showing through its soft edge as stair-steps."""
    radius = max(radius - inset, 0)
    region = gdi32.CreateRoundRectRgn(
        inset, inset, width + 1 - inset, height + 1 - inset, radius * 2, radius * 2
    )
    if not region:
        raise OSError("CreateRoundRectRgn failed")
    # On success the system owns the region; only free it if that failed.
    if not user32.SetWindowRgn(hwnd, region, True):
        gdi32.DeleteObject(region)
        raise OSError("SetWindowRgn failed")


def clip_visible(hwnd: int, width: int, height: int, visible: int,
                 radius: int = 0, inset: int = 0) -> None:
    """Clips a window to the rounded rectangle ``round_hwnd`` would give it,
    cut off ``visible`` pixels from its left edge: the part of a window
    sliding past a screen edge that is still on that screen. (A window
    reaching into the next monitor is otherwise drawn there.)"""
    radius = max(radius - inset, 0)
    if radius:
        base = gdi32.CreateRoundRectRgn(
            inset, inset, width + 1 - inset, height + 1 - inset, radius * 2, radius * 2
        )
    else:
        base = gdi32.CreateRectRgn(inset, inset, width + 1 - inset, height + 1 - inset)
    cut = gdi32.CreateRectRgn(0, 0, max(visible, 0), height + 1)
    if not base or not cut:
        for item in (base, cut):
            if item:
                gdi32.DeleteObject(item)
        raise OSError("could not create the clip region")
    gdi32.CombineRgn(base, base, cut, 1)  # RGN_AND
    gdi32.DeleteObject(cut)
    if not user32.SetWindowRgn(hwnd, base, True):
        gdi32.DeleteObject(base)
        raise OSError("SetWindowRgn failed")


def ring_hwnd(hwnd: int, width: int, height: int, radius: int, thickness: int) -> None:
    """Clips a window to a rounded *outline*: the rounded rectangle with a
    smaller, concentric one (``thickness`` in, ``radius - thickness``)
    cut out — so a plain coloured widget laid over a card reads as a
    stroke that curves with the card's corners, the same width all round."""
    outer = gdi32.CreateRoundRectRgn(0, 0, width + 1, height + 1, radius * 2, radius * 2)
    inner_radius = max(radius - thickness, 0)
    inner = gdi32.CreateRoundRectRgn(
        thickness, thickness, width + 1 - thickness, height + 1 - thickness,
        inner_radius * 2, inner_radius * 2,
    )
    try:
        if not outer or not inner:
            raise OSError("CreateRoundRectRgn failed")
        gdi32.CombineRgn(outer, outer, inner, 4)  # RGN_DIFF
        if not user32.SetWindowRgn(hwnd, outer, True):
            raise OSError("SetWindowRgn failed")
        outer = None  # the system owns it now
    finally:
        if outer:
            gdi32.DeleteObject(outer)
        if inner:
            gdi32.DeleteObject(inner)

def clear_region(hwnd: int) -> None:
    user32.SetWindowRgn(hwnd, None, True)


def root_hwnd(hwnd: int) -> int:
    """The top-level window a Tk widget's own handle sits inside."""
    return user32.GetAncestor(hwnd, 2) or hwnd  # GA_ROOT


_sig(user32.SetWindowPos, wintypes.BOOL, H, H, ctypes.c_int, ctypes.c_int, ctypes.c_int,
     ctypes.c_int, wintypes.UINT)
_sig(user32.GetWindow, H, H, wintypes.UINT)

RING_BUCKETS = 64

#: Ring masks already worked out, by (width, height, radius, thickness).
_RING_MASKS: dict[tuple, list] = {}


def _rounded_rect_distance(px: float, py: float, cx: float, cy: float,
                           half_w: float, half_h: float, radius: float) -> float:
    """Signed distance from a point to a rounded rectangle (negative inside)."""
    qx = abs(px - cx) - (half_w - radius)
    qy = abs(py - cy) - (half_h - radius)
    outside = (max(qx, 0.0) ** 2 + max(qy, 0.0) ** 2) ** 0.5
    return outside + min(max(qx, qy), 0.0) - radius


class RingWindow:
    """An anti-aliased rounded-rectangle ring, drawn in its own click-through
    per-pixel-alpha window laid exactly over another window.

    The ring's outer edge follows that window's own rounded corners and its
    inner edge is rounded too, so whatever square-cornered content sits
    inside is masked into a matching rounded shape. Each pixel carries its
    angle around the ring as a palette bucket, so a whole frame is just a
    palette lookup — the colours (and so any gradient or motion) are chosen
    by the caller per frame."""

    def __init__(self, width: int, height: int, radius: float, thickness: float) -> None:
        self.width, self.height = width, height
        self.thickness = thickness
        self._surface = _Surface(width, height)
        self._buffer = bytearray(width * height * 4)
        self._screen = user32.GetDC(None)
        self.hwnd = user32.CreateWindowExW(
            _GHOST_EXSTYLE, "Static", None, _WS_POPUP, 0, 0, 1, 1, None, None, None, None
        )
        if not self.hwnd:
            self.close()
            raise OSError("could not create the ring window")
        import math

        cx, cy = width / 2, height / 2
        # Concentric with the outer edge, so the stroke is the same width
        # all the way round, corners included.
        inner_r = max(radius - thickness, 0.5)
        key = (width, height, radius, thickness)
        if key in _RING_MASKS:
            self._pixels = _RING_MASKS[key]
            self._shown = False
            return
        pixels = []  # (byte offset, palette bucket, coverage 0..1)
        band = int(thickness) + 2  # only the border band can hold ring pixels
        for y in range(height):
            near_edge = y < band or y >= height - band
            columns = range(width) if near_edge else (*range(band), *range(width - band, width))
            for x in columns:
                px, py = x + 0.5, y + 0.5
                outer = min(max(0.5 - _rounded_rect_distance(px, py, cx, cy, cx, cy, radius), 0.0), 1.0)
                if outer <= 0.01:
                    continue
                inner = min(max(0.5 - _rounded_rect_distance(
                    px, py, cx, cy, cx - thickness, cy - thickness, inner_r), 0.0), 1.0)
                coverage = outer * (1.0 - inner)
                if coverage <= 0.01:
                    continue
                turn = (math.atan2(py - cy, px - cx) / (2 * math.pi)) % 1.0
                pixels.append(((y * width + x) * 4, int(turn * RING_BUCKETS) % RING_BUCKETS, coverage))
        self._pixels = _RING_MASKS[key] = pixels
        self._shown = False

    def draw(self, x: int, y: int, palette: list, opacity: float, above: int | None = None) -> None:
        """Paints the ring with ``palette`` (``RING_BUCKETS`` ``(r, g, b)``
        tuples, 0-255), places it at ``x, y``, and stacks it directly above
        the window ``above`` (the one it decorates)."""
        buf = self._buffer
        for offset, bucket, cover in self._pixels:
            r, g, b = palette[bucket]
            buf[offset] = int(b * cover)
            buf[offset + 1] = int(g * cover)
            buf[offset + 2] = int(r * cover)
            buf[offset + 3] = int(255 * cover)
        ctypes.memmove(self._surface.bits, bytes(buf), len(buf))
        blend = _BLENDFUNCTION(0, 0, round(255 * min(max(opacity, 0.0), 1.0)), 1)  # AC_SRC_ALPHA
        ok = user32.UpdateLayeredWindow(
            self.hwnd, self._screen, ctypes.byref(wintypes.POINT(x, y)),
            ctypes.byref(wintypes.SIZE(self.width, self.height)), self._surface.dc,
            ctypes.byref(wintypes.POINT(0, 0)), 0, ctypes.byref(blend), _ULW_ALPHA,
        )
        if not ok:
            raise OSError("UpdateLayeredWindow failed")
        if above:
            previous = user32.GetWindow(above, 3)  # GW_HWNDPREV: what's over it now
            # SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER
            flags = 0x0002 | 0x0001 | 0x0010 | 0x0200
            if previous != self.hwnd:
                user32.SetWindowPos(self.hwnd, previous or 0, 0, 0, 0, 0, flags)
        if not self._shown:
            user32.ShowWindow(self.hwnd, _SW_SHOWNOACTIVATE)
            self._shown = True

    def close(self) -> None:
        if getattr(self, "hwnd", None):
            user32.DestroyWindow(self.hwnd)
            self.hwnd = None
        if getattr(self, "_screen", None):
            user32.ReleaseDC(None, self._screen)
            self._screen = None
        self._surface.close()
