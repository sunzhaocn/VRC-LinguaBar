"""Native Windows window, focus, input-method and hook bindings."""
from __future__ import annotations

import ctypes
import logging
import ntpath
import sys
from ctypes import wintypes as W

if sys.platform != "win32":
    raise RuntimeError("VRC 语伴需要 Windows 10 或 Windows 11。")

LOG = logging.getLogger("vrchat-ime")
WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105
WM_QUIT = 0x0012
VK_Y = 0x59
LLKHF_INJECTED = 0x10
GCS_COMPSTR, NI_COMPOSITIONSTR, CPS_CANCEL = 0x0008, 0x0015, 0x0004

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
imm32 = ctypes.WinDLL("imm32", use_last_error=True)
LRESULT = ctypes.c_ssize_t
ULONG_PTR = ctypes.c_size_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, W.WPARAM, W.LPARAM)
WINEVENTPROC = ctypes.WINFUNCTYPE(None, W.HANDLE, W.DWORD, W.HWND,
                                 W.LONG, W.LONG, W.DWORD, W.DWORD)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", W.DWORD), ("scanCode", W.DWORD),
                ("flags", W.DWORD), ("time", W.DWORD), ("dwExtraInfo", ULONG_PTR)]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", W.DWORD), ("flags", W.DWORD),
                ("hwndActive", W.HWND), ("hwndFocus", W.HWND),
                ("hwndCapture", W.HWND), ("hwndMenuOwner", W.HWND),
                ("hwndMoveSize", W.HWND), ("hwndCaret", W.HWND),
                ("rcCaret", W.RECT)]


def bind(dll, name, result, *args):
    fn = getattr(dll, name)
    fn.restype, fn.argtypes = result, list(args)
    return fn


GetForegroundWindow = bind(user32, "GetForegroundWindow", W.HWND)
GetGUIThreadInfo = bind(user32, "GetGUIThreadInfo", W.BOOL, W.DWORD, ctypes.POINTER(GUITHREADINFO))
GetClipCursor = bind(user32, "GetClipCursor", W.BOOL, ctypes.POINTER(W.RECT))
GetAsyncKeyState = bind(user32, "GetAsyncKeyState", W.SHORT, ctypes.c_int)
IsIconic = bind(user32, "IsIconic", W.BOOL, W.HWND)
GetClassName = bind(user32, "GetClassNameW", ctypes.c_int, W.HWND, W.LPWSTR, ctypes.c_int)
GetWindowThreadProcessId = bind(user32, "GetWindowThreadProcessId", W.DWORD, W.HWND, ctypes.POINTER(W.DWORD))
IsWindow = bind(user32, "IsWindow", W.BOOL, W.HWND)
GetAncestor = bind(user32, "GetAncestor", W.HWND, W.HWND, W.UINT)
SetWinEventHook = bind(user32, "SetWinEventHook", W.HANDLE, W.DWORD, W.DWORD,
                       W.HMODULE, WINEVENTPROC, W.DWORD, W.DWORD, W.DWORD)
UnhookWinEvent = bind(user32, "UnhookWinEvent", W.BOOL, W.HANDLE)
SetForegroundWindow = bind(user32, "SetForegroundWindow", W.BOOL, W.HWND)
SetFocus = bind(user32, "SetFocus", W.HWND, W.HWND)
GetFocus = bind(user32, "GetFocus", W.HWND)
AttachThreadInput = bind(user32, "AttachThreadInput", W.BOOL, W.DWORD, W.DWORD, W.BOOL)
SetWindowsHookEx = bind(user32, "SetWindowsHookExW", W.HHOOK, ctypes.c_int, HOOKPROC, W.HINSTANCE, W.DWORD)
UnhookWindowsHookEx = bind(user32, "UnhookWindowsHookEx", W.BOOL, W.HHOOK)
CallNextHookEx = bind(user32, "CallNextHookEx", LRESULT, W.HHOOK, ctypes.c_int, W.WPARAM, W.LPARAM)
PeekMessage = bind(user32, "PeekMessageW", W.BOOL, ctypes.POINTER(W.MSG), W.HWND, W.UINT, W.UINT, W.UINT)
GetMessage = bind(user32, "GetMessageW", ctypes.c_int, ctypes.POINTER(W.MSG), W.HWND, W.UINT, W.UINT)
TranslateMessage = bind(user32, "TranslateMessage", W.BOOL, ctypes.POINTER(W.MSG))
DispatchMessage = bind(user32, "DispatchMessageW", LRESULT, ctypes.POINTER(W.MSG))
PostThreadMessage = bind(user32, "PostThreadMessageW", W.BOOL, W.DWORD, W.UINT, W.WPARAM, W.LPARAM)
GetCurrentThreadId = bind(kernel32, "GetCurrentThreadId", W.DWORD)
CreateMutex = bind(kernel32, "CreateMutexW", W.HANDLE, W.LPVOID, W.BOOL, W.LPCWSTR)
GetModuleHandle = bind(kernel32, "GetModuleHandleW", W.HMODULE, W.LPCWSTR)
OpenProcess = bind(kernel32, "OpenProcess", W.HANDLE, W.DWORD, W.BOOL, W.DWORD)
CloseHandle = bind(kernel32, "CloseHandle", W.BOOL, W.HANDLE)
QueryFullProcessImageName = bind(kernel32, "QueryFullProcessImageNameW", W.BOOL, W.HANDLE, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD))
ImmGetContext = bind(imm32, "ImmGetContext", W.HANDLE, W.HWND)
ImmReleaseContext = bind(imm32, "ImmReleaseContext", W.BOOL, W.HWND, W.HANDLE)
ImmGetCompositionString = bind(imm32, "ImmGetCompositionStringW", W.LONG, W.HANDLE, W.DWORD, W.LPVOID, W.DWORD)
ImmNotifyIME = bind(imm32, "ImmNotifyIME", W.BOOL, W.HANDLE, W.DWORD, W.DWORD, W.DWORD)


def foreground() -> int:
    return int(GetForegroundWindow() or 0)


def game_foreground() -> int:
    """Normalize a focused Unity child window to its top-level game window."""
    hwnd = foreground()
    return int(GetAncestor(hwnd, 2) or hwnd) if hwnd else 0  # GA_ROOT


def window_ids(hwnd: int) -> tuple[int, int]:
    pid = W.DWORD()
    tid = GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) if hwnd else 0
    return int(tid), int(pid.value)


def class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    return buf.value if hwnd and GetClassName(hwnd, buf, len(buf)) else ""


def process_name(pid: int) -> str:
    """Never called from the low-level keyboard callback; no title heuristics."""
    handle = OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = W.DWORD(len(buf))
        if QueryFullProcessImageName(handle, 0, buf, ctypes.byref(size)):
            return ntpath.basename(buf.value).casefold()
        return ""
    finally:
        CloseHandle(handle)


def imm_composing(hwnd: int) -> bool:
    context = ImmGetContext(hwnd) if hwnd else None
    if not context:
        return False
    try:
        return ImmGetCompositionString(context, GCS_COMPSTR, None, 0) > 0
    finally:
        ImmReleaseContext(hwnd, context)


def cancel_imm(hwnd: int) -> None:
    context = ImmGetContext(hwnd) if hwnd else None
    if context:
        try:
            # Do not CPS_COMPLETE: cancellation must not commit a draft.
            ImmNotifyIME(context, NI_COMPOSITIONSTR, CPS_CANCEL, 0)
        finally:
            ImmReleaseContext(hwnd, context)


def acquire_focus(bar: int, edit: int, source: int) -> bool:
    """Bounded activation; any successful input-thread attachment is detached."""
    def focus_edit() -> bool:
        SetFocus(edit)
        focused = int(GetFocus() or 0)
        if focused != edit:
            LOG.warning("Input window active but editor not focused: bar=%s focus=%s edit=%s",
                        bar, focused, edit)
        return focused == edit

    current = foreground()
    game = game_foreground()
    if current != bar and game != source:
        LOG.warning("Focus request expired: current=%s game=%s source=%s", current, game, source)
        return False  # The user switched away while the queued wakeup waited.
    if current != bar:
        SetForegroundWindow(bar)
    if foreground() == bar:
        return focus_edit()
    if game_foreground() != source:
        if foreground() == bar:  # The activation completed between reads.
            return focus_edit()
        LOG.warning("Focus request lost game foreground before attach")
        return False
    src_tid = int(GetCurrentThreadId())
    dst_tid, _ = window_ids(source)
    attached = False
    try:
        if dst_tid and dst_tid != src_tid:
            attached = bool(AttachThreadInput(src_tid, dst_tid, True))
        # Never change SPI_SETFOREGROUNDLOCKTIMEOUT or grant ASFW_ANY.
        if foreground() == bar:
            return focus_edit()  # The first activation finished during attach.
        if game_foreground() != source:
            if foreground() == bar:
                return focus_edit()
            LOG.warning("Focus request lost game foreground during attach")
            return False
        SetForegroundWindow(bar)
        if foreground() == bar:
            return focus_edit()
        LOG.warning("Input window could not activate: bar=%s game=%s attached=%s error=%s",
                    bar, game_foreground(), attached, ctypes.get_last_error())
        return False
    finally:
        if attached and not AttachThreadInput(src_tid, dst_tid, False):
            LOG.error("AttachThreadInput detach failed: %s", ctypes.get_last_error())
