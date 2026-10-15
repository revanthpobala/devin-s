"""Windows: every child process this repo starts gets no console window, unless the caller chose its own flags.

Imported from src/__init__.py, so UI, backend, notifier, tracker and research scripts all inherit it.
Set PINE_SPAWN_LOG=1 to append every spawn to logs/spawn.log.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

CREATE_NEW_CONSOLE = 0x00000010
DETACHED_PROCESS = 0x00000008
CREATE_NO_WINDOW = 0x08000000


def _log(args) -> None:
    try:
        cmd = args if isinstance(args, str) else " ".join(str(a) for a in args)
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "spawn.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} pid={os.getpid()} {cmd[:200]}\n")
    except Exception:
        pass


def install(popen_cls=subprocess.Popen, force: bool = False) -> bool:
    if (sys.platform != "win32" and not force) or getattr(popen_cls, "_pine_hidden", False):
        return False
    original = popen_cls.__init__

    def __init__(self, *args, **kwargs):
        flags = kwargs.get("creationflags", 0) or 0
        if not flags & (CREATE_NEW_CONSOLE | DETACHED_PROCESS | CREATE_NO_WINDOW):
            kwargs["creationflags"] = flags | CREATE_NO_WINDOW
        if os.environ.get("PINE_SPAWN_LOG"):
            _log(args[0] if args else kwargs.get("args", ""))
        original(self, *args, **kwargs)

    popen_cls.__init__ = __init__
    popen_cls._pine_hidden = True
    return True


install()
