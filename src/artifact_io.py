"""Atomic local exports; this is not leased or fenced artifact publication."""
import os
from pathlib import Path
import tempfile


def write_text_atomic(destination: Path, text: str) -> None:
    """Flush a sibling temporary file before atomically replacing destination.

    Readers observe either the previous file or the complete new file. Concurrent
    exporters are last-writer-wins; callers needing fencing must use a publisher.
    """
    destination = Path(destination)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n',
                                         dir=destination.parent, prefix='.export-',
                                         suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
