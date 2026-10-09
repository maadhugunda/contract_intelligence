import unittest
from pydantic import ValidationError
from cir import CIRSnapshot, Element, Relationship

class CIRTests(unittest.TestCase):
    def test_valid_snapshot(self):
        snapshot = CIRSnapshot(document_id='D1', source_revision_id='R1', extraction_run_id='X1', elements=[Element(element_id='E1', page_number=1, order=0, type='paragraph', text='Test', extraction_method='native')])
        self.assertEqual(snapshot.elements[0].element_id, 'E1')
    def test_missing_relationship_target(self):
        with self.assertRaises(ValidationError):
            CIRSnapshot(document_id='D1', source_revision_id='R1', extraction_run_id='X1', elements=[], relationships=[Relationship(source_id='E1', target_id='E2', type='FOOTNOTE_OF')])
    def test_invalid_confidence(self):
        with self.assertRaises(ValidationError):
            Element(element_id='E1', page_number=1, order=0, type='paragraph', extraction_method='ocr', confidence=1.2)

if __name__ == '__main__':
    unittest.main()
