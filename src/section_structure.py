"""Conservative section inference for standalone, styled numeric headings.

Numbering alone is insufficient: numbered body lists must not become sections.
Unnumbered headings, mixed heading/body blocks and lettered sections are left
unclassified. This heuristic preserves the original text and geometry.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import re

try:
    from .cir import Element, ElementType, Relationship
except ImportError:
    from cir import Element, ElementType, Relationship

SECTION_STRUCTURE_VERSION = '2'
NUMBERED_HEADING = re.compile(r'^(\d+(?:\.\d+)*)(?:[.)]|\s)\s*(\S.*)$')


def classify_page_roles(elements, pages):
    """Preserve every block; repetition plus consistent margin geometry is evidence.

Only explicit page-number text is classified without repetition. Repeated legal
notes are deliberately excluded from running-header/footer inference.
"""
    groups = defaultdict(list)
    for element in elements:
        content = element.structured_content or {}
        element.structured_content = content
        text = (element.text or '').strip()
        if re.match(r'^(?:note|condition|important|point to (?:be )?noted|subject to)\b', text, re.I):
            content.update(page_role='note', role_detection_method='explicit_note_prefix')
            continue
        spans = [s for line in content.get('lines', []) for s in line.get('spans', [])]
        if re.match(r'^\d+[.)]\s+\S', text) and spans and max(s.get('size', 0) for s in spans) <= 9:
            content.update(page_role='possible_footnote', role_detection_method='small_numbered_text')
            continue
        page = pages.get(element.page_number)
        bbox = content.get('bbox')
        if not page or not bbox or not text or len(text) > 160:
            continue
        height, width = page.height, page.width
        if not height or not width:
            continue
        band = 'header' if bbox[3] <= height * .08 else 'footer' if bbox[1] >= height * .92 else None
        if band is None:
            continue
        if re.fullmatch(r'(?:page\s+)?\d+(?:\s+(?:of|/)\s+\d+)?', text, re.I):
            content.update(page_role=band, role_detection_method='page_number_in_margin')
            continue
        normalized = re.sub(r'\s+', ' ', text.casefold())
        # Normalize only explicit page counters, not arbitrary legal numbers.
        normalized = re.sub(r'\bpage \d+(?: of \d+)?\b', 'page #', normalized)
        key = (band, normalized, round(bbox[0] / width, 1), round(bbox[1] / height, 2))
        groups[key].append(element)
    for (band, *_), candidates in groups.items():
        count = len({e.page_number for e in candidates})
        if count >= 2 and count >= len(pages) * .6:
            for element in candidates:
                element.structured_content.update(page_role=band,
                    role_detection_method='repeated_text_at_consistent_margin_position')


def paragraph_continuations(elements, pages):
    """Return tentative links, never merge source fragments or assert certainty."""
    body = defaultdict(list)
    for element in elements:
        if (element.structured_content or {}).get('page_role') not in {'header', 'footer', 'note', 'possible_footnote'}:
            body[element.page_number].append(element)
    relationships = []
    for number in sorted(body):
        if not body.get(number + 1) or number not in pages or number + 1 not in pages:
            continue
        previous, current = body[number][-1], body[number + 1][0]
        a, b = previous.structured_content or {}, current.structured_content or {}
        text_a, text_b = (previous.text or '').strip(), (current.text or '').strip()
        if (previous.type != ElementType.PARAGRAPH or current.type != ElementType.PARAGRAPH
                or not previous.parent_id or previous.parent_id != current.parent_id
                or not text_a or not text_b or text_a[-1] in '.!?;:'
                or not text_b[0].islower() or not a.get('bbox') or not b.get('bbox')):
            continue
        if (a['bbox'][1] < pages[number].height * .65
                or b['bbox'][3] > pages[number + 1].height * .35
                or abs(a['bbox'][0] - b['bbox'][0]) > 12):
            continue
        def style(content, last):
            spans = [s for line in content.get('lines', []) for s in line.get('spans', [])
                     if s.get('text', '').strip()]
            return (spans[-1] if last else spans[0]) if spans else {}
        end, start = style(a, True), style(b, False)
        if (not end or not start or abs(end.get('size', 0) - start.get('size', 0)) > .5
                or end.get('font') != start.get('font')):
            continue
        b['continuation_evidence'] = ['adjacent_pages', 'same_section', 'matching_style_and_indent',
                                      'page_boundary_position', 'unfinished_text_then_lowercase']
        relationships.append(Relationship(source_id=current.element_id, target_id=previous.element_id,
                                          type='POSSIBLE_CONTINUATION_OF', confidence=.75))
    return relationships


def assign_section_parents(elements: list[Element], pages=None) -> list[Relationship]:
    """Annotate newly constructed CIR elements in reading order, across pages."""
    pages = {p.page_number: p for p in (pages or [])}
    classify_page_roles(elements, pages)
    sizes = Counter()
    for element in elements:
        if (element.structured_content or {}).get('page_role') in {'header', 'footer'}:
            continue
        for line in (element.structured_content or {}).get('lines', []):
            for span in line.get('spans', []):
                if span.get('text', '').strip():
                    sizes[round(span.get('size', 0), 2)] += len(span['text'])
    body_size = sizes.most_common(1)[0][0] if sizes else 0
    stack = []
    for element in elements:
        content = element.structured_content or {}
        spans = [span for line in content.get('lines', [])
                 for span in line.get('spans', []) if span.get('text', '').strip()]
        text = (element.text or '').strip()
        if content.get('page_role') in {'header', 'footer'}:
            element.parent_id = None
            continue
        match = NUMBERED_HEADING.match(text)
        styled = bool(spans) and all(
            (span.get('flags', 0) & 16 or 'bold' in span.get('font', '').lower())
            and span.get('size', 0) >= body_size for span in spans)
        if (element.type == ElementType.PARAGRAPH and match and styled
                and content.get('page_role') not in {'note', 'possible_footnote'}
                and '\n' not in text and len(text) <= 160):
            number = match.group(1)
            parts = number.split('.')
            level = len(parts)
            while stack and stack[-1][0] >= level:
                stack.pop()
            # A numbered subsection belongs only to its matching numeric ancestor.
            ancestors = [entry for entry in stack if number.startswith(entry[1] + '.')]
            element.parent_id = ancestors[-1][2] if ancestors else None
            element.type = ElementType.HEADING
            content.update(heading_level=level, section_number=number,
                           section_detection_method='numbered_bold_heading')
            element.structured_content = content
            stack.append((level, number, element.element_id))
        elif stack:
            element.parent_id = stack[-1][2]
    return paragraph_continuations(elements, pages)
