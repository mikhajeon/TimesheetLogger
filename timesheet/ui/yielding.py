"""A cooperative "let the window breathe" hook for long view builds.

Building a tab is a long run of widget creation on the one UI thread, during
which Tk can't see a click on the nav bar. Code that creates many widgets
calls :func:`checkpoint`; while the overlay is building a view behind its
skeleton it installs a hook here that (throttled) lets pending input and
animation run, and — if that input was a switch to another tab — raises
:class:`Abandoned` to unwind the build that is no longer wanted.
"""

from __future__ import annotations

from typing import Callable

_hook: Callable[[], None] | None = None


class Abandoned(BaseException):
    """The view being built is no longer the one wanted. A ``BaseException``
    so the ``except Exception`` blocks a view may have don't swallow it."""


def install(hook: Callable[[], None] | None) -> None:
    global _hook
    _hook = hook


def checkpoint() -> None:
    hook = _hook
    if hook is not None:
        hook()
