from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from cir import CIRSnapshot
from extraction import ExtractionError, ExtractionService, PageExtraction, extract_document

ROOT = Path(__file__).resolve().parents[1]
HAS_PDF = importlib.util.find_spec('fitz') is not None


class StubAdapter:
    def extract(self, content):
        return [PageExtraction(page_number=1, width=100, height=200, rotation=0,
                               text='1. Terms\n\nBody', blocks=[
                                   {'type': 'paragraph', 'text': '1. Terms', 'bbox': [0, 0, 50, 10]},
                                   {'type': 'paragraph', 'text': 'Body', 'bbox': [0, 20, 50, 30]}]),
                PageExtraction(page_number=2, width=100, height=200, rotation=0,
                               text='', warnings=['no_embedded_text'])], {'engine': 'stub'}


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'source.pdf'
        self.path.write_bytes(b'source bytes')
        self.service = ExtractionService({'.pdf': StubAdapter()})

    def test_deterministic_provenance_and_cir_roundtrip(self):
        result = self.service.extract(self.path, 'D1', 'V1')
        self.assertEqual(result, self.service.extract(self.path, 'D1', 'V1'))
        self.assertEqual(len(result.pages), 2)
        cir = CIRSnapshot.model_validate_json(result.to_cir().model_dump_json())
        self.assertEqual(cir.source_revision_id, 'V1')
        self.assertEqual(cir.pages[1]['text'], '')
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

    def test_invalid_inputs(self):
        with self.assertRaises(ExtractionError):
            self.service.extract(self.path, '', 'V')
        with self.assertRaises(FileNotFoundError):
            self.service.extract(self.path.with_name('missing.pdf'), 'D', 'V')
        with self.assertRaises(ExtractionError):
            ExtractionService({}).extract(self.path, 'D', 'V')

    def test_missing_page_rejected(self):
        class BrokenAdapter:
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
