"""Cache layout for converted documents.

Artifacts live in a `.es/<doc_id>/` subdirectory of Hermes's own document
cache. We reuse Hermes's directory but own our namespace: its cleanup
(`_cleanup_cache_dir`) iterates files only and never recurses, so it will
never touch ours — and ours never touches its inbound files.
"""
import hashlib
import os
import shutil
import time
from pathlib import Path

DOC_ID_LEN = 12
ES_NAMESPACE = ".es"
TTL_SECONDS = 24 * 3600


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


def touch(adir: Path) -> None:
    """Mark a document as used. Makes the directory mtime an ACCESS time, so
    the TTL means '24h since last use' rather than '24h since conversion' —
    otherwise a document expires while a conversation is still using it."""
    now = time.time()
    try:
        os.utime(adir, (now, now))
    except OSError:
        pass


def purge(cache_root: Path, ttl_seconds: int = TTL_SECONDS) -> int:
    """Delete artifact directories unused for longer than the TTL. Returns the
    count removed. Only touches our `.es/` namespace — Hermes's own inbound
    files in the parent directory are left alone."""
    ns = Path(cache_root) / ES_NAMESPACE
    if not ns.is_dir():
        return 0
    cutoff = time.time() - ttl_seconds
    removed = 0
    for d in ns.iterdir():
        try:
            if d.is_symlink():
                # Nothing here creates symlinks (artifact_dir only mkdirs), and
                # rmtree refuses to act through one — so counting it as removed
                # would overstate the result. Skip rather than lie.
                continue
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
        except OSError:
            # Removed concurrently between iterdir() and stat() — not our problem.
            continue
    return removed
