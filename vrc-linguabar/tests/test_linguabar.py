import ctypes
import gc
import socket
import threading
import unittest
from unittest.mock import patch
import weakref

import importlib.util
import os
from pathlib import Path
import sys

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
SOURCE = Path(__file__).resolve().parents[1] / 'vrchat_linguabar.py'
spec = importlib.util.spec_from_file_location('linguabar_under_test', SOURCE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
from PyQt5 import sip
from PyQt5.QtCore import Qt, QCoreApplication, QEvent
from PyQt5.QtTest import QTest
from PyQt5.QtGui import QFontDatabase

APP = m.QApplication.instance() or m.QApplication(['test'])
APP.setQuitOnLastWindowClosed(False)
for font_file in ('segoeui.ttf', 'msyh.ttc'):
    QFontDatabase.addApplicationFont(str(Path(os.environ['WINDIR']) / 'Fonts' / font_file))


class Recorder:
    def __init__(self):
        self.events = []

    def text(self, text, notify=False):
        self.events.append(('text', text, notify))
        return True

    def typing(self, active, force=False):
        self.events.append(('typing', active))

from PyQt5.QtGui import QInputMethodEvent, QFocusEvent
from pythonosc.osc_message import OscMessage


class BarTests(unittest.TestCase):
    def setUp(self):
        self.rec = Recorder()
        self.bar = m.InputBar(self.rec, 99, m.IDLE_MS)
        self.imm = patch.object(m, 'imm_composing', return_value=False)
        self.imm.start()

    def tearDown(self):
        if not sip.isdeleted(self.bar):
            self.bar.finish(False, 'test')
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.imm.stop()

    def test_plain_text_no_automatic_suffix_and_user_punctuation_preserved(self):
        self.bar.edit.setText('中文...')
        QTest.qWait(850)
        self.assertEqual([e for e in self.rec.events if e[0] == 'text'],
                         [('text', '中文...', False)])
        self.assertEqual(self.bar.edit.text(), '中文...')
        self.assertFalse(hasattr(self.bar, 'sync_timer'))

    def test_focus_loss_closes_and_clears_once(self):
        self.bar.edit.setText('draft')
        self.rec.events.clear()
        self.bar.bar_hwnd = 456
        self.bar._focus_ready = True
        with patch.object(m, 'foreground', return_value=987), \
             patch.object(m, 'GetAncestor', return_value=0):
            APP.sendEvent(self.bar, QEvent(QEvent.WindowDeactivate))
            QTest.qWait(20)
        self.assertTrue(sip.isdeleted(self.bar))
        self.assertEqual(self.rec.events, [('text', '', False), ('typing', False)])

    def test_foreground_notification_without_qt_deactivate_closes(self):
        self.bar._focus_ready = True
        with patch.object(self.bar, '_foreground_is_input', return_value=False):
            self.bar.schedule_focus_check()
            QTest.qWait(20)
        self.assertTrue(sip.isdeleted(self.bar))

    def test_stale_deactivation_does_not_close_current_foreground_bar(self):
        self.bar._focus_ready = True
        self.bar.bar_hwnd = 456
        with patch.object(m, 'foreground', return_value=456):
            APP.sendEvent(self.bar, QEvent(QEvent.WindowDeactivate))
            QTest.qWait(20)
        self.assertFalse(self.bar.closing)

    def test_composing_does_not_exempt_browser_focus(self):
        self.bar._focus_ready = True
        self.bar.edit.preedit = 'ni'
        with patch.object(m, 'foreground', return_value=987), \
             patch.object(m, 'GetAncestor', return_value=0), \
             patch.object(m, 'class_name', return_value='Chrome_WidgetWin_1'):
            self.assertFalse(self.bar._ensure_session_focus())
        self.assertTrue(self.bar.closing)

    def test_trusted_ime_candidate_does_not_close_input(self):
        self.bar._focus_ready = True
        self.bar.edit.preedit = 'ni'
        with patch.object(m, 'foreground', return_value=987), \
             patch.object(m, 'GetAncestor', return_value=0), \
             patch.object(m, 'class_name', return_value='Microsoft.IME.UIManager.CandidateWindow.Host'), \
             patch.object(m, 'window_ids', return_value=(20, 30)), \
             patch.object(m, 'process_name', return_value='textinputhost.exe'):
            self.assertTrue(self.bar._ensure_session_focus())
        self.assertFalse(self.bar.closing)

    def test_idle_only_changes_typing_state(self):
        self.bar.edit.setText('draft')
        self.rec.events.clear()
        self.bar._idle()
        self.assertEqual(self.rec.events, [('typing', False)])
        self.assertFalse(self.bar.closing)

    def test_enter_once_no_clear_after_close(self):
        self.bar.edit.setText('送信')
        self.rec.events.clear()
        QTest.keyClick(self.bar.edit, Qt.Key_Return)
        self.bar.close()
        self.bar.finish(False, 'again')
        self.assertEqual(self.rec.events, [('text', '送信', True), ('typing', False)])

    def test_escape_cancels(self):
        self.bar.edit.setText('draft')
        self.rec.events.clear()
        QTest.keyClick(self.bar.edit, Qt.Key_Escape)
        self.assertEqual(self.rec.events, [('text', '', False), ('typing', False)])

    def test_ime_commit_not_accidental_send(self):
        APP.sendEvent(self.bar.edit, QInputMethodEvent('にほん', []))
        QTest.keyClick(self.bar.edit, Qt.Key_Return)
        self.assertFalse(self.bar.closing)
        self.assertFalse(any(e[0] == 'text' for e in self.rec.events))
        commit = QInputMethodEvent('', [])
        commit.setCommitString('日本')
        APP.sendEvent(self.bar.edit, commit)
        QTest.keyClick(self.bar.edit, Qt.Key_Return)
        self.assertFalse(self.bar.closing)
        self.bar.edit.last_commit -= 1
        QTest.keyClick(self.bar.edit, Qt.Key_Return)
        self.assertEqual(self.rec.events[-2:], [('text', '日本', True), ('typing', False)])

    def test_unicode_limit(self):
        for n in (140, 141, 144):
            self.bar.edit.setText('界' * n)
            self.assertEqual(self.bar.edit.text(), '界' * n)
        self.bar.edit.setText('a' * 143 + '\U0001f600')
        self.assertEqual(self.bar.edit.text(), 'a' * 143)

    def test_focus_acquisition_failure_closes_bar(self):
        with patch.object(m, 'acquire_focus', return_value=False):
            self.assertFalse(self.bar.open_bar())
        self.assertTrue(self.bar.closing)
        self.assertEqual(self.rec.events, [('text', '', False), ('typing', False)])

    def test_refocus_does_not_clear_draft(self):
        self.bar.edit.setText('draft')
        self.rec.events.clear()
        with patch.object(m, 'acquire_focus', return_value=True):
            self.bar.refocus()
        self.assertEqual(self.bar.edit.text(), 'draft')
        self.assertFalse(any(e[0] == 'text' for e in self.rec.events))

    def test_repeated_disposal(self):
        refs = []
        for _ in range(200):
            bar = m.InputBar(Recorder(), 99, m.IDLE_MS)
            refs.append(weakref.ref(bar))
            bar.finish(False, 'cycle')
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        del bar
        gc.collect()
        self.assertTrue(all(ref() is None for ref in refs))


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.guard = m.MenuGuard()
        self.native = patch.object(m, 'native_ui_reason', return_value='')
        self.native.start()

    def tearDown(self):
        self.native.stop()

    def test_initial_state_automatically_allows_y(self):
        self.assertEqual(self.guard.check(10, 20), '')

    def test_menu_close_automatically_allows_y(self):
        with patch.object(m, 'native_ui_reason', return_value='Menu cursor visible'):
            self.assertTrue(self.guard.check(10, 20))
        self.assertEqual(self.guard.check(10, 20), '')

    def test_camera_close_automatically_allows_y(self):
        self.guard.camera_update(1)
        self.assertTrue(self.guard.check(10, 20))
        self.guard.camera_update(0)
        self.assertEqual(self.guard.check(10, 20), '')

    def test_visible_ui_blocks_y(self):
        with patch.object(m, 'native_ui_reason', return_value='Visible cursor'):
            self.assertTrue(self.guard.check(10, 20))

    def test_new_session_does_not_keep_old_camera_lock(self):
        self.guard.camera_update(1)
        self.guard.reset_session()
        self.assertIsNone(self.guard.camera_mode)
        self.assertEqual(self.guard.check(10, 20), '')

    def test_revision_invalidates_queued_request(self):
        old = self.guard.ticket()
        self.guard.reset_session()
        self.assertNotEqual(old, self.guard.ticket())
        old = self.guard.ticket()
        self.guard.camera_update(1)
        self.assertNotEqual(old, self.guard.ticket())


class HookTests(unittest.TestCase):
    def setUp(self):
        self.bridge = m.Bridge()
        self.events = []
        self.bridge.wake.connect(self.events.append)
        self.guard = m.MenuGuard()
        self.hook = m.YHook(self.bridge, self.guard)
        self.hwnd = 0x123456789
        self.hook.targets = {self.hwnd: 5}
        self.patches = [patch.object(m, 'foreground', return_value=self.hwnd),
                        patch.object(m, 'window_ids', return_value=(3, 5)),
                        patch.object(m, 'class_name', return_value=m.VRCHAT_CLASS),
                        patch.object(m, 'native_ui_reason', return_value=''),
                        patch.object(m, 'GetAsyncKeyState', return_value=0),
                        patch.object(m, 'CallNextHookEx', return_value=77)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def key(self, message, vk=m.VK_Y, flags=0):
        key = m.KBDLLHOOKSTRUCT(vk, 0, flags, 0, 0)
        return self.hook._callback(0, message, ctypes.addressof(key))

    def test_y_directly_captures_full_press_once(self):
        self.assertEqual(self.key(m.WM_KEYDOWN), 1)
        self.assertEqual(self.key(m.WM_KEYDOWN), 1)
        self.assertEqual(self.key(m.WM_KEYUP), 1)
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.events[0][:2], (self.hwnd, 5))

    def test_menu_keys_do_not_leave_capture_latched_off(self):
        for key in (0x1B, 0x52, 0x09):
            self.assertEqual(self.key(m.WM_KEYDOWN, key), 77)
            self.assertEqual(self.key(m.WM_KEYDOWN), 1)
            self.assertEqual(self.key(m.WM_KEYUP), 1)
            self.hook.pending.clear()

    def test_y_passes_in_menu_then_captures_after_menu_closes(self):
        with patch.object(m, 'native_ui_reason', return_value='Menu active'):
            self.assertEqual(self.key(m.WM_KEYDOWN), 77)
            self.assertEqual(self.key(m.WM_KEYUP), 77)
        self.assertEqual(self.key(m.WM_KEYDOWN), 1)

    def test_y_passes_in_camera_then_captures_after_camera_closes(self):
        self.guard.camera_update(1)
        self.assertEqual(self.key(m.WM_KEYDOWN), 77)
        self.guard.camera_update(0)
        self.assertEqual(self.key(m.WM_KEYDOWN), 1)

    def test_other_process_injection_and_modifier_y_pass(self):
        with patch.object(m, 'window_ids', return_value=(3, 6)):
            self.assertEqual(self.key(m.WM_KEYDOWN), 77)
        self.assertEqual(self.key(m.WM_KEYDOWN, flags=m.LLKHF_INJECTED), 77)
        with patch.object(m, 'GetAsyncKeyState', side_effect=lambda vk: 0x8000 if vk == 0x11 else 0):
            self.assertEqual(self.key(m.WM_KEYDOWN), 77)

    def test_native_install_shutdown_empty_targets(self):
        hook = m.YHook(self.bridge, m.MenuGuard())
        try:
            hook.start()
            self.assertTrue(hook.handle)
        finally:
            hook.stop()
        self.assertFalse(hook.thread.is_alive())
        self.assertIsNone(hook.handle)

    def test_native_foreground_hook_install_shutdown(self):
        monitor = m.ForegroundMonitor()
        try:
            monitor.start()
            self.assertTrue(monitor.handle)
        finally:
            monitor.stop()
        self.assertIsNone(monitor.handle)


class NetworkTests(unittest.TestCase):
    def test_actual_camera_osc_and_invalid_packets(self):
        temp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        temp.bind(('127.0.0.1', 0))
        port = temp.getsockname()[1]
        temp.close()
        guard = m.MenuGuard()
        monitor = m.CameraMonitor(guard, port)
        sender = m.OscSender('127.0.0.1', port, '/chatbox/input')
        try:
            self.assertTrue(monitor.enabled)
            sender.send('/usercamera/Mode', [1])
            QTest.qWait(30)
            self.assertEqual(guard.camera_mode, 1)
            sender.send('/usercamera/Mode', ['0'])
            sender.socket.sendto(b'garbage', sender.target)
            QTest.qWait(30)
            self.assertEqual(guard.camera_mode, 1)
            sender.send('/usercamera/Mode', [0])
            QTest.qWait(30)
            self.assertEqual(guard.camera_mode, 0)
        finally:
            sender.close()
            monitor.socket.close()
            monitor.deleteLater()

    def test_actual_plain_preview_and_final_order(self):
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        receiver.bind(('127.0.0.1', 0))
        receiver.settimeout(.05)
        sender = m.OscSender('127.0.0.1', receiver.getsockname()[1], '/chatbox/input')
        bar = m.InputBar(sender, 99, m.IDLE_MS)
        try:
            with patch.object(m, 'imm_composing', return_value=False):
                bar.edit.setText('hello中文')
                QTest.qWait(450)
                bar.finish(True, 'send')
                bar._idle()
                bar.close()
            packets = []
            while True:
                try:
                    msg = OscMessage(receiver.recv(4096))
                except socket.timeout:
                    break
                packets.append((msg.address, msg.params))
            self.assertEqual(packets, [('/chatbox/input', ['hello中文', True, False]),
                                      ('/chatbox/typing', [True]),
                                      ('/chatbox/input', ['hello中文', True, True]),
                                      ('/chatbox/typing', [False])])
        finally:
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            sender.close()
            receiver.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
