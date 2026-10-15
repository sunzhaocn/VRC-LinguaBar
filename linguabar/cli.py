"""Small CLI that can show help before loading native Windows services."""
from __future__ import annotations

import sys

from .config import parse_args


def main(argv=None) -> int:
    args = parse_args(argv)
    if sys.platform != "win32":
        print("VRC 语伴需要 Windows 10 或 Windows 11。", file=sys.stderr)
        return 1
    from .app import run

    return run(args)
