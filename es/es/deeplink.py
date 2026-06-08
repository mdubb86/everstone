from urllib.parse import quote


def build_deeplink(vault_name: str, note_path: str) -> str:
    note_path = note_path.lstrip("/")
    return f"obsidian://open?vault={quote(vault_name, safe='')}&file={quote(note_path, safe='')}"
