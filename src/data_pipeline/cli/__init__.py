"""Zero to Pipeline CLI \u2014 main entry point and top-level commands."""

from __future__ import annotations

import sys

import typer
from rich.console import Console
from typer._click.exceptions import UsageError as ClickUsageError

from data_pipeline.auth import CredentialStore
from data_pipeline.cli.auth import auth_app
from data_pipeline.cli.chat import chat_command
from data_pipeline.cli.helpers import console
from data_pipeline.cli.source import source_app
from data_pipeline.cli.sync import sync_app
from data_pipeline.connectors import provider_registry
from data_pipeline.observability import configure_logging
from data_pipeline.sources import SourceStore

app = typer.Typer(
    name="pipeline",
    help="""Zero to Pipeline: Self-configuring data ingestion for Data Engineering & MLOps.

Connect to any API \u2014 MLflow, W&B, Airflow, Prometheus, or your internal tools:

  pipeline source add mlflow
  pipeline auth set mlflow
  pipeline sync run mlflow""",
    no_args_is_help=True,
)

app.add_typer(source_app, name="source")
app.add_typer(auth_app, name="auth")
app.add_typer(sync_app, name="sync")


def configure_logging_from_cli(log_level: str = "WARNING", log_format: str = "console") -> None:
    configure_logging(level=log_level, log_format=log_format)


@app.callback()
def main_callback(
    log_level: str = typer.Option("WARNING", help="Log level"),
    log_format: str = typer.Option("console", help="Log format: json or console"),
):
    configure_logging_from_cli(log_level=log_level, log_format=log_format)


@app.command("chat")
def chat(
    message: str | None = typer.Argument(None, help="Opening message (optional \u2014 omit to start interactive session)"),
):
    """Interactive AI assistant for Zero to Pipeline."""
    chat_command(message)


@app.command("doctor")
def doctor():
    """Run health checks on the pipeline system."""
    checks = [
        ("Credential store accessible", _check_credential_store),
        ("Provider registry loaded", _check_registry),
        ("Checkpoint dir writable", _check_checkpoints),
        ("Source store accessible", _check_source_store),
    ]

    all_ok = True
    for name, check_fn in checks:
        try:
            check_fn()
            console.print(f"  [green]OK[/green]  {name}")
        except Exception as e:
            console.print(f"  [red]FAIL[/red] {name}: {e}")
            all_ok = False

    if all_ok:
        console.print("\n[green bold]All checks passed![/green bold]")
    else:
        console.print("\n[red bold]Some checks failed.[/red bold]")
        raise typer.Exit(1)


def _check_credential_store():
    store = CredentialStore()
    store.retrieve("__health_check__")
    if store.backend_type == "file":
        console.print("  [dim]  (using encrypted file \u2014 no OS keychain detected)[/dim]")


def _check_registry():
    providers = provider_registry.known_providers
    if not providers:
        raise RuntimeError("No providers in registry")


def _check_checkpoints():
    from data_pipeline.config import settings
    settings.checkpoint_dir.mkdir(parents=True, exist_ok=True)
    test_file = settings.checkpoint_dir / ".write_test"
    test_file.write_text("ok")
    test_file.unlink()


def _check_source_store():
    store = SourceStore()
    store._base_dir.mkdir(parents=True, exist_ok=True)
    test_file = store._base_dir / ".write_test"
    test_file.write_text("ok")
    test_file.unlink()


# ---------------------------------------------------------------------------
# Suggestion map \u2014 intercept unknown commands and show likely alternatives
# ---------------------------------------------------------------------------

_COMMAND_KEYWORDS = {
    "add", "connect", "run", "sync", "list", "ls", "show", "fetch",
    "pull", "extract", "login", "test", "check", "health", "sources",
    "remove", "delete", "register", "setup", "source", "auth", "status",
    "configure", "credentials", "diagnose", "token",
}

_SUGGESTIONS: dict[tuple[str, ...], str] = {
    ("add",):         "pipeline source add <provider>",
    ("connect",):     "pipeline source add <provider>",
    ("register",):    "pipeline source add <provider>",
    ("setup",):       "pipeline source add <provider>",
    ("configure",):   "pipeline source add <provider>",
    ("list",):        "pipeline source list",
    ("ls",):          "pipeline source list",
    ("show",):        "pipeline source list",
    ("sources",):     "pipeline source list",
    ("run",):         "pipeline sync run <provider>",
    ("extract",):     "pipeline sync run <provider>",
    ("fetch",):       "pipeline sync run <provider>",
    ("pull",):        "pipeline sync run <provider>",
    ("test",):        "pipeline source test <provider>",
    ("check",):       "pipeline source test <provider>",
    ("remove",):      "pipeline source remove <provider>",
    ("delete",):      "pipeline source remove <provider>",
    ("login",):       "pipeline auth set <provider>",
    ("token",):       "pipeline auth set <provider>",
    ("credentials",): "pipeline auth status",
    ("status",):      "pipeline sync status  OR  pipeline auth status",
    ("health",):      "pipeline doctor",
    ("diagnose",):    "pipeline doctor",
    ("sync",):        "pipeline sync run <provider>",
    ("auth",):        "pipeline auth set <provider>",
    ("source", "run"):   "pipeline sync run <provider>",
    ("source", "sync"):  "pipeline sync run <provider>",
    ("source", "fetch"): "pipeline sync run <provider>",
}


def main():
    raw_args = [a for a in sys.argv[1:] if not a.startswith("-")]
    provider = raw_args[-1] if raw_args and raw_args[-1] not in _COMMAND_KEYWORDS else "<provider>"

    captured_args = [a for a in sys.argv[1:] if not a.startswith("-")]

    try:
        app(standalone_mode=False)
    except ClickUsageError:
        args = captured_args
        key2 = tuple(args[:2])
        key1 = tuple(args[:1])

        suggestion = _SUGGESTIONS.get(key2) or _SUGGESTIONS.get(key1)
        c = Console()
        c.print(f"[red]Unknown command:[/red] pipeline {' '.join(args)}")
        if suggestion:
            c.print(f"\n[yellow]Did you mean?[/yellow]  [bold cyan]{suggestion.replace('<provider>', provider)}[/bold cyan]")
        else:
            c.print("\n[yellow]Common commands:[/yellow]")
            c.print("  [cyan]pipeline source add <provider>[/cyan]     \u2014 add any data source")
            c.print("  [cyan]pipeline auth set <provider>[/cyan]       \u2014 store credentials")
            c.print("  [cyan]pipeline sync run <provider>[/cyan]       \u2014 extract data")
            c.print("  [cyan]pipeline source list[/cyan]               \u2014 list configured sources")
            c.print("  [cyan]pipeline doctor[/cyan]                    \u2014 health check")
            c.print("  [cyan]pipeline chat[/cyan]                      \u2014 interactive assistant")
        c.print("\n[dim]Run 'pipeline --help' for all commands.[/dim]")
        sys.exit(2)
    except SystemExit:
        raise
    except Exception:
        raise


if __name__ == "__main__":
    main()
