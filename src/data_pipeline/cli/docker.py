"""Local Docker container management for dev-mode providers."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass

import httpx
from rich.console import Console

console = Console()


@dataclass
class LocalContainerConfig:
    """Configuration for spinning up a provider locally via Docker."""

    image: str
    port: int
    run_cmd: tuple[str, ...] = ()
    auth_type: str = "none"
    health: str = "/"
    note: str | None = None


LOCAL_CONFIGS: dict[str, LocalContainerConfig] = {
    "mlflow": LocalContainerConfig(
        image="ghcr.io/mlflow/mlflow",
        port=5000,
        run_cmd=("mlflow", "server", "--host", "0.0.0.0"),
        health="/",
    ),
    "prometheus": LocalContainerConfig(
        image="prom/prometheus",
        port=9090,
        auth_type="none",
        health="/-/healthy",
    ),
    "grafana": LocalContainerConfig(
        image="grafana/grafana",
        port=3000,
        auth_type="api_key",
        health="/api/health",
    ),
    "airflow": LocalContainerConfig(
        image="apache/airflow",
        port=8080,
        run_cmd=("standalone",),
        auth_type="basic",
        health="/health",
        note="Default credentials: airflow / airflow",
    ),
    "prefect": LocalContainerConfig(
        image="prefecthq/prefect",
        port=4200,
        run_cmd=("prefect", "server", "start", "--host", "0.0.0.0"),
        health="/api/health",
    ),
    "wandb": LocalContainerConfig(
        image="wandb/local",
        port=8080,
        auth_type="api_key",
        health="/healthz",
        note="W&B local server \u2014 requires a license for production use",
    ),
}


def ensure_local(provider: str, port: int, image: str, run_cmd: tuple[str, ...]) -> str | None:
    """Ensure the provider\u2019s Docker container is running.

    Returns the base URL if successful, None if setup failed.
    Self-heals by:
    - Pulling the image if not present
    - Starting the container if not running
    - Waiting up to 30s for the health endpoint to respond
    """
    slug = provider.lower()
    container_name = f"zero-pipeline-{slug}"
    base_url = f"http://127.0.0.1:{port}"

    # ── Check Docker is available
    try:
        subprocess.run(["docker", "info"], capture_output=True, check=True, timeout=10)
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        console.print("[red]  \u2717 Docker not found.[/red] Install Docker Desktop and try again.")
        console.print("  [dim]https://docs.docker.com/get-docker/[/dim]")
        return None

    # ── Check if container is already running
    result = subprocess.run(
        ["docker", "ps", "--filter", f"name={container_name}", "--format", "{{.Names}}"],
        capture_output=True, text=True, timeout=10,
    )
    already_running = container_name in result.stdout

    if already_running:
        console.print(f"[dim]  \u2713 Container '{container_name}' already running[/dim]")
    else:
        # ── Check if port is already in use
        port_check = subprocess.run(
            ["docker", "ps", "--filter", f"publish={port}", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=10,
        )
        if port_check.stdout.strip():
            other = port_check.stdout.strip()
            console.print(f"[yellow]  \u26a0 Port {port} is in use by container '{other}'.[/yellow]")
            console.print("[dim]  Self-healing: using that container as the source[/dim]")
        else:
            # ── Pull image
            console.print(f"[dim]  \u2192 Pulling {image}...[/dim]")
            pull = subprocess.run(
                ["docker", "pull", image],
                capture_output=True, text=True, timeout=300,
            )
            if pull.returncode != 0:
                console.print(f"[red]  \u2717 Failed to pull {image}[/red]")
                console.print(f"  [dim]{pull.stderr.strip()[:200]}[/dim]")
                return None

            # ── Start container
            console.print(f"[dim]  \u2192 Starting {container_name} on port {port}...[/dim]")
            docker_run = [
                "docker", "run", "-d", "--name", container_name,
                "-p", f"127.0.0.1:{port}:{port}", image,
                *run_cmd,
            ]
            start = subprocess.run(docker_run, capture_output=True, text=True, timeout=60)
            if start.returncode != 0:
                err = start.stderr.strip()
                if "already in use" in err or "Conflict" in err:
                    console.print("[dim]  Self-healing: container exists but stopped \u2014 restarting...[/dim]")
                    subprocess.run(["docker", "start", container_name], capture_output=True, timeout=30)
                else:
                    console.print("[red]  \u2717 Failed to start container[/red]")
                    console.print(f"  [dim]{err[:200]}[/dim]")
                    return None

    # ── Wait for health endpoint
    cfg = LOCAL_CONFIGS.get(provider.lower())
    health_path = cfg.health if cfg else "/"
    console.print(f"[dim]  \u2192 Waiting for {base_url} to become ready...[/dim]")

    for _ in range(15):
        try:
            r = httpx.get(f"{base_url}{health_path}", timeout=2)
            if r.status_code < 500:
                console.print(f"[green]  \u2713 {provider} is ready at {base_url}[/green]")
                return base_url
        except Exception:
            pass
        time.sleep(2)

    console.print(f"[yellow]  \u26a0 {provider} did not become ready within 30s.[/yellow]")
    console.print(f"  [dim]The source has been added \u2014 retry: pipeline source test {provider}[/dim]")
    return base_url
