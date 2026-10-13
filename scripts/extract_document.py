"""Export native page extraction or its CIR projection as JSON."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.extraction import ExtractionService
from src.task_registry import TaskRegistry
import json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source_path')
    parser.add_argument('--document-id')
    parser.add_argument('--source-version-id')
    parser.add_argument('--expected-sha256')
    parser.add_argument('--extraction-run-id', help='Persist and reuse for retries; use a fresh ID for a new extraction')
    parser.add_argument('--format', choices=['extraction', 'cir'], default='cir')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--mode', choices=['local', 'plan', 'worker'], default='local')
    parser.add_argument('--registry', type=Path, help='Shared durable SQLite registry path')
    args = parser.parse_args()
    if args.mode != 'worker' and (not args.document_id or not args.source_version_id):
        parser.error('--document-id and --source-version-id are required for local/plan modes')
    if args.mode == 'worker' and not args.extraction_run_id:
        parser.error('--extraction-run-id is required for worker mode')
    if args.mode == 'worker' and (args.document_id or args.source_version_id or args.expected_sha256):
        parser.error('Worker inputs come from the registry; omit document/version/hash overrides')
    service = ExtractionService(run_registry=TaskRegistry(str(args.registry)) if args.registry else None)
    if args.mode == 'plan':
        contract = service.plan_run(args.source_path, args.document_id, args.source_version_id,
                                    expected_sha256=args.expected_sha256, extraction_run_id=args.extraction_run_id)
        payload = json.dumps(contract, indent=2)
    else:
        result = (service.execute_run(args.source_path, args.extraction_run_id) if args.mode == 'worker'
                  else service.extract(args.source_path, args.document_id, args.source_version_id,
                                       expected_sha256=args.expected_sha256, extraction_run_id=args.extraction_run_id))
        payload = (result.to_cir() if args.format == 'cir' else result).model_dump_json(indent=2)
    if args.output:
        args.output.write_text(payload + '\n', encoding='utf-8')
    else:
        print(payload)


if __name__ == '__main__':
    main()
