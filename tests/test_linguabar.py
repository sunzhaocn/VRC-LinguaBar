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

from linguabar import app, guards, hooks, startup, ui, win32
from linguabar.config import ENTRY_SCRIPT


class GuardTests(unittest.TestCase):
    def test_released_cursor_distinguishes_live_menu_from_gameplay_lock(self):
        def clip(left, top, right, bottom):
            def fill(pointer):
                rect = ctypes.cast(pointer, ctypes.POINTER(win32.W.RECT)).contents
                rect.left, rect.top, rect.right, rect.bottom = left, top, right, bottom
                return True
            return fill

        with patch.object(guards, "GetClipCursor", side_effect=clip(960, 540, 960, 540)):
            self.assertEqual(guards.released_game_cursor_reason(), "")
        with patch.object(guards, "GetClipCursor", side_effect=clip(0, 0, 1920, 1080)):
            self.assertEqual(guards.released_game_cursor_reason(), "Game cursor released / possible menu")
        with patch.object(guards, "GetClipCursor", return_value=False):
            self.assertEqual(guards.released_game_cursor_reason(), "")

    def test_native_text_control_takes_precedence_over_released_cursor(self):
        guard = guards.MenuGuard()
        with patch.object(guards, "native_ui_reason", return_value="Native text/child control active"), \
             patch.object(guards, "released_game_cursor_reason", return_value="Game cursor released / possible menu"):
            self.assertEqual(guard.check(11, 22), "Native text/child control active")

    def test_visible_cursor_and_non_text_child_do_not_block_y(self):
        def gui_info(_tid, pointer):
            info = ctypes.cast(pointer, ctypes.POINTER(win32.GUITHREADINFO)).contents
            info.hwndFocus = 22
            return True

        with patch.object(guards, "IsIconic", return_value=False), \
             patch.object(guards, "window_ids", return_value=(7, 8)), \
             patch.object(guards, "GetGUIThreadInfo", side_effect=gui_info), \
             patch.object(guards, "class_name", return_value="UnityWndClass"):
            self.assertEqual(guards.native_ui_reason(11), "")

    def test_native_menu_still_blocks_y(self):
        def gui_info(_tid, pointer):
            info = ctypes.cast(pointer, ctypes.POINTER(win32.GUITHREADINFO)).contents
            info.flags = 0x04
            return True

        with patch.object(guards, "IsIconic", return_value=False), \
             patch.object(guards, "window_ids", return_value=(7, 8)), \
             patch.object(guards, "GetGUIThreadInfo", side_effect=gui_info):
            self.assertEqual(guards.native_ui_reason(11), "Native menu active")

    def test_unavailable_gui_thread_info_does_not_disable_y(self):
        with patch.object(guards, "IsIconic", return_value=False), \
             patch.object(guards, "window_ids", return_value=(7, 8)), \
             patch.object(guards, "GetGUIThreadInfo", return_value=False):
            self.assertEqual(guards.native_ui_reason(11), "")


class HookTests(unittest.TestCase):
    def make_hook(self):
        events = []
        bridge = SimpleNamespace(wake=SimpleNamespace(emit=events.append))
        guard = guards.MenuGuard()
        hook = hooks.YHook(bridge, guard)
        hook.targets = {11: 22}
        key = hooks.KBDLLHOOKSTRUCT()
        key.vkCode = hooks.VK_Y
        return hook, guard, key, events

    def test_y_is_captured_once_for_existing_game(self):
        hook, guard, key, events = self.make_hook()
        with patch.object(hooks, "game_foreground", return_value=11), \
             patch.object(hooks, "window_ids", return_value=(1, 22)), \
             patch.object(hooks, "class_name", return_value=hooks.VRCHAT_CLASS), \
             patch.object(hooks, "GetAsyncKeyState", return_value=0), \
             patch.object(guards, "native_ui_reason", return_value=""), \
             patch.object(guards, "released_game_cursor_reason", return_value=""):
            pointer = ctypes.addressof(key)
            self.assertEqual(hook._callback(0, hooks.WM_KEYDOWN, pointer), 1)
            self.assertEqual(hook._callback(0, hooks.WM_KEYDOWN, pointer), 1)
            self.assertEqual(hook._callback(0, hooks.WM_KEYUP, pointer), 1)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][:2], (11, 22))

    def test_shift_y_while_moving_opens_bar_without_reaching_game(self):
        hook, guard, key, events = self.make_hook()
        held = {0x10, ord("W"), ord("A"), ord("S"), ord("D")}
        with patch.object(hooks, "game_foreground", return_value=11), \
             patch.object(hooks, "window_ids", return_value=(1, 22)), \
             patch.object(hooks, "class_name", return_value=hooks.VRCHAT_CLASS), \
             patch.object(hooks, "GetAsyncKeyState", side_effect=lambda vk: 0x8000 if vk in held else 0), \
             patch.object(guards, "native_ui_reason", return_value=""), \
             patch.object(guards, "released_game_cursor_reason", return_value=""), \
             patch.object(hooks, "CallNextHookEx") as next_hook:
            pointer = ctypes.addressof(key)
            self.assertEqual(hook._callback(0, hooks.WM_KEYDOWN, pointer), 1)
            self.assertEqual(hook._callback(0, hooks.WM_KEYUP, pointer), 1)
        next_hook.assert_not_called()
        self.assertEqual(len(events), 1)

    def test_y_in_menu_is_ignored_by_bar_and_game(self):
        hook, guard, key, events = self.make_hook()
        with patch.object(hooks, "game_foreground", return_value=11), \
             patch.object(hooks, "window_ids", return_value=(1, 22)), \
             patch.object(hooks, "class_name", return_value=hooks.VRCHAT_CLASS), \
             patch.object(hooks, "GetAsyncKeyState", return_value=0), \
             patch.object(guards, "native_ui_reason", return_value=""), \
             patch.object(guards, "released_game_cursor_reason", return_value="Game cursor released / possible menu"), \
             patch.object(hooks, "CallNextHookEx") as next_hook:
            pointer = ctypes.addressof(key)
            self.assertEqual(hook._callback(0, hooks.WM_KEYDOWN, pointer), 1)
            self.assertEqual(hook._callback(0, hooks.WM_KEYDOWN, pointer), 1)
            self.assertEqual(hook._callback(0, hooks.WM_KEYUP, pointer), 1)
        next_hook.assert_not_called()
        self.assertEqual(events, [])

    def test_y_in_native_text_control_remains_typable(self):
        hook, guard, key, events = self.make_hook()
        with patch.object(hooks, "game_foreground", return_value=11), \
             patch.object(hooks, "window_ids", return_value=(1, 22)), \
             patch.object(hooks, "class_name", return_value=hooks.VRCHAT_CLASS), \
             patch.object(hooks, "GetAsyncKeyState", return_value=0), \
             patch.object(guards, "native_ui_reason", return_value="Native text/child control active"), \
             patch.object(guards, "released_game_cursor_reason", return_value="Game cursor released / possible menu"), \
             patch.object(hooks, "CallNextHookEx", return_value=0) as next_hook:
            self.assertEqual(hook._callback(0, hooks.WM_KEYDOWN, ctypes.addressof(key)), 0)
        next_hook.assert_called_once()
        self.assertEqual(events, [])

    def test_focus_must_reach_edit(self):
        state = {"foreground": 11, "focus": 0}
        def set_foreground(hwnd):
            state["foreground"] = hwnd
            return True
        def set_focus(hwnd):
            state["focus"] = hwnd
            return 0

        with patch.object(win32, "foreground", side_effect=lambda: state["foreground"]), \
             patch.object(win32, "game_foreground", side_effect=lambda: state["foreground"]), \
             patch.object(win32, "SetForegroundWindow", side_effect=set_foreground), \
             patch.object(win32, "SetFocus", side_effect=set_focus), \
             patch.object(win32, "GetFocus", side_effect=lambda: state["focus"]):
            self.assertTrue(win32.acquire_focus(33, 44, 11))
            state.update(foreground=11, focus=0)
            with patch.object(win32, "GetFocus", return_value=0):
                self.assertFalse(win32.acquire_focus(33, 44, 11))

    def test_focus_activation_finishing_during_thread_attach_is_success(self):
        state = {"foreground": 11, "focus": 0}
        def attach(_src, _dst, enabled):
            if enabled:
                state["foreground"] = 33
            return True

        with patch.object(win32, "foreground", side_effect=lambda: state["foreground"]), \
             patch.object(win32, "game_foreground", side_effect=lambda: state["foreground"]), \
             patch.object(win32, "SetForegroundWindow", return_value=False), \
             patch.object(win32, "SetFocus", side_effect=lambda hwnd: state.update(focus=hwnd)), \
             patch.object(win32, "GetFocus", side_effect=lambda: state["focus"]), \
             patch.object(win32, "window_ids", return_value=(2, 22)), \
             patch.object(win32, "GetCurrentThreadId", return_value=1), \
             patch.object(win32, "AttachThreadInput", side_effect=attach) as attached:
            self.assertTrue(win32.acquire_focus(33, 44, 11))
        self.assertEqual(state["focus"], 44)
        self.assertEqual(attached.call_count, 2)  # Attach and detach.


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
             patch.object(app.win32gui, "EnumWindows", side_effect=enumerate_windows), \
             patch.object(app.win32gui, "IsWindowVisible", return_value=True), \
             patch.object(app, "class_name", return_value=app.VRCHAT_CLASS), \
             patch.object(app, "window_ids", side_effect=lambda hwnd: (1, hwnd + 100)), \
             patch.object(app, "process_name", return_value="vrchat.exe"):
            controller = app.Controller(self.app, args)
            try:
                controller.refresh_targets()
                self.assertEqual(controller.hook.targets, {11: 111})
                windows[:] = [33]
                controller.refresh_targets()
                self.assertEqual(controller.hook.targets, {33: 133})
            finally:
                controller.shutdown()

    def test_input_window_is_allowed_to_activate(self):
        with patch.object(ui, "window_ids", return_value=(1, 2)):
            bar = ui.InputBar(SimpleNamespace(), 11, 2500)
            self.assertFalse(bar.testAttribute(Qt.WA_ShowWithoutActivating))
            bar.deleteLater()

    def test_bar_open_and_close_releases_session_immediately(self):
        sent = []
        osc = SimpleNamespace(text=lambda *args, **kwargs: sent.append(("text", args)),
                              typing=lambda *args, **kwargs: sent.append(("typing", args)))
        closed = []
        with patch.object(ui, "window_ids", return_value=(1, 2)), \
             patch.object(ui.win32api, "MonitorFromWindow", side_effect=OSError()), \
             patch.object(ui, "acquire_focus", return_value=True), \
             patch.object(ui, "cancel_imm"):
            bar = ui.InputBar(osc, 11, 2500)
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

        script = os.path.abspath(str(ENTRY_SCRIPT))
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
                startup.configure("game", str(ENTRY_SCRIPT), sys.executable)
        self.assertEqual(calls[-1][-1], "old-command")

    def test_watcher_starts_once_per_game_session(self):
        from unittest.mock import MagicMock
        kernel = MagicMock()
        kernel.CreateMutexW.return_value = 123
        expected = startup.run_command("game", str(ENTRY_SCRIPT), sys.executable)
        with patch.object(startup.ctypes, "WinDLL", return_value=kernel), \
             patch.object(startup.ctypes, "get_last_error", return_value=0), \
             patch.object(startup, "read_run_command", side_effect=[expected] * 4 + [""]), \
             patch.object(startup, "process_running", side_effect=[True, True, False, True]), \
             patch.object(startup.time, "sleep"), \
             patch.object(startup.subprocess, "Popen") as launched:
            startup.watch_game(str(ENTRY_SCRIPT), sys.executable)
        self.assertEqual(launched.call_count, 2)
        kernel.CloseHandle.assert_called_once_with(123)


if __name__ == "__main__":
    unittest.main()
