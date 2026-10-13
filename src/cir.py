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

class CIRSnapshot(BaseModel):
    schema_version: str = '0.1'
    document_id: str
    source_revision_id: str
    extraction_run_id: str
    elements: list[Element]
    relationships: list[Relationship] = Field(default_factory=list)
    source_path: Optional[str] = None
    source_sha256: Optional[str] = None
    source_format: Optional[str] = None
    pipeline_fingerprint: Optional[str] = None
    extraction_metadata: dict[str, Any] = Field(default_factory=dict)
    pages: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode='after')
    def check_references(self):
        ids = [e.element_id for e in self.elements]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate element IDs')
        known = set(ids)
        parents = {e.element_id: e.parent_id for e in self.elements}
        for element in self.elements:
            if element.parent_id is not None and element.parent_id not in known:
                raise ValueError('Parent refers to missing element')
            seen = {element.element_id}
            parent = element.parent_id
            while parent is not None:
                if parent in seen:
                    raise ValueError('Parent references contain a cycle')
                seen.add(parent)
                parent = parents.get(parent)
        for rel in self.relationships:
            if rel.source_id not in known or rel.target_id not in known:
                raise ValueError('Relationship refers to missing element')
        return self
