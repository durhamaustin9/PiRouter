"""Verify fixture snapshots without calling tmutil or mounting real backups."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'scripts/verify-time-machine-volumes.py'
SPEC = importlib.util.spec_from_file_location('isolated_time_machine_verifier', SOURCE)
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


class TimeMachineVerifier(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='pirouter-verifier-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.snapshot = self.root / 'Snapshot With Spaces.backup'
        self.snapshot.mkdir()
        self.reports = self.root / 'Reports & Results'

    def add_file(self, volume, relative='document.txt', data=b'restored fixture data'):
        target = self.snapshot / volume / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def run_verifier(self, *volumes, default_report_root=False):
        args = [str(SOURCE), '--snapshot-path', str(self.snapshot)]
        if not default_report_root:
            args += ['--report-root', str(self.reports)]
        args += list(volumes)
        out, errors = io.StringIO(), io.StringIO()
        original_cwd = os.getcwd()
        try:
            with mock.patch.object(sys, 'argv', args), \
                 mock.patch.dict(os.environ, {'PIROUTER_SUPPORT_DIR': str(self.root / 'Isolated Support')}), \
                 mock.patch.object(VERIFY.subprocess, 'run', side_effect=AssertionError('Unexpected system command')) as run, \
                 mock.patch.object(VERIFY.subprocess, 'Popen', side_effect=AssertionError('Unexpected mounted snapshot')) as start, \
                 redirect_stdout(out), redirect_stderr(errors):
                result = VERIFY.main()
                run.assert_not_called()
                start.assert_not_called()
        finally:
            os.chdir(original_cwd)
        reports = self.root / 'Isolated Support/restore-verification' if default_report_root else self.reports
        progress = json.loads((reports / 'progress.json').read_text())
        return result, progress, out.getvalue(), errors.getvalue()

    def test_discovers_actual_volume_names_and_hashes_contents(self):
        personal = self.add_file('Personal Files', data=b'personal document')
        work = self.add_file('Work Data', 'Projects/report.bin', b'work document\x00bytes')
        self.add_file('.metadata', data=b'not a backed-up volume')
        (self.snapshot / 'Snapshot Notes.txt').write_text('ordinary top-level file')
        before = {path: path.read_bytes() for path in [personal, work]}
        result, progress, output, errors = self.run_verifier()
        self.assertEqual(result, 0, errors)
        self.assertEqual(progress['state'], 'passed')
        self.assertEqual(progress['requestedVolumes'], ['Personal Files', 'Work Data'])
        report = json.loads(Path(progress['reportPath']).read_text())
        self.assertTrue(report['passed'])
        self.assertEqual([entry['volume'] for entry in report['volumes']], ['Personal Files', 'Work Data'])
        for entry in report['volumes']:
            manifest = Path(entry['manifestPath'])
            contents = manifest.read_bytes()
            self.assertEqual(entry['manifestSha256'], hashlib.sha256(contents).hexdigest())
            record = json.loads(contents.splitlines()[0])
            path = self.snapshot / entry['volume'] / record['path']
            self.assertEqual(record['sha256'], hashlib.sha256(before[path]).hexdigest())
            self.assertEqual(entry['bytesRead'], len(before[path]))
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assertIn('VERIFY_RESULT passed=true', output)

    def test_explicit_selection_does_not_require_other_volumes(self):
        self.add_file('Chosen Volume')
        (self.snapshot / 'Other Empty Volume').mkdir()
        result, progress, _, errors = self.run_verifier('Chosen Volume')
        self.assertEqual(result, 0, errors)
        self.assertEqual(progress['requestedVolumes'], ['Chosen Volume'])
        self.assertEqual(len(progress['report']['volumes']), 1)

    def test_no_visible_volume_directories_cannot_report_success(self):
        self.add_file('.hidden-volume')
        (self.snapshot / 'notes').write_text('not a volume directory')
        result, progress, _, _ = self.run_verifier()
        self.assertEqual(result, 1)
        self.assertEqual(progress['state'], 'error')
        self.assertIn('No backed-up volumes', progress['error'])
        self.assertNotIn('report', progress)

    def test_discovery_rejects_a_visible_symlink_instead_of_following_it(self):
        self.add_file('Actual Volume')
        outside = self.root / 'Outside Data'
        outside.mkdir()
        (outside / 'private.txt').write_text('must not be read')
        (self.snapshot / 'Linked Volume').symlink_to(outside, target_is_directory=True)
        result, progress, _, _ = self.run_verifier()
        self.assertEqual(result, 1)
        self.assertIn('link instead of mounted data', progress['error'])
        self.assertFalse(list(self.reports.glob('*/*.sha256.jsonl')))

    def test_explicit_traversal_or_linked_volumes_are_rejected(self):
        outside = self.root / 'Outside Data'
        outside.mkdir()
        (outside / 'document').write_text('outside contents')
        (self.snapshot / 'Linked Volume').symlink_to(outside, target_is_directory=True)
        for name in ('../Outside Data', str(outside), '.', '..', 'Volume/Child', '', 'Linked Volume'):
            with self.subTest(name=name):
                result, progress, _, _ = self.run_verifier(name)
                self.assertEqual(result, 1)
                self.assertEqual(progress['state'], 'error')
                self.assertFalse(list(self.reports.glob('*/*.sha256.jsonl')))

    def test_empty_volume_is_a_failed_verification(self):
        (self.snapshot / 'Empty Volume/nested/empty').mkdir(parents=True)
        result, progress, output, _ = self.run_verifier()
        self.assertEqual(result, 2)
        self.assertEqual(progress['state'], 'failed')
        self.assertFalse(progress['report']['passed'])
        self.assertEqual(progress['report']['volumes'][0]['regularFiles'], 0)
        self.assertIn('VERIFY_RESULT passed=false', output)

    def test_links_inside_a_volume_are_recorded_without_reading_their_targets(self):
        self.add_file('Volume', data=b'inside volume')
        outside = self.root / 'Outside Document'
        outside.write_bytes(b'not part of the backup volume')
        (self.snapshot / 'Volume/shortcut').symlink_to(outside)
        result, progress, _, errors = self.run_verifier('Volume')
        self.assertEqual(result, 0, errors)
        volume = progress['report']['volumes'][0]
        self.assertEqual((volume['regularFiles'], volume['symlinks'], volume['bytesRead']), (1, 1, len(b'inside volume')))
        entries = [json.loads(line) for line in Path(volume['manifestPath']).read_text().splitlines()]
        self.assertEqual(next(entry for entry in entries if entry['path'] == 'shortcut'),
                         {'path': 'shortcut', 'symlink': str(outside)})

    def test_default_reports_use_isolated_support_directory(self):
        self.add_file('Volume')
        result, progress, _, errors = self.run_verifier(default_report_root=True)
        self.assertEqual(result, 0, errors)
        self.assertTrue(Path(progress['reportPath']).is_relative_to(self.root / 'Isolated Support/restore-verification'))
        self.assertFalse(self.reports.exists())


if __name__ == '__main__':
    unittest.main()
