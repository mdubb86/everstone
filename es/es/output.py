"""Uniform JSON output envelope for the es CLI.

Success: {"ok": true, "data": ...}
Failure: {"ok": false, "error": {"code": "...", "message": "..."}}
The agent parses these; --pretty indents the same JSON for humans.
"""
import json


def _print(obj: dict, pretty: bool) -> None:
    print(json.dumps(obj, indent=2 if pretty else None, default=str))


def emit(data, pretty: bool = False) -> int:
    _print({"ok": True, "data": data}, pretty)
    return 0


def emit_error(code: str, message: str, pretty: bool = False) -> int:
    _print({"ok": False, "error": {"code": code, "message": message}}, pretty)
    return 1
