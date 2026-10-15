import contextlib
import io
import logging
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from linguabar import startup
from linguabar.config import AppConfig, ENTRY_SCRIPT, parse_args, startup_arguments


class StartupSettingsTests(unittest.TestCase):
    def setUp(self):
        self.settings = AppConfig(osc_host="localhost", osc_port=9010,
                                  osc_address="/custom/chat", idle_ms=700,
                                  camera_listen_port=0, vrchat_pid=123,
                                  startup_mode="game", verbose=True)

    def test_runtime_settings_round_trip_without_stale_pid_or_configuration_command(self):
        saved = startup_arguments(self.settings)
        restored = parse_args(saved)
        self.assertEqual(restored, AppConfig(osc_host="localhost", osc_port=9010,
                                             osc_address="/custom/chat", idle_ms=700,
                                             camera_listen_port=0, verbose=True))
        self.assertEqual(startup_arguments(AppConfig()), [])

    def test_run_command_preserves_settings_and_quotes_paths(self):
        script = r"C:\my apps\中文\vrchat_linguabar.py"
        executable = r"C:\Python 3\python.exe"
        for mode in ("game", "login"):
            with self.subTest(mode=mode), patch.object(startup, "python_without_console", side_effect=lambda path: path):
                arguments = startup._split_command(startup.run_command(mode, script, executable, self.settings))
                self.assertEqual(arguments[0], executable)
                self.assertEqual(arguments[1], script if mode == "login" else
                                 os.path.join(os.path.dirname(script), "startup.py"))
                offset = 2 if mode == "login" else 3
                if mode == "game":
                    self.assertEqual(arguments[2], "--watch-game")
                self.assertEqual(parse_args(arguments[offset:]), parse_args(startup_arguments(self.settings)))

    def test_current_mode_recognizes_custom_settings_for_exact_entrypoint(self):
        script = str(ENTRY_SCRIPT)
        for mode in ("game", "login"):
            with self.subTest(mode=mode), patch.object(startup, "read_run_command", return_value=
                    startup.run_command(mode, script, sys.executable, self.settings)):
                self.assertEqual(startup.current_mode(script, sys.executable), mode)

    def test_current_mode_rejects_other_script_or_interpreter(self):
        script = str(ENTRY_SCRIPT)
        commands = [
            startup.run_command("login", script + ".other", sys.executable),
            startup.run_command("game", os.path.join("C:\\another project", "vrchat_linguabar.py"), sys.executable),
            startup.run_command("login", script, r"C:\another python\python.exe"),
            startup.subprocess.list2cmdline([sys.executable]),
        ]
        for command in commands:
            with self.subTest(command=command), patch.object(startup, "read_run_command", return_value=command):
                self.assertEqual(startup.current_mode(script, sys.executable), "off")

    def test_configure_stores_and_starts_watcher_with_same_settings(self):
        key = MagicMock()
        with patch.object(startup, "read_run_command", return_value=""), \
             patch.object(startup.winreg, "CreateKeyEx", return_value=key), \
             patch.object(startup.winreg, "SetValueEx") as save, \
             patch.object(startup.subprocess, "Popen") as launch:
            startup.configure("game", str(ENTRY_SCRIPT), sys.executable, settings=self.settings)
        command = save.call_args.args[-1]
        self.assertEqual(startup._split_command(command), launch.call_args.args[0])
        self.assertIn("9010", command)
        self.assertNotIn("--vrchat-pid", command)
        self.assertNotIn("--startup-mode", command)

    def test_watcher_entrypoint_parses_and_forwards_runtime_settings(self):
        with patch.object(startup, "configure_logging") as configure_logging, \
             patch.object(startup, "watch_game") as watch:
            self.assertEqual(startup.main(["--watch-game", *startup_arguments(self.settings)]), 0)
        configure_logging.assert_called_once_with(True, watcher=True)
        watch.assert_called_once_with(str(ENTRY_SCRIPT), sys.executable,
                                      settings=parse_args(startup_arguments(self.settings)))

    def test_watcher_entrypoint_rejects_configuration_commands(self):
        with patch.object(startup, "watch_game") as watch, self.assertRaises(SystemExit):
            startup.main(["--watch-game", "--startup-mode", "login"])
        watch.assert_not_called()


class StartupLoggingTests(unittest.TestCase):
    def test_app_and_watcher_logs_can_rotate_while_both_are_open(self):
        handlers = []
        with tempfile.TemporaryDirectory() as folder:
            try:
                with patch.dict(os.environ, {"LOCALAPPDATA": folder}), \
                     patch.object(startup.logging, "basicConfig") as configure:
                    startup.configure_logging()
                    handlers.extend(configure.call_args.kwargs["handlers"])
                    app_log = handlers[-1]
                    startup.configure_logging(watcher=True)
                    watcher_handlers = configure.call_args.kwargs["handlers"]
                    handlers.extend(watcher_handlers)
                    watcher_log = watcher_handlers[-1]
                self.assertNotEqual(app_log.baseFilename, watcher_log.baseFilename)
                errors = io.StringIO()
                with contextlib.redirect_stderr(errors):
                    for handler in (app_log, watcher_log):
                        handler.maxBytes = 10
                        handler.emit(logging.LogRecord("test", logging.INFO, "", 0, "abc", (), None))
                        handler.emit(logging.LogRecord("test", logging.INFO, "", 0, "x" * 100, (), None))
                        self.assertTrue(os.path.isfile(handler.baseFilename + ".1"))
                self.assertEqual(errors.getvalue(), "")
            finally:
                for handler in handlers:
                    handler.close()


class WatcherHandoffTests(unittest.TestCase):
    def test_replacement_waits_for_previous_watcher_to_release_mutex(self):
        for wait_result in (startup.WAIT_OBJECT_0, startup.WAIT_ABANDONED):
            with self.subTest(wait_result=wait_result):
                kernel = MagicMock()
                kernel.CreateMutexW.return_value = 123
                kernel.WaitForSingleObject.return_value = wait_result
                settings = AppConfig(osc_port=9010, camera_listen_port=0)
                command = startup.run_command("game", str(ENTRY_SCRIPT), sys.executable, settings)
                with patch.object(startup.ctypes, "WinDLL", return_value=kernel), \
                     patch.object(startup.ctypes, "get_last_error", return_value=startup.ERROR_ALREADY_EXISTS), \
                     patch.object(startup, "read_run_command", side_effect=[command, ""]), \
                     patch.object(startup, "process_running", return_value=True), \
                     patch.object(startup.time, "sleep"), \
                     patch.object(startup.subprocess, "Popen") as launch:
                    startup.watch_game(str(ENTRY_SCRIPT), sys.executable, settings)
                kernel.CreateMutexW.assert_called_once_with(None, True, startup.WATCH_MUTEX)
                kernel.WaitForSingleObject.assert_called_once_with(123, 5000)
                kernel.ReleaseMutex.assert_called_once_with(123)
                kernel.CloseHandle.assert_called_once_with(123)
                launched = parse_args(launch.call_args.args[0][2:])
                self.assertEqual(launched.osc_port, 9010)
                self.assertEqual(launched.camera_listen_port, 0)

    def test_active_duplicate_waits_then_exits_without_releasing_another_owners_mutex(self):
        kernel = MagicMock()
        kernel.CreateMutexW.return_value = 123
        kernel.WaitForSingleObject.return_value = startup.WAIT_TIMEOUT
        with patch.object(startup.ctypes, "WinDLL", return_value=kernel), \
             patch.object(startup.ctypes, "get_last_error", return_value=startup.ERROR_ALREADY_EXISTS), \
             patch.object(startup, "process_running") as inspect, \
             patch.object(startup.subprocess, "Popen") as launch:
            startup.watch_game(str(ENTRY_SCRIPT), sys.executable)
        launch.assert_not_called()
        inspect.assert_not_called()
        kernel.ReleaseMutex.assert_not_called()
        kernel.CloseHandle.assert_called_once_with(123)

    def test_watcher_never_relaunches_closed_app_during_same_game_session(self):
        kernel = MagicMock()
        kernel.CreateMutexW.return_value = 123
        command = startup.run_command("game", str(ENTRY_SCRIPT), sys.executable)
        child = MagicMock()
        child.poll.return_value = 0  # An app that the user has already closed.
        with patch.object(startup.ctypes, "WinDLL", return_value=kernel), \
             patch.object(startup.ctypes, "get_last_error", return_value=0), \
             patch.object(startup, "read_run_command", side_effect=[command] * 3 + [""]), \
             patch.object(startup, "process_running", return_value=True), \
             patch.object(startup.time, "sleep"), \
             patch.object(startup.subprocess, "Popen", return_value=child) as launch:
            startup.watch_game(str(ENTRY_SCRIPT), sys.executable)
        launch.assert_called_once()
        kernel.ReleaseMutex.assert_called_once_with(123)

    def test_changed_mode_before_handoff_does_not_start_app(self):
        kernel = MagicMock()
        kernel.CreateMutexW.return_value = 123
        kernel.WaitForSingleObject.return_value = startup.WAIT_OBJECT_0
        with patch.object(startup.ctypes, "WinDLL", return_value=kernel), \
             patch.object(startup.ctypes, "get_last_error", return_value=startup.ERROR_ALREADY_EXISTS), \
             patch.object(startup, "read_run_command", return_value=""), \
             patch.object(startup.subprocess, "Popen") as launch:
            startup.watch_game(str(ENTRY_SCRIPT), sys.executable)
        launch.assert_not_called()
        kernel.ReleaseMutex.assert_called_once_with(123)
        kernel.CloseHandle.assert_called_once_with(123)


class InvalidSettingsTests(unittest.TestCase):
    def test_unusable_hosts_addresses_and_out_of_range_pids_fail_at_parse_time(self):
        cases = [("--osc-host", value) for value in ("", "bad host", "localhost\n", "a\x00b", "\ud800")]
        cases += [("--osc-address", "/\ud800"), ("--vrchat-pid", "4294967296")]
        for option, value in cases:
            with self.subTest(option=option, value=repr(value)), \
                 contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                parse_args([option, value])
            self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
