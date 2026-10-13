import unittest
from pydantic import ValidationError
from cir import CIRSnapshot, Element, Relationship

class CIRTests(unittest.TestCase):
    def test_invalid_relationships(self):
        heading = Element(element_id='H', page_number=1, order=0, type='heading', extraction_method='native')
        first = Element(element_id='A', page_number=1, order=1, type='paragraph', parent_id='H', extraction_method='native')
        second = Element(element_id='B', page_number=2, order=0, type='paragraph', parent_id='H', extraction_method='native')
        def snapshot(relationships):
            return CIRSnapshot(document_id='D', source_revision_id='V', extraction_run_id='R',
                               elements=[heading, first, second], relationships=relationships)
        valid = Relationship(source_id='B', target_id='A', type='POSSIBLE_CONTINUATION_OF')
        snapshot([valid])
        for relationships in ([valid, valid],
                              [Relationship(source_id='A', target_id='A', type='RELATED_TO')],
                              [Relationship(source_id='A', target_id='B', type='POSSIBLE_CONTINUATION_OF')],
                              [Relationship(source_id='H', target_id='A', type='POSSIBLE_CONTINUATION_OF')],
                              [Relationship(source_id='A', target_id='B', type='FOOTNOTE_OF')]):
            with self.assertRaises(ValidationError):
                snapshot(relationships)
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
