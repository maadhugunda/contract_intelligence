from enum import Enum
from typing import Any,Optional,List
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
    security_source_ids: List[str] = Field(default_factory=list)

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

    @model_validator(mode='after')
    def check_references(self):
        ids = [e.element_id for e in self.elements]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate element IDs')
        known = set(ids)
        for rel in self.relationships:
            if rel.source_id not in known or rel.target_id not in known:
                raise ValueError('Relationship refers to missing element')
        return self
