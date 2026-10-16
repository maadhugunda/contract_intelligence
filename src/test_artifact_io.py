from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from artifact_io import write_text_atomic


class AtomicExportTests(unittest.TestCase):
    def test_success_replaces_complete_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'result.json'
            path.write_text('previous')
            write_text_atomic(path, '{"text": "✓"}\n')
            self.assertEqual(path.read_text(), '{"text": "✓"}\n')
            self.assertEqual(list(Path(folder).iterdir()), [path])

    def test_failures_preserve_previous_output_and_clean_temp(self):
        for failure in ('fsync', 'replace'):
            for exists in (True, False):
                with self.subTest(failure=failure, exists=exists), tempfile.TemporaryDirectory() as folder:
                    path = Path(folder) / 'result.json'
                    if exists:
                        path.write_text('previous')
                    with patch('artifact_io.os.' + failure, side_effect=OSError('simulated failure')):
                        with self.assertRaises(OSError):
                            write_text_atomic(path, 'new output')
                    self.assertEqual(path.exists(), exists)
                    if exists:
                        self.assertEqual(path.read_text(), 'previous')
                    self.assertEqual(list(Path(folder).glob('.export-*.tmp')), [])
