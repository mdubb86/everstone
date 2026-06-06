import json
from es import output


def test_emit_success_envelope(capsys):
    output.emit({"uid": "abc"}, pretty=False)
    out = capsys.readouterr().out
    assert json.loads(out) == {"ok": True, "data": {"uid": "abc"}}


def test_emit_error_envelope_and_exit_code(capsys):
    rc = output.emit_error("not_found", "no such task", pretty=False)
    out = capsys.readouterr().out
    assert rc == 1
    assert json.loads(out) == {
        "ok": False,
        "error": {"code": "not_found", "message": "no such task"},
    }


def test_pretty_is_indented(capsys):
    output.emit({"a": 1}, pretty=True)
    assert "\n  " in capsys.readouterr().out
