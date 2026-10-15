# -*- coding: utf-8 -*-
"""Per-user startup settings and the small VRChat launch watcher (Windows)."""
from __future__ import annotations

import ctypes
import logging
from logging.handlers import RotatingFileHandler
import os
import subprocess
import sys
import time
import winreg
from ctypes import wintypes as W

from .config import ENTRY_SCRIPT


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "VRC LinguaBar"
WATCH_MUTEX = "Local\\VRCLinguaBarWatcher"
TH32CS_SNAPPROCESS = 0x00000002
ERROR_ALREADY_EXISTS = 183
LOG = logging.getLogger("vrchat-ime.startup")


def configure_logging(verbose: bool = False) -> None:
    handlers = []
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    try:
        folder = os.path.join(os.environ["LOCALAPPDATA"], "VRC LinguaBar")
        os.makedirs(folder, exist_ok=True)
        handlers.append(RotatingFileHandler(os.path.join(folder, "linguabar.log"),
                                            maxBytes=1_000_000, backupCount=2,
                                            encoding="utf-8"))
    except (KeyError, OSError) as exc:
        if sys.stderr is not None:
            print(f"Cannot open VRC LinguaBar log: {exc}", file=sys.stderr)
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", W.DWORD), ("cntUsage", W.DWORD),
                ("th32ProcessID", W.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", W.DWORD), ("cntThreads", W.DWORD),
                ("th32ParentProcessID", W.DWORD), ("pcPriClassBase", W.LONG),
                ("dwFlags", W.DWORD), ("szExeFile", W.WCHAR * 260)]


def python_without_console(executable: str) -> str:
    candidate = os.path.join(os.path.dirname(executable), "pythonw.exe")
    return candidate if os.path.isfile(candidate) else executable


def run_command(mode: str, script: str, executable: str) -> str:
    python = python_without_console(executable)
    path = os.path.abspath(script)
    if mode == "login":
        args = [python, path]
    elif mode == "game":
        args = [python, os.path.join(os.path.dirname(path), "startup.py"), "--watch-game"]
    else:
        raise ValueError(f"Invalid startup mode: {mode}")
    return subprocess.list2cmdline(args)


def read_run_command() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, RUN_NAME)
            return value if isinstance(value, str) else ""
    except FileNotFoundError:
        return ""


def current_mode(script: str, executable: str) -> str:
    command = read_run_command()
    for mode in ("game", "login"):
        if command == run_command(mode, script, executable):
            return mode
    return "off"


def configure(mode: str, script: str, executable: str) -> None:
    if mode not in ("off", "game", "login"):
        raise ValueError(f"Invalid startup mode: {mode}")
    previous = read_run_command()
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if mode == "off":
            try:
                winreg.DeleteValue(key, RUN_NAME)
            except FileNotFoundError:
                pass
        else:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ,
                              run_command(mode, script, executable))
    if mode == "game":
        # The current app is already running. Start the watcher now so the
        # setting also works if the user closes this app before opening VRChat.
        try:
            subprocess.Popen([python_without_console(executable),
                              os.path.join(os.path.dirname(os.path.abspath(script)), "startup.py"),
                              "--watch-game"], creationflags=subprocess.CREATE_NO_WINDOW,
                             close_fds=True)
        except OSError:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                    winreg.KEY_SET_VALUE) as key:
                if previous:
                    winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, previous)
                else:
                    winreg.DeleteValue(key, RUN_NAME)
            raise


def process_running(name: str) -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    snapshot = kernel32.CreateToolhelp32Snapshot
    snapshot.argtypes = [W.DWORD, W.DWORD]
    snapshot.restype = W.HANDLE
    first = kernel32.Process32FirstW
    first.argtypes = [W.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    first.restype = W.BOOL
    next_process = kernel32.Process32NextW
    next_process.argtypes = [W.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    next_process.restype = W.BOOL
    close = kernel32.CloseHandle
    close.argtypes = [W.HANDLE]
    close.restype = W.BOOL
    handle = snapshot(TH32CS_SNAPPROCESS, 0)
    if not handle or handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        if not first(handle, ctypes.byref(entry)):
            raise ctypes.WinError(ctypes.get_last_error())
        while True:
            if entry.szExeFile.casefold() == name.casefold():
                return True
            if not next_process(handle, ctypes.byref(entry)):
                error = ctypes.get_last_error()
                if error not in (0, 18):  # ERROR_NO_MORE_FILES
                    raise ctypes.WinError(error)
                return False
    finally:
        close(handle)


def watch_game(script: str, executable: str) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = [W.LPVOID, W.BOOL, W.LPCWSTR]
    create_mutex.restype = W.HANDLE
    close = kernel32.CloseHandle
    close.argtypes = [W.HANDLE]
    close.restype = W.BOOL
    mutex = create_mutex(None, False, WATCH_MUTEX)
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        close(mutex)
        return
    expected = run_command("game", script, executable)
    launched_for_this_game = False
    try:
        LOG.info("VRChat startup watcher ready")
        while read_run_command() == expected:
            try:
                running = process_running("VRChat.exe")
            except OSError as exc:
                LOG.warning("Cannot inspect game processes: %s", exc)
                time.sleep(3)
                continue
            if running and not launched_for_this_game:
                LOG.info("VRChat detected; starting VRC LinguaBar")
                try:
                    subprocess.Popen([python_without_console(executable), script],
                                     creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)
                except OSError as exc:
                    LOG.warning("Cannot launch input bar; will retry: %s", exc)
                else:
                    launched_for_this_game = True
            elif not running:
                launched_for_this_game = False
            time.sleep(3)
    finally:
        LOG.info("VRChat startup watcher stopped")
        close(mutex)


def main(argv=None) -> int:
    if (sys.argv[1:] if argv is None else argv) != ["--watch-game"]:
        raise SystemExit("Usage: startup.py --watch-game")
    configure_logging()
    try:
        watch_game(str(ENTRY_SCRIPT), sys.executable)
        return 0
    except Exception:
        LOG.exception("VRChat startup watcher failed")
        return 1
