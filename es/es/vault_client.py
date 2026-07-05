"""es notes — Obsidian vault read/write (in-process), mirroring TasksClient.

Two layers over one vault: atomic journal entries (chronological record) and
curated topic docs (state + backlink trail). Content lives only in the journal
entry; topics are hand-curated. The topics/ folder IS the topic-name registry.
"""
import os
import re
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

import yaml

from es.deeplink import build_deeplink

ILLEGAL = re.compile(r'[/\\:*?"<>|]')


class NoteNotFound(Exception):
    es_code = "note_not_found"


class TopicNotFound(Exception):
    es_code = "topic_not_found"


class InvalidTopic(Exception):
    es_code = "invalid_topic"


class InvalidCategory(Exception):
    es_code = "invalid_category"


class AttachmentSourceNotFound(Exception):
    es_code = "attachment_source_not_found"


def _sanitize_title(title: str) -> str:
    """Keep spaces/unicode; strip filesystem-illegal chars. Collapse whitespace."""
    cleaned = ILLEGAL.sub("", title)
    return re.sub(r"\s+", " ", cleaned).strip()


def _unique_filename(folder: Path, stem: str) -> str:
    """`<stem>.md`, appending ' 2', ' 3', … if it already exists in folder."""
    if not (folder / f"{stem}.md").exists():
        return f"{stem}.md"
    n = 2
    while (folder / f"{stem} {n}.md").exists():
        n += 1
    return f"{stem} {n}.md"


def _unique_attachment(folder: Path, filename: str) -> str:
    """Sanitized original basename, deduped ' 2', ' 3'… before the extension."""
    clean = re.sub(r"\s+", " ", ILLEGAL.sub("", filename)).strip() or "attachment"
    stem, dot, ext = clean.rpartition(".")
    if not dot:
        stem, ext = clean, ""
    cand, n = clean, 2
    while (folder / cand).exists():
        cand = f"{stem} {n}.{ext}" if ext else f"{stem} {n}"
        n += 1
    return cand


def _normalize_topic(topic: str) -> str:
    """Accept 'Name' or '[[Name]]'; return canonical '[[Name]]'. Rejects empty."""
    inner = topic.strip()
    if inner.startswith("[[") and inner.endswith("]]"):
        inner = inner[2:-2].strip()
    if not inner:
        raise InvalidTopic(f"empty topic name: {topic!r}")
    return f"[[{inner}]]"


def _render_frontmatter(created: str, author: str, tags: List[str],
                        topics: List[str], meta: dict) -> str:
    """YAML frontmatter block. Topic wikilinks are emitted QUOTED so Obsidian
    Properties treats them as real links (unquoted [[X]] is invalid YAML)."""
    fm: dict = {"created": created, "author": author}
    if tags:
        fm["tags"] = list(tags)
    if topics:
        fm["topics"] = [_normalize_topic(t) for t in topics]
    for k, v in (meta or {}).items():
        fm[k] = v
    dumped = yaml.safe_dump(fm, default_flow_style=False, allow_unicode=True,
                            sort_keys=False)
    return f"---\n{dumped}---\n"


def _split_frontmatter(text: str):
    """Return (frontmatter_dict, body_str). No frontmatter → ({}, text)."""
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            fm = yaml.safe_load(text[4:end]) or {}
            return (fm if isinstance(fm, dict) else {}), text[end + 5:]
    return {}, text


def _today() -> str:
    return date.today().isoformat()


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M")


class VaultClient:
    def __init__(self, root, vault_name: str = "",
                 journal_folder: str = "Journal", categories=("Topics",)):
        self.root = Path(root)
        self.vault_name = vault_name
        self.journal_folder = journal_folder or "Journal"
        self.categories = list(categories) if categories else ["Topics"]

    def _rel(self, path: Path) -> str:
        return str(path.relative_to(self.root))

    def _result(self, path: Path, **extra) -> dict:
        rel = self._rel(path)
        return {"path": rel,
                "obsidian_deeplink": build_deeplink(self.vault_name, rel),
                **extra}

    def write_journal(self, title: str, body: str, tags: Optional[List[str]] = None,
                      topics: Optional[List[str]] = None, meta: Optional[dict] = None) -> dict:
        clean = _sanitize_title(title)
        if not clean:
            raise InvalidTopic(f"empty title after sanitization: {title!r}")
        folder = self.root / self.journal_folder / _today()
        folder.mkdir(parents=True, exist_ok=True)
        fname = _unique_filename(folder, clean)
        fm = _render_frontmatter(_now_iso(), "everstone", tags or [], topics or [], meta or {})
        (folder / fname).write_text(fm + (body or "") + "\n")
        return self._result(folder / fname)

    def _find_topic(self, clean: str) -> Optional[Path]:
        """First existing `clean` topic across category folders (flat OR folder-note form)."""
        for cat in self.categories:
            flat = self.root / cat / f"{clean}.md"
            if flat.is_file():
                return flat
            folder = self.root / cat / clean / f"{clean}.md"
            if folder.is_file():
                return folder
        return None

    def write_topic(self, name: str, body: Optional[str] = None,
                    update: Optional[str] = None, category: Optional[str] = None) -> dict:
        clean = _sanitize_title(name)
        if not clean:
            raise InvalidTopic(f"empty topic name: {name!r}")
        if category is not None and category not in self.categories:
            raise InvalidCategory(f"category not allowed: {category!r}")
        path = self._find_topic(clean)
        if path is None:
            cat = category or self.categories[0]
            path = self.root / cat / f"{clean}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        created = not path.exists()
        text = "" if created else path.read_text()
        if body is not None:
            text = body.rstrip() + "\n"
        if update is not None:
            if "## Updates" not in text:
                text = text.rstrip() + "\n\n## Updates\n"
            text = text.rstrip() + f"\n- {_today()}: {update}\n"
        if created and body is None and update is None:
            text = ""
        path.write_text(text)
        return self._result(path, created=created, updated=not created)

    @staticmethod
    def _md_entries(folder: Path) -> List[Path]:
        """Every note body in `folder`: flat *.md plus same-name folder-notes."""
        out = list(folder.glob("*.md"))
        for sub in folder.iterdir():
            if sub.is_dir() and (sub / f"{sub.name}.md").is_file():
                out.append(sub / f"{sub.name}.md")
        return out

    def list_topics(self, like: Optional[str] = None) -> List[str]:
        names = set()
        for cat in self.categories:
            folder = self.root / cat
            if folder.is_dir():
                names.update(p.stem for p in self._md_entries(folder))
        result = sorted(names)
        if not like:
            return result
        q = like.lower()

        def matches(name: str) -> bool:
            low = name.lower()
            if q in low:
                return True
            # subsequence fuzzy: all chars of q appear in order
            it = iter(low)
            return all(ch in it for ch in q)

        return [n for n in result if matches(n)]

    def _resolve(self, target: str) -> Path:
        cand = self.root / target
        if cand.is_file():
            return cand
        if target.endswith(".md"):
            p = Path(target)
            promoted = self.root / p.parent / p.stem / p.name
            if promoted.is_file():
                return promoted
        else:
            clean = _sanitize_title(target)
            found = self._find_topic(clean) if clean else None
            if found:
                return found
        raise NoteNotFound(f"note not found: {target!r}")

    def _is_structural_folder(self, folder: Path) -> bool:
        """True if `folder` is a note *container* (a category folder, or a day folder
        under the journal folder) rather than a note's own folder-note directory."""
        return ((folder.parent == self.root and folder.name in self.categories)
                or folder.parent == self.root / self.journal_folder)

    def attach(self, target: str, source: str) -> dict:
        note = self._resolve(target)
        src = Path(source)
        if not src.is_file():
            raise AttachmentSourceNotFound(f"source not found: {source!r}")
        if self._is_structural_folder(note.parent):   # flat → promote to same-name folder-note
            folder = note.parent / note.stem
            folder.mkdir(parents=True, exist_ok=True)
            note = note.rename(folder / note.name)
        folder = note.parent
        att = _unique_attachment(folder, src.name)
        shutil.copy2(src, folder / att)
        return self._result(note, ref=f"![[{att}]]", attachment=self._rel(folder / att))

    def read_note(self, target: str) -> dict:
        path = self._resolve(target)
        fm, body = _split_frontmatter(path.read_text())
        return {"path": self._rel(path), "frontmatter": fm, "body": body}

    def list_journal(self, topic: Optional[str] = None, since: Optional[str] = None,
                     day: Optional[str] = None) -> List[dict]:
        base = self.root / self.journal_folder
        if not base.is_dir():
            return []
        want_topic = _normalize_topic(topic) if topic else None
        out: List[dict] = []
        for dayfolder in sorted(base.iterdir()):
            if not dayfolder.is_dir():
                continue
            d = dayfolder.name
            if day and d != day:
                continue
            if since and d < since:
                continue
            for f in sorted(self._md_entries(dayfolder)):
                fm, _ = _split_frontmatter(f.read_text())
                if want_topic and want_topic not in (fm.get("topics") or []):
                    continue
                out.append({"path": self._rel(f), "title": f.stem,
                            "created": fm.get("created"), "author": fm.get("author"),
                            "tags": fm.get("tags") or [], "topics": fm.get("topics") or []})
        return out
