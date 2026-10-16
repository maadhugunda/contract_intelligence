import unittest
from types import SimpleNamespace

from pydantic import ValidationError
from cir import CIRSnapshot, Element
from section_structure import assign_section_parents


def block(id, text, bold=False, page=1, size=None):
    return Element(element_id=id, page_number=page, order=0, type='paragraph',
                   text=text, extraction_method='pdf_native', structured_content={
                       'lines': [{'spans': [{'text': text, 'size': size or (12 if bold else 10),
                                            'flags': 16 if bold else 0}]}]})


class SectionStructureTests(unittest.TestCase):
    def multipage_example(self):
        def positioned(id, text, y, page, bold=False, size=None):
            element = block(id, text, bold, page, size)
            element.structured_content['bbox'] = [50, y, 450, y + 12]
            return element
        elements = [positioned('header1', 'Contract ACME', 20, 1),
                    positioned('heading', '1. Conditions', 90, 1, True),
                    positioned('body1', 'Customer shall provide the required access and maintain', 680, 1),
                    positioned('note', 'Note: prior written consent is required.', 880, 1, size=8),
                    positioned('footnote', '1. Subject to local requirements.', 905, 1, size=8),
                    positioned('footer1', 'Page 1 of 2', 950, 1, size=8),
                    positioned('header2', 'Contract ACME', 20, 2),
                    positioned('body2', 'the necessary permissions throughout the service period.', 100, 2),
                    positioned('small', 'Written approval remains necessary.', 880, 2, size=8),
                    positioned('footer2', 'Page 2 of 2', 950, 2, size=8)]
        pages = [SimpleNamespace(page_number=n, width=600, height=1000) for n in (1, 2)]
        return elements, pages

    def test_headers_notes_footnotes_and_paragraph_continuation(self):
        elements, pages = self.multipage_example()
        original = [e.text for e in elements]
        relationships = assign_section_parents(elements, pages)
        counts = {}
        for element in elements:
            element.order = counts.get(element.page_number, 0)
            counts[element.page_number] = element.order + 1
        by_id = {e.element_id: e for e in elements}
        for id in ('header1', 'header2', 'footer1', 'footer2'):
            self.assertIsNone(by_id[id].parent_id)
            self.assertIn(by_id[id].structured_content['page_role'], ('header', 'footer'))
        for id in ('body1', 'body2', 'note', 'footnote', 'small'):
            self.assertEqual(by_id[id].parent_id, 'heading')
        self.assertEqual(by_id['note'].structured_content['page_role'], 'note')
        self.assertEqual(by_id['footnote'].structured_content['page_role'], 'possible_footnote')
        self.assertEqual([e.text for e in elements], original)
        self.assertEqual(len(relationships), 1)
        self.assertEqual((relationships[0].source_id, relationships[0].target_id,
                          relationships[0].type), ('body2', 'body1', 'POSSIBLE_CONTINUATION_OF'))
        CIRSnapshot(document_id='D', source_revision_id='V', extraction_run_id='R',
                    elements=elements, relationships=relationships)

    def test_no_continuation_when_evidence_is_missing(self):
        for case in ('completed_sentence', 'new_section', 'different_font', 'blank_page'):
            with self.subTest(case=case):
                elements, pages = self.multipage_example()
                by_id = {e.element_id: e for e in elements}
                if case == 'completed_sentence':
                    by_id['body1'].text += '.'
                elif case == 'new_section':
                    heading = block('new', '2. Services', True, page=2)
                    elements.insert(elements.index(by_id['body2']), heading)
                elif case == 'different_font':
                    by_id['body2'].structured_content['lines'][0]['spans'][0]['font'] = 'OtherFont'
                else:
                    for element in elements:
                        if element.page_number == 2:
                            element.page_number = 3
                    pages.append(SimpleNamespace(page_number=3, width=600, height=1000))
                self.assertEqual(assign_section_parents(elements, pages), [])

    def test_repeated_note_in_margin_stays_in_section(self):
        elements, pages = self.multipage_example()
        for element in elements:
            if element.element_id.startswith('footer'):
                element.text = 'Condition: written consent is required.'
        assign_section_parents(elements, pages)
        for element in elements:
            if element.element_id.startswith('footer'):
                self.assertEqual(element.parent_id, 'heading')
                self.assertEqual(element.structured_content['page_role'], 'note')

    def test_nested_sections_page_continuation_and_boundary(self):
        elements = [block('pre', 'Introductory text before any numbered heading.'),
                    block('h1', '1. Parties', True),
                    block('p1', 'The agreement is between the customer and the vendor.'),
                    block('h11', '1.1 Customer', True),
                    block('p11', 'Customer obligations continue on the following page.', page=2),
                    block('h2', '2. Services', True, page=2),
                    block('p2', 'Services are described in the statement of work.', page=2),
                    block('footer', 'Page 2', page=2, size=8)]
        assign_section_parents(elements)
        self.assertEqual([e.parent_id for e in elements],
                         [None, None, 'h1', 'h1', 'h11', None, 'h2', 'h2'])
        self.assertEqual(elements[1].type, 'heading')
        self.assertEqual(elements[3].structured_content['heading_level'], 2)

    def test_numbered_list_is_not_heading(self):
        elements = [block('h', '1. Parties', True),
                    block('p', '1. The customer must provide access to the service.'),
                    block('p2', 'Additional regular body text belongs to the same section.')]
        assign_section_parents(elements)
        self.assertEqual(elements[1].type, 'paragraph')
        self.assertEqual(elements[1].parent_id, 'h')

    def test_missing_parent_and_cycles_rejected(self):
        for parent in ('missing', 'e'):
            element = block('e', 'Body')
            element.parent_id = parent
            with self.assertRaises(ValidationError):
                CIRSnapshot(document_id='D', source_revision_id='V', extraction_run_id='R', elements=[element])
        a, b = block('a', 'A'), block('b', 'B')
        a.parent_id, b.parent_id = 'b', 'a'
        with self.assertRaises(ValidationError):
            CIRSnapshot(document_id='D', source_revision_id='V', extraction_run_id='R', elements=[a, b])
