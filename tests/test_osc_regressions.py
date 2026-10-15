import os
import socket
import struct
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication
from pythonosc.osc_bundle_builder import IMMEDIATELY, OscBundleBuilder
from pythonosc.osc_message import OscMessage
from pythonosc.osc_message_builder import OscMessageBuilder
from pythonosc.parsing.osc_types import write_string

from linguabar.guards import MenuGuard
from linguabar.osc import CameraMonitor, OscSender


def camera_message(mode):
    builder = OscMessageBuilder(address="/usercamera/Mode")
    builder.add_arg(mode)
    return builder.build()


def bundle(*contents, timestamp=IMMEDIATELY):
    builder = OscBundleBuilder(timestamp)
    for content in contents:
        builder.add_content(content)
    return builder.build()


class CameraPacketRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.guard = MenuGuard()
        self.monitor = CameraMonitor(self.guard, port)
        self.addCleanup(self.monitor.deleteLater)
        self.addCleanup(self.monitor.close)
        self.assertTrue(self.monitor.enabled, self.monitor.warning)
        self.sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(self.sender.close)
        self.target = ("127.0.0.1", port)

    def deliver(self, data):
        self.sender.sendto(data, self.target)
        self.assertTrue(self.monitor.socket.waitForReadyRead(1000))
        self.monitor.drain()

    def test_immediate_nested_bundle_preserves_observation_order(self):
        data = bundle(camera_message(2), bundle(camera_message(3)),
                      camera_message(0)).dgram
        self.deliver(data)
        self.assertEqual(self.guard.camera_mode, 0)
        self.assertEqual(self.guard.camera_revision, 3)

    def test_already_due_bundle_updates_camera(self):
        self.deliver(bundle(camera_message(6), timestamp=time.time() - 60).dgram)
        self.assertEqual(self.guard.camera_mode, 6)

    def test_future_parent_cannot_apply_nested_immediate_state_early(self):
        self.deliver(camera_message(2).dgram)
        self.deliver(bundle(bundle(camera_message(0)),
                            timestamp=time.time() + 60).dgram)
        self.assertEqual(self.guard.camera_mode, 2)
        self.deliver(camera_message(0).dgram)
        self.assertEqual(self.guard.camera_mode, 0)

    def test_future_nested_bundle_does_not_hide_current_siblings(self):
        self.deliver(bundle(camera_message(2),
                            bundle(camera_message(0), timestamp=time.time() + 60),
                            camera_message(3)).dgram)
        self.assertEqual(self.guard.camera_mode, 3)
        self.assertEqual(self.guard.camera_revision, 2)

    def test_unknown_type_cannot_be_misread_as_a_camera_integer(self):
        self.deliver(camera_message(0).dgram)
        malformed = (write_string("/usercamera/Mode") + write_string(",xi")
                     + struct.pack(">i", 3))
        self.deliver(malformed)
        self.assertEqual(self.guard.camera_mode, 0)
        self.assertEqual(self.guard.camera_revision, 1)

    def test_bad_bundle_sizes_are_discarded_without_partial_state(self):
        self.deliver(camera_message(0).dgram)
        prefix = bundle(camera_message(3)).dgram
        for size in (0, -4, 3, 10000):
            with self.subTest(size=size):
                self.deliver(prefix + struct.pack(">i", size) + b"bad!")
                self.assertEqual(self.guard.camera_mode, 0)
        self.deliver(prefix + b"\0")
        self.assertEqual(self.guard.camera_mode, 0)
        self.deliver(bundle(camera_message(2)).dgram)
        self.assertEqual(self.guard.camera_mode, 2)

    def test_excessive_nesting_and_element_count_do_not_block_followup_packets(self):
        header = bundle().dgram
        nested = camera_message(3).dgram
        for _ in range(1000):
            nested = header + struct.pack(">i", len(nested)) + nested
        many_messages = bundle(*[camera_message(3)] * 1000).dgram
        for data in (nested, many_messages):
            self.deliver(data)
            self.assertIsNone(self.guard.camera_mode)
        self.deliver(camera_message(0).dgram)
        self.assertEqual(self.guard.camera_mode, 0)

    def test_truncated_or_extra_camera_payload_does_not_change_state(self):
        self.deliver(camera_message(0).dgram)
        data = camera_message(3).dgram
        for malformed in (data[:-1], data + b"\0\0\0\0", b"#bundle\0"):
            self.deliver(malformed)
            self.assertEqual(self.guard.camera_mode, 0)


class SenderEncodingRegressionTests(unittest.TestCase):
    def test_build_error_is_recorded_and_next_valid_send_still_succeeds(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
            receiver.bind(("127.0.0.1", 0))
            receiver.settimeout(1)
            sender = OscSender("127.0.0.1", receiver.getsockname()[1], "/\ud800")
            self.addCleanup(sender.close)
            self.assertFalse(sender.text("hello"))
            self.assertEqual(sender.failures, 1)
            self.assertTrue(sender.last_error)
            sender.address = "/chatbox/input"
            self.assertTrue(sender.text("\ud800中\udfff" + "😀" * 100))
            message = OscMessage(receiver.recvfrom(65536)[0])
            self.assertEqual(message.params, ["中" + "😀" * 71, True, False])


if __name__ == "__main__":
    unittest.main()
