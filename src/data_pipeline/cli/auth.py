"""Authentication CLI commands."""

from __future__ import annotations

import typer
from rich.table import Table

from data_pipeline.auth import CredentialStore
from data_pipeline.cli.helpers import console, show_auth_docs
from data_pipeline.connectors import provider_registry
from data_pipeline.schemas import AuthType
from data_pipeline.sources import SourceStore

auth_app = typer.Typer(help="Manage authentication")


@auth_app.command("login")
def auth_login(
    provider: str = typer.Argument(help="Provider to authenticate via OAuth Device Flow"),
) -> None:
    """Authenticate a source using OAuth Device Flow (browser-based)."""
    preset = provider_registry.get_preset(provider)
    if not preset:
        console.print(f"[yellow]No preset for '{provider}' \u2014 OAuth config unknown.[/yellow]")
        console.print(f"Use: pipeline auth set {provider}")
        return

    console.print("[dim]OAuth Device Flow requires provider-specific client_id registration.[/dim]")
    console.print(f"[yellow]For {provider}, use API key auth instead:[/yellow]")
    console.print(f"  pipeline auth set {provider}")


@auth_app.command("set")
def auth_set(
    provider: str = typer.Argument(help="Provider name"),
    token: str | None = typer.Option(None, "--token", help="API token (prompted if not provided)"),
) -> None:
    """Store an API key/token for a provider in the OS keychain."""
    if not token:
        import getpass
        show_auth_docs(provider, context="setup")
        console.print()
        token = getpass.getpass(f"Enter API token for {provider}: ")

    if not token or not token.strip():
        console.print("[red]Token cannot be empty[/red]")
        raise typer.Exit(1)

    store = CredentialStore()
    console.print("[dim]  \u2192 Storing token in OS keychain (never written to disk)...[/dim]")
    store.store(provider, {"access_token": token.strip(), "token_type": "bearer"})
    console.print(f"[green]\u2713 Token stored securely for {provider}[/green]")
    console.print("[dim]  Location: macOS Keychain / Linux Secret Service[/dim]")
    console.print(f"[dim]  Test: pipeline source test {provider}[/dim]")


@auth_app.command("status")
def auth_status() -> None:
    """Show authentication status for all sources \u2014 preset and user-added."""
    table = Table(title="Auth Status")
    table.add_column("Provider", style="cyan")
    table.add_column("Auth type", style="dim")
    table.add_column("Status")

    cred_store = CredentialStore()
    source_store = SourceStore()

    seen: set[str] = set()
    rows: list[tuple[str, str, str]] = []

    for provider in provider_registry.known_providers:
        seen.add(provider)
        preset = provider_registry.get_preset(provider)
        auth_type = preset.auth_type.value if preset else "api_key"
        if preset and preset.auth_type == AuthType.NONE:
            status = "[dim]no auth required[/dim]"
        else:
            has_cred = cred_store.retrieve(provider) is not None
            status = "[green]authenticated[/green]" if has_cred else "[red]not authenticated[/red]"
        rows.append((provider, auth_type, status))

    for source in source_store.list():
        if source.provider not in seen:
            seen.add(source.provider)
            auth_type = source.api.auth_type.value if source.api else "api_key"
            if source.api and source.api.auth_type == AuthType.NONE:
                status = "[dim]no auth required[/dim]"
            else:
                has_cred = cred_store.retrieve(source.provider) is not None
                status = "[green]authenticated[/green]" if has_cred else "[red]not authenticated[/red]"
            rows.append((source.provider, auth_type, status))

    for provider, auth_type, status in sorted(rows):
        table.add_row(provider, auth_type, status)

    console.print(table)


@auth_app.command("revoke")
def auth_revoke(
    provider: str = typer.Argument(help="Provider to revoke credentials for"),
) -> None:
    """Remove stored credentials for a provider."""
    store = CredentialStore()
    if store.delete(provider):
        console.print(f"[green]Credentials revoked for {provider}[/green]")
    else:
        console.print(f"[yellow]No credentials found for {provider}[/yellow]")
