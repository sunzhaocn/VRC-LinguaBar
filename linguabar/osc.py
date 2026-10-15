"""OSC chat messages and local camera-state observation."""
from __future__ import annotations

import logging
import socket
import time
from typing import TYPE_CHECKING

from pythonosc.osc_message import ParseError
from pythonosc.osc_message_builder import BuildError, OscMessageBuilder
from PyQt5.QtCore import QObject, QTimer
from PyQt5.QtNetwork import QHostAddress, QUdpSocket

from .text import clip_text

if TYPE_CHECKING:
    from .guards import MenuGuard

LOG = logging.getLogger("vrchat-ime")


def _camera_modes(data: bytes) -> list[int]:
    """Read present camera observations, with bounded bundle traversal.

    This listener only consumes the camera's one-int32 message. Matching its
    complete wire shape also rejects unsupported type tags that python-osc
    otherwise skips. Future bundles are not current observations, so discard
    them rather than blocking the GUI or retaining state across game sessions.
    """
    camera_header = b"/usercamera/Mode\0\0\0\0,i\0\0"
    pending = [(data, 0)]
    modes = []
    elements = 0
    now_ntp = time.time() + 2208988800
    while pending:
        packet, depth = pending.pop()
        elements += 1
        if elements > 128 or depth > 16 or len(packet) % 4:
            return []
        if packet.startswith(b"#bundle\0"):
            if len(packet) < 16:
                return []
            timestamp = int.from_bytes(packet[8:16], "big")
            if timestamp != 1 and timestamp / (1 << 32) > now_ntp:
                continue
            children = []
            index = 16
            while index < len(packet):
                if index + 4 > len(packet):
                    return []
                size = int.from_bytes(packet[index:index + 4], "big", signed=True)
                index += 4
                if size <= 0 or size % 4 or index + size > len(packet):
                    return []
                children.append((packet[index:index + size], depth + 1))
                if elements + len(pending) + len(children) > 128:
                    return []
                index += size
            pending.extend(reversed(children))
        elif (len(packet) == len(camera_header) + 4
              and packet.startswith(camera_header)):
            mode = int.from_bytes(packet[-4:], "big", signed=True)
            if 0 <= mode <= 6:
                modes.append(mode)
    return modes


class OscSender:
    """GUI-thread only; one nonblocking socket, no deferred send queue."""

    def __init__(self, host: str, port: int, address: str):
        family, kind, proto, _, target = socket.getaddrinfo(
            host, port, type=socket.SOCK_DGRAM)[0]
        self.socket = socket.socket(family, kind, proto)
        self.socket.setblocking(False)
        self.target, self.address = target, address
        self.failures = 0
        self.last_error = ""
        self._typing = None

    def send(self, address: str, args: list) -> bool:
        try:
            builder = OscMessageBuilder(address=address)
            for value in args:
                builder.add_arg(value)
            self.socket.sendto(builder.build().dgram, self.target)
            return True
        except (OSError, ValueError, UnicodeError, BuildError, ParseError) as exc:
            # No synchronous console I/O in the per-keystroke path.
            self.failures += 1
            self.last_error = str(exc)
            return False

    def text(self, text: str, notify: bool = False) -> bool:
        return self.send(self.address, [clip_text(text), True, bool(notify)])

    def typing(self, active: bool, force: bool = False) -> None:
        if force or active != self._typing:
            if self.send("/chatbox/typing", [bool(active)]):
                self._typing = active

    def close(self) -> None:
        self.socket.close()


class CameraMonitor(QObject):
    """Read-only OSC observation; never sends commands to the camera.

    VRChat normally outputs UDP to 9001. Another OSC app may already own it.
    No shared binding: shared UDP ports can silently lose/deliver packets to
    the wrong listener. In that case configure forwarding/a dedicated port.
    """
    def __init__(self, guard: MenuGuard, port: int, parent=None):
        super().__init__(parent)
        self.guard = guard
        self.socket = QUdpSocket(self)
        self.warning = ""
        self.enabled = False
        if not port or not self.socket.bind(QHostAddress.LocalHost, port, QUdpSocket.DontShareAddress):
            self.warning = "Camera OSC unavailable; camera state is unknown"
            LOG.warning("%s; port=%s", self.warning, port)
        else:
            self.enabled = True
            self.socket.readyRead.connect(self.drain)

    def drain(self):
        if not self.enabled:
            return
        # Bound work per event-loop turn; do not starve keyboard/UI events.
        for _ in range(128):
            if not self.socket.hasPendingDatagrams():
                return
            data, source, _ = self.socket.readDatagram(65536)
            if not source.isLoopback():
                continue
            for mode in _camera_modes(bytes(data)):
                self.guard.camera_update(mode)
        if self.socket.hasPendingDatagrams():
            QTimer.singleShot(0, self.drain)

    def close(self) -> None:
        self.enabled = False
        self.socket.close()
