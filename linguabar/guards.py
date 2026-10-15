"""Decide whether the current game window can open the input bar."""
from __future__ import annotations

import ctypes
from ctypes import wintypes as W

from .win32 import (
    GUITHREADINFO, GetClipCursor, GetGUIThreadInfo, IsIconic,
    class_name, window_ids,
)


def native_ui_reason(hwnd: int) -> str:
    """Wakeup-time heuristic only. Unity menus need not be native HWNDs."""
    if not hwnd or IsIconic(hwnd):
        return "Game window unavailable"
    # Cursor visibility is not a reliable menu signal in desktop VRChat.
    # Blocking Y here can leave the input bar permanently unreachable.
    tid, _ = window_ids(hwnd)
    info = GUITHREADINFO()
    info.cbSize = ctypes.sizeof(info)
    if not tid or not GetGUIThreadInfo(tid, ctypes.byref(info)):
        return ""  # Unknown is not proof that the game is in a menu.
    if info.flags & (0x04 | 0x08 | 0x10) or info.hwndMenuOwner:
        return "Native menu active"
    focus_class = class_name(int(info.hwndFocus or 0)).casefold()
    if info.hwndCaret or focus_class == "edit" or focus_class.startswith("richedit") or "windowsforms10.edit" in focus_class:
        return "Native text/child control active"
    return ""


def released_game_cursor_reason() -> str:
    """Desktop VRChat unlocks the cursor for its Unity-drawn menus.

    Check confinement rather than cursor visibility: the latter can be the
    same in gameplay and menus. An unavailable reading is not proof of a menu.
    """
    rect = W.RECT()
    if GetClipCursor(ctypes.byref(rect)) and (rect.right - rect.left > 2
                                              or rect.bottom - rect.top > 2):
        return "Game cursor released / possible menu"
    return ""


class MenuGuard:
    """No remembered menu toggle: evaluate the current UI on every wakeup.

    Camera/session snapshots are updated by the GUI, read by the hook.
    Missing camera updates do not permanently disable ordinary Y capture.
    """

    def __init__(self):
        self.camera_revision = 0
        self.session_revision = 0
        self.camera_mode = None
        self.reason = "Automatic Y capture enabled"

    def camera_update(self, mode):
        if mode != self.camera_mode:
            self.camera_mode = mode
            self.camera_revision += 1
        self.reason = (f"Camera active (mode {mode})" if mode not in (None, 0)
                       else "Automatic Y capture enabled")

    def ticket(self):
        return self.camera_revision, self.session_revision

    def reset_session(self):
        self.session_revision += 1
        self.camera_update(None)

    def check(self, hwnd: int, pid: int) -> str:
        native = native_ui_reason(hwnd)
        if native:
            return native
        if self.camera_mode not in (None, 0):
            return f"Camera active (mode {self.camera_mode})"
        return released_game_cursor_reason()
