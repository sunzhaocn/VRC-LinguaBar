"""Wire together the tray, game discovery, input sessions and shutdown."""
from __future__ import annotations

import ctypes
import logging
import signal
import sys

import win32gui
from PyQt5.QtCore import QCoreApplication, QEvent, QObject, Qt, QTimer, pyqtSlot
from PyQt5.QtWidgets import QApplication, QMenu, QStyle, QSystemTrayIcon

from . import startup
from .config import APP_MUTEX, APP_NAME, ENTRY_SCRIPT, VRCHAT_CLASS, AppConfig
from .guards import MenuGuard
from .hooks import Bridge, ForegroundMonitor, YHook
from .osc import CameraMonitor, OscSender
from .ui import InputBar
from .win32 import CloseHandle, CreateMutex, class_name, game_foreground, process_name, window_ids

LOG = logging.getLogger("vrchat-ime")


class Controller(QObject):
    def __init__(self, app: QApplication, args):
        super().__init__(app)
        self.app, self.args = app, args
        self.osc = OscSender(args.osc_host, args.osc_port, args.osc_address)
        self.camera = None
        self.tray = None
        self.menu = None
        try:
            self._setup()
        except Exception:
            self.osc.close()
            if self.camera is not None:
                self.camera.close()
            if self.tray is not None:
                self.tray.hide()
            if self.menu is not None:
                self.menu.deleteLater()
            self.deleteLater()
            raise

    def _setup(self):
        app, args = self.app, self.args
        self.bridge = Bridge(self)
        self.guard = MenuGuard()
        self.camera = CameraMonitor(self.guard, args.camera_listen_port, self)
        self.foreground_monitor = ForegroundMonitor(self)
        self.foreground_monitor.changed.connect(self.on_foreground_changed, Qt.QueuedConnection)
        self.hook = YHook(self.bridge, self.guard)
        self.bar = None
        self.stopping = False
        self._failures_reported = 0
        self._hook_error_reported = ""
        self._status_reported = None
        self._target_snapshot = None
        self.bridge.wake.connect(self.show_bar, Qt.QueuedConnection)
        self.scan_timer = QTimer(self)
        self.scan_timer.setInterval(500)
        self.scan_timer.timeout.connect(self.refresh_targets)
        # Also lets CPython process Ctrl+C while Qt owns the event loop.
        self.heartbeat = QTimer(self)
        self.heartbeat.setInterval(250)
        self.heartbeat.timeout.connect(self.check_health)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(app.style().standardIcon(QStyle.SP_MessageBoxInformation), self)
            self.tray.setToolTip(APP_NAME + " · 按 Y 聊天")
            self.menu = QMenu()
            self.startup_menu = self.menu.addMenu("自动启动")
            self.startup_actions = {}
            for mode, label in (("off", "关闭"), ("game", "随 VRChat 启动"),
                                ("login", "登录 Windows 时启动")):
                action = self.startup_menu.addAction(label)
                action.setCheckable(True)
                action.triggered.connect(lambda _checked=False, selected=mode: self.set_startup_mode(selected))
                self.startup_actions[mode] = action
            self.update_startup_actions()
            self.menu.addSeparator()
            self.menu.addAction("退出", app.quit)
            self.tray.setContextMenu(self.menu)
            self.tray.show()
        app.aboutToQuit.connect(self.shutdown)

    def update_startup_actions(self):
        try:
            mode = startup.current_mode(str(ENTRY_SCRIPT), sys.executable)
        except OSError as exc:
            LOG.warning("Cannot read automatic startup setting: %s", exc)
            mode = "off"
        for key, action in self.startup_actions.items():
            action.setChecked(key == mode)

    def set_startup_mode(self, mode):
        try:
            startup.configure(mode, str(ENTRY_SCRIPT), sys.executable, settings=self.args)
        except OSError as exc:
            LOG.error("Cannot update automatic startup: %s", exc)
        self.update_startup_actions()

    def start(self):
        self.refresh_targets()
        self.foreground_monitor.start()
        self.hook.start()
        self.scan_timer.start()
        self.heartbeat.start()

    @pyqtSlot()
    def refresh_targets(self):
        if self.stopping:
            return
        targets = {}
        names = {}

        def visit(hwnd, _):
            if win32gui.IsWindowVisible(hwnd) and class_name(hwnd) == VRCHAT_CLASS:
                _, pid = window_ids(hwnd)
                if pid not in names:
                    names[pid] = process_name(pid)
                if names[pid] == "vrchat.exe" and (not self.args.vrchat_pid or pid == self.args.vrchat_pid):
                    targets[int(hwnd)] = pid
            return True

        try:
            win32gui.EnumWindows(visit, None)
        except win32gui.error as exc:
            LOG.debug("Window enumeration failed: %s", exc)
            return  # An incomplete scan cannot establish that the game exited.
        # CPython/GIL: publish a new snapshot, never mutate shared dictionaries.
        self.hook.targets = targets
        snapshot = tuple(sorted(targets.items()))
        if snapshot != self._target_snapshot:
            self._target_snapshot = snapshot
            self.guard.reset_session()  # Discard camera state for the previous game.
        if self.bar is not None and targets.get(self.bar.target) != self.bar.target_pid:
            self.bar.finish(False, "game-exit")

    @pyqtSlot(object)
    def on_foreground_changed(self, hwnd):
        # Discover a game that appeared after this program started without
        # waiting for the next periodic scan or a missed first Y press.
        if not self.stopping and hwnd and class_name(game_foreground()) == VRCHAT_CLASS:
            self.refresh_targets()
        if not self.stopping and self.bar is not None:
            # Re-read the CURRENT foreground when the queued check executes;
            # an old notification must not close a newly activated bar.
            self.bar.schedule_focus_check()

    @pyqtSlot(object)
    def show_bar(self, target):
        try:
            if self.stopping:
                return
            hwnd, pid, ticket = target
            self.camera.drain()
            if (game_foreground() != hwnd or window_ids(hwnd)[1] != pid
                    or class_name(hwnd) != VRCHAT_CLASS
                    or process_name(pid) != "vrchat.exe"
                    or self.hook.targets.get(hwnd) != pid
                    or ticket != self.guard.ticket()
                    or self.guard.check(hwnd, pid)):
                return
            if self.bar is not None:
                if self.bar.target == hwnd and self.bar.target_pid == pid:
                    self.bar.refocus()
                return
            self.bar = InputBar(self.osc, hwnd, self.args.idle_ms)
            self.bar.finished.connect(self.bar_finished)
            self.bar.open_bar()
        except Exception:
            LOG.exception("Cannot open input bar")
            if self.bar is not None:
                self.bar.finish(False, "open-error")
        finally:
            self.hook.pending.clear()

    def bar_finished(self):
        # The old QWidget is already hidden and scheduled for deletion.
        # Release it now so a new Y can open a fresh bar immediately.
        self.bar = None

    @pyqtSlot()
    def check_health(self):
        # This timer checks service health only. No foreground polling/cleanup.
        status = (f"Camera active: {self.guard.camera_mode}" if self.guard.camera_mode not in (None, 0)
                  else self.guard.reason)
        snapshot = (status, bool(self.hook.targets))
        if snapshot != self._status_reported:
            self._status_reported = snapshot
            LOG.info("%s", status)
            if self.tray:
                if self.guard.camera_mode not in (None, 0):
                    label = "相机使用中"
                elif not self.hook.targets:
                    label = "等待 VRChat"
                else:
                    label = "按 Y 聊天"
                self.tray.setToolTip(APP_NAME + " · " + label)
        if self.hook.error and self.hook.error != self._hook_error_reported:
            self._hook_error_reported = self.hook.error
            LOG.error("%s", self.hook.error)
        if self.hook.thread and not self.hook.thread.is_alive() and not self.stopping:
            LOG.error("Keyboard hook thread exited; stopping application")
            self.app.exit(1)
        if self.osc.failures != self._failures_reported:
            self._failures_reported = self.osc.failures
            LOG.warning("OSC send failures=%d; latest: %s", self.osc.failures, self.osc.last_error)

    @pyqtSlot()
    def shutdown(self):
        if self.stopping:
            return
        self.stopping = True
        self.scan_timer.stop()
        self.heartbeat.stop()
        self.foreground_monitor.stop()
        self.camera.close()
        self.hook.stop()
        try:
            if self.bar is not None:
                self.bar.finish(False, "app-exit")
            else:
                # Do not clear a previously submitted final message on exit.
                self.osc.typing(False, force=True)
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        finally:
            self.osc.close()
            if self.tray:
                self.tray.hide()
                self.menu.deleteLater()


def run(args: AppConfig) -> int:
    startup.configure_logging(args.verbose)
    if args.startup_mode:
        try:
            startup.configure(args.startup_mode, str(ENTRY_SCRIPT), sys.executable, settings=args)
            LOG.info("Automatic startup set to %s", args.startup_mode)
            return 0
        except OSError:
            LOG.exception("Cannot update automatic startup")
            return 1
    # Take the lock before creating any Qt windows, tray icons or keyboard hooks.
    mutex = CreateMutex(None, False, APP_MUTEX)
    if not mutex:
        LOG.error("Cannot create single-instance mutex: %s", ctypes.get_last_error())
        return 1
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        CloseHandle(mutex)
        LOG.info("VRC LinguaBar is already running; duplicate launch ignored")
        return 0
    controller = None
    old_sigint = None
    try:
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
        app = QApplication([sys.argv[0]])
        app.setApplicationName(APP_NAME)
        app.setQuitOnLastWindowClosed(False)
        old_sigint = signal.signal(signal.SIGINT, lambda *_: app.quit())
        controller = Controller(app, args)
        controller.start()
        LOG.info("Ready: Y chats directly; Enter sends; Esc/focus loss closes; Ctrl+C/tray exits")
        return int(app.exec_())
    except Exception:
        LOG.exception("Startup/runtime failure")
        return 1
    finally:
        if controller is not None:
            controller.shutdown()
        if old_sigint is not None:
            signal.signal(signal.SIGINT, old_sigint)
        CloseHandle(mutex)
