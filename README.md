# Healthcare Contract Intelligence — Phase A starter

A fictional healthcare-focused B2B contract intelligence capstone. No patient data or real parties.

## D0 dataset

`datasets/d0/contracts/` contains five digitally generated PDFs: MSA, BAA, SOW, pricing schedule, amendment. `datasets/d0/manifest.json` records SHA-256 hashes; `datasets/d0/ground_truth/questions.json` provides six independent evaluation cases including amendment supersession and abstention.

## Existing CIR

`src/cir.py` defines CIR schema 0.1. The extraction service converts its page-level intermediate representation into this model, with additive source metadata and page records. `src/task_registry.py` provides task leasing separately.

## Run first extraction

```bash
python -m pip install -r requirements.txt
python scripts/inspect_pdf.py datasets/d0/contracts/HC001-MSA.pdf
python -m unittest discover -s src -p 'test_*.py' -v
```

## PDF extraction service

```python
from src.extraction import extract_document

document = extract_document(
    'datasets/d0/contracts/HC001-MSA.pdf',
    document_id='HC001-MSA',
    source_version_id='v1',
    # Optional: expected_sha256=the_expected_source_hash,
)
snapshot = document.to_cir()
payload = snapshot.model_dump_json(indent=2)
```

The service reads and hashes the same source bytes, extracts every page, and then
converts to CIR explicitly. `source_version_id` maps to CIR's `source_revision_id`.
Source provenance uses `source_artifact_ref = urn:sha256:<source hash>` rather
than a local file path. Page references append `#page=N`; element references append
`#page=N&block=B`. These identifiers survive copying or relocating the PDF.
The input path is only used to load bytes and is not emitted in extraction or CIR.
An artifact resolver/storage layer must map this identity to actual source bytes;
the URN is not itself a download URL.

CIR validates ordered contiguous page records, element page membership and unique
per-page order, artifact hashes and page/block provenance, heading parents, and
relationship endpoints. Duplicate/self relationships are rejected. Continuation
links must connect adjacent-page paragraphs within the same section. Legacy CIR
without an artifact reference may omit pages; new artifact-backed output cannot.
`pages` now contains typed `CIRPage` models; access `snapshot.pages[0].text` in Python.
Regenerate legacy path-based outputs to migrate to artifact provenance.

Each page retains text, dimensions, rotation and source provenance. Ordered text
blocks retain lines, spans, font attributes and bounding boxes; image blocks retain
placement and metadata. CIR elements carry this layout in `structured_content`.
Blank or image-only pages remain present with a warning. Source files are never
modified. Run and element IDs are deterministic for the same document, version,
source bytes and extraction engine metadata. Workers should pass an explicit
`extraction_run_id` created and persisted when the logical extraction is queued.
Every retry reuses that ID, including after a crash; lease generations and worker
attempt IDs must not replace it. A deliberate new extraction of the same PDF gets
a fresh run ID, producing distinct element IDs. Use `--extraction-run-id` in the
CLI. Omitting the ID retains deterministic content-derived identity for local use.
Run IDs are now atomically bound in a durable `extraction_runs` registry to the
document/version, source SHA-256 and pipeline fingerprint. Reusing a run ID after
any of these inputs change raises `ExtractionError`; it does not silently create
new element IDs under the old run. The pipeline fingerprint includes engine/version,
adapter/settings, section inference version and hashes of extraction, section and
CIR model code. Paths and worker/lease identities are excluded. Output includes
the fingerprint and its manifest for audit. Source bytes are hashed and extracted
from the same in-memory copy; raw source remains untouched.

The default registry is `output/extraction/runs.sqlite`. Workers participating in
the same deployment must share a durable registry; inject `TaskRegistry` through
`ExtractionService(run_registry=...)` for a configured database location. SQLite
binding uses a transaction so concurrent first registrations cannot disagree.
Bindings from before this contract cannot be reconstructed automatically; create
new runs for legacy outputs. This version changes generated IDs.

Pin the engine/settings and pass `expected_sha256` on retries. This service
currently re-extracts the full document and checks the binding before returning it;
stable block IDs support idempotent downstream writes, not partial extraction yet.

```python
# Create and persist this once when queuing the job (for example, a UUID).
logical_run_id = 'extraction-job-001'
document = extract_document(
    'datasets/d0/contracts/HC001-MSA.pdf', 'HC001-MSA', 'v1',
    extraction_run_id=logical_run_id,
)
# A retry uses logical_run_id again. A new job receives a new ID.
```

Reading order uses PyMuPDF's geometric block ordering. Section numbers are kept
verbatim. During CIR conversion, `src/section_structure.py` detects standalone
numeric headings with bold styling (such as `1. Parties` or `1.1 Customer`) and
assigns following elements to them through `parent_id`. A section continues
across pages until another heading closes it. Numeric subsections attach to their
matching ancestor; regular numbered lists do not become headings. Small-print
text retains its section parent. CIR validates missing parents and hierarchy cycles.
This is a heuristic: unnumbered/lettered headings, mixed heading-and-body blocks,
are not supported yet. Raw page extraction
remains unchanged; section annotations are added only to CIR elements.

Running headers/footers are annotated with `structured_content.page_role` when
short text repeats at a consistent position in the top/bottom 8% of at least two
pages and 60% of the document's pages. Explicit page counters in these margins
also qualify. These elements remain in CIR but have no section parent. Explicit
note/condition prefixes are preserved as notes even in margins; small numbered
text is annotated as `possible_footnote` without guessing its reference. Unique
or uncertain margin text remains in its section. These are conservative heuristics,
not reliable classification for every document layout.

Cross-page paragraph fragments can receive `POSSIBLE_CONTINUATION_OF` relationships
(source is the later fragment, target is the earlier fragment). Evidence requires
adjacent pages, the same section, matching font/size and indentation, page-boundary
placement, an unfinished preceding sentence and a lowercase continuation. Header,
footer, note and possible-footnote blocks are excluded from candidate selection.
Links have heuristic confidence 0.75, not a calibrated probability. Text is never
merged or dropped. Missing evidence leaves fragments separate under their section.
Complex multi-column reading order needs further work. Tables remain native text
blocks, without reconstructed cells. OCR and vector graphics extraction are not
implemented. Coordinates use PDF points in unrotated page space, with a top-left
origin. Format adapters implement `FormatExtractor` and can be registered with
`ExtractionService` when additional formats are supported.

Export CIR JSON (use `--format extraction` for the intermediate representation):

```bash
python scripts/extract_document.py datasets/d0/contracts/HC001-MSA.pdf \
  --document-id HC001-MSA --source-version-id v1 --output cir.json
```

Integration tests verify all five dataset hashes, page coverage, text preservation
and deterministic results. They are explicitly skipped if PyMuPDF is unavailable.

## Day 1 engineering assignment

1. Create a private GitHub repository and commit this starter (do not commit credentials).
2. Implement `extract_document(pdf_path, document_id, source_revision_id)` in `src/extraction.py`, returning a CIR-compatible document/page/element representation with page references and provenance.
3. Write tests verifying all five PDFs can be read, document IDs match, and source hashes are verified against the manifest.
4. Preserve text order and section numbering. Do not yet add OCR, embeddings, or LLM calls.
5. Open a pull request and share the diff/repository access for review.

**Definition of done:** deterministic extraction of all five PDFs with page numbers, text provenance, passing tests, and no silently dropped pages. A first grounded Q&A is the subsequent milestone.

## Architecture guardrails

Modular monolith; open-source first; immutable raw source; CIR distinct from canonical legal knowledge; applicability is not equivalent to retrieval; never use employer data or real PHI.
