"""OSC chat messages and local camera-state observation."""
from __future__ import annotations

import logging
import socket
from typing import TYPE_CHECKING

from pythonosc.osc_message import OscMessage, ParseError
from pythonosc.osc_message_builder import OscMessageBuilder
from PyQt5.QtCore import QObject, QTimer
from PyQt5.QtNetwork import QHostAddress, QUdpSocket

from .text import clip_text

if TYPE_CHECKING:
    from .guards import MenuGuard

LOG = logging.getLogger("vrchat-ime")


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
        except (OSError, ValueError, UnicodeError) as exc:
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
            try:
                message = OscMessage(bytes(data))
                values = message.params
                if (message.address == "/usercamera/Mode" and len(values) == 1
                        and type(values[0]) is int and 0 <= values[0] <= 6):
                    self.guard.camera_update(values[0])
            except (ParseError, ValueError, TypeError, UnicodeError):
                pass
        if self.socket.hasPendingDatagrams():
            QTimer.singleShot(0, self.drain)

    def close(self) -> None:
        self.enabled = False
        self.socket.close()
