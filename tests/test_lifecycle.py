import os
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import MagicMock, Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QCoreApplication, QEvent
from PyQt5.QtWidgets import QApplication, QSystemTrayIcon

from linguabar import app, startup
from linguabar.config import AppConfig, ENTRY_SCRIPT
from linguabar.osc import CameraMonitor, OscSender


class ControllerLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def test_service_creation_failure_releases_both_udp_sockets(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        resources = {}

        def sender(*args):
            resources["sender"] = OscSender(*args)
            return resources["sender"]

        def camera(*args):
            resources["camera"] = CameraMonitor(*args)
            return resources["camera"]

        with patch.object(app, "OscSender", side_effect=sender), \
             patch.object(app, "CameraMonitor", side_effect=camera), \
             patch.object(app, "ForegroundMonitor", side_effect=RuntimeError("setup failed")):
            with self.assertRaisesRegex(RuntimeError, "setup failed"):
                app.Controller(self.qt_app, AppConfig(camera_listen_port=port))
        self.assertEqual(resources["sender"].socket.fileno(), -1)
        self.assertFalse(resources["camera"].enabled)
        # Rebinding verifies the listener was released immediately, before Qt
        # processes deferred object destruction.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(("127.0.0.1", port))
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_tray_startup_setting_keeps_using_the_root_entrypoint(self):
        root_script = str(Path(__file__).resolve().parents[1] / "vrchat_linguabar.py")
        with patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=False), \
             patch.object(app, "OscSender"), patch.object(app, "CameraMonitor"):
            controller = app.Controller(self.qt_app, AppConfig(camera_listen_port=0))
        controller.startup_actions = {}
        try:
            with patch.object(startup, "configure") as configure, \
                 patch.object(startup, "current_mode", return_value="game") as current_mode:
                controller.set_startup_mode("game")
            configure.assert_called_once_with("game", root_script, sys.executable)
            current_mode.assert_called_once_with(root_script, sys.executable)
        finally:
            controller.shutdown()

    def test_tray_changes_when_game_appears_and_exits_without_camera_events(self):
        sender = Mock(failures=0, last_error="")
        with patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=False), \
             patch.object(app, "OscSender", return_value=sender), \
             patch.object(app, "CameraMonitor"):
            controller = app.Controller(self.qt_app, AppConfig(camera_listen_port=0))
        controller.tray = Mock()
        controller.menu = Mock()
        try:
            controller.check_health()
            self.assertIn("等待 VRChat", controller.tray.setToolTip.call_args.args[0])
            controller.hook.targets = {11: 22}
            controller.check_health()
            self.assertIn("按 Y 聊天", controller.tray.setToolTip.call_args.args[0])
            controller.hook.targets = {}
            controller.check_health()
            self.assertIn("等待 VRChat", controller.tray.setToolTip.call_args.args[0])
            self.assertEqual(controller.tray.setToolTip.call_count, 3)
        finally:
            controller.shutdown()


class StartupEntryTests(unittest.TestCase):
    def test_startup_only_cli_uses_root_entrypoint_without_creating_gui(self):
        root_script = Path(__file__).resolve().parents[1] / "vrchat_linguabar.py"
        self.assertEqual(ENTRY_SCRIPT, root_script)
        with patch.object(startup, "configure_logging"), \
             patch.object(startup, "configure") as configure, \
             patch.object(app, "CreateMutex") as mutex:
            self.assertEqual(app.run(AppConfig(startup_mode="login")), 0)
        configure.assert_called_once_with("login", str(root_script), sys.executable)
        mutex.assert_not_called()

    def test_watcher_entrypoint_launches_root_script_after_module_move(self):
        root_script = str(Path(__file__).resolve().parents[1] / "vrchat_linguabar.py")
        with patch.object(startup, "configure_logging"), \
             patch.object(startup, "watch_game") as watch:
            self.assertEqual(startup.main(["--watch-game"]), 0)
        watch.assert_called_once_with(root_script, sys.executable)

    def test_watcher_retries_failed_launch_then_waits_for_next_game_session(self):
        kernel = MagicMock()
        kernel.CreateMutexW.return_value = 123
        expected = startup.run_command("game", str(ENTRY_SCRIPT), sys.executable)
        with patch.object(startup.ctypes, "WinDLL", return_value=kernel), \
             patch.object(startup.ctypes, "get_last_error", return_value=0), \
             patch.object(startup, "read_run_command", side_effect=[expected] * 3 + [""]), \
             patch.object(startup, "process_running", return_value=True), \
             patch.object(startup.time, "sleep"), \
             patch.object(startup.subprocess, "Popen", side_effect=[OSError("launch failed"), Mock()]) as launched, \
             self.assertLogs("vrchat-ime.startup", level="WARNING"):
            startup.watch_game(str(ENTRY_SCRIPT), sys.executable)
        self.assertEqual(launched.call_count, 2)
        kernel.CloseHandle.assert_called_once_with(123)


if __name__ == "__main__":
    unittest.main()
