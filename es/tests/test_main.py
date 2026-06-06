import json
import typer
from typer.testing import CliRunner
from es.runner import envelope

runner = CliRunner()


def test_envelope_wraps_return_value():
    app = typer.Typer()

    @app.command()
    @envelope
    def hello(ctx: typer.Context):
        return {"msg": "hi"}

    res = runner.invoke(app, [])
    assert res.exit_code == 0
    assert json.loads(res.stdout) == {"ok": True, "data": {"msg": "hi"}}


def test_envelope_catches_exception_into_error():
    app = typer.Typer()

    @app.command()
    @envelope
    def boom(ctx: typer.Context):
        raise KeyError("missing")

    res = runner.invoke(app, [])
    assert res.exit_code == 1
    body = json.loads(res.stdout)
    assert body["ok"] is False
    assert body["error"]["code"] == "KeyError"
    assert "missing" in body["error"]["message"]
