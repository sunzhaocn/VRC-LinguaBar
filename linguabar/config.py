"""Application settings, paths and command-line options."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "VRC 语伴"
MAX_CHARS = 144
IDLE_MS = 2500
VRCHAT_CLASS = "UnityWndClass"
APP_MUTEX = "Local\\VRCLinguaBarApp"
ENTRY_SCRIPT = Path(__file__).resolve().parent.parent / "vrchat_linguabar.py"


@dataclass(frozen=True)
class AppConfig:
    osc_host: str = "127.0.0.1"
    osc_port: int = 9000
    osc_address: str = "/chatbox/input"
    idle_ms: int = IDLE_MS
    camera_listen_port: int = 9001
    vrchat_pid: int = 0
    startup_mode: str | None = None
    verbose: bool = False


def startup_arguments(settings: AppConfig | None = None) -> list[str]:
    """Keep persistent runtime choices without saving one-shot commands or PIDs."""
    if settings is None:
        return []
    defaults = AppConfig()
    arguments = []
    for name in ("osc_host", "osc_port", "osc_address", "idle_ms", "camera_listen_port"):
        value = getattr(settings, name)
        if value != getattr(defaults, name):
            arguments.extend(("--" + name.replace("_", "-"), str(value)))
    if settings.verbose:
        arguments.append("--verbose")
    return arguments


def parse_args(argv=None) -> AppConfig:
    defaults = AppConfig()
    parser = argparse.ArgumentParser(
        description="VRC 语伴：按 Y 打开系统输入法，向 VRChat 聊天气泡发送文字。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--osc-host", default=defaults.osc_host, help="VRChat 的 OSC 接收地址")
    parser.add_argument("--osc-port", type=int, default=defaults.osc_port, help="发送聊天文字的 UDP 端口")
    parser.add_argument("--osc-address", default=defaults.osc_address, help="聊天文字的 OSC 消息地址")
    parser.add_argument("--idle-ms", type=int, default=defaults.idle_ms,
                        help="停止输入多少毫秒后关闭打字提示")
    parser.add_argument("--camera-listen-port", type=int, default=defaults.camera_listen_port,
                        help="接收本机相机状态的 UDP 端口；0 表示关闭监听")
    parser.add_argument("--vrchat-pid", type=int, default=defaults.vrchat_pid,
                        help="只接管指定的 VRChat 进程；0 表示自动识别")
    parser.add_argument("--startup-mode", choices=("off", "game", "login"),
                        help="设置自动启动后退出：off 关闭，game 随游戏启动，login 登录时启动")
    parser.add_argument("--verbose", action="store_true", help="记录详细日志")
    args = parser.parse_args(argv)
    if not 1 <= args.osc_port <= 65535:
        parser.error("--osc-port must be between 1 and 65535")
    if not 0 <= args.camera_listen_port <= 65535 or not 0 <= args.vrchat_pid <= 0xFFFFFFFF:
        parser.error("Invalid camera port or VRChat PID")
    if not args.osc_host or "\x00" in args.osc_host or any(char.isspace() for char in args.osc_host):
        parser.error("--osc-host must contain a hostname or IP address without whitespace or NUL")
    if not args.osc_address.startswith("/") or "\x00" in args.osc_address:
        parser.error("--osc-address must start with / and contain no NUL")
    try:
        args.osc_address.encode("utf-8")
        args.osc_host.encode("utf-8")
    except UnicodeEncodeError:
        parser.error("OSC host and address must contain valid Unicode text")
    if not 1 <= args.idle_ms <= 2147483647:
        parser.error("--idle-ms must be between 1 and 2147483647")
    return AppConfig(**vars(args))
