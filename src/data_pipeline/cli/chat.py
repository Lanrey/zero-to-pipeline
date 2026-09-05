"""Interactive AI assistant CLI command."""

from __future__ import annotations

import json
from typing import Any

import typer
from rich.markdown import Markdown

from data_pipeline.cli.helpers import console
from data_pipeline.connectors import provider_registry
from data_pipeline.sources import SourceStore

SYSTEM_PROMPT = """\
You are the Zero-to-Pipeline assistant \u2014 an expert in connecting to APIs and running data pipelines.

You help users:
- Add data sources (pipeline source add <provider> [--base-url URL])
- Sync / extract data (pipeline sync run <provider>)
- Check authentication (pipeline auth set <provider> / pipeline auth status)
- Test connections (pipeline source test <provider>)
- List sources (pipeline source list)
- Run the full demo pipeline (python -m examples.demo_pipeline)

When a user asks you to do something, respond conversationally AND include a
JSON action block at the very end of your response when you want to execute a command.

Action block format (always last, always valid JSON):
{"action": "source_add", "provider": "mlflow", "base_url": null}
{"action": "sync_run", "provider": "mlflow"}
{"action": "source_list"}
{"action": "auth_status"}
{"action": "source_test", "provider": "mlflow"}
{"action": "none"}

Rules:
- Always be helpful, concise, and direct \u2014 like Claude or ChatGPT.
- Explain what you are doing and why.
- If you need more information (e.g. which source to sync), ask a follow-up question and use action "none".
- Never hallucinate commands. Only use the actions listed above.
- Keep your conversational response SHORT \u2014 1-3 sentences before the action block.
- ALWAYS end with the action JSON block, even if action is "none".
"""


def chat_command(
    message: str | None = typer.Argument(None, help="Opening message (optional \u2014 omit to start interactive session)"),
) -> None:
    """Interactive AI assistant for Zero to Pipeline."""
    from data_pipeline.connectors.llm_discovery import get_llm_provider

    llm = get_llm_provider()
    if not llm:
        console.print("[red]LLM not available.[/red] Run: pipeline auth set <provider>")
        return

    source_store = SourceStore()

    def _context_snapshot() -> str:
        sources = source_store.list()
        if not sources:
            sources_str = "No sources configured yet."
        else:
            lines = [f"  - {s.provider} ({s.api.base_url if s.api else 'no url'}) [{s.connection_status.value}]"
                     for s in sources]
            sources_str = "\n".join(lines)
        known = ", ".join(provider_registry.known_providers)
        return f"Configured sources:\n{sources_str}\n\nKnown provider presets: {known}"

    def _execute_action(action: dict[str, Any]) -> str | None:
        name = action.get("action", "none")
        if name == "none":
            return None

        # Lazy imports to avoid circular deps at module level
        from data_pipeline.cli.auth import auth_status
        from data_pipeline.cli.source import source_add, source_list, source_test
        from data_pipeline.cli.sync import sync_run

        try:
            if name == "source_list":
                source_list()
                return "Listed current sources."
            if name == "auth_status":
                auth_status()
                return "Showed auth status."
            if name == "source_add":
                provider = action.get("provider")
                if not provider:
                    console.print("[yellow]No provider specified.[/yellow]")
                    return "Provider name missing."
                source_add(provider=provider, base_url=action.get("base_url"), auth_type=None, force=False)
                return f"Added source: {provider}"
            if name == "sync_run":
                provider = action.get("provider")
                if not provider:
                    console.print("[yellow]No provider specified.[/yellow]")
                    return "Provider missing for sync."
                sync_run(provider=provider, path=None, base_url=None, full=False)
                return f"Sync complete for: {provider}"
            if name == "source_test":
                provider = action.get("provider")
                if provider:
                    source_test(provider=provider)
                    return f"Tested connection for: {provider}"
        except (SystemExit, typer.Exit):
            return f"Action failed for: {action.get('provider')}"
        except Exception as e:
            console.print(f"[red]Action error:[/red] {e}")
            return f"Action error: {e}"
        return None

    history: list[dict[str, Any]] = []

    def _turn(user_msg: str) -> None:
        system = SYSTEM_PROMPT + f"\n\nCurrent pipeline state:\n{_context_snapshot()}"
        history.append({"role": "user", "content": user_msg})

        with console.status("[dim]Thinking...[/dim]", spinner="dots"):
            result = llm.chat(history, system=system, max_tokens=1024, timeout=45)

        if not result.success:
            console.print(f"[red]LLM error:[/red] {result.error}")
            history.pop()
            return

        raw = result.text
        action: dict[str, Any] = {"action": "none"}
        display_text = raw

        last_brace = raw.rfind("{")
        if last_brace != -1:
            candidate = raw[last_brace:]
            end = candidate.find("}")
            if end != -1:
                try:
                    parsed = json.loads(candidate[:end + 1])
                    if "action" in parsed:
                        action = parsed
                        display_text = raw[:last_brace].strip()
                except json.JSONDecodeError:
                    pass

        if display_text:
            console.print()
            console.print(Markdown(display_text))

        action_result = _execute_action(action)
        history.append({"role": "assistant", "content": raw})

        if action_result:
            history.append({"role": "user", "content": f"[system: action result \u2014 {action_result}]"})
            history.append({"role": "assistant", "content": "Got it."})

    if message:
        _turn(message)
        return

    console.print("[bold cyan]Zero-to-Pipeline Assistant[/bold cyan]  [dim](type 'exit' or Ctrl-C to quit)[/dim]")
    console.print()

    while True:
        try:
            user_input = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Goodbye.[/dim]")
            break

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit", "bye", ":q"}:
            console.print("[dim]Goodbye.[/dim]")
            break

        _turn(user_input)
