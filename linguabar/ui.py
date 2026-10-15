"""The IME-aware editor and one input session's window and cleanup."""
from __future__ import annotations

import logging
import time

import win32api
import win32gui
from PyQt5.QtCore import QEvent, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import QApplication, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from .config import APP_NAME, MAX_CHARS
from .osc import OscSender
from .text import clip_text, text_units
from .win32 import (
    GetAncestor, IsWindow, SetForegroundWindow, acquire_focus, cancel_imm,
    class_name, foreground, imm_composing, process_name, window_ids,
)

LOG = logging.getLogger("vrchat-ime")


class ImeLineEdit(QLineEdit):
    submit = pyqtSignal()
    cancel = pyqtSignal()
    ime_activity = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.preedit = ""
        self.handling_ime_event = False
        self.native_hwnd = 0
        self.last_commit = float("-inf")
        self.setMaxLength(MAX_CHARS)

    def composing(self) -> bool:
        return bool(self.preedit) or imm_composing(self.native_hwnd)

    def inputMethodEvent(self, event):
        self.preedit = event.preeditString()
        if event.commitString():
            self.last_commit = time.monotonic()
        # Qt emits textChanged synchronously while committing a candidate.
        # Its external IME window may still own foreground during this call.
        previous = self.handling_ime_event
        self.handling_ime_event = True
        try:
            super().inputMethodEvent(event)
            self.ime_activity.emit()
        finally:
            self.handling_ime_event = previous

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
    finished = pyqtSignal()
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
        # This window must activate so the native IME can own a real editor.
        # One explicit deleteLater path; no WA_DeleteOnClose / manual child deletion.
        self.setWindowTitle(APP_NAME)
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
        if ((self.edit.composing() or self.edit.handling_ime_event)
                and class_name(hwnd) in self.IME_CLASSES):
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
            try:
                self.osc.text(text, notify=submit and bool(text))
            finally:
                try:
                    self.osc.typing(False, force=True)
                finally:
                    try:
                        if self.edit.hasFocus():
                            QApplication.inputMethod().reset()
                    finally:
                        cancel_imm(self.edit.native_hwnd)
        except Exception:
            LOG.exception("Cannot complete all input session cleanup steps")
        finally:
            self.hide()
            self.deleteLater()
            self.finished.emit()
        # IME cleanup and hiding can dispatch native focus changes. Do not
        # override a switch to another application or focus a reused HWND.
        if (can_restore and foreground() in (self.bar_hwnd, self.target)
                and IsWindow(self.target)
                and window_ids(self.target)[1] == self.target_pid):
            SetForegroundWindow(self.target)
        (LOG.warning if reason == "focus-acquisition-failed" else LOG.debug)(
            "Bar finished: %s", reason)

    def closeEvent(self, event):
        self.finish(False, "window-close")
        event.accept()
