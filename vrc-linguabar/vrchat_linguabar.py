# -*- coding: utf-8 -*-
"""VRC LinguaBar - multilingual IME input for VRChat.

Windows / Python 3.10+ / PyQt5 / python-osc / pywin32.

Y directly opens the bar for a verified VRChat.exe / UnityWndClass window.
Menu/camera protection is evaluated automatically; no enable shortcut/latch.
Committed text is sent immediately, without any automatic suffix or animation.
After 2500 ms idle, the typing indicator is turned off.
Enter sends, Esc cancels. External focus loss cancels and closes the bar.
Foreground changes and Qt deactivation events drive cleanup; no polling loop.

Menu protection is conservative, not a complete Unity UI-state detector.
Visible OS cursors/native menus block wakeup. Camera OSC updates on UDP 9001
also block wakeup when /usercamera/Mode != 0; mode 0 automatically unblocks it.
Missing camera updates mean unknown; native UI checks are then the fallback.
Controller/mouse/custom UI paths are not all observable. No game injection.

Install: python -m pip install PyQt5 python-osc pywin32
Run:     python vrchat_linguabar.py
Enable OSC in VRChat first. Ctrl+C or the tray menu exits the application.

UDP has no delivery acknowledgement. Empty chatbox messages are best-effort
clears, not a documented guaranteed delete API. deleteLater() is deliberately
deferred until Qt can safely destroy the object. No claim of hard real time.
"""
from __future__ import annotations

import argparse
import ctypes
import logging
import ntpath
import signal
import socket
import sys
import threading
import time
from ctypes import wintypes as W

if sys.platform != "win32":
    raise SystemExit("This application requires Windows.")

import win32api
import win32gui
from pythonosc.osc_message_builder import OscMessageBuilder
from pythonosc.osc_message import OscMessage, ParseError
from PyQt5.QtCore import QCoreApplication, QEvent, QObject, Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt5.QtNetwork import QHostAddress, QUdpSocket
from PyQt5.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QLineEdit, QMenu, QStyle,
    QSystemTrayIcon, QVBoxLayout, QWidget,
)

LOG = logging.getLogger("vrchat-ime")
MAX_CHARS = 144
IDLE_MS = 2500
VRCHAT_CLASS = "UnityWndClass"
WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105
WM_QUIT = 0x0012
VK_Y = 0x59
LLKHF_INJECTED = 0x10
GCS_COMPSTR, NI_COMPOSITIONSTR, CPS_CANCEL = 0x0008, 0x0015, 0x0004

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
imm32 = ctypes.WinDLL("imm32", use_last_error=True)
LRESULT = ctypes.c_ssize_t
ULONG_PTR = ctypes.c_size_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, W.WPARAM, W.LPARAM)
WINEVENTPROC = ctypes.WINFUNCTYPE(None, W.HANDLE, W.DWORD, W.HWND,
                                 W.LONG, W.LONG, W.DWORD, W.DWORD)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", W.DWORD), ("scanCode", W.DWORD),
                ("flags", W.DWORD), ("time", W.DWORD), ("dwExtraInfo", ULONG_PTR)]


class CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", W.DWORD), ("flags", W.DWORD),
                ("hCursor", W.HANDLE), ("ptScreenPos", W.POINT)]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", W.DWORD), ("flags", W.DWORD),
                ("hwndActive", W.HWND), ("hwndFocus", W.HWND),
                ("hwndCapture", W.HWND), ("hwndMenuOwner", W.HWND),
                ("hwndMoveSize", W.HWND), ("hwndCaret", W.HWND),
                ("rcCaret", W.RECT)]


def bind(dll, name, result, *args):
    fn = getattr(dll, name)
    fn.restype, fn.argtypes = result, list(args)
    return fn


GetForegroundWindow = bind(user32, "GetForegroundWindow", W.HWND)
GetCursorInfo = bind(user32, "GetCursorInfo", W.BOOL, ctypes.POINTER(CURSORINFO))
GetGUIThreadInfo = bind(user32, "GetGUIThreadInfo", W.BOOL, W.DWORD, ctypes.POINTER(GUITHREADINFO))
GetAsyncKeyState = bind(user32, "GetAsyncKeyState", W.SHORT, ctypes.c_int)
IsIconic = bind(user32, "IsIconic", W.BOOL, W.HWND)
GetClassName = bind(user32, "GetClassNameW", ctypes.c_int, W.HWND, W.LPWSTR, ctypes.c_int)
GetWindowThreadProcessId = bind(user32, "GetWindowThreadProcessId", W.DWORD, W.HWND, ctypes.POINTER(W.DWORD))
IsWindow = bind(user32, "IsWindow", W.BOOL, W.HWND)
GetAncestor = bind(user32, "GetAncestor", W.HWND, W.HWND, W.UINT)
SetWinEventHook = bind(user32, "SetWinEventHook", W.HANDLE, W.DWORD, W.DWORD,
                       W.HMODULE, WINEVENTPROC, W.DWORD, W.DWORD, W.DWORD)
UnhookWinEvent = bind(user32, "UnhookWinEvent", W.BOOL, W.HANDLE)
SetForegroundWindow = bind(user32, "SetForegroundWindow", W.BOOL, W.HWND)
SetFocus = bind(user32, "SetFocus", W.HWND, W.HWND)
AttachThreadInput = bind(user32, "AttachThreadInput", W.BOOL, W.DWORD, W.DWORD, W.BOOL)
SetWindowsHookEx = bind(user32, "SetWindowsHookExW", W.HHOOK, ctypes.c_int, HOOKPROC, W.HINSTANCE, W.DWORD)
UnhookWindowsHookEx = bind(user32, "UnhookWindowsHookEx", W.BOOL, W.HHOOK)
CallNextHookEx = bind(user32, "CallNextHookEx", LRESULT, W.HHOOK, ctypes.c_int, W.WPARAM, W.LPARAM)
PeekMessage = bind(user32, "PeekMessageW", W.BOOL, ctypes.POINTER(W.MSG), W.HWND, W.UINT, W.UINT, W.UINT)
GetMessage = bind(user32, "GetMessageW", ctypes.c_int, ctypes.POINTER(W.MSG), W.HWND, W.UINT, W.UINT)
TranslateMessage = bind(user32, "TranslateMessage", W.BOOL, ctypes.POINTER(W.MSG))
DispatchMessage = bind(user32, "DispatchMessageW", LRESULT, ctypes.POINTER(W.MSG))
PostThreadMessage = bind(user32, "PostThreadMessageW", W.BOOL, W.DWORD, W.UINT, W.WPARAM, W.LPARAM)
GetCurrentThreadId = bind(kernel32, "GetCurrentThreadId", W.DWORD)
GetModuleHandle = bind(kernel32, "GetModuleHandleW", W.HMODULE, W.LPCWSTR)
OpenProcess = bind(kernel32, "OpenProcess", W.HANDLE, W.DWORD, W.BOOL, W.DWORD)
CloseHandle = bind(kernel32, "CloseHandle", W.BOOL, W.HANDLE)
QueryFullProcessImageName = bind(kernel32, "QueryFullProcessImageNameW", W.BOOL, W.HANDLE, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD))
ImmGetContext = bind(imm32, "ImmGetContext", W.HANDLE, W.HWND)
ImmReleaseContext = bind(imm32, "ImmReleaseContext", W.BOOL, W.HWND, W.HANDLE)
ImmGetCompositionString = bind(imm32, "ImmGetCompositionStringW", W.LONG, W.HANDLE, W.DWORD, W.LPVOID, W.DWORD)
ImmNotifyIME = bind(imm32, "ImmNotifyIME", W.BOOL, W.HANDLE, W.DWORD, W.DWORD, W.DWORD)


def foreground() -> int:
    return int(GetForegroundWindow() or 0)


def window_ids(hwnd: int) -> tuple[int, int]:
    pid = W.DWORD()
    tid = GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) if hwnd else 0
    return int(tid), int(pid.value)


def class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    return buf.value if hwnd and GetClassName(hwnd, buf, len(buf)) else ""


def process_name(pid: int) -> str:
    """Never called from the low-level keyboard callback; no title heuristics."""
    handle = OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = W.DWORD(len(buf))
        if QueryFullProcessImageName(handle, 0, buf, ctypes.byref(size)):
            return ntpath.basename(buf.value).casefold()
        return ""
    finally:
        CloseHandle(handle)


def imm_composing(hwnd: int) -> bool:
    context = ImmGetContext(hwnd) if hwnd else None
    if not context:
        return False
    try:
        return ImmGetCompositionString(context, GCS_COMPSTR, None, 0) > 0
    finally:
        ImmReleaseContext(hwnd, context)


def cancel_imm(hwnd: int) -> None:
    context = ImmGetContext(hwnd) if hwnd else None
    if context:
        try:
            # Do not CPS_COMPLETE: cancellation must not commit a draft.
            ImmNotifyIME(context, NI_COMPOSITIONSTR, CPS_CANCEL, 0)
        finally:
            ImmReleaseContext(hwnd, context)


def acquire_focus(bar: int, edit: int, source: int) -> bool:
    """One attempt; any successful attachment is detached in finally."""
    current = foreground()
    if current not in (source, bar):
        return False  # The user switched away while the queued wakeup waited.
    if current != bar:
        SetForegroundWindow(bar)
    if foreground() == bar:
        SetFocus(edit)
        return True
    if foreground() != source:
        return False
    src_tid = int(GetCurrentThreadId())
    dst_tid, _ = window_ids(source)
    attached = False
    try:
        if dst_tid and dst_tid != src_tid:
            attached = bool(AttachThreadInput(src_tid, dst_tid, True))
        # Never change SPI_SETFOREGROUNDLOCKTIMEOUT or grant ASFW_ANY.
        if foreground() != source:
            return False
        SetForegroundWindow(bar)
        if foreground() == bar:
            SetFocus(edit)
            return True
        return False
    finally:
        if attached and not AttachThreadInput(src_tid, dst_tid, False):
            LOG.error("AttachThreadInput detach failed: %s", ctypes.get_last_error())


def clip_text(text: str, limit: int = MAX_CHARS) -> str:
    """Conservative UTF-16 budget, matching Qt 5; never split a surrogate pair.

    VRChat documents 144 characters but not its counting algorithm. UTF-16
    budgeting can accept fewer emoji than a Unicode-code-point limit.
    """
    text = text.replace("\x00", "").replace("\r", " ").replace("\n", " ")
    # Strip isolated surrogates that can arise from a Qt maxLength boundary.
    clean = text.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "ignore")
    return clean.encode("utf-16-le")[:limit * 2].decode("utf-16-le", "ignore")


def text_units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


class OscSender:
    """GUI-thread only; one nonblocking socket, no deferred send queue."""

    def __init__(self, host: str, port: int, address: str):
        family, kind, proto, _, target = socket.getaddrinfo(
            host, port, type=socket.SOCK_DGRAM)[0]
        self.socket = socket.socket(family, kind, proto)
        self.socket.setblocking(False)
        self.target, self.address = target, address
        self.failures = 0
        self.last_error = ""
        self._typing = None

    def send(self, address: str, args: list) -> bool:
        try:
            builder = OscMessageBuilder(address=address)
            for value in args:
                builder.add_arg(value)
            self.socket.sendto(builder.build().dgram, self.target)
            return True
        except (OSError, ValueError, UnicodeError) as exc:
            # No synchronous console I/O in the per-keystroke path.
            self.failures += 1
            self.last_error = str(exc)
            return False

    def text(self, text: str, notify: bool = False) -> bool:
        return self.send(self.address, [clip_text(text), True, bool(notify)])

    def typing(self, active: bool, force: bool = False) -> None:
        if force or active != self._typing:
            if self.send("/chatbox/typing", [bool(active)]):
                self._typing = active

    def close(self) -> None:
        self.socket.close()


class Bridge(QObject):
    # Python object preserves HWND width on both 32-bit and 64-bit Windows.
    wake = pyqtSignal(object)


def native_ui_reason(hwnd: int) -> str:
    """Wakeup-time heuristic only. Unity menus need not be native HWNDs."""
    if not hwnd or IsIconic(hwnd):
        return "Game window unavailable"
    cursor = CURSORINFO()
    cursor.cbSize = ctypes.sizeof(cursor)
    if not GetCursorInfo(ctypes.byref(cursor)):
        return "Cannot determine cursor state"
    if cursor.flags & 1:  # CURSOR_SHOWING: normal Desktop gameplay hides it.
        return "Visible interaction cursor / possible menu"
    tid, _ = window_ids(hwnd)
    info = GUITHREADINFO()
    info.cbSize = ctypes.sizeof(info)
    if not tid or not GetGUIThreadInfo(tid, ctypes.byref(info)):
        return "Cannot determine native UI state"
    if info.flags & (0x04 | 0x08 | 0x10) or info.hwndMenuOwner:
        return "Native menu active"
    if info.hwndCaret or (info.hwndFocus and int(info.hwndFocus) != hwnd):
        return "Native text/child control active"
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
        if self.camera_mode not in (None, 0):
            return f"Camera active (mode {self.camera_mode})"
        return native_ui_reason(hwnd)


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


class CameraMonitor(QObject):
    """Read-only OSC observation; never sends commands to the camera.

    VRChat normally outputs UDP to 9001. Another OSC app may already own it.
    No shared binding: shared UDP ports can silently lose/deliver packets to
    the wrong listener. In that case configure forwarding/a dedicated port.
    """
    def __init__(self, guard: MenuGuard, port: int, parent=None):
        super().__init__(parent)
        self.guard = guard
        self.socket = QUdpSocket(self)
        self.warning = ""
        self.enabled = False
        if not port or not self.socket.bind(QHostAddress.LocalHost, port, QUdpSocket.DontShareAddress):
            self.warning = "Camera OSC unavailable; camera state is unknown"
            LOG.warning("%s; port=%s", self.warning, port)
        else:
            self.enabled = True
            self.socket.readyRead.connect(self.drain)

    def drain(self):
        if not self.enabled:
            return
        # Bound work per event-loop turn; do not starve keyboard/UI events.
        for _ in range(128):
            if not self.socket.hasPendingDatagrams():
                return
            data, source, _ = self.socket.readDatagram(65536)
            if not source.isLoopback():
                continue
            try:
                message = OscMessage(bytes(data))
                values = message.params
                if (message.address == "/usercamera/Mode" and len(values) == 1
                        and type(values[0]) is int and 0 <= values[0] <= 6):
                    self.guard.camera_update(values[0])
            except (ParseError, ValueError, TypeError, UnicodeError):
                pass
        if self.socket.hasPendingDatagrams():
            QTimer.singleShot(0, self.drain)


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
                        hwnd = foreground()
                        pid = self.targets.get(hwnd)
                        if pid and window_ids(hwnd)[1] == pid and class_name(hwnd) == VRCHAT_CLASS:
                            ctrl = bool(GetAsyncKeyState(0x11) & 0x8000)
                            alt = bool(GetAsyncKeyState(0x12) & 0x8000)
                            shift = bool(GetAsyncKeyState(0x10) & 0x8000)
                            win = bool((GetAsyncKeyState(0x5B) | GetAsyncKeyState(0x5C)) & 0x8000)
                            if not (ctrl or alt or shift or win):
                                reason = self.guard.check(hwnd, pid)
                                self.guard.reason = reason or "Automatic Y capture enabled"
                                if not reason:
                                    self.swallow_y = True
                                    if not self.pending.is_set():
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


class ImeLineEdit(QLineEdit):
    submit = pyqtSignal()
    cancel = pyqtSignal()
    ime_activity = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.preedit = ""
        self.native_hwnd = 0
        self.last_commit = float("-inf")
        self.setMaxLength(MAX_CHARS)

    def composing(self) -> bool:
        return bool(self.preedit) or imm_composing(self.native_hwnd)

    def inputMethodEvent(self, event):
        self.preedit = event.preeditString()
        if event.commitString():
            self.last_commit = time.monotonic()
        # Set preedit state BEFORE super emits textChanged.
        super().inputMethodEvent(event)
        self.ime_activity.emit()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if self.composing():
                super().keyPressEvent(event)
            elif not event.isAutoRepeat() and time.monotonic() - self.last_commit >= 0.08:
                self.submit.emit()
            event.accept()
            return
        if event.key() == Qt.Key_Escape:
            if self.composing():
                # Normally the native IME consumes this first; preserve Qt's
                # normal handling when it reaches the widget during preedit.
                super().keyPressEvent(event)
            else:
                self.cancel.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class InputBar(QWidget):
    # Only relevant to a foreground-change EVENT, never polled. A browser is
    # never exempted just because an IME preedit string happens to exist.
    IME_CLASSES = frozenset({"Microsoft.IME.UIManager.CandidateWindow.Host",
                            "MSCandUIWindow_Candidate", "MSCTFIME UI",
                            "CiceroUIWndFrame", "Windows.UI.Core.CoreWindow"})

    def __init__(self, osc: OscSender, target: int, idle_ms: int):
        super().__init__()
        self.osc, self.target = osc, target
        self.target_pid = window_ids(target)[1]
        self.closing = False
        self._focus_ready = False
        self.bar_hwnd = 0
        self._last_text = ""
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        # One explicit deleteLater path; no WA_DeleteOnClose / manual child deletion.
        self.setObjectName("Bar")
        self.setStyleSheet("""
            QWidget#Panel { background: rgba(18,18,22,225);
                border: 1px solid rgba(255,255,255,48); border-radius: 14px; }
            QLineEdit { background: transparent; border: none; color: #F5F7FA;
                font: 20px 'Microsoft YaHei UI'; padding: 12px;
                selection-background-color: #3d7eff; }
            QLabel { background: transparent; border: none; color: #7dffa3;
                font: 11px 'Segoe UI'; }
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        panel = QWidget(self)
        panel.setObjectName("Panel")
        row = QHBoxLayout(panel)
        row.setContentsMargins(4, 4, 14, 4)
        self.edit = ImeLineEdit(panel)
        self.edit.setPlaceholderText("中 / 日 / 英 · Enter 发送 · Esc 取消")
        self.edit.setClearButtonEnabled(True)
        self.edit.setFocusPolicy(Qt.StrongFocus)
        row.addWidget(self.edit, 1)
        self.live = QLabel("LIVE", panel)
        self.live.setFixedWidth(36)
        row.addWidget(self.live)
        outer.addWidget(panel)
        self.setFixedSize(600, 64)
        self.idle_timer = self._timer(idle_ms, self._idle, single=True)
        self.focus_check = self._timer(0, self._ensure_session_focus, single=True)
        self.edit.textChanged.connect(self._text_changed)
        self.edit.ime_activity.connect(self._ime_activity)
        self.edit.submit.connect(self._submit)
        self.edit.cancel.connect(self._cancel)

    def _timer(self, interval, callback, single=False):
        timer = QTimer(self)
        timer.setTimerType(Qt.PreciseTimer)
        timer.setInterval(interval)
        timer.setSingleShot(single)
        timer.timeout.connect(callback)
        return timer

    def open_bar(self) -> bool:
        # A native handle for the editor makes IMM fallback unambiguous.
        self.bar_hwnd = int(self.winId())
        self.edit.native_hwnd = int(self.edit.winId())
        screen = QApplication.primaryScreen()
        try:
            monitor = win32api.MonitorFromWindow(self.target, 2)
            name = win32api.GetMonitorInfo(monitor)["Device"]
            screen = next((s for s in QApplication.screens() if s.name() == name), screen)
        except (OSError, win32gui.error):
            pass
        if screen:
            rect = screen.availableGeometry()
            self.setFixedWidth(min(600, max(200, rect.width() - 24)))
            self.move(rect.x() + (rect.width() - self.width()) // 2,
                      rect.y() + int(rect.height() * 0.8) - self.height())
        self.show()
        if not acquire_focus(self.bar_hwnd, self.edit.native_hwnd, self.target):
            self.finish(False, "focus-acquisition-failed")
            return False
        self.edit.setFocus(Qt.OtherFocusReason)
        self._focus_ready = True
        self.osc.text("")
        self.osc.typing(False, force=True)
        return True

    def refocus(self):
        if not self.closing:
            if acquire_focus(self.bar_hwnd, self.edit.native_hwnd, self.target):
                self.edit.setFocus(Qt.OtherFocusReason)

    def event(self, event):
        result = super().event(event)
        if event.type() == QEvent.WindowDeactivate:
            self.schedule_focus_check()
        return result

    def schedule_focus_check(self):
        # A one-shot callback after the current activation event settles.
        # No periodic watchdog, grace period, or focus-reclaiming loop.
        if getattr(self, "_focus_ready", False) and not self.closing:
            self.focus_check.start()

    def _foreground_is_input(self):
        hwnd = foreground()
        if not hwnd:
            return False
        if (hwnd in (self.bar_hwnd, self.edit.native_hwnd)
                or (self.bar_hwnd and int(GetAncestor(hwnd, 3) or 0) == self.bar_hwnd)):  # GA_ROOTOWNER
            return True
        if self.edit.composing() and class_name(hwnd) in self.IME_CLASSES:
            return process_name(window_ids(hwnd)[1]) in {"ctfmon.exe", "textinputhost.exe"}
        return False

    def _ensure_session_focus(self):
        if self.closing:
            return False
        if self._focus_ready and not self._foreground_is_input():
            self.finish(False, "focus-lost")
            return False
        return True

    def _text_changed(self, raw: str):
        if not self._ensure_session_focus():
            return
        text = clip_text(raw)
        if raw != text:
            position = self.edit.cursorPosition()
            previous = self.edit.blockSignals(True)
            try:
                self.edit.setText(text)
                self.edit.setCursorPosition(min(position, text_units(text)))
            finally:
                self.edit.blockSignals(previous)
        self._last_text = text
        self.idle_timer.stop()
        self.osc.text(text)  # Immediate plain-text attempt, no debounce/queue.
        self.osc.typing(bool(text) or self.edit.composing())
        if text:
            self.idle_timer.start()

    def _ime_activity(self):
        if not self._ensure_session_focus():
            return
        # Preedit belongs to the native IME; only committed text is broadcast.
        # Activity still postpones typing-off.
        if self.edit.composing():
            self.osc.typing(True)
            self.idle_timer.start()
        elif not self._last_text:
            # Cancelling the first preedit need not emit textChanged at all.
            self.idle_timer.stop()
            self.osc.typing(False)

    def _idle(self):
        if not self._ensure_session_focus():
            return
        self.osc.typing(False)

    def _submit(self):
        if self._ensure_session_focus() and not self.edit.composing():
            self.finish(True, "enter", restore=True)

    def _cancel(self):
        self.finish(False, "escape", restore=True)

    def finish(self, submit: bool, reason: str, restore: bool = False):
        if self.closing:
            return
        self.closing = True  # Set before IME reset, focus changes or signals.
        self._focus_ready = False
        self.idle_timer.stop()
        self.focus_check.stop()
        self.edit.blockSignals(True)
        text = clip_text(self.edit.text()) if submit else ""
        can_restore = (restore and foreground() == self.bar_hwnd
                       and window_ids(self.target)[1] == self.target_pid)
        # All send attempts happen synchronously before hiding/deferred deletion.
        # Enter is never followed by an empty packet from closeEvent/shutdown.
        try:
            self.osc.text(text, notify=submit and bool(text))
        finally:
            try:
                self.osc.typing(False, force=True)
            finally:
                try:
                    if self.edit.hasFocus():
                        QApplication.inputMethod().reset()
                    cancel_imm(self.edit.native_hwnd)
                finally:
                    self.hide()
                    self.deleteLater()
        if can_restore and IsWindow(self.target):
            SetForegroundWindow(self.target)
        LOG.debug("Bar finished: %s", reason)

    def closeEvent(self, event):
        self.finish(False, "window-close")
        event.accept()


class Controller(QObject):
    def __init__(self, app: QApplication, args):
        super().__init__(app)
        self.app, self.args = app, args
        self.osc = OscSender(args.osc_host, args.osc_port, args.osc_address)
        self.bridge = Bridge(self)
        self.guard = MenuGuard()
        self.camera = CameraMonitor(self.guard, args.camera_listen_port, self)
        self.foreground_monitor = ForegroundMonitor(self)
        self.foreground_monitor.changed.connect(self.on_foreground_changed, Qt.QueuedConnection)
        self.hook = YHook(self.bridge, self.guard)
        self.bar = None
        self.stopping = False
        self._failures_reported = 0
        self._hook_error_reported = ""
        self._status_reported = ""
        self._target_snapshot = None
        self.bridge.wake.connect(self.show_bar, Qt.QueuedConnection)
        self.scan_timer = QTimer(self)
        self.scan_timer.setInterval(500)
        self.scan_timer.timeout.connect(self.refresh_targets)
        # Also lets CPython process Ctrl+C while Qt owns the event loop.
        self.heartbeat = QTimer(self)
        self.heartbeat.setInterval(250)
        self.heartbeat.timeout.connect(self.check_health)
        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(app.style().standardIcon(QStyle.SP_MessageBoxInformation), self)
            self.tray.setToolTip("VRC LinguaBar · Y 直接聊天 · 失焦自动关闭")
            self.menu = QMenu()
            self.menu.addAction("退出", app.quit)
            self.tray.setContextMenu(self.menu)
            self.tray.show()
        app.aboutToQuit.connect(self.shutdown)

    def start(self):
        self.refresh_targets()
        self.foreground_monitor.start()
        self.hook.start()
        self.scan_timer.start()
        self.heartbeat.start()

    @pyqtSlot()
    def refresh_targets(self):
        if self.stopping:
            return
        targets = {}
        names = {}

        def visit(hwnd, _):
            if win32gui.IsWindowVisible(hwnd) and class_name(hwnd) == VRCHAT_CLASS:
                _, pid = window_ids(hwnd)
                if pid not in names:
                    names[pid] = process_name(pid)
                if names[pid] == "vrchat.exe" and (not self.args.vrchat_pid or pid == self.args.vrchat_pid):
                    targets[int(hwnd)] = pid
            return True

        try:
            win32gui.EnumWindows(visit, None)
        except win32gui.error as exc:
            LOG.debug("Window enumeration failed: %s", exc)
        # CPython/GIL: publish a new snapshot, never mutate shared dictionaries.
        self.hook.targets = targets
        snapshot = tuple(sorted(targets.items()))
        if snapshot != self._target_snapshot:
            self._target_snapshot = snapshot
            self.guard.reset_session()  # Discard camera state for the previous game.

    @pyqtSlot(object)
    def on_foreground_changed(self, hwnd):
        if not self.stopping and self.bar is not None:
            # Re-read the CURRENT foreground when the queued check executes;
            # an old notification must not close a newly activated bar.
            self.bar.schedule_focus_check()

    @pyqtSlot(object)
    def show_bar(self, target):
        try:
            if self.stopping:
                return
            hwnd, pid, ticket = target
            self.camera.drain()
            if (foreground() != hwnd or window_ids(hwnd)[1] != pid
                    or class_name(hwnd) != VRCHAT_CLASS
                    or process_name(pid) != "vrchat.exe"
                    or self.hook.targets.get(hwnd) != pid
                    or ticket != self.guard.ticket()
                    or self.guard.check(hwnd, pid)):
                return
            if self.bar is not None:
                if self.bar.target == hwnd and self.bar.target_pid == pid:
                    self.bar.refocus()
                return
            self.bar = InputBar(self.osc, hwnd, self.args.idle_ms)
            self.bar.destroyed.connect(self.bar_destroyed)
            self.bar.open_bar()
        except Exception:
            LOG.exception("Cannot open input bar")
            if self.bar is not None:
                self.bar.finish(False, "open-error")
        finally:
            self.hook.pending.clear()

    def bar_destroyed(self, _=None):
        # Keep the wrapper until actual QObject destruction, not just hide().
        self.bar = None

    @pyqtSlot()
    def check_health(self):
        # This timer checks service health only. No foreground polling/cleanup.
        status = (f"Camera active: {self.guard.camera_mode}" if self.guard.camera_mode not in (None, 0)
                  else self.guard.reason)
        if status != self._status_reported:
            self._status_reported = status
            LOG.info("%s", status)
            if self.tray:
                self.tray.setToolTip("VRC LinguaBar · " + status)
        if self.hook.error and self.hook.error != self._hook_error_reported:
            self._hook_error_reported = self.hook.error
            LOG.error("%s", self.hook.error)
        if self.hook.thread and not self.hook.thread.is_alive() and not self.stopping:
            LOG.error("Keyboard hook thread exited; stopping application")
            self.app.exit(1)
        if self.osc.failures != self._failures_reported:
            self._failures_reported = self.osc.failures
            LOG.warning("OSC send failures=%d; latest: %s", self.osc.failures, self.osc.last_error)

    @pyqtSlot()
    def shutdown(self):
        if self.stopping:
            return
        self.stopping = True
        self.scan_timer.stop()
        self.heartbeat.stop()
        self.foreground_monitor.stop()
        self.camera.socket.close()
        self.hook.stop()
        try:
            if self.bar is not None:
                self.bar.finish(False, "app-exit")
            else:
                # Do not clear a previously submitted final message on exit.
                self.osc.typing(False, force=True)
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        finally:
            self.osc.close()
            if self.tray:
                self.tray.hide()
                self.menu.deleteLater()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--osc-host", default="127.0.0.1")
    parser.add_argument("--osc-port", type=int, default=9000)
    parser.add_argument("--osc-address", default="/chatbox/input")
    parser.add_argument("--idle-ms", type=int, default=IDLE_MS,
                        help="Turn off the typing indicator after this idle interval (default: 2500)")
    parser.add_argument("--camera-listen-port", type=int, default=9001,
                        help="Local VRChat OSC output/forwarding port; 0 disables observation")
    parser.add_argument("--vrchat-pid", type=int, default=0,
                        help="Restrict capture to this VRChat PID; 0 accepts verified VRChat processes")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.osc_port <= 65535:
        parser.error("--osc-port must be between 1 and 65535")
    if not 0 <= args.camera_listen_port <= 65535 or args.vrchat_pid < 0:
        parser.error("Invalid camera port or VRChat PID")
    if not args.osc_address.startswith("/") or "\x00" in args.osc_address:
        parser.error("--osc-address must start with / and contain no NUL")
    if not 1 <= args.idle_ms <= 2147483647:
        parser.error("--idle-ms must be between 1 and 2147483647")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication([sys.argv[0]])
    app.setApplicationName("VRC LinguaBar")
    app.setQuitOnLastWindowClosed(False)
    controller = None
    old_sigint = signal.signal(signal.SIGINT, lambda *_: app.quit())
    try:
        controller = Controller(app, args)
        controller.start()
        LOG.info("Ready: Y chats directly; Enter sends; Esc/focus loss closes; Ctrl+C/tray exits")
        return int(app.exec_())
    except Exception:
        LOG.exception("Startup/runtime failure")
        return 1
    finally:
        if controller is not None:
            controller.shutdown()
        signal.signal(signal.SIGINT, old_sigint)


if __name__ == "__main__":
    raise SystemExit(main())
