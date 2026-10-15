import os
import socket
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication
from pythonosc.osc_message import OscMessage
from pythonosc.osc_message_builder import OscMessageBuilder

from linguabar.guards import MenuGuard
from linguabar.osc import CameraMonitor, OscSender


def packet(address, values):
    builder = OscMessageBuilder(address=address)
    for value in values:
        builder.add_arg(value)
    return builder.build().dgram


class OscSenderTests(unittest.TestCase):
    def setUp(self):
        self.receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.receiver.bind(("127.0.0.1", 0))
        self.receiver.settimeout(1)
        self.addCleanup(self.receiver.close)
        self.sender = OscSender("127.0.0.1", self.receiver.getsockname()[1],
                                "/chatbox/input")
        self.addCleanup(self.sender.close)

    def receive(self):
        return OscMessage(self.receiver.recvfrom(65536)[0])

    def test_chat_round_trip_preserves_language_and_vrchat_flags(self):
        self.assertTrue(self.sender.text("中文\n日本語 😀", notify=True))
        message = self.receive()
        self.assertEqual(message.address, "/chatbox/input")
        self.assertEqual(message.params, ["中文 日本語 😀", True, True])
        self.assertTrue(self.sender.text(""))
        self.assertEqual(self.receive().params, ["", True, False])

    def test_typing_changes_are_sent_once_and_final_off_can_be_forced(self):
        for active, force in ((True, False), (True, False), (False, False),
                              (False, False), (False, True)):
            self.sender.typing(active, force=force)
        messages = [self.receive() for _ in range(3)]
        self.assertEqual([message.address for message in messages],
                         ["/chatbox/typing"] * 3)
        self.assertEqual([message.params for message in messages],
                         [[True], [False], [False]])
        self.receiver.settimeout(0.02)
        with self.assertRaises(socket.timeout):
            self.receive()

    def test_failed_typing_send_is_retried_after_socket_recovers(self):
        self.sender.close()
        self.sender.typing(True)
        self.assertEqual(self.sender.failures, 1)
        self.assertTrue(self.sender.last_error)
        self.sender.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sender.socket.setblocking(False)
        self.sender.typing(True)
        self.assertEqual(self.receive().params, [True])


class CameraMonitorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.guard = MenuGuard()
        # Select an unused loopback port; never bind VRChat's normal OSC port.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.monitor = CameraMonitor(self.guard, port)
        self.addCleanup(self.monitor.socket.close)
        self.addCleanup(self.monitor.deleteLater)
        self.assertTrue(self.monitor.enabled, self.monitor.warning)
        self.sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(self.sender.close)
        self.target = ("127.0.0.1", port)

    def deliver(self, data):
        self.sender.sendto(data, self.target)
        self.assertTrue(self.monitor.socket.waitForReadyRead(1000))
        self.monitor.drain()

    def test_camera_modes_received_over_udp_update_wakeup_guard(self):
        before = self.guard.ticket()
        self.deliver(packet("/usercamera/Mode", [2]))
        self.assertEqual(self.guard.camera_mode, 2)
        self.assertNotEqual(self.guard.ticket(), before)
        active_ticket = self.guard.ticket()
        self.deliver(packet("/usercamera/Mode", [2]))
        self.assertEqual(self.guard.ticket(), active_ticket)
        self.deliver(packet("/usercamera/Mode", [0]))
        self.assertEqual(self.guard.camera_mode, 0)
        self.assertNotEqual(self.guard.ticket(), active_ticket)
        self.guard.reset_session()
        self.assertIsNone(self.guard.camera_mode)

    def test_malformed_and_unrelated_messages_leave_camera_state_unchanged(self):
        self.deliver(packet("/usercamera/Mode", [3]))
        ticket = self.guard.ticket()
        invalid_packets = [
            b"not an OSC message", packet("/other", [0]),
            packet("/usercamera/Mode", []), packet("/usercamera/Mode", [0, 1]),
            packet("/usercamera/Mode", [True]), packet("/usercamera/Mode", [0.0]),
            packet("/usercamera/Mode", ["0"]), packet("/usercamera/Mode", [-1]),
            packet("/usercamera/Mode", [7]),
        ]
        for data in invalid_packets:
            with self.subTest(data=data):
                self.deliver(data)
                self.assertEqual(self.guard.camera_mode, 3)
                self.assertEqual(self.guard.ticket(), ticket)


if __name__ == "__main__":
    unittest.main()
