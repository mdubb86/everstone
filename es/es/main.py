"""es — EverStone agent tool-gateway CLI. Explicit sub-app registry."""
import typer

from es.capabilities import tasks

app = typer.Typer(no_args_is_help=True, add_completion=False, help="EverStone agent CLI")


@app.callback()
def _root(ctx: typer.Context,
          pretty: bool = typer.Option(False, "--pretty", help="Pretty-print JSON.")):
    ctx.obj = {"pretty": pretty}


app.add_typer(tasks.app, name="tasks", help="CalDAV tasks.")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
