"""Single-instance guard: the BLE link (and the HTTP port) has one owner.

Second copy exits with a message instead of fighting over the radio.
Windows uses a named mutex; POSIX uses a lock file in the temp dir.
"""

from __future__ import annotations

import os
import sys

_handle = None


def acquire(name: str) -> bool:
    """Return True when this process owns `name`, False when another does."""
    global _handle
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        h = kernel32.CreateMutexW(None, False, "Global\\" + name)
        if not h:
            return False
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(h)
            return False
        _handle = h
        return True
    import fcntl

    import tempfile

    path = os.path.join(tempfile.gettempdir(), f"bs3-{name}.lock")
    try:
        fh = open(path, "w")
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        return False
    _handle = fh
    return True


def holder_hint() -> str:
    return ("another copy is already running (one BLE link / one HTTP port — "
            "stop it first)")
