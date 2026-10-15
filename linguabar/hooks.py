"""Keyboard capture and foreground notifications; Qt owns UI work."""
from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes as W

from PyQt5.QtCore import QObject, pyqtSignal

from .config import VRCHAT_CLASS
from .guards import MenuGuard
from .win32 import (
    HOOKPROC, WINEVENTPROC, KBDLLHOOKSTRUCT, LLKHF_INJECTED,
    VK_Y, WH_KEYBOARD_LL, WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP, WM_QUIT,
    CallNextHookEx, DispatchMessage, GetAsyncKeyState, GetCurrentThreadId,
    GetMessage, GetModuleHandle, PeekMessage, PostThreadMessage, SetWinEventHook,
    SetWindowsHookEx, TranslateMessage, UnhookWinEvent, UnhookWindowsHookEx,
    class_name, game_foreground, window_ids,
)

LOG = logging.getLogger("vrchat-ime")


class Bridge(QObject):
    # Python object preserves HWND width on both 32-bit and 64-bit Windows.
    wake = pyqtSignal(object)


class ForegroundMonitor(QObject):
    """Event-driven foreground notification, installed on the GUI thread."""
    changed = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.handle = None
        self.proc = WINEVENTPROC(self._callback)
        self.stopping = False

    def _callback(self, hook, event, hwnd, object_id, child_id, thread, stamp):
        try:
            if not self.stopping:
                self.changed.emit(int(hwnd or 0))
        except Exception:
            # Never propagate Python exceptions across a native callback.
            pass

    def start(self):
        # EVENT_SYSTEM_FOREGROUND, WINEVENT_OUTOFCONTEXT. Qt pumps messages.
        self.handle = SetWinEventHook(3, 3, None, self.proc, 0, 0, 0)
        if not self.handle:
            LOG.warning("Foreground hook unavailable; using Qt deactivation events")

    def stop(self):
        self.stopping = True
        if self.handle:
            if not UnhookWinEvent(self.handle):
                LOG.warning("UnhookWinEvent failed: %s", ctypes.get_last_error())
            self.handle = None


class YHook:
    def __init__(self, bridge: Bridge, guard: MenuGuard):
        self.bridge, self.guard = bridge, guard
        self.targets: dict[int, int] = {}  # Replaced, never mutated in place.
        self.pending = threading.Event()
        self.ready = threading.Event()
        self.stopping = threading.Event()
        self.thread = None
        self.thread_id = 0
        self.handle = None
        self.error = ""
        self.swallow_y = False  # Only the hook thread writes this field.
        self.proc = HOOKPROC(self._callback)  # Keep alive until thread exits.

    def _callback(self, code, message, data):
        try:
            if code >= 0 and message in (WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP):
                key = ctypes.cast(data, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if not key.flags & LLKHF_INJECTED:
                    is_up = message in (WM_KEYUP, WM_SYSKEYUP)
                    if key.vkCode == VK_Y and self.swallow_y:
                        # Consume the entire wakeup press, including repeats/up,
                        # even when focus has moved to the new input window.
                        if is_up:
                            self.swallow_y = False
                        return 1
                    if not is_up and not self.stopping.is_set() and key.vkCode == VK_Y:
                        hwnd = game_foreground()
                        pid = self.targets.get(hwnd)
                        if pid and window_ids(hwnd)[1] == pid and class_name(hwnd) == VRCHAT_CLASS:
                            ctrl = bool(GetAsyncKeyState(0x11) & 0x8000)
                            alt = bool(GetAsyncKeyState(0x12) & 0x8000)
                            win = bool((GetAsyncKeyState(0x5B) | GetAsyncKeyState(0x5C)) & 0x8000)
                            # Shift is VRChat's sprint key. Shift+Y is still a
                            # chat request, even while movement keys are held.
                            if not (ctrl or alt or win):
                                reason = self.guard.check(hwnd, pid)
                                self.guard.reason = reason or "Automatic Y capture enabled"
                                # Keep Y out of VRChat while a menu/camera is
                                # active, but preserve typing in a native edit.
                                if reason != "Native text/child control active":
                                    self.swallow_y = True
                                    if not reason and not self.pending.is_set():
                                        self.pending.set()
                                        self.bridge.wake.emit((hwnd, pid, self.guard.ticket()))
                                    return 1
        except Exception as exc:
            # No Python exception may escape a ctypes callback.
            self.error = f"Keyboard callback failed: {exc}"
            self.pending.clear()
            self.swallow_y = False
        return CallNextHookEx(self.handle, code, message, data)

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name="VRChat-Y-hook", daemon=True)
        self.thread.start()
        if not self.ready.wait(2.0):
            self.stop()
            raise RuntimeError("Keyboard hook startup timed out")
        if not self.handle:
            self.stop()
            raise RuntimeError(self.error or "Keyboard hook installation failed")

    def _run(self) -> None:
        try:
            self.thread_id = int(GetCurrentThreadId())
            msg = W.MSG()
            # Create a message queue before ready/stop can post WM_QUIT.
            PeekMessage(ctypes.byref(msg), None, 0, 0, 0)
            self.handle = SetWindowsHookEx(WH_KEYBOARD_LL, self.proc, GetModuleHandle(None), 0)
            if not self.handle:
                raise ctypes.WinError(ctypes.get_last_error())
            self.ready.set()
            while not self.stopping.is_set():
                result = GetMessage(ctypes.byref(msg), None, 0, 0)
                if result == -1:
                    raise ctypes.WinError(ctypes.get_last_error())
                if result == 0:
                    break
                TranslateMessage(ctypes.byref(msg))
                DispatchMessage(ctypes.byref(msg))
        except Exception as exc:
            self.error = str(exc)
        finally:
            if self.handle:
                if not UnhookWindowsHookEx(self.handle):
                    self.error = f"Unhook failed: {ctypes.get_last_error()}"
                self.handle = None
            self.ready.set()

    def stop(self) -> None:
        self.stopping.set()
        if self.thread_id:
            PostThreadMessage(self.thread_id, WM_QUIT, 0, 0)
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
            if self.thread.is_alive():
                # Keep self/thread/proc references; never pretend it stopped.
                LOG.error("Hook thread has not exited; references retained until process exit")
