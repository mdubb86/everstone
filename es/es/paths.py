"""Path confinement for agent-supplied file paths.

One rule, used by every tool that accepts a path from the agent: resolve
symlinks FIRST, then require the real path to sit inside an allowlisted root.
Fails closed — an empty root list permits nothing.
"""
from pathlib import Path
from typing import Iterable


class SourceNotFound(Exception):
    es_code = "doc_not_found"


class SourceForbidden(Exception):
    es_code = "doc_forbidden"


def resolve_readable(source: str, roots: Iterable[Path]) -> Path:
    """Return the resolved path, or raise. `roots` are the allowed directories."""
    src = Path(source)
    if not src.is_file():
        raise SourceNotFound(
            f"file not found: {source!r} — uploads are removed from the cache "
            "after 24 hours; ask the user to resend it")
    real = src.resolve()
    allowed = [Path(r).resolve() for r in roots]
    if not any(real.is_relative_to(d) for d in allowed):
        raise SourceForbidden(
            f"not a readable location: {source!r} — documents can only be read "
            "from uploads or the vault")
    return real
