"""@envelope: wrap a Typer command so its return value becomes the success
envelope and any exception becomes the error envelope (never a traceback).
Reads --pretty from the root context (ctx.obj)."""
import functools

import typer

from es import output


def _pretty(ctx: typer.Context) -> bool:
    return bool(ctx.obj and ctx.obj.get("pretty"))


def envelope(fn):
    @functools.wraps(fn)
    def wrapper(ctx: typer.Context, *args, **kwargs):
        try:
            data = fn(ctx, *args, **kwargs)
        except Exception as e:  # noqa: BLE001 - CLI boundary: never leak a traceback
            code = getattr(e, "es_code", None) or type(e).__name__
            raise typer.Exit(output.emit_error(code, str(e), _pretty(ctx)))
        raise typer.Exit(output.emit(data, _pretty(ctx)))

    return wrapper
