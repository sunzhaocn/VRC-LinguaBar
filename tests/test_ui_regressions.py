import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QCoreApplication, QEvent
from PyQt5.QtGui import QInputMethodEvent
from PyQt5.QtWidgets import QApplication

from linguabar import ui


class FocusRestoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.state = {"foreground": 33, "pid": 111}
        for name, kwargs in (
            ("window_ids", {"side_effect": lambda hwnd: (1, self.state["pid"])}),
            ("foreground", {"side_effect": lambda: self.state["foreground"]}),
            ("IsWindow", {"return_value": True}),
            ("imm_composing", {"return_value": False}),
            ("cancel_imm", {}),
        ):
            patcher = patch.object(ui, name, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.bar = ui.InputBar(Mock(), 11, 2500)
        self.bar.bar_hwnd = 33
        self.bar.edit.setText("你好")

    def tearDown(self):
        if not self.bar.closing:
            self.bar.finish(False, "test-cleanup")
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_focus_switch_during_close_is_not_overridden(self):
        with patch.object(ui, "cancel_imm", side_effect=lambda hwnd: self.state.update(foreground=99)), \
             patch.object(ui, "SetForegroundWindow") as restore:
            self.bar.finish(True, "enter", restore=True)
        restore.assert_not_called()

    def test_replaced_game_process_is_not_focused_after_close(self):
        with patch.object(ui, "cancel_imm", side_effect=lambda hwnd: self.state.update(pid=222)), \
             patch.object(ui, "SetForegroundWindow") as restore:
            self.bar.finish(True, "enter", restore=True)
        restore.assert_not_called()

    def test_normal_close_still_restores_game_focus(self):
        with patch.object(ui, "SetForegroundWindow") as restore:
            self.bar.finish(True, "enter", restore=True)
        restore.assert_called_once_with(11)

    def test_candidate_commit_keeps_text_while_ime_window_still_has_foreground(self):
        self.bar.edit.clear()
        self.bar._focus_ready = True
        self.state["foreground"] = 99
        with patch.object(ui, "GetAncestor", return_value=99), \
             patch.object(ui, "class_name", return_value="MSCTFIME UI"), \
             patch.object(ui, "process_name", return_value="ctfmon.exe"):
            QCoreApplication.sendEvent(self.bar.edit, QInputMethodEvent("ni hao", []))
            self.bar.osc.reset_mock()
            commit = QInputMethodEvent("", [])
            commit.setCommitString("你好")
            QCoreApplication.sendEvent(self.bar.edit, commit)
            self.assertFalse(self.bar.closing)
            self.bar.osc.text.assert_called_once_with("你好")
            self.assertFalse(self.bar.edit.handling_ime_event)
            self.assertFalse(self.bar._foreground_is_input())
        self.state["foreground"] = 33
        self.assertTrue(self.bar._ensure_session_focus())

    def test_candidate_commit_does_not_exempt_an_unrelated_application(self):
        for window_class, process in (("Chrome_WidgetWin_1", "chrome.exe"),
                                      ("MSCTFIME UI", "chrome.exe")):
            with self.subTest(window_class=window_class):
                bar = ui.InputBar(Mock(), 11, 2500)
                bar.bar_hwnd = 33
                bar._focus_ready = True
                bar.edit.preedit = "ni"
                self.state["foreground"] = 99
                with patch.object(ui, "GetAncestor", return_value=99), \
                     patch.object(ui, "class_name", return_value=window_class), \
                     patch.object(ui, "process_name", return_value=process):
                    commit = QInputMethodEvent("", [])
                    commit.setCommitString("你")
                    QCoreApplication.sendEvent(bar.edit, commit)
                self.assertTrue(bar.closing)
                self.assertFalse(bar.edit.handling_ime_event)
                bar.osc.text.assert_called_once_with("", notify=False)


if __name__ == "__main__":
    unittest.main()
