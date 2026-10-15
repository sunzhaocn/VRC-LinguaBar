import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import unittest

from linguabar.config import parse_args


class ConfigTests(unittest.TestCase):
    def test_default_settings_match_local_vrchat_osc(self):
        args = parse_args([])
        self.assertEqual((args.osc_host, args.osc_port, args.osc_address),
                         ("127.0.0.1", 9000, "/chatbox/input"))
        self.assertEqual(args.camera_listen_port, 9001)
        self.assertEqual(args.idle_ms, 2500)
        self.assertEqual(args.vrchat_pid, 0)
        self.assertIsNone(args.startup_mode)

    def test_custom_settings_and_valid_boundaries(self):
        args = parse_args(["--osc-host", "localhost", "--osc-port", "65535",
                           "--osc-address", "/custom/chat", "--idle-ms", "1",
                           "--camera-listen-port", "0", "--vrchat-pid", "42",
                           "--startup-mode", "game", "--verbose"])
        self.assertEqual((args.osc_host, args.osc_port, args.osc_address),
                         ("localhost", 65535, "/custom/chat"))
        self.assertEqual((args.idle_ms, args.camera_listen_port, args.vrchat_pid),
                         (1, 0, 42))
        self.assertEqual(args.startup_mode, "game")
        self.assertTrue(args.verbose)
        self.assertEqual(parse_args(["--idle-ms", "2147483647"]).idle_ms, 2147483647)

    def test_invalid_settings_fail_before_application_start(self):
        invalid = [
            ["--osc-port", "0"], ["--osc-port", "65536"],
            ["--camera-listen-port", "-1"], ["--camera-listen-port", "65536"],
            ["--vrchat-pid", "-1"], ["--idle-ms", "0"],
            ["--idle-ms", "2147483648"], ["--osc-address", "chatbox/input"],
            ["--osc-address", "/chat\x00box"], ["--startup-mode", "invalid"],
        ]
        for argv in invalid:
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    parse_args(argv)
                self.assertEqual(error.exception.code, 2)

    def test_script_and_package_entrypoints_show_help(self):
        root = Path(__file__).resolve().parents[1]
        for entrypoint in ([str(root / "vrchat_linguabar.py")], ["-m", "linguabar"]):
            with self.subTest(entrypoint=entrypoint):
                result = subprocess.run([sys.executable, *entrypoint, "--help"],
                                        cwd=root, env={**os.environ, "PYTHONUTF8": "1"},
                                        capture_output=True, text=True, encoding="utf-8",
                                        timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("--osc-port", result.stdout)
                self.assertIn("--startup-mode", result.stdout)


if __name__ == "__main__":
    unittest.main()
