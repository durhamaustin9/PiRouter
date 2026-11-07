#!/usr/bin/env python3
"""Record and release only the idle Time Machine image used by an export.

The caller must stop/wait for its readers and leave the snapshot directory
before release. Ejection is always non-forced, so macOS can refuse busy images.
"""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import subprocess
import sys
import tempfile
import uuid
from xml.parsers.expat import ExpatError


HDIUTIL = '/usr/bin/hdiutil'
DISKUTIL = '/usr/sbin/diskutil'
TMUTIL = '/usr/bin/tmutil'
DEVICE = re.compile(r'^/dev/(disk\d+(?:s\d+)*)$')
BASE_DEVICE = re.compile(r'^/dev/disk\d+$')
SOURCE = re.compile(r'^/Volumes/\.timemachine/([0-9a-fA-F-]{36})/(\d{4}-\d{2}-\d{2}-\d{6})\.backup(?:/\2\.backup)?/?$')


class Deferred(Exception):
    """Keep the attachment and receipt until a later safe retry."""


def run_command(arguments, timeout=15):
    try:
        return subprocess.run(arguments, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Deferred('Could not check the Time Machine connection: ' + str(error)) from error


def command_plist(arguments, runner):
    result = runner(arguments)
    if result.returncode != 0:
        raise Deferred('A disk identity check failed; the connection was left attached')
    try:
        value = plistlib.loads(result.stdout)
    except (ValueError, plistlib.InvalidFileException, ExpatError, UnicodeError, OverflowError) as error:
        raise Deferred('A disk identity report was unreadable') from error
    if not isinstance(value, dict) or value.get('Error'):
        raise Deferred('A disk identity report was unavailable')
    return value


def source_uuid(source):
    match = SOURCE.fullmatch(str(source))
    if not match:
        raise Deferred('The source is not a mounted Time Machine APFS snapshot')
    try:
        return str(uuid.UUID(match.group(1))).upper()
    except ValueError as error:
        raise Deferred('The Time Machine source UUID is invalid') from error


def backup_image_path(value):
    return (isinstance(value, str) and value.startswith('/Volumes/.timemachine/')
            and '\0' not in value and '..' not in Path(value).parts
            and value.lower().endswith(('.sparsebundle', '.backupbundle')))


def images(runner):
    value = command_plist([HDIUTIL, 'info', '-plist'], runner).get('images')
    if not isinstance(value, list) or any(not isinstance(image, dict) for image in value):
        raise Deferred('The attached disk-image report is incomplete')
    if any(not isinstance(image.get('system-entities', []), list) for image in value):
        raise Deferred('An attached disk-image device report is incomplete')
    return value


def identity(image, volume_uuid, runner):
    """Map the APFS volume UUID to its physical disk-image store, not its container."""
    path = image.get('image-path')
    pid = image.get('hdid-pid')
    entities = image.get('system-entities')
    if not backup_image_path(path) or type(pid) is not int or pid <= 0 or not isinstance(entities, list):
        return None
    devices = [entity.get('dev-entry') for entity in entities if isinstance(entity, dict)]
    devices = [device for device in devices if isinstance(device, str) and DEVICE.fullmatch(device)]
    matches = []
    for device in devices:
        # A base disk or APFS container is not the Time Machine volume.
        if not re.search(r's\d+$', device):
            continue
        info = command_plist([DISKUTIL, 'info', '-plist', device], runner)
        if str(info.get('VolumeUUID', '')).upper() != volume_uuid:
            continue
        if info.get('DeviceIdentifier') != device.removeprefix('/dev/') or info.get('BusProtocol') != 'Disk Image':
            raise Deferred('The Time Machine volume is not backed by the expected disk image')
        stores = info.get('APFSPhysicalStores')
        if not isinstance(stores, list) or len(stores) != 1 or not isinstance(stores[0], dict):
            raise Deferred('The Time Machine image has an unsupported physical-store layout')
        store = stores[0].get('APFSPhysicalStore')
        if not isinstance(store, str) or not DEVICE.fullmatch('/dev/' + store):
            raise Deferred('The Time Machine physical-store identity is invalid')
        physical = command_plist([DISKUTIL, 'info', '-plist', '/dev/' + store], runner)
        base = physical.get('ParentWholeDisk') or physical.get('DeviceIdentifier')
        if not isinstance(base, str) or not BASE_DEVICE.fullmatch('/dev/' + base):
            raise Deferred('The Time Machine image has no identifiable whole disk')
        base_device = '/dev/' + base
        if base_device not in devices:
            raise Deferred('The Time Machine whole disk is not part of its image attachment')
        whole = physical if store == base else command_plist([DISKUTIL, 'info', '-plist', base_device], runner)
        if whole.get('DeviceIdentifier') != base or whole.get('WholeDisk') is not True or whole.get('BusProtocol') != 'Disk Image':
            raise Deferred('The proposed eject target is not a whole disk image')
        matches.append(dict(version=1, imagePath=path, baseDevice=base_device,
                            imagePid=pid, volumeUUID=volume_uuid, volumeDevice=device))
    if len(matches) > 1:
        raise Deferred('The Time Machine volume identity is ambiguous')
    return matches[0] if matches else None


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def state_path(value):
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts or not path.parent.is_dir():
        raise Deferred('The mount receipt must be in an existing absolute support directory')
    return path


def checked_state_stat(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise Deferred('The mount receipt is not a private regular file owned by this user')
    if info.st_size > 65536:
        raise Deferred('The mount receipt is too large')
    return info


@contextmanager
def state_lock(path):
    descriptor = os.open(str(path) + '.lock', os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
            raise Deferred('The mount-receipt lock is unsafe')
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise Deferred('Another mount-lifecycle check is running') from error
        yield
    finally:
        os.close(descriptor)


def write_receipt(path, receipt):
    checked_state_stat(path)
    descriptor, temporary = tempfile.mkstemp(prefix='.export-mount-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as output:
            json.dump(receipt, output, ensure_ascii=True)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_receipt(path):
    info = checked_state_stat(path)
    if info is None:
        return None
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(descriptor) as source:
        opened = os.fstat(source.fileno())
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise Deferred('The mount receipt changed during its identity check')
        try:
            receipt = json.load(source)
        except (ValueError, UnicodeError) as error:
            raise Deferred('The mount receipt is unreadable') from error
    if not isinstance(receipt, dict) or type(receipt.get('version')) is not int or receipt['version'] != 1:
        raise Deferred('The mount receipt format is invalid')
    if not backup_image_path(receipt.get('imagePath')) or not isinstance(receipt.get('baseDevice'), str) or not BASE_DEVICE.fullmatch(receipt['baseDevice']):
        raise Deferred('The saved Time Machine image identity is invalid')
    if type(receipt.get('imagePid')) is not int or receipt['imagePid'] <= 0:
        raise Deferred('The saved Time Machine image process identity is invalid')
    if not isinstance(receipt.get('volumeDevice'), str) or not DEVICE.fullmatch(receipt['volumeDevice']):
        raise Deferred('The saved Time Machine volume device is invalid')
    if source_uuid(receipt.get('sourcePath', '')) != receipt.get('volumeUUID'):
        raise Deferred('The saved snapshot and image volume UUID do not agree')
    return receipt


def clear_receipt(path):
    checked_state_stat(path)
    path.unlink(missing_ok=True)
    sync_directory(path.parent)


def capture(statefile, source, runner=run_command, source_isdir=os.path.isdir, if_mounted=False):
    path = state_path(statefile)
    volume_uuid = source_uuid(source)
    if not source_isdir(source) and not if_mounted:
        raise Deferred('The saved Time Machine snapshot is not currently mounted')
    matches = []
    with state_lock(path):
        for image in images(runner):
            candidate = identity(image, volume_uuid, runner)
            if candidate:
                matches.append(candidate)
        if not matches and if_mounted:
            return None
        if len(matches) != 1:
            raise Deferred('Could not uniquely identify the mounted Time Machine backup image')
        receipt = dict(matches[0], sourcePath=str(source))
        write_receipt(path, receipt)
        return receipt


def release(statefile, runner=run_command):
    path = state_path(statefile)
    with state_lock(path):
        receipt = read_receipt(path)
        if receipt is None:
            return 'absent'
        attached = images(runner)
        matching = [image for image in attached if image.get('image-path') == receipt['imagePath']]
        if not matching:
            reused = any(receipt['baseDevice'] == entity.get('dev-entry')
                         for image in attached for entity in image.get('system-entities', [])
                         if isinstance(entity, dict))
            if reused:
                raise Deferred('The saved disk device now belongs to another image; nothing was ejected')
            clear_receipt(path)
            return 'absent'
        if len(matching) != 1 or matching[0].get('hdid-pid') != receipt['imagePid']:
            raise Deferred('The Time Machine image attachment changed; nothing was ejected')
        current = identity(matching[0], receipt['volumeUUID'], runner)
        if current is None or any(current[key] != receipt[key] for key in current):
            raise Deferred('The Time Machine disk identity changed; nothing was ejected')
        status = runner([TMUTIL, 'status'])
        values = re.findall(rb'^\s*Running\s*=\s*([01]);?\s*$', status.stdout, re.MULTILINE)
        if status.returncode != 0 or values != [b'0']:
            raise Deferred('Time Machine is running or its state is unknown; the connection was kept')
        result = runner([DISKUTIL, 'eject', receipt['baseDevice']], timeout=45)
        if result.returncode != 0:
            raise Deferred('macOS kept the Time Machine image attached because it is busy or could not be ejected')
        if any(image.get('image-path') == receipt['imagePath'] for image in images(runner)):
            raise Deferred('The Time Machine image is still attached; its receipt was retained')
        clear_receipt(path)
        return 'released'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='operation', required=True)
    capture_parser = commands.add_parser('capture')
    capture_parser.add_argument('--statefile', required=True)
    capture_parser.add_argument('--source', required=True)
    capture_parser.add_argument('--if-mounted', action='store_true',
                                help='Succeed without a receipt when a saved snapshot is no longer attached')
    release_parser = commands.add_parser('release')
    release_parser.add_argument('--statefile', required=True)
    args = parser.parse_args(argv)
    try:
        if args.operation == 'capture':
            capture(args.statefile, args.source, if_mounted=args.if_mounted)
        elif release(args.statefile) == 'released':
            print('Released the idle Time Machine backup connection.', file=sys.stderr)
    except (Deferred, OSError) as error:
        print('Time Machine connection kept: ' + str(error), file=sys.stderr)
        return 75
    return 0


if __name__ == '__main__':
    sys.exit(main())
