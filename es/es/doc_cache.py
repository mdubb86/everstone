"""Cache layout for converted documents.

Artifacts live in a `.es/<doc_id>/` subdirectory of Hermes's own document
cache. We reuse Hermes's directory but own our namespace: its cleanup
(`_cleanup_cache_dir`) iterates files only and never recurses, so it will
never touch ours — and ours never touches its inbound files.
"""
import hashlib
from pathlib import Path

DOC_ID_LEN = 12
ES_NAMESPACE = ".es"


def doc_id(source: Path) -> str:
    """Content hash of the source file. Stable, so re-extracting the same
    document is a cache hit rather than a re-conversion."""
    h = hashlib.sha256()
    with open(source, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:DOC_ID_LEN]


def artifact_dir(cache_root: Path, did: str) -> Path:
    """The per-document artifact directory, created if absent."""
    d = Path(cache_root) / ES_NAMESPACE / did
    d.mkdir(parents=True, exist_ok=True)
    return d


def page_image_path(adir: Path, page_no: int) -> Path:
    """Zero-padded so lexical order matches page order."""
    return Path(adir) / f"p{page_no:03d}.png"
