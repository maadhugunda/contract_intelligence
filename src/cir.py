from __future__ import annotations

from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field, model_validator

class ElementType(str, Enum):
    HEADING = 'heading'
    PARAGRAPH = 'paragraph'
    TABLE = 'table'
    IMAGE = 'image'
    FOOTNOTE = 'footnote'

class Element(BaseModel):
    element_id: str
    page_number: int = Field(ge=1)
    order: int = Field(ge=0)
    type: ElementType
    text: Optional[str] = None
    structured_content: Optional[dict[str, Any]] = None
    parent_id: Optional[str] = None
    source_ref: Optional[str] = None
    extraction_method: str
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    security_source_ids: list[str] = Field(default_factory=list)

class Relationship(BaseModel):
    source_id: str
    target_id: str
    type: str
    confidence: Optional[float] = Field(default=None, ge=0, le=1)

class CIRPage(BaseModel):
    page_number: int = Field(ge=1, strict=True)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    rotation: int = 0
    text: str = ''
    warnings: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)

class CIRSnapshot(BaseModel):
    schema_version: str = '0.1'
    document_id: str
    source_revision_id: str
    extraction_run_id: str
    elements: list[Element]
    relationships: list[Relationship] = Field(default_factory=list)
    source_artifact_ref: Optional[str] = None
    source_sha256: Optional[str] = None
    source_format: Optional[str] = None
    pipeline_fingerprint: Optional[str] = None
    extraction_metadata: dict[str, Any] = Field(default_factory=dict)
    pages: list[CIRPage] = Field(default_factory=list)

    @model_validator(mode='after')
    def check_references(self):
        ids = [e.element_id for e in self.elements]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate element IDs')
        known = set(ids)
        by_id = {e.element_id: e for e in self.elements}
        page_numbers = [p.page_number for p in self.pages]
        if self.pages and page_numbers != list(range(1, len(self.pages) + 1)):
            raise ValueError('Pages must be unique, contiguous and ordered from 1')
        if self.source_artifact_ref is not None:
            if (not self.source_sha256 or len(self.source_sha256) != 64
                    or any(c not in '0123456789abcdef' for c in self.source_sha256)
                    or self.source_artifact_ref != f'urn:sha256:{self.source_sha256}'):
                raise ValueError('Artifact reference must match the source SHA-256')
            if not self.pages:
                raise ValueError('Artifact-backed CIR requires page records')
            for page in self.pages:
                expected = f'{self.source_artifact_ref}#page={page.page_number}'
                if (page.provenance.get('source_ref') != expected
                        or page.provenance.get('page_number') != page.page_number
                        or page.provenance.get('source_artifact_ref') != self.source_artifact_ref
                        or page.provenance.get('source_sha256') != self.source_sha256
                        or page.provenance.get('document_id') != self.document_id
                        or page.provenance.get('source_version_id') != self.source_revision_id
                        or page.provenance.get('extraction_run_id') != self.extraction_run_id
                        or page.provenance.get('pipeline_fingerprint') != self.pipeline_fingerprint):
                    raise ValueError('Page provenance does not match snapshot')
        locations = set()
        parents = {e.element_id: e.parent_id for e in self.elements}
        for element in self.elements:
            if self.pages and element.page_number not in page_numbers:
                raise ValueError('Element refers to missing page')
            location = (element.page_number, element.order)
            if location in locations:
                raise ValueError('Duplicate element order within page')
            locations.add(location)
            if self.source_artifact_ref is not None:
                expected = f'{self.source_artifact_ref}#page={element.page_number}&block={element.order}'
                if element.source_ref != expected:
                    raise ValueError('Element source reference does not match page/block')
                page = self.pages[element.page_number - 1]
                if (element.structured_content or {}).get('provenance') != page.provenance:
                    raise ValueError('Element provenance does not match page')
            if element.parent_id is not None and element.parent_id not in known:
                raise ValueError('Parent refers to missing element')
            seen = {element.element_id}
            parent = element.parent_id
            while parent is not None:
                if parent in seen:
                    raise ValueError('Parent references contain a cycle')
                seen.add(parent)
                parent = parents.get(parent)
            if element.parent_id is not None:
                owner = by_id[element.parent_id]
                if owner.type != ElementType.HEADING:
                    raise ValueError('Section parent must be a heading')
                if (owner.page_number, owner.order) >= (element.page_number, element.order):
                    raise ValueError('Section parent must precede child')
        relationships = set()
        for rel in self.relationships:
            if rel.source_id not in known or rel.target_id not in known:
                raise ValueError('Relationship refers to missing element')
            key = (rel.source_id, rel.target_id, rel.type)
            if key in relationships or rel.source_id == rel.target_id or not rel.type.strip():
                raise ValueError('Duplicate, self-referencing or empty relationship')
            relationships.add(key)
            source, target = by_id[rel.source_id], by_id[rel.target_id]
            if rel.type == 'POSSIBLE_CONTINUATION_OF':
                if (source.type != ElementType.PARAGRAPH or target.type != ElementType.PARAGRAPH
                        or source.page_number != target.page_number + 1
                        or not source.parent_id or source.parent_id != target.parent_id):
                    raise ValueError('Continuation must connect adjacent-page paragraphs in the same section')
            if rel.type == 'FOOTNOTE_OF' and source.type != ElementType.FOOTNOTE:
                raise ValueError('FOOTNOTE_OF source must be a footnote')
        return self
