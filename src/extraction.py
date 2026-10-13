"""Layout-aware native extraction. Format adapters precede CIR conversion.

Coordinates use PDF points with a top-left origin in unrotated page space.
Reading order is geometric (top-to-bottom, then left-to-right), not a semantic
guarantee for arbitrary multi-column layouts. No OCR or table inference is used.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

try:
    from .cir import CIRSnapshot, Element
    from .section_structure import assign_section_parents, SECTION_STRUCTURE_VERSION
    from .task_registry import TaskRegistry
except ImportError:  # Support the repository's unittest discovery convention.
    from cir import CIRSnapshot, Element
    from section_structure import assign_section_parents, SECTION_STRUCTURE_VERSION
    from task_registry import TaskRegistry


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def pipeline_manifest(metadata: dict[str, Any]) -> dict[str, Any]:
    """Include engine/options and executable pipeline code, excluding source paths."""
    root = Path(__file__).parent
    return {'identity_contract_version': '3', 'settings': metadata,
            'stages': ['native_extraction', 'section_structure', 'cir_conversion'],
            'code_sha256': {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                            for name in ('extraction.py', 'section_structure.py', 'cir.py')}}


class ExtractionError(ValueError):
    """The source cannot be extracted safely."""


class PageExtraction(BaseModel):
    page_number: int = Field(ge=1)
    width: float
    height: float
    rotation: int
    text: str
    blocks: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)


class ExtractedDocument(BaseModel):
    document_id: str
    source_version_id: str
    source_artifact_ref: str
    source_sha256: str
    source_format: str
    extraction_run_id: str
    pipeline_fingerprint: str
    extraction_metadata: dict[str, Any]
    pages: list[PageExtraction]

    def to_cir(self) -> CIRSnapshot:
        """Map source_version_id onto CIR's existing source_revision_id field."""
        elements = []
        for page in self.pages:
            for block in page.blocks:
                elements.append(Element(
                    element_id=block['element_id'], page_number=page.page_number,
                    order=block['order'], type=block['type'], text=block.get('text'),
                    source_ref=block['source_ref'], extraction_method='pdf_native',
                    structured_content={k: v for k, v in block.items()
                                        if k not in {'element_id', 'order', 'type', 'text', 'source_ref'}},
                ))
        relationships = assign_section_parents(elements, self.pages)
        return CIRSnapshot(
            document_id=self.document_id, source_revision_id=self.source_version_id,
            extraction_run_id=self.extraction_run_id, elements=elements, relationships=relationships,
            source_artifact_ref=self.source_artifact_ref, source_sha256=self.source_sha256,
            source_format=self.source_format, extraction_metadata=self.extraction_metadata,
            pipeline_fingerprint=self.pipeline_fingerprint,
            pages=[p.model_dump(exclude={'blocks'}) for p in self.pages],
        )


class FormatExtractor(Protocol):
    def describe(self) -> dict[str, Any]: ...
    def extract(self, content: bytes) -> tuple[list[PageExtraction], dict[str, Any]]: ...


class PDFExtractor:
    def describe(self):
        try:
            import fitz
        except ImportError as exc:
            raise RuntimeError('PDF extraction requires PyMuPDF; install requirements.txt') from exc
        return {'engine': 'pymupdf', 'engine_version': fitz.VersionBind,
                'adapter_version': '1', 'reading_order': 'geometric',
                'coordinate_system': 'unrotated_top_left_pdf_points',
                'ocr_enabled': False, 'table_detection_enabled': False}

    def extract(self, content: bytes) -> tuple[list[PageExtraction], dict[str, Any]]:
        try:
            import fitz
        except ImportError as exc:
            raise RuntimeError('PDF extraction requires PyMuPDF; install requirements.txt') from exc
        try:
            document = fitz.open(stream=content, filetype='pdf')
        except Exception as exc:
            raise ExtractionError('Invalid or unreadable PDF') from exc
        with document:
            if document.needs_pass:
                raise ExtractionError('Encrypted PDF requires a decrypted source')
            if not document.page_count:
                raise ExtractionError('PDF contains no pages')
            pages = []
            for page in document:
                # Keep span geometry and font attributes; discard embedded image
                # bytes to avoid duplicating the immutable raw source in JSON.
                raw = page.get_text('dict', sort=True)
                blocks = []
                for source_block in raw['blocks']:
                    block = {'bbox': list(source_block['bbox']),
                             'source_block_number': source_block['number']}
                    if source_block['type'] == 0:
                        lines = []
                        for line in source_block['lines']:
                            spans = [{key: value for key, value in span.items()}
                                     for span in line['spans']]
                            lines.append({'bbox': list(line['bbox']),
                                          'direction': list(line['dir']),
                                          'writing_mode': line['wmode'], 'spans': spans,
                                          'text': ''.join(s['text'] for s in spans)})
                        block.update(type='paragraph', lines=lines,
                                     text='\n'.join(line['text'] for line in lines))
                    elif source_block['type'] == 1:
                        block.update(type='image', image_metadata={
                            key: source_block[key] for key in
                            ('width', 'height', 'ext', 'colorspace', 'xres', 'yres', 'transform')
                            if key in source_block})
                    else:
                        raise ExtractionError(f'Unsupported block type on page {page.number + 1}')
                    blocks.append(block)
                text = '\n\n'.join(b['text'] for b in blocks if 'text' in b)
                pages.append(PageExtraction(
                    page_number=page.number + 1, width=raw['width'], height=raw['height'],
                    rotation=page.rotation, text=text, blocks=blocks,
                    warnings=[] if text.strip() else ['no_embedded_text; OCR may be required'],
                ))
            return pages, self.describe()


class ExtractionService:
    def __init__(self, adapters: dict[str, FormatExtractor] | None = None,
                 run_registry: TaskRegistry | None = None):
        self.adapters = {'.pdf': PDFExtractor()} if adapters is None else adapters
        if run_registry is None:
            directory = Path(__file__).resolve().parents[1] / 'output' / 'extraction'
            directory.mkdir(parents=True, exist_ok=True)
            run_registry = TaskRegistry(str(directory / 'runs.sqlite'))
        self.run_registry = run_registry

    def plan_run(self, source_path: str | Path, document_id: str, source_version_id: str,
                *, expected_sha256: str | None = None,
                extraction_run_id: str | None = None) -> dict[str, Any]:
        """Coordinator: persist the contract before any adapter extraction."""
        if not document_id.strip() or not source_version_id.strip():
            raise ExtractionError('document_id and source_version_id must be non-empty')
        if extraction_run_id is not None and not extraction_run_id.strip():
            raise ExtractionError('extraction_run_id must be non-empty when supplied')
        path = Path(source_path).expanduser().resolve(strict=True)
        adapter = self.adapters.get(path.suffix.lower())
        if adapter is None:
            raise ExtractionError(f'Unsupported source format: {path.suffix}')
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if expected_sha256 is not None and digest != expected_sha256.lower():
            raise ExtractionError('Source SHA-256 does not match expected hash')
        metadata = {**adapter.describe(), 'section_structure_version': SECTION_STRUCTURE_VERSION}
        manifest = pipeline_manifest(metadata)
        pipeline_digest = fingerprint(manifest)
        default_run_id = fingerprint([document_id, source_version_id, digest, pipeline_digest])
        run_id = extraction_run_id if extraction_run_id is not None else default_run_id
        try:
            self.run_registry.bind_extraction_run(run_id, document_id, source_version_id,
                                                  digest, pipeline_digest, pipeline_manifest=manifest,
                                                  source_format=path.suffix.lower()[1:])
        except ValueError as exc:
            raise ExtractionError(str(exc)) from exc
        return self.run_registry.get_run_contract(run_id)

    def execute_run(self, source_path: str | Path, extraction_run_id: str) -> ExtractedDocument:
        """Worker: load a registered contract, verify it, then extract. Never plans."""
        contract = self.run_registry.get_run_contract(extraction_run_id)
        if contract is None:
            raise ExtractionError('Worker requires a preplanned extraction run')
        path = Path(source_path).expanduser().resolve(strict=True)
        adapter = self.adapters.get('.' + contract['source_format'])
        if adapter is None or path.suffix.lower()[1:] != contract['source_format']:
            raise ExtractionError('Source format does not match planned run')
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        metadata = {**adapter.describe(), 'section_structure_version': SECTION_STRUCTURE_VERSION}
        manifest = pipeline_manifest(metadata)
        pipeline_digest = fingerprint(manifest)
        if (digest != contract['source_sha256'] or pipeline_digest != contract['pipeline_fingerprint']
                or manifest != contract['pipeline_manifest']):
            raise ExtractionError('Worker source or pipeline does not match planned run')
        pages, actual_metadata = adapter.extract(content)
        if actual_metadata != adapter.describe():
            raise ExtractionError('Adapter execution settings differ from planned pipeline')
        if not pages or [p.page_number for p in pages] != list(range(1, len(pages) + 1)):
            raise ExtractionError('Adapter must return every page in contiguous order')
        document_id, source_version_id = contract['document_id'], contract['source_version_id']
        run_id = extraction_run_id
        artifact_ref = f'urn:sha256:{digest}'
        default_run_id = fingerprint([document_id, source_version_id, digest, pipeline_digest])
        element_namespace = fingerprint([run_id, default_run_id])
        metadata = {**metadata, 'pipeline_manifest': manifest}
        for page in pages:
            page.provenance = {'document_id': document_id, 'source_version_id': source_version_id,
                               'source_artifact_ref': artifact_ref, 'source_sha256': digest,
                               'extraction_run_id': run_id, 'pipeline_fingerprint': pipeline_digest,
                               'page_number': page.page_number,
                               'source_ref': f'{artifact_ref}#page={page.page_number}'}
            for order, block in enumerate(page.blocks):
                block.update(order=order, element_id=f'{element_namespace}:p{page.page_number}:b{order}',
                             source_ref=f'{artifact_ref}#page={page.page_number}&block={order}',
                             provenance=dict(page.provenance))
        return ExtractedDocument(
            document_id=document_id, source_version_id=source_version_id, source_artifact_ref=artifact_ref,
            source_sha256=digest, source_format=path.suffix.lower()[1:], extraction_run_id=run_id,
            pipeline_fingerprint=pipeline_digest,
            extraction_metadata=metadata, pages=pages,
        )

    def extract(self, source_path, document_id, source_version_id, *, expected_sha256=None,
                extraction_run_id=None):
        """Local coordinator convenience; workers should call execute_run directly."""
        contract = self.plan_run(source_path, document_id, source_version_id,
                                 expected_sha256=expected_sha256, extraction_run_id=extraction_run_id)
        return self.execute_run(source_path, contract['extraction_run_id'])


def extract_document(source_path: str | Path, document_id: str, source_version_id: str,
                     *, expected_sha256: str | None = None,
                     extraction_run_id: str | None = None) -> ExtractedDocument:
    """Retries reuse extraction_run_id; new logical extractions use a fresh ID.

    Omitting it retains deterministic content-derived identity for local use.
    Caller must persist the ID and pin source hash/engine settings across retries.
    """
    return ExtractionService().extract(source_path, document_id, source_version_id,
                                       expected_sha256=expected_sha256,
                                       extraction_run_id=extraction_run_id)
