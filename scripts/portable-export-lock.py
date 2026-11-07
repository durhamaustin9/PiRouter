#!/usr/bin/python3
"""Hold a crash-safe process lock while the macOS export wrapper runs."""
import fcntl
import os
from pathlib import Path
import sys


def main():
    target = Path(sys.argv[1])
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(str(target), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(handle)
        return 0
    # The wrapper uses descriptor 3 for tmutil. Descriptor 9 survives exec and
    # keeps the lock until the wrapper and its exporter have both exited.
    if handle != 9:
        os.dup2(handle, 9)
        os.close(handle)
    os.set_inheritable(9, True)
    environment = dict(os.environ, PIROUTER_EXPORT_LOCKED="1")
    os.execve("/bin/bash", ["/bin/bash", *sys.argv[2:]], environment)


if __name__ == "__main__":
    sys.exit(main())
