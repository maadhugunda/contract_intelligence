# Healthcare Contract Intelligence — Phase A starter

A fictional healthcare-focused B2B contract intelligence capstone. No patient data or real parties.

## D0 dataset

`datasets/d0/contracts/` contains five digitally generated PDFs: MSA, BAA, SOW, pricing schedule, amendment. `datasets/d0/manifest.json` records SHA-256 hashes; `datasets/d0/ground_truth/questions.json` provides six independent evaluation cases including amendment supersession and abstention.

## Existing CIR v0.3

`src/cir.py` and `src/task_registry.py` are carried forward from the prior CIR v0.3 archive, along with its tests. They are NOT yet integrated into the new extraction flow.

## Run first extraction

```bash
python -m pip install pymupdf pydantic
python scripts/inspect_pdf.py datasets/d0/contracts/HC001-MSA.pdf
python -m unittest discover -s src -p 'test_*.py' -v
```

## Day 1 engineering assignment

1. Create a private GitHub repository and commit this starter (do not commit credentials).
2. Implement `extract_document(pdf_path, document_id, source_revision_id)` in `src/extraction.py`, returning a CIR-compatible document/page/element representation with page references and provenance.
3. Write tests verifying all five PDFs can be read, document IDs match, and source hashes are verified against the manifest.
4. Preserve text order and section numbering. Do not yet add OCR, embeddings, or LLM calls.
5. Open a pull request and share the diff/repository access for review.

**Definition of done:** deterministic extraction of all five PDFs with page numbers, text provenance, passing tests, and no silently dropped pages. A first grounded Q&A is the subsequent milestone.

## Architecture guardrails

Modular monolith; open-source first; immutable raw source; CIR distinct from canonical legal knowledge; applicability is not equivalent to retrieval; never use employer data or real PHI.
