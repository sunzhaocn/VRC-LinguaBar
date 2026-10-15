import os
import unittest
from unittest.mock import Mock, call, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QCoreApplication, QEvent, Qt
from PyQt5.QtGui import QInputMethodEvent, QKeyEvent
from PyQt5.QtWidgets import QApplication

from linguabar import ui


class InputSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        for name, value in (("window_ids", (1, 2)), ("imm_composing", False),
                            ("cancel_imm", None)):
            patcher = patch.object(ui, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.osc = Mock()
        self.bar = ui.InputBar(self.osc, 11, 2500)
        self.closed = []
        self.bar.finished.connect(lambda: self.closed.append(True))

    def tearDown(self):
        if not self.bar.closing:
            self.bar.finish(False, "test-cleanup")
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def key(self, key, repeat=False):
        self.bar.edit.keyPressEvent(QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier,
                                             "", repeat))

    def test_preedit_is_private_and_committed_text_is_sent_immediately(self):
        self.bar.edit.inputMethodEvent(QInputMethodEvent("ni hao", []))
        self.osc.text.assert_not_called()
        self.osc.typing.assert_called_with(True)
        commit = QInputMethodEvent("", [])
        commit.setCommitString("你好")
        self.bar.edit.inputMethodEvent(commit)
        self.osc.text.assert_called_once_with("你好")
        self.assertEqual(self.bar.edit.text(), "你好")
        self.assertTrue(self.bar.idle_timer.isActive())

    def test_cancelled_preedit_turns_typing_off_without_sending_text(self):
        self.bar.edit.inputMethodEvent(QInputMethodEvent("ni", []))
        self.bar.edit.inputMethodEvent(QInputMethodEvent("", []))
        self.osc.text.assert_not_called()
        self.assertEqual(self.osc.typing.call_args_list, [call(True), call(False)])
        self.assertFalse(self.bar.idle_timer.isActive())

    def test_ime_confirmation_enter_does_not_also_submit_the_message(self):
        self.bar.edit.inputMethodEvent(QInputMethodEvent("ni", []))
        self.key(Qt.Key_Return)
        self.assertFalse(self.bar.closing)
        self.key(Qt.Key_Escape)
        self.assertFalse(self.bar.closing)
        commit = QInputMethodEvent("", [])
        commit.setCommitString("你")
        with patch.object(ui.time, "monotonic", return_value=100.0):
            self.bar.edit.inputMethodEvent(commit)
        with patch.object(ui.time, "monotonic", return_value=100.04):
            self.key(Qt.Key_Return)
        self.assertFalse(self.bar.closing)
        with patch.object(ui.time, "monotonic", return_value=100.1):
            self.key(Qt.Key_Return, repeat=True)
            self.assertFalse(self.bar.closing)
            self.key(Qt.Key_Return)
        self.assertTrue(self.bar.closing)
        self.osc.text.assert_called_with("你", notify=True)

    def test_submit_is_not_cleared_by_later_close_or_idle_callbacks(self):
        self.bar.edit.setText("已经发送 😀")
        self.osc.reset_mock()
        self.bar.finish(True, "enter")
        self.bar.finish(False, "window-close")
        self.bar._idle()
        self.assertEqual(self.closed, [True])
        self.osc.text.assert_called_once_with("已经发送 😀", notify=True)
        self.osc.typing.assert_called_once_with(False, force=True)
        self.assertFalse(self.bar.idle_timer.isActive())
        self.assertFalse(self.bar.focus_check.isActive())

    def test_external_focus_loss_closes_even_during_ime_composition(self):
        self.bar.edit.setText("未发送")
        self.bar.edit.preedit = "ni"
        self.bar._focus_ready = True
        self.bar.bar_hwnd = 33
        self.osc.reset_mock()
        with patch.object(ui, "foreground", return_value=99), \
             patch.object(ui, "GetAncestor", return_value=99), \
             patch.object(ui, "class_name", return_value="Chrome_WidgetWin_1"):
            self.assertFalse(self.bar._ensure_session_focus())
        self.assertTrue(self.bar.closing)
        self.assertEqual(self.closed, [True])
        self.osc.text.assert_called_once_with("", notify=False)
        self.osc.typing.assert_called_once_with(False, force=True)

    def test_native_ime_candidate_window_can_keep_composition_active(self):
        self.bar.edit.preedit = "ni"
        self.bar._focus_ready = True
        self.bar.bar_hwnd = 33
        with patch.object(ui, "foreground", return_value=99), \
             patch.object(ui, "GetAncestor", return_value=99), \
             patch.object(ui, "class_name", return_value="MSCTFIME UI"), \
             patch.object(ui, "process_name", return_value="ctfmon.exe"):
            self.assertTrue(self.bar._ensure_session_focus())
        self.assertFalse(self.bar.closing)
        self.assertEqual(self.closed, [])

    def test_session_is_released_even_when_final_send_raises(self):
        self.bar.edit.setText("消息")
        self.osc.text.side_effect = RuntimeError("send failed")
        with self.assertLogs("vrchat-ime", level="ERROR"):
            self.bar.finish(True, "enter")
        self.assertTrue(self.bar.closing)
        self.assertEqual(self.closed, [True])
        self.osc.typing.assert_called_with(False, force=True)
        self.assertFalse(self.bar.isVisible())


if __name__ == "__main__":
    unittest.main()
