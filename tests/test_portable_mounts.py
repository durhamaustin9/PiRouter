"""Validate Time Machine image ownership with mocked macOS reports; never eject live disks."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('portable_mounts', ROOT / 'scripts/portable-export-mounts.py')
MOUNTS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOUNTS)
# Synthetic identifiers and dates must never describe a real backup.
VOLUME_UUID = '11111111-2222-4333-8444-555555555555'
SOURCE = f'/Volumes/.timemachine/{VOLUME_UUID}/2000-01-01-000000.backup/2000-01-01-000000.backup'
IMAGE_PATH = '/Volumes/.timemachine/FILESERVER._smb._tcp.local./destination/TimeMachine/Mac.sparsebundle'


class MacReports:
    def __init__(self):
        self.attached = True
        self.image = {'image-path': IMAGE_PATH, 'hdid-pid': 2023, 'owner-uid': 0, 'writeable': True,
                      'system-entities': [
                          {'dev-entry': '/dev/disk4'},
                          {'dev-entry': '/dev/disk5'},
                          {'dev-entry': '/dev/disk5s1', 'mount-point': '/Volumes/Backups of Mac'},
                      ]}
        self.extra_images = []
        self.volume = {'DeviceIdentifier': 'disk5s1', 'VolumeUUID': VOLUME_UUID,
                       'BusProtocol': 'Disk Image', 'APFSPhysicalStores': [{'APFSPhysicalStore': 'disk4'}]}
        self.whole = {'DeviceIdentifier': 'disk4', 'ParentWholeDisk': 'disk4',
                      'WholeDisk': True, 'BusProtocol': 'Disk Image'}
        self.status = b'Backup session status:\n{\n    Running = 0;\n}\n'
        self.status_result = 0
        self.eject_result = 0
        self.keep_attached_after_eject = False
        self.fail_report = False
        self.calls = []

    def run(self, arguments, timeout=15):
        self.calls.append((arguments, timeout))
        if arguments == [MOUNTS.HDIUTIL, 'info', '-plist']:
            if self.fail_report:
                return subprocess.CompletedProcess(arguments, 1, b'', b'Cannot query images')
            value = {'images': ([self.image] if self.attached else []) + self.extra_images}
        elif arguments == [MOUNTS.DISKUTIL, 'info', '-plist', '/dev/disk5s1']:
            value = self.volume
        elif arguments == [MOUNTS.DISKUTIL, 'info', '-plist', '/dev/disk4']:
            value = self.whole
        elif arguments == [MOUNTS.TMUTIL, 'status']:
            return subprocess.CompletedProcess(arguments, self.status_result, self.status, b'')
        elif arguments == [MOUNTS.DISKUTIL, 'eject', '/dev/disk4']:
            if self.eject_result == 0 and not self.keep_attached_after_eject:
                self.attached = False
            return subprocess.CompletedProcess(arguments, self.eject_result, b'', b'busy' if self.eject_result else b'')
        else:
            raise AssertionError(f'Unexpected or unsafe command: {arguments}')
        return subprocess.CompletedProcess(arguments, 0, plistlib.dumps(value), b'')

    def eject_calls(self):
        return [arguments for arguments, timeout in self.calls if arguments[:2] == [MOUNTS.DISKUTIL, 'eject']]


class PortableMounts(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name) / 'mount.json'
        self.reports = MacReports()

    def capture(self, **options):
        return MOUNTS.capture(self.state, SOURCE, runner=self.reports.run,
                              source_isdir=lambda value: True, **options)

    def release(self):
        return MOUNTS.release(self.state, runner=self.reports.run)

    def test_capture_records_private_exact_image_identity_and_physical_base(self):
        receipt = self.capture()
        self.assertEqual(receipt['volumeUUID'], VOLUME_UUID)
        self.assertEqual(receipt['imagePath'], IMAGE_PATH)
        self.assertEqual(receipt['baseDevice'], '/dev/disk4')
        self.assertEqual(receipt['volumeDevice'], '/dev/disk5s1')
        self.assertEqual(receipt['imagePid'], 2023)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(self.state.read_text()), receipt)
        self.assertFalse(self.reports.eject_calls())

    def test_release_ejects_only_verified_whole_image_and_removes_receipt(self):
        self.capture()
        self.assertEqual(self.release(), 'released')
        self.assertEqual(self.reports.eject_calls(), [[MOUNTS.DISKUTIL, 'eject', '/dev/disk4']])
        self.assertFalse(self.state.exists())
        self.assertFalse(any('force' in argument for command, timeout in self.reports.calls for argument in command))
        status_index = next(index for index, item in enumerate(self.reports.calls)
                            if item[0] == [MOUNTS.TMUTIL, 'status'])
        self.assertEqual(self.reports.calls[status_index + 1][0], [MOUNTS.DISKUTIL, 'eject', '/dev/disk4'])

    def test_running_unknown_or_failed_time_machine_status_keeps_receipt_and_image(self):
        self.capture()
        for status, result in ((b'{\nRunning = 1;\n}\n', 0), (b'unavailable', 0),
                               (b'{\nRunning = 0;\n}\n', 1),
                               (b'{\nRunning = 0;\nRunning = 1;\n}\n', 0)):
            with self.subTest(status=status, result=result):
                self.reports.status = status
                self.reports.status_result = result
                with self.assertRaises(MOUNTS.Deferred):
                    self.release()
                self.assertTrue(self.state.exists())
                self.assertTrue(self.reports.attached)
                self.assertFalse(self.reports.eject_calls())

    def test_changed_pid_device_or_volume_uuid_never_ejects(self):
        self.capture()
        original = deepcopy(self.reports.image)
        for change in ('pid', 'device', 'uuid'):
            with self.subTest(change=change):
                self.reports.image = deepcopy(original)
                self.reports.volume['VolumeUUID'] = VOLUME_UUID
                if change == 'pid':
                    self.reports.image['hdid-pid'] += 1
                elif change == 'device':
                    self.reports.image['system-entities'][0]['dev-entry'] = '/dev/disk8'
                else:
                    self.reports.volume['VolumeUUID'] = '11111111-1111-1111-1111-111111111111'
                with self.assertRaises(MOUNTS.Deferred):
                    self.release()
                self.assertTrue(self.state.exists())
                self.assertFalse(self.reports.eject_calls())

    def test_reused_device_with_different_image_path_is_not_ejected(self):
        self.capture()
        self.reports.image['image-path'] = '/Volumes/.timemachine/other/Other.sparsebundle'
        with self.assertRaises(MOUNTS.Deferred):
            self.release()
        self.assertTrue(self.state.exists())
        self.assertFalse(self.reports.eject_calls())

    def test_missing_attachment_clears_stale_receipt_without_eject(self):
        self.capture()
        self.reports.attached = False
        self.assertEqual(self.release(), 'absent')
        self.assertFalse(self.state.exists())
        self.assertFalse(self.reports.eject_calls())
        self.assertEqual(self.release(), 'absent')

    def test_busy_image_keeps_receipt_for_a_later_retry_without_force(self):
        self.capture()
        self.reports.eject_result = 1
        with self.assertRaises(MOUNTS.Deferred):
            self.release()
        self.assertTrue(self.state.exists())
        self.assertTrue(self.reports.attached)
        self.assertEqual(self.reports.eject_calls(), [[MOUNTS.DISKUTIL, 'eject', '/dev/disk4']])
        self.reports.eject_result = 0
        self.assertEqual(self.release(), 'released')

    def test_eject_success_without_disappearance_retains_receipt(self):
        self.capture()
        self.reports.keep_attached_after_eject = True
        with self.assertRaises(MOUNTS.Deferred):
            self.release()
        self.assertTrue(self.state.exists())

    def test_unrelated_image_or_non_time_machine_source_cannot_be_adopted(self):
        for source in ('/Volumes/Other/backup', '/Volumes/.timemachine/not-a-uuid/2000-01-01-000000.backup',
                       SOURCE.replace('2000-01-01-000000.backup/2000', '2000-01-01-000000.backup/../2000')):
            with self.subTest(source=source), self.assertRaises(MOUNTS.Deferred):
                MOUNTS.capture(self.state, source, runner=self.reports.run, source_isdir=lambda value: True)
        self.reports.image['image-path'] = '/Volumes/Unrelated/UserImage.sparsebundle'
        with self.assertRaises(MOUNTS.Deferred):
            self.capture()
        self.assertFalse(self.state.exists())
        self.assertFalse(self.reports.eject_calls())

    def test_capture_if_mounted_tolerates_a_stale_mountpoint_but_not_unknown_reports(self):
        self.reports.attached = False
        self.assertIsNone(self.capture(if_mounted=True))
        self.assertFalse(self.state.exists())
        with self.assertRaises(MOUNTS.Deferred):
            self.capture()
        self.reports.fail_report = True
        with self.assertRaises(MOUNTS.Deferred):
            self.capture(if_mounted=True)

    def test_startup_capture_can_identify_backing_image_after_snapshot_path_disappears(self):
        receipt = MOUNTS.capture(self.state, SOURCE, runner=self.reports.run,
                                 source_isdir=lambda value: False, if_mounted=True)
        self.assertEqual(receipt['baseDevice'], '/dev/disk4')
        self.assertFalse(self.reports.eject_calls())

    def test_unrelated_attached_images_are_never_queried_or_ejected(self):
        unrelated = deepcopy(self.reports.image)
        unrelated['image-path'] = '/Volumes/Other/ordinary.sparsebundle'
        unrelated['system-entities'] = [{'dev-entry': '/dev/disk99s1'}]
        self.reports.extra_images = [unrelated]
        self.capture()
        self.assertEqual(self.release(), 'released')
        self.assertFalse(any('/dev/disk99s1' in arguments for arguments, timeout in self.reports.calls))

    def test_malformed_xml_report_defers_without_clearing_receipt(self):
        self.capture()

        def malformed(arguments, timeout=15):
            return subprocess.CompletedProcess(arguments, 0, b'<?xml version="1.0"?><plist><dict>', b'')

        with self.assertRaises(MOUNTS.Deferred):
            MOUNTS.release(self.state, runner=malformed)
        self.assertTrue(self.state.exists())
        self.assertFalse(self.reports.eject_calls())

    def test_malformed_or_public_receipt_is_not_used(self):
        self.capture()
        self.state.chmod(0o644)
        with self.assertRaises(MOUNTS.Deferred):
            self.release()
        self.state.chmod(0o600)
        self.state.write_text('{broken')
        with self.assertRaises(MOUNTS.Deferred):
            self.release()
        self.assertFalse(self.reports.eject_calls())

    def test_symlink_receipt_cannot_redirect_read_or_overwrite(self):
        original = self.state.parent / 'keep'
        original.write_text('unrelated')
        self.state.symlink_to(original)
        with self.assertRaises(MOUNTS.Deferred):
            self.capture()
        with self.assertRaises(MOUNTS.Deferred):
            self.release()
        self.assertEqual(original.read_text(), 'unrelated')
        self.assertFalse(self.reports.eject_calls())

    def test_receipt_is_fsynced_before_replace_and_directory_synced_after(self):
        operations = []
        real_replace = os.replace
        real_fsync = os.fsync
        with mock.patch.object(MOUNTS.os, 'fsync', side_effect=lambda descriptor: (operations.append('sync'), real_fsync(descriptor))[1]), \
             mock.patch.object(MOUNTS.os, 'replace', side_effect=lambda source, target: (operations.append('replace'), real_replace(source, target))[1]):
            self.capture()
        self.assertEqual(operations, ['sync', 'replace', 'sync'])


if __name__ == '__main__':
    unittest.main()
