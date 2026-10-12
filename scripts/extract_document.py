"""Export native page extraction or its CIR projection as JSON."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.extraction import extract_document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source_path')
    parser.add_argument('--document-id', required=True)
    parser.add_argument('--source-version-id', required=True)
    parser.add_argument('--expected-sha256')
    parser.add_argument('--format', choices=['extraction', 'cir'], default='cir')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = extract_document(args.source_path, args.document_id, args.source_version_id,
                              expected_sha256=args.expected_sha256)
    payload = (result.to_cir() if args.format == 'cir' else result).model_dump_json(indent=2)
    if args.output:
        args.output.write_text(payload + '\n', encoding='utf-8')
    else:
        print(payload)


if __name__ == '__main__':
    main()
