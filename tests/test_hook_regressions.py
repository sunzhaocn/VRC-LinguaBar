import ctypes
import os
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from linguabar import guards, hooks


class HookPressLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        bridge = SimpleNamespace(wake=SimpleNamespace(emit=self.events.append))
        self.guard = guards.MenuGuard()
        self.hook = hooks.YHook(bridge, self.guard)
        self.hook.targets = {11: 22}
        self.held = set()
        self.foreground = 11
        self.reason = ""
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(hooks, "game_foreground", side_effect=lambda: self.foreground))
        self.stack.enter_context(patch.object(hooks, "window_ids", return_value=(1, 22)))
        self.stack.enter_context(patch.object(hooks, "class_name", return_value=hooks.VRCHAT_CLASS))
        self.stack.enter_context(patch.object(hooks, "GetAsyncKeyState", side_effect=lambda vk: 0x8000 if vk in self.held else 0))
        self.stack.enter_context(patch.object(self.guard, "check", side_effect=lambda *_: self.reason))
        self.next_hook = self.stack.enter_context(patch.object(hooks, "CallNextHookEx", return_value=0))

    def key(self, message, flags=0):
        key = hooks.KBDLLHOOKSTRUCT()
        key.vkCode = hooks.VK_Y
        key.flags = flags
        return self.hook._callback(0, message, ctypes.addressof(key))

    def test_shortcut_y_remains_forwarded_until_keyup_after_modifier_release(self):
        for modifier in (0x11, 0x12, 0x5B, 0x5C):
            with self.subTest(modifier=modifier):
                self.events.clear()
                self.held = {modifier}
                message = hooks.WM_SYSKEYDOWN if modifier == 0x12 else hooks.WM_KEYDOWN
                initial = self.key(message)
                self.held.clear()
                repeat = self.key(hooks.WM_KEYDOWN)
                release = self.key(hooks.WM_KEYUP)
                self.assertEqual((initial, repeat, release), (0, 0, 0))
                self.assertEqual(self.events, [])
        # A genuinely new, unmodified press still opens the bar.
        self.assertEqual(self.key(hooks.WM_KEYDOWN), 1)
        self.assertEqual(self.key(hooks.WM_KEYUP), 1)
        self.assertEqual(len(self.events), 1)

    def test_y_started_outside_game_does_not_open_bar_when_focus_changes(self):
        self.foreground = 99
        self.assertEqual(self.key(hooks.WM_KEYDOWN), 0)
        self.foreground = 11
        self.assertEqual(self.key(hooks.WM_KEYDOWN), 0)
        self.assertEqual(self.key(hooks.WM_KEYUP), 0)
        self.assertEqual(self.events, [])

    def test_native_text_press_keeps_its_keyup_when_control_loses_focus(self):
        self.reason = "Native text/child control active"
        self.assertEqual(self.key(hooks.WM_KEYDOWN), 0)
        self.reason = ""
        self.assertEqual(self.key(hooks.WM_KEYDOWN), 0)
        self.assertEqual(self.key(hooks.WM_KEYUP), 0)
        self.assertEqual(self.events, [])

    def test_injected_release_does_not_end_captured_physical_press(self):
        self.assertEqual(self.key(hooks.WM_KEYDOWN), 1)
        self.assertEqual(self.key(hooks.WM_KEYUP, hooks.LLKHF_INJECTED), 0)
        self.foreground = 99
        self.assertEqual(self.key(hooks.WM_KEYDOWN), 1)
        self.assertEqual(self.key(hooks.WM_KEYUP), 1)
        self.assertEqual(len(self.events), 1)

    def test_callback_error_keeps_forwarded_press_forwarded(self):
        with patch.object(self.guard, "check", side_effect=RuntimeError("unavailable")):
            self.assertEqual(self.key(hooks.WM_KEYDOWN), 0)
        self.assertEqual(self.key(hooks.WM_KEYDOWN), 0)
        self.assertEqual(self.key(hooks.WM_KEYUP), 0)
        self.assertEqual(self.events, [])
        self.assertIn("unavailable", self.hook.error)


if __name__ == "__main__":
    unittest.main()
