import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QCoreApplication, QEvent
from PyQt5.QtWidgets import QApplication, QSystemTrayIcon

from linguabar import app
from linguabar.config import AppConfig


class GameSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self):
        with patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=False), \
             patch.object(app, "OscSender"), patch.object(app, "CameraMonitor"):
            self.controller = app.Controller(self.qt_app, AppConfig())
        self.controller.hook.targets = {11: 111}
        self.controller._target_snapshot = ((11, 111),)

    def tearDown(self):
        self.controller.shutdown()
        self.controller.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_game_exit_closes_the_input_session_even_if_bar_keeps_focus(self):
        bar = Mock(target=11, target_pid=111)
        bar.finish.side_effect = lambda *args: self.controller.bar_finished()
        self.controller.bar = bar
        with patch.object(app.win32gui, "EnumWindows"):
            self.controller.refresh_targets()
        bar.finish.assert_called_once_with(False, "game-exit")
        self.assertIsNone(self.controller.bar)

    def test_reused_window_handle_does_not_keep_the_previous_game_session(self):
        bar = Mock(target=11, target_pid=111)
        bar.finish.side_effect = lambda *args: self.controller.bar_finished()
        self.controller.bar = bar
        with patch.object(app.win32gui, "EnumWindows", side_effect=lambda visit, data: visit(11, data)), \
             patch.object(app.win32gui, "IsWindowVisible", return_value=True), \
             patch.object(app, "class_name", return_value=app.VRCHAT_CLASS), \
             patch.object(app, "window_ids", return_value=(1, 222)), \
             patch.object(app, "process_name", return_value="vrchat.exe"):
            self.controller.refresh_targets()
        bar.finish.assert_called_once_with(False, "game-exit")
        self.assertEqual(self.controller.hook.targets, {11: 222})

    def test_failed_window_scan_preserves_camera_and_previous_targets(self):
        self.controller.guard.camera_update(3)
        before = self.controller.guard.ticket()
        with patch.object(app.win32gui, "EnumWindows", side_effect=app.win32gui.error(5, "EnumWindows", "failed")):
            self.controller.refresh_targets()
        self.assertEqual(self.controller.hook.targets, {11: 111})
        self.assertEqual(self.controller.guard.camera_mode, 3)
        self.assertEqual(self.controller.guard.ticket(), before)


if __name__ == "__main__":
    unittest.main()
