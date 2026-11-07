#!/usr/bin/python3
"""Reject missing, empty, or unreadable Time Machine volumes before export."""
import json
import os
from pathlib import Path
import stat
import sys

root = sys.argv[1]
volumes = []


def fail(error):
    raise error


for entry in os.scandir(root):
    if entry.name.startswith('.'):
        continue
    if entry.is_symlink():
        raise RuntimeError(f'Backup volume is a link instead of mounted data: {entry.name}')
    if not entry.is_dir(follow_symlinks=False):
        continue
    excluded = {
        'private/var', 'private/etc/cups/certs',
        'Library/Application Support/Apple/ParentalControls/Users',
        'Library/Preferences/com.express.vpn/api_client',
        'Library/Preferences/com.express.vpn/csdk',
    } if entry.name == 'Macintosh HD - Data' else set()
    count = 0
    for directory, dirs, files in os.walk(entry.path, onerror=fail):
        relative = os.path.relpath(directory, entry.path)
        dirs[:] = [name for name in dirs if (Path(relative) / name).as_posix() not in excluded]
        count += sum(stat.S_ISREG(os.lstat(os.path.join(directory, name)).st_mode) for name in files)
    if count == 0:
        raise RuntimeError(f'Backup volume is empty or not mounted: {entry.name}. No completed export was published.')
    volumes.append(dict(name=entry.name, regularFiles=count))
if not volumes:
    raise RuntimeError('No readable backup volumes were found')
json.dump(volumes, sys.stdout)
