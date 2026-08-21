"""Path confinement for agent-supplied file paths.

One rule, used by every tool that accepts a path from the agent: resolve the
candidate path (and every allowed root) FIRST, confirm the real path sits
inside an allowlisted root, and only THEN check whether it exists.

Confinement is checked before existence on purpose: this module is the
security boundary for ingesting files from untrusted senders, and checking
existence first would let a caller distinguish "exists but forbidden" from
"does not exist" for arbitrary paths anywhere on the filesystem — a
path-probing oracle. Fails closed: no roots, or roots that fail to resolve,
permit nothing.

Messages here are generic and caller-agnostic — callers should re-raise with
their own wording/hints (e.g. attach vs. document-read have different nouns
and different remediation advice).
"""
import os
from pathlib import Path
from typing import Iterable, Union

PathLike = Union[str, os.PathLike]


class SourceNotFound(Exception):
    es_code = "doc_not_found"


class SourceForbidden(Exception):
    es_code = "doc_forbidden"


def resolve_readable(source: PathLike, roots: Iterable[PathLike]) -> Path:
    """Return the resolved path, or raise. `roots` are the allowed directories."""
    try:
        real = Path(source).resolve()
    except (OSError, RuntimeError) as e:
        # Path.resolve() is non-strict (fine for a nonexistent path) but can
        # still raise on e.g. a symlink loop.
        raise SourceNotFound(f"file not found: {source!r}") from e

    allowed = []
    for r in (roots or []):
        try:
            allowed.append(Path(r).resolve())
        except (OSError, RuntimeError):
            continue   # a root that can't be resolved grants nothing

    if not any(real.is_relative_to(d) for d in allowed):
        raise SourceForbidden(f"path is not in an allowed directory: {source!r}")

    if not real.is_file():
        raise SourceNotFound(f"file not found: {source!r}")

    return real
