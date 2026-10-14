import ctypes
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QSystemTrayIcon

import startup
import vrchat_linguabar as vrc


class GuardTests(unittest.TestCase):
    def test_visible_cursor_and_non_text_child_do_not_block_y(self):
        def gui_info(_tid, pointer):
            info = ctypes.cast(pointer, ctypes.POINTER(vrc.GUITHREADINFO)).contents
            info.hwndFocus = 22
            return True

        with patch.object(vrc, "IsIconic", return_value=False), \
             patch.object(vrc, "window_ids", return_value=(7, 8)), \
             patch.object(vrc, "GetGUIThreadInfo", side_effect=gui_info), \
             patch.object(vrc, "class_name", return_value="UnityWndClass"):
            self.assertEqual(vrc.native_ui_reason(11), "")

    def test_native_menu_still_blocks_y(self):
        def gui_info(_tid, pointer):
            info = ctypes.cast(pointer, ctypes.POINTER(vrc.GUITHREADINFO)).contents
            info.flags = 0x04
            return True

        with patch.object(vrc, "IsIconic", return_value=False), \
             patch.object(vrc, "window_ids", return_value=(7, 8)), \
             patch.object(vrc, "GetGUIThreadInfo", side_effect=gui_info):
            self.assertEqual(vrc.native_ui_reason(11), "Native menu active")

    def test_unavailable_gui_thread_info_does_not_disable_y(self):
        with patch.object(vrc, "IsIconic", return_value=False), \
             patch.object(vrc, "window_ids", return_value=(7, 8)), \
             patch.object(vrc, "GetGUIThreadInfo", return_value=False):
            self.assertEqual(vrc.native_ui_reason(11), "")


class HookTests(unittest.TestCase):
    def test_y_is_captured_once_for_existing_game(self):
        events = []
        bridge = SimpleNamespace(wake=SimpleNamespace(emit=events.append))
        guard = vrc.MenuGuard()
        hook = vrc.YHook(bridge, guard)
        hook.targets = {11: 22}
        key = vrc.KBDLLHOOKSTRUCT()
        key.vkCode = vrc.VK_Y
        with patch.object(vrc, "game_foreground", return_value=11), \
             patch.object(vrc, "window_ids", return_value=(1, 22)), \
             patch.object(vrc, "class_name", return_value=vrc.VRCHAT_CLASS), \
             patch.object(vrc, "GetAsyncKeyState", return_value=0), \
             patch.object(vrc, "native_ui_reason", return_value=""):
            pointer = ctypes.addressof(key)
            self.assertEqual(hook._callback(0, vrc.WM_KEYDOWN, pointer), 1)
            self.assertEqual(hook._callback(0, vrc.WM_KEYDOWN, pointer), 1)
            self.assertEqual(hook._callback(0, vrc.WM_KEYUP, pointer), 1)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][:2], (11, 22))

    def test_focus_must_reach_edit(self):
        state = {"foreground": 11, "focus": 0}
        def set_foreground(hwnd):
            state["foreground"] = hwnd
            return True
        def set_focus(hwnd):
            state["focus"] = hwnd
            return 0

        with patch.object(vrc, "foreground", side_effect=lambda: state["foreground"]), \
             patch.object(vrc, "game_foreground", side_effect=lambda: state["foreground"]), \
             patch.object(vrc, "SetForegroundWindow", side_effect=set_foreground), \
             patch.object(vrc, "SetFocus", side_effect=set_focus), \
             patch.object(vrc, "GetFocus", side_effect=lambda: state["focus"]):
            self.assertTrue(vrc.acquire_focus(33, 44, 11))
            state.update(foreground=11, focus=0)
            with patch.object(vrc, "GetFocus", return_value=0):
                self.assertFalse(vrc.acquire_focus(33, 44, 11))


class ControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_finds_game_running_before_app_and_later(self):
        args = SimpleNamespace(osc_host="127.0.0.1", osc_port=9000,
                               osc_address="/chatbox/input", camera_listen_port=0,
                               vrchat_pid=0, idle_ms=2500)
        windows = [11]
        def enumerate_windows(callback, data):
            for hwnd in windows:
                callback(hwnd, data)

        with patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=False), \
             patch.object(vrc.win32gui, "EnumWindows", side_effect=enumerate_windows), \
             patch.object(vrc.win32gui, "IsWindowVisible", return_value=True), \
             patch.object(vrc, "class_name", return_value=vrc.VRCHAT_CLASS), \
             patch.object(vrc, "window_ids", side_effect=lambda hwnd: (1, hwnd + 100)), \
             patch.object(vrc, "process_name", return_value="vrchat.exe"):
            controller = vrc.Controller(self.app, args)
            try:
                controller.refresh_targets()
                self.assertEqual(controller.hook.targets, {11: 111})
                windows[:] = [33]
                controller.refresh_targets()
                self.assertEqual(controller.hook.targets, {33: 133})
            finally:
                controller.shutdown()

    def test_input_window_is_allowed_to_activate(self):
        with patch.object(vrc, "window_ids", return_value=(1, 2)):
            bar = vrc.InputBar(SimpleNamespace(), 11, 2500)
            self.assertFalse(bar.testAttribute(Qt.WA_ShowWithoutActivating))
            bar.deleteLater()

    def test_bar_open_and_close_releases_session_immediately(self):
        sent = []
        osc = SimpleNamespace(text=lambda *args, **kwargs: sent.append(("text", args)),
                              typing=lambda *args, **kwargs: sent.append(("typing", args)))
        closed = []
        with patch.object(vrc, "window_ids", return_value=(1, 2)), \
             patch.object(vrc.win32api, "MonitorFromWindow", side_effect=OSError()), \
             patch.object(vrc, "acquire_focus", return_value=True), \
             patch.object(vrc, "cancel_imm"):
            bar = vrc.InputBar(osc, 11, 2500)
            bar.finished.connect(lambda: closed.append(True))
            self.assertTrue(bar.open_bar())
            bar.finish(False, "test")
            self.assertTrue(bar.closing)
            self.assertEqual(closed, [True])
            self.assertEqual(sent[-2:], [("text", ("",)), ("typing", (False,))])


class StartupTests(unittest.TestCase):
    def test_running_process_snapshot(self):
        self.assertTrue(startup.process_running(os.path.basename(sys.executable)))

    def test_game_mode_uses_per_user_run_key_and_watcher(self):
        calls = []
        class Key:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        script = os.path.abspath(vrc.__file__)
        with patch.object(startup.winreg, "CreateKeyEx", return_value=Key()), \
             patch.object(startup.winreg, "SetValueEx", side_effect=lambda *args: calls.append(args)), \
             patch.object(startup.subprocess, "Popen") as launched:
            startup.configure("game", script, sys.executable)
        self.assertEqual(calls[0][1], startup.RUN_NAME)
        self.assertEqual(calls[0][-1], startup.run_command("game", script, sys.executable))
        launched.assert_called_once()

    def test_failed_watcher_launch_restores_previous_setting(self):
        calls = []
        class Key:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        with patch.object(startup, "read_run_command", return_value="old-command"), \
             patch.object(startup.winreg, "CreateKeyEx", return_value=Key()), \
             patch.object(startup.winreg, "SetValueEx", side_effect=lambda *args: calls.append(args)), \
             patch.object(startup.subprocess, "Popen", side_effect=OSError("launch failed")):
            with self.assertRaises(OSError):
                startup.configure("game", vrc.__file__, sys.executable)
        self.assertEqual(calls[-1][-1], "old-command")

    def test_watcher_starts_once_per_game_session(self):
        from unittest.mock import MagicMock
        kernel = MagicMock()
        kernel.CreateMutexW.return_value = 123
        expected = startup.run_command("game", vrc.__file__, sys.executable)
        with patch.object(startup.ctypes, "WinDLL", return_value=kernel), \
             patch.object(startup.ctypes, "get_last_error", return_value=0), \
             patch.object(startup, "read_run_command", side_effect=[expected] * 4 + [""]), \
             patch.object(startup, "process_running", side_effect=[True, True, False, True]), \
             patch.object(startup.time, "sleep"), \
             patch.object(startup.subprocess, "Popen") as launched:
            startup.watch_game(vrc.__file__, sys.executable)
        self.assertEqual(launched.call_count, 2)
        kernel.CloseHandle.assert_called_once_with(123)


if __name__ == "__main__":
    unittest.main()
