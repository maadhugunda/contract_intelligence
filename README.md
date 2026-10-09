# Contract Intelligence — CIR starter

Initial Pydantic CIR schema (v0.1), with element identity, extraction provenance, security source identifiers, relationships, and a versioned snapshot.

Install: `pip install 'pydantic>=2,<3'`

Test: `python -m unittest discover -s . -p 'test_*.py'`

This is a starter contract, not a complete production CIR. Next: document/page models, immutable snapshot writer, projection store, adapters, richer source geometry, and extraction lineage.

## Task registry (v0.1)
`task_registry.py` adds SQLite-backed task leases, monotonically increasing fencing generations, renewal, and conditional result publication. It assumes artifacts are written immutably and validated before `commit_result`; manifest publication and artifact validation are next milestones.

Run: `python -m unittest discover -v`
