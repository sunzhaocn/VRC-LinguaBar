import os
from pathlib import Path
import runpy
import sys
import unittest
from unittest.mock import MagicMock, patch

from linguabar import cli, config, startup
from linguabar.config import AppConfig, parse_args, startup_arguments


class FrozenStartupTests(unittest.TestCase):
    def setUp(self):
        frozen = patch.object(sys, "frozen", True, create=True)
        frozen.start()
        self.addCleanup(frozen.stop)
        self.executable = r"C:\Portable apps\VRC 语伴\VRC 语伴.exe"
        self.resource_script = r"C:\Temp\_MEI1234\vrchat_linguabar.py"
        self.settings = AppConfig(osc_port=9010, camera_listen_port=0,
                                  vrchat_pid=123, startup_mode="game", verbose=True)

    def test_commands_use_persistent_executable_without_python_or_bundled_script(self):
        with patch.object(startup, "python_without_console") as python:
            for mode in ("login", "game"):
                with self.subTest(mode=mode):
                    arguments = startup._split_command(startup.run_command(
                        mode, self.resource_script, self.executable, self.settings))
                    self.assertEqual(arguments[0], self.executable)
                    offset = 1 if mode == "login" else 2
                    if mode == "game":
                        self.assertEqual(arguments[1], "--watch-game")
                    self.assertEqual(parse_args(arguments[offset:]),
                                     parse_args(startup_arguments(self.settings)))
                    self.assertNotIn(self.resource_script, arguments)
            python.assert_not_called()

    def test_entry_path_stays_outside_pyinstaller_resource_directory(self):
        with patch.object(sys, "executable", self.executable), \
             patch.object(sys, "_MEIPASS", r"C:\Temp\_MEI1234", create=True):
            namespace = runpy.run_path(config.__file__)
        self.assertEqual(namespace["ENTRY_SCRIPT"], Path(self.executable))

    def test_current_mode_recognizes_both_modes_with_custom_settings(self):
        for mode in ("login", "game"):
            with self.subTest(mode=mode):
                command = startup.run_command(mode, self.resource_script,
                                              self.executable, self.settings)
                with patch.object(startup, "read_run_command", return_value=command):
                    self.assertEqual(startup.current_mode(self.resource_script, self.executable), mode)

    def test_current_mode_rejects_other_executable_and_source_installation(self):
        commands = [
            startup.subprocess.list2cmdline([r"C:\Another folder\VRC 语伴.exe"]),
            startup.subprocess.list2cmdline([r"C:\Python\pythonw.exe", self.resource_script]),
            "",
        ]
        for command in commands:
            with self.subTest(command=command), \
                 patch.object(startup, "read_run_command", return_value=command):
                self.assertEqual(startup.current_mode(self.resource_script, self.executable), "off")

    def test_configure_launches_independent_watcher_and_preserves_settings(self):
        with patch.object(startup, "read_run_command", return_value=""), \
             patch.object(startup.winreg, "CreateKeyEx", return_value=MagicMock()), \
             patch.object(startup.winreg, "SetValueEx") as save, \
             patch.object(startup.subprocess, "Popen") as launch, \
             patch.dict(os.environ, {"PACKAGING_TEST_VALUE": "keep", "PYINSTALLER_RESET_ENVIRONMENT": "0"}):
            startup.configure("game", self.resource_script, self.executable, self.settings)
            self.assertEqual(os.environ["PYINSTALLER_RESET_ENVIRONMENT"], "0")
        arguments = launch.call_args.args[0]
        self.assertEqual(arguments, startup._split_command(save.call_args.args[-1]))
        self.assertEqual(arguments[:2], [self.executable, "--watch-game"])
        self.assertEqual(launch.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        self.assertEqual(launch.call_args.kwargs["env"]["PACKAGING_TEST_VALUE"], "keep")
        self.assertTrue(launch.call_args.kwargs["close_fds"])
        self.assertEqual(launch.call_args.kwargs["creationflags"], startup.subprocess.CREATE_NO_WINDOW)

    def test_watcher_launches_independent_app_without_watcher_flag(self):
        kernel = MagicMock()
        kernel.CreateMutexW.return_value = 123
        expected = startup.run_command("game", self.resource_script, self.executable, self.settings)
        with patch.object(startup.ctypes, "WinDLL", return_value=kernel), \
             patch.object(startup.ctypes, "get_last_error", return_value=0), \
             patch.object(startup, "read_run_command", side_effect=[expected, ""]), \
             patch.object(startup, "process_running", return_value=True), \
             patch.object(startup.time, "sleep"), \
             patch.object(startup.subprocess, "Popen") as launch:
            startup.watch_game(self.resource_script, self.executable, self.settings)
        arguments = launch.call_args.args[0]
        self.assertEqual(arguments[0], self.executable)
        self.assertEqual(parse_args(arguments[1:]), parse_args(startup_arguments(self.settings)))
        self.assertEqual(launch.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        kernel.ReleaseMutex.assert_called_once_with(123)
        kernel.CloseHandle.assert_called_once_with(123)


class PackagedEntryTests(unittest.TestCase):
    def test_cli_dispatches_watcher_and_forwards_arguments_before_gui_parser(self):
        arguments = ["--watch-game", "--osc-port", "9010", "--camera-listen-port", "0"]
        with patch.object(startup, "main", return_value=7) as watch, \
             patch.object(cli, "parse_args") as parse:
            self.assertEqual(cli.main(arguments), 7)
        watch.assert_called_once_with(arguments)
        parse.assert_not_called()

    def test_cli_dispatches_watcher_from_process_command_line(self):
        with patch.object(sys, "argv", ["VRC 语伴.exe", "--watch-game"]), \
             patch.object(startup, "main", return_value=0) as watch:
            self.assertEqual(cli.main(), 0)
        watch.assert_called_once_with(["--watch-game"])

    def test_source_launch_does_not_reset_pyinstaller_environment(self):
        with patch.object(sys, "frozen", False, create=True):
            self.assertIsNone(startup._subprocess_environment())


if __name__ == "__main__":
    unittest.main()
