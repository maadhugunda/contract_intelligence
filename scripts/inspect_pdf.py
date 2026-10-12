import hashlib
import sys
from pathlib import Path
import fitz

path = Path(sys.argv[1])
with fitz.open(path) as doc:
    print(f'document={path.name} pages={len(doc)} sha256={hashlib.sha256(path.read_bytes()).hexdigest()}')
    for idx, page in enumerate(doc, 1):
        text = page.get_text(sort=True)
        print(f'page={idx} chars={len(text)} preview={text[:150]!r}')
