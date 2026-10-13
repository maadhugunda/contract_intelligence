from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from cir import CIRSnapshot
from extraction import ExtractionError, ExtractionService, PageExtraction, extract_document
from task_registry import TaskRegistry
from unittest.mock import patch
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
HAS_PDF = importlib.util.find_spec('fitz') is not None


class StubAdapter:
    def describe(self):
        return {'engine': 'stub'}
    def extract(self, content):
        return [PageExtraction(page_number=1, width=100, height=200, rotation=0,
                               text='1. Terms\n\nBody', blocks=[
                                   {'type': 'paragraph', 'text': '1. Terms', 'bbox': [0, 0, 50, 10]},
                                   {'type': 'paragraph', 'text': 'Body', 'bbox': [0, 20, 50, 30]}]),
                PageExtraction(page_number=2, width=100, height=200, rotation=0,
                               text='', warnings=['no_embedded_text'])], {'engine': 'stub'}


class ExtractionTests(unittest.TestCase):
    def test_worker_requires_planning_and_checks_before_extraction(self):
        adapter = self.service.adapters['.pdf']
        with patch.object(adapter, 'extract', wraps=adapter.extract) as extract:
            with self.assertRaisesRegex(ExtractionError, 'preplanned'):
                self.service.execute_run(self.path, 'planned-job')
            contract = self.service.plan_run(self.path, 'D', 'V', extraction_run_id='planned-job')
            extract.assert_not_called()
            self.registry.create_extraction_task('task-1', 'planned-job')
            self.assertIsNotNone(self.registry.acquire_lease('task-1', 'worker-1'))
            result = self.service.execute_run(self.path, 'planned-job')
            self.assertEqual(result.pipeline_fingerprint, contract['pipeline_fingerprint'])
            self.assertEqual(extract.call_count, 1)
            self.path.write_bytes(b'changed')
            with self.assertRaisesRegex(ExtractionError, 'does not match'):
                self.service.execute_run(self.path, 'planned-job')
            self.assertEqual(extract.call_count, 1)
            self.path.write_bytes(b'source bytes')
            with patch('extraction.SECTION_STRUCTURE_VERSION', 'other'):
                with self.assertRaisesRegex(ExtractionError, 'does not match'):
                    self.service.execute_run(self.path, 'planned-job')
            self.assertEqual(extract.call_count, 1)

    def test_worker_cannot_register_legacy_unplanned_binding(self):
        self.registry.bind_extraction_run('legacy', 'D', 'V', 'a' * 64, 'b' * 64)
        with self.assertRaisesRegex(ExtractionError, 'preplanned'):
            self.service.execute_run(self.path, 'legacy')
        self.assertIsNone(self.registry.get_run_contract('legacy'))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'source.pdf'
        self.path.write_bytes(b'source bytes')
        self.registry = TaskRegistry(str(Path(self.temp.name) / 'runs.sqlite'))
        self.service = ExtractionService({'.pdf': StubAdapter()}, run_registry=self.registry)

    def test_deterministic_provenance_and_cir_roundtrip(self):
        result = self.service.extract(self.path, 'D1', 'V1')
        self.assertEqual(result, self.service.extract(self.path, 'D1', 'V1'))
        self.assertEqual(len(result.pages), 2)
        cir = CIRSnapshot.model_validate_json(result.to_cir().model_dump_json())
        self.assertEqual(cir.source_revision_id, 'V1')
        self.assertEqual(cir.pages[1].text, '')
        self.assertEqual([e.order for e in cir.elements], [0, 1])
        self.assertEqual(cir.elements[0].text, '1. Terms')
        self.assertEqual(cir.elements[0].structured_content['bbox'], [0, 0, 50, 10])
        self.assertEqual(cir.elements[0].structured_content['provenance']['document_id'], 'D1')
        self.assertIn('#page=1&block=0', cir.elements[0].source_ref)
        self.assertNotEqual(result.extraction_run_id,
                            self.service.extract(self.path, 'D1', 'V2').extraction_run_id)

    def test_hash_validation(self):
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.assertEqual(self.service.extract(self.path, 'D', 'V', expected_sha256=digest).source_sha256, digest)
        with self.assertRaisesRegex(ExtractionError, 'SHA-256'):
            self.service.extract(self.path, 'D', 'V', expected_sha256='0' * 64)

    def test_stable_artifact_and_invalid_references(self):
        result = self.service.extract(self.path, 'D', 'V')
        moved = self.path.with_name('moved.pdf')
        moved.write_bytes(self.path.read_bytes())
        self.assertEqual(result, self.service.extract(moved, 'D', 'V'))
        self.assertNotIn(str(self.path), result.model_dump_json())
        self.assertEqual(result.source_artifact_ref, 'urn:sha256:' + result.source_sha256)
        for case in ('missing_page', 'duplicate_page', 'wrong_ref', 'wrong_artifact', 'provenance', 'order'):
            with self.subTest(case=case):
                payload = result.to_cir().model_dump()
                if case == 'missing_page':
                    payload['elements'][0]['page_number'] = 3
                elif case == 'duplicate_page':
                    payload['pages'][1]['page_number'] = 1
                elif case == 'wrong_ref':
                    payload['elements'][0]['source_ref'] = 'urn:sha256:wrong#page=1&block=0'
                elif case == 'wrong_artifact':
                    payload['source_artifact_ref'] = 'urn:sha256:' + '0' * 64
                elif case == 'provenance':
                    payload['pages'][0]['provenance']['document_id'] = 'other'
                else:
                    payload['elements'][1]['order'] = 0
                with self.assertRaises(ValidationError):
                    CIRSnapshot.model_validate(payload)

    def test_retry_ids_and_new_extraction_ids(self):
        original = self.service.extract(self.path, 'D', 'V', extraction_run_id='job-1')
        retry = self.service.extract(self.path, 'D', 'V', extraction_run_id='job-1')
        fresh = self.service.extract(self.path, 'D', 'V', extraction_run_id='job-2')
        ids = lambda result: [e.element_id for e in result.to_cir().elements]
        self.assertEqual(original, retry)
        self.assertEqual(original.extraction_run_id, 'job-1')
        self.assertEqual(ids(original), ids(retry))
        self.assertTrue(set(ids(original)).isdisjoint(ids(fresh)))
        # Retrying only a selected block retains its original deterministic ID.
        self.assertEqual(original.pages[0].blocks[1]['element_id'],
                         retry.pages[0].blocks[1]['element_id'])
        copied = self.path.with_name('copied.pdf')
        copied.write_bytes(self.path.read_bytes())
        self.assertEqual(ids(original), ids(self.service.extract(copied, 'D', 'V', extraction_run_id='job-1')))
        self.path.write_bytes(b'changed source')
        with self.assertRaisesRegex(ExtractionError, 'already bound'):
            self.service.extract(self.path, 'D', 'V', extraction_run_id='job-1')
        with self.assertRaises(ExtractionError):
            self.service.extract(self.path, 'D', 'V', extraction_run_id=' ')

    def test_binding_survives_restart_and_rejects_pipeline_drift(self):
        original = self.service.extract(self.path, 'D', 'V', extraction_run_id='bound-run')
        restarted = ExtractionService({'.pdf': StubAdapter()},
                         run_registry=TaskRegistry(str(Path(self.temp.name) / 'runs.sqlite')))
        self.assertEqual(original, restarted.extract(self.path, 'D', 'V', extraction_run_id='bound-run'))
        binding = self.registry.get_extraction_run('bound-run')
        self.assertEqual(binding['source_sha256'], original.source_sha256)
        self.assertEqual(binding['pipeline_fingerprint'], original.pipeline_fingerprint)
        self.assertEqual(original.to_cir().pipeline_fingerprint, original.pipeline_fingerprint)
        with patch('extraction.SECTION_STRUCTURE_VERSION', 'new-version'):
            with self.assertRaisesRegex(ExtractionError, 'already bound'):
                restarted.extract(self.path, 'D', 'V', extraction_run_id='bound-run')
            fresh = restarted.extract(self.path, 'D', 'V', extraction_run_id='fresh-run')
            self.assertNotEqual(fresh.pipeline_fingerprint, original.pipeline_fingerprint)
        with self.assertRaisesRegex(ExtractionError, 'already bound'):
            restarted.extract(self.path, 'Other-document', 'V', extraction_run_id='bound-run')

    def test_invalid_inputs(self):
        with self.assertRaises(ExtractionError):
            self.service.extract(self.path, '', 'V')
        with self.assertRaises(FileNotFoundError):
            self.service.extract(self.path.with_name('missing.pdf'), 'D', 'V')
        with self.assertRaises(ExtractionError):
            ExtractionService({}).extract(self.path, 'D', 'V')

    def test_missing_page_rejected(self):
        class BrokenAdapter:
            def describe(self):
                return {}
            def extract(self, content):
                return [PageExtraction(page_number=2, width=1, height=1, rotation=0, text='')], {}
        with self.assertRaisesRegex(ExtractionError, 'every page'):
            ExtractionService({'.pdf': BrokenAdapter()}).extract(self.path, 'D', 'V')


@unittest.skipUnless(HAS_PDF, 'PyMuPDF is not installed')
class PDFIntegrationTests(unittest.TestCase):
    def test_dataset_hashes_pages_text_and_determinism(self):
        import fitz
        manifest = json.loads((ROOT / 'datasets/d0/manifest.json').read_text())
        for entry in manifest['documents']:
            with self.subTest(document=entry['document_id']):
                path = ROOT / 'datasets/d0/contracts' / entry['file']
                result = extract_document(path, entry['document_id'], 'v1', expected_sha256=entry['sha256'])
                self.assertEqual(result.document_id, entry['document_id'])
                self.assertEqual(result, extract_document(path, entry['document_id'], 'v1'))
                with fitz.open(path) as pdf:
                    self.assertEqual(len(result.pages), len(pdf))
                    for page, extracted in zip(pdf, result.pages):
                        self.assertEqual(''.join(extracted.text.split()), ''.join(page.get_text(sort=True).split()))
                        self.assertTrue(extracted.text.strip())
                self.assertEqual(result.to_cir().source_sha256, entry['sha256'])
                snapshot = result.to_cir()
                headings = [e for e in snapshot.elements if e.type == 'heading']
                self.assertEqual(len(headings), entry['clause_count'])
                active = None
                for element in snapshot.elements:
                    if element.type == 'heading':
                        active = element.element_id
                        self.assertIsNone(element.parent_id)
                    elif element.structured_content.get('page_role') in {'header', 'footer'}:
                        self.assertIsNone(element.parent_id)
                    else:
                        self.assertEqual(element.parent_id, active)
                self.assertTrue(all(any(e.parent_id == h.element_id for e in snapshot.elements)
                                    for h in headings))

    def test_blank_page_and_invalid_pdf(self):
        import fitz
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'blank.pdf'
            with fitz.open() as pdf:
                pdf.new_page()
                path.write_bytes(pdf.tobytes())
            result = extract_document(path, 'D', 'V')
            self.assertEqual(len(result.pages), 1)
            self.assertTrue(result.pages[0].warnings)
            self.assertEqual(len(result.to_cir().pages), 1)
            path.write_bytes(b'not a pdf')
            with self.assertRaises(ExtractionError):
                extract_document(path, 'D', 'V')
