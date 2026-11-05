"""
Command Line Interface for Stremio Desktop Watch Party launcher powered by Typer.
"""

import os
import sys
import json
import time
import signal
import socket
import asyncio
import subprocess
from typing import Optional
from enum import Enum

import typer
from loguru import logger
from rich.console import Console

from launcher.compatibility import (
    find_stremio_app,
    get_stremio_version,
    check_entitlements,
    check_architecture,
    check_compatibility,
    find_manifest_path,
    find_shim_path
)
from launcher.runner import RuntimeEnvironment
from launcher.ui import TerminalDashboard
from launcher.tunnel import start_tunnel, stop_tunnel
from relay.protocol import Role, TunnelProvider, SessionStatus
from relay.server import RelayServer

app = typer.Typer(
    name="watch-party",
    help="Stremio Desktop Watch Party macOS CLI companion.",
    no_args_is_help=True,
    add_completion=True
)
console = Console()


@app.command()
def doctor(
    force: bool = typer.Option(False, "--force", "-f", help="Ignore compatibility warnings"),
    relay: Optional[str] = typer.Option(None, "--relay", help="Relay WebSocket URL to test reachability")
):
    """Run comprehensive diagnostic checks on Stremio Desktop and the native Mach-O shim."""
    console.print("\n[bold cyan]=== Stremio Watch Party Doctor ===[/bold cyan]")
    all_passed = True

    manifest_path = find_manifest_path()

    # 1. Stremio app bundle found
    app_path = find_stremio_app()
    if app_path and os.path.exists(app_path):
        console.print(f" [green][PASS][/green] Stremio app bundle found at: {app_path}")
    else:
        console.print(" [red][FAIL][/red] Stremio app bundle not found in /Applications or ~/Applications")
        all_passed = False
        app_path = "/Applications/Stremio.app"

    # 2. Version and manifest check
    is_compat, details = check_compatibility(manifest_path, app_path, force=force)
    version = details.get("version")
    if details.get("version_supported"):
        console.print(f" [green][PASS][/green] Stremio version {version} is supported")
    else:
        console.print(f" [red][FAIL][/red] Stremio version {version} is NOT supported by manifest")
        all_passed = False

    # 3. Entitlements check
    ents = details.get("entitlements", {})
    allow_dyld = ents.get("allow_dyld", False)
    disable_lib_val = ents.get("disable_lib_val", False)
    ignore_ents = os.environ.get("STREMIO_WP_IGNORE_ENTITLEMENTS") == "1"

    if allow_dyld and disable_lib_val:
        console.print(" [green][PASS][/green] Code signing entitlements (dyld injection & library validation disabled)")
    elif ignore_ents:
        console.print(" [yellow][WARN][/yellow] Code signing entitlements check bypassed via STREMIO_WP_IGNORE_ENTITLEMENTS")
    else:
        console.print(f" [red][FAIL][/red] Code signing entitlements missing (allow-dyld={allow_dyld}, disable-lib-val={disable_lib_val})")
        if not force:
            all_passed = False

    # 4. Architecture match
    arch = details.get("architecture", {})
    if arch.get("matches", True):
        console.print(f" [green][PASS][/green] CPU architecture match (host: {arch.get('host_arch')}, app: {arch.get('app_arches')})")
    else:
        console.print(f" [red][FAIL][/red] CPU architecture mismatch (host: {arch.get('host_arch')}, app: {arch.get('app_arches')})")
        all_passed = False

    # 5. Mach-O shim dylib existence and permissions
    shim_path = find_shim_path()
    if not os.path.exists(shim_path):
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        shim_dir = os.path.join(repo_root, "shim")
        if os.path.exists(os.path.join(shim_dir, "Makefile")):
            console.print(" [yellow][INFO][/yellow] Shim dylib not found. Attempting auto-compilation...")
            try:
                subprocess.run(["make", "-C", shim_dir], check=True, capture_output=True)
            except Exception as e:
                console.print(f" [red][FAIL][/red] Failed to auto-compile shim: {e}")

    if os.path.exists(shim_path):
        st_mode = os.stat(shim_path).st_mode
        is_exec = bool(st_mode & 0o111)
        if is_exec:
            console.print(f" [green][PASS][/green] Mach-O shim dylib exists and is executable: {shim_path}")
        else:
            console.print(f" [yellow][WARN][/yellow] Mach-O shim dylib exists but lacks execute permissions: {shim_path}")
    else:
        console.print(f" [red][FAIL][/red] Mach-O shim dylib NOT found at {shim_path}. Build shim first!")
        all_passed = False

    # 6. Runtime socket creation test
    try:
        import tempfile
        test_dir = tempfile.mkdtemp(prefix="stremio-wp-test-", dir="/tmp")
        os.chmod(test_dir, 0o700)
        test_sock = os.path.join(test_dir, "test.sock")
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(test_sock)
        s.close()
        os.unlink(test_sock)
        os.rmdir(test_dir)
        console.print(" [green][PASS][/green] Runtime socket creation and 0700 permissions test")
    except Exception as e:
        console.print(f" [red][FAIL][/red] Runtime socket creation test failed: {e}")
        all_passed = False

    relay_url = relay or "ws://localhost:8765"
    console.print(f" [cyan][INFO][/cyan] Relay endpoint target: {relay_url}")

    console.print("\n================================")
    if all_passed or force:
        console.print("[bold green] RESULT: ALL DIAGNOSTICS PASSED (or forced)[/bold green]")
        console.print("================================\n")
        raise typer.Exit(code=0)
    else:
        console.print("[bold red] RESULT: SOME DIAGNOSTICS FAILED[/bold red]")
        console.print("================================\n")
        raise typer.Exit(code=1)


def copy_to_clipboard(text: str) -> bool:
    """Copy text to macOS system clipboard via pbcopy."""
    if sys.platform == "darwin":
        try:
            p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
            p.communicate(text.encode("utf-8"), timeout=1.0)
            return True
        except Exception:
            return False
    return False


@app.command()
def start(
    role: Role = typer.Option(Role.GUEST, "--role", "-r", help="Participant role: 'host' (broadcasting) or 'guest' (following)"),
    room: str = typer.Option("default-room", "--room", help="Watch party room identifier"),
    secret: str = typer.Option("default-secret", "--secret", "-s", help="Room pre-shared authorization secret"),
    relay: str = typer.Option("ws://localhost:8765", "--relay", help="WebSocket relay endpoint URL"),
    port: int = typer.Option(8765, "--port", "-p", help="Port for embedded relay server (when role=host)"),
    tunnel: bool = typer.Option(
        False,
        "--tunnel",
        "-t",
        help="Expose host relay via secure internet tunnel (cloudflared)"
    ),
    tunnel_provider: TunnelProvider = typer.Option(
        TunnelProvider.AUTO,
        "--tunnel-provider",
        help="Provider for tunnel (auto, cloudflared, tailscale)"
    ),
    auto_relay: bool = typer.Option(True, "--auto-relay/--no-auto-relay", help="Automatically launch embedded relay server when role=host"),
    force: bool = typer.Option(False, "--force", "-f", help="Force launch despite compatibility warnings"),
    raw: bool = typer.Option(False, "--raw", "--no-ui", help="Disable live interactive UI and emit raw logs")
):
    """Launch Stremio with Mach-O IPC interposition and start synchronized playback session."""
    _run_session(
        role=role,
        room=room,
        secret=secret,
        relay=relay,
        port=port,
        tunnel=tunnel,
        tunnel_provider=tunnel_provider,
        auto_relay=auto_relay,
        force=force,
        raw=raw
    )


def _run_session(
    role: Role,
    room: str,
    secret: str,
    relay: str,
    port: int,
    tunnel: bool,
    tunnel_provider: TunnelProvider,
    auto_relay: bool,
    force: bool,
    raw: bool
):
    manifest_path = find_manifest_path()
    app_path = find_stremio_app()

    if not app_path or not os.path.exists(app_path):
        console.print("[bold red]Error:[/bold red] Stremio app bundle not found. Set STREMIO_PATH or install in /Applications.")
        raise typer.Exit(code=1)

    is_compat, details = check_compatibility(manifest_path, app_path, force=force)
    if not is_compat and not force:
        console.print(f"[bold red]Error:[/bold red] Stremio version {details.get('version')} is not compatible and --force not specified.")
        console.print(json.dumps(details, indent=2))
        raise typer.Exit(code=1)

    effective_tunnel = tunnel_provider.value if (tunnel and tunnel_provider) else None
    role_val = role.value if isinstance(role, Role) else str(role)

    runner = RuntimeEnvironment(
        app_path=app_path,
        role=role_val,
        room=room,
        secret=secret,
        relay_url=relay,
        auto_relay=auto_relay,
        relay_port=port,
        tunnel=effective_tunnel
    )

    use_ui = not raw and sys.stdout.isatty()
    dashboard = None

    try:
        runner.start()

        if role == Role.HOST:
            invite_relay = runner.public_url or runner.relay_url
            invite_cmd = f"watch-party join {room} --secret {secret} --relay {invite_relay}"
            copied = copy_to_clipboard(invite_cmd)
            if not use_ui:
                console.print(f"\n[bold green]Room Ready![/bold green] Share this command with guests:")
                console.print(f"  [bold cyan]{invite_cmd}[/bold cyan]")
                if copied:
                    console.print("  [dim]📋 Copied to clipboard![/dim]\n")

        if use_ui:
            dashboard = TerminalDashboard(
                room=room,
                role=role,
                relay_url=runner.relay_url,
                public_url=runner.public_url,
                secret=secret,
                runtime_dir=runner.runtime_dir
            )
            dashboard.setup()
        else:
            console.print(f"Stremio Watch Party started in {role.value} mode for room '{room}'.")
            console.print(f"Runtime directory: {runner.runtime_dir}")
            console.print("Press Ctrl+C to stop.")

        while True:
            status = runner.get_status()
            if use_ui and dashboard:
                dashboard.render(status)
            elif not use_ui and status:
                st = status.get("state", {})
                p_state = "PAUSED" if st.get("paused") else "PLAYING"
                drift_ms = int(st.get("drift_s", 0) * 1000)
                console.print(
                    f"[{time.strftime('%X')}] {p_state} "
                    f"pos={st.get('time_s', 0):.1f}s/{st.get('duration_s', 0):.1f}s "
                    f"drift={drift_ms:+d}ms peers={len(status.get('clients', []))}"
                )

            # Check if child processes exited
            if runner.stremio_proc and runner.stremio_proc.poll() is not None:
                if use_ui and dashboard:
                    dashboard.cleanup()
                rc = runner.stremio_proc.returncode
                console.print(f"\nStremio application process terminated (exit code {rc}).")
                if rc != 0 and runner.runtime_dir:
                    stremio_log = os.path.join(runner.runtime_dir, "stremio.log")
                    if os.path.exists(stremio_log):
                        with open(stremio_log, "r", encoding="utf-8") as f:
                            err = f.read().strip()
                            if err:
                                console.print("\n[bold yellow]Stremio Log:[/bold yellow]\n" + err)
                break

            if runner.controller_proc and runner.controller_proc.poll() is not None:
                if use_ui and dashboard:
                    dashboard.cleanup()
                rc = runner.controller_proc.returncode
                console.print(f"\n[bold red]Watch Party controller daemon terminated unexpectedly (exit code {rc}).[/bold red]")
                if runner.runtime_dir:
                    ctrl_log = os.path.join(runner.runtime_dir, "controller.log")
                    if os.path.exists(ctrl_log):
                        with open(ctrl_log, "r", encoding="utf-8") as f:
                            err = f.read().strip()
                            if err:
                                console.print("\n[bold red]Controller Log:[/bold red]\n" + err)
                break

            time.sleep(0.25)
    except KeyboardInterrupt:
        if use_ui and dashboard:
            dashboard.cleanup()
        console.print("\n[yellow]Stopping Watch Party session...[/yellow]")
    finally:
        if use_ui and dashboard:
            dashboard.cleanup()
        runner.stop()


@app.command()
def status():
    """Inspect active Watch Party runtime sessions and playback metrics."""
    import glob
    runtime_dirs = sorted(glob.glob("/tmp/stremio-wp-*"))
    if not runtime_dirs:
        console.print("[dim]Stremio Watch Party status: STOPPED (no active runtime sessions found)[/dim]")
        return

    console.print(f"\n[bold]Found {len(runtime_dirs)} active session runtime(s):[/bold]\n")
    for rdir in runtime_dirs:
        sock_path = os.path.join(rdir, "mpv.sock")
        status_path = os.path.join(rdir, "status.json")
        pid_file = os.path.join(rdir, "watch-party.pid")

        sock_active = os.path.exists(sock_path)
        status_obj: Optional[SessionStatus] = None
        if os.path.exists(status_path):
            try:
                with open(status_path, "r", encoding="utf-8") as f:
                    status_obj = SessionStatus.model_validate_json(f.read())
            except Exception:
                pass

        console.print(f"[bold cyan]Session Dir:[/bold cyan] {rdir}")
        sock_str = "[green]ACTIVE[/green]" if sock_active else "[red]INACTIVE[/red]"
        console.print(f"  Socket: {sock_str} ({sock_path})")

        if status_obj:
            role_badge = f"[bold green]{status_obj.role.value.upper()}[/bold green]" if status_obj.role == Role.HOST else f"[bold blue]{status_obj.role.value.upper()}[/bold blue]"
            relay_conn = "[green]CONNECTED[/green]" if status_obj.relay_connected else "[red]DISCONNECTED[/red]"
            mpv_conn = "[green]CONNECTED[/green]" if status_obj.mpv_connected else "[red]DISCONNECTED[/red]"
            playback_state = "PAUSED" if status_obj.state.paused else "PLAYING"
            peers = ", ".join(status_obj.clients) if status_obj.clients else "None"
            title = status_obj.state.media_title or "None"

            console.print(f"  Room: [bold]{status_obj.room}[/bold] | Role: {role_badge}")
            console.print(f"  Relay: {status_obj.relay_url} ({relay_conn}) | Peers: {peers}")
            console.print(f"  MPV: {mpv_conn} | Status: {playback_state} ({status_obj.state.time_s:.1f}s / {status_obj.state.duration_s:.1f}s) | Media: {title}")
        elif os.path.exists(pid_file):
            try:
                with open(pid_file, "r") as f:
                    pids = f.read().splitlines()
                    console.print(f"  PIDs: {pids}")
            except Exception:
                pass
        console.print("")


@app.command()
def stop():
    """Terminate all active Watch Party processes and clean up temporary runtime sockets."""
    import glob
    runtime_dirs = glob.glob("/tmp/stremio-wp-*")
    stopped = 0
    for rdir in runtime_dirs:
        if not os.path.isdir(rdir):
            continue
        pid_file = os.path.join(rdir, "watch-party.pid")
        if os.path.exists(pid_file):
            try:
                with open(pid_file, "r") as f:
                    pids = f.read().splitlines()
                    for pstr in pids:
                        if pstr.strip():
                            pid = int(pstr.strip())
                            try:
                                os.kill(pid, signal.SIGTERM)
                                stopped += 1
                            except ProcessLookupError:
                                pass
            except Exception:
                pass
        try:
            import shutil
            shutil.rmtree(rdir)
        except Exception:
            pass

    tunnel_state_path = "/tmp/stremio-wp-tunnel.json"
    if os.path.exists(tunnel_state_path):
        try:
            with open(tunnel_state_path, "r", encoding="utf-8") as f:
                tdata = json.load(f)
            for k in ("tunnel_pid", "relay_pid"):
                pid = tdata.get(k)
                if pid:
                    try:
                        os.kill(int(pid), signal.SIGTERM)
                        stopped += 1
                    except ProcessLookupError:
                        pass
            os.unlink(tunnel_state_path)
        except Exception:
            pass

    console.print(f"[green]Stopped {stopped} active process(es) and cleaned up runtime directories.[/green]")


async def _serve_relay_async(port: int, secret: str, tunnel_opt: Optional[TunnelProvider]):
    tunnel_proc = None
    public_url = None

    if tunnel_opt:
        provider_name = tunnel_opt.value
        console.print(f"Setting up public internet tunnel via [bold cyan]{provider_name}[/bold cyan]...")
        tunnel_proc, public_url = start_tunnel(port, provider=provider_name)

    console.print("\n=======================================================")
    console.print(f"[bold cyan]🚀 Stremio Watch Party Relay Server (Port {port})[/bold cyan]")
    console.print("=======================================================")
    console.print(f"Local WebSocket URL:  [green]ws://localhost:{port}[/green]")
    if public_url:
        console.print(f"Public Secure Tunnel: [bold green]{public_url}[/bold green]")
        console.print("\n[bold]Share with friends:[/bold]")
        console.print(f"  watch-party join movie-night --secret {secret} --relay {public_url}")
    elif tunnel_opt:
        console.print("[yellow]Warning: Tunnel creation failed. Use --tunnel cloudflared or run cloudflared manually.[/yellow]")
    console.print("=======================================================\n")

    server = RelayServer(port=port, default_secret=secret)
    await server.start()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):
            pass

    try:
        await stop_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        console.print("\nStopping relay server...")
        if tunnel_proc:
            stop_tunnel(tunnel_proc)
        await server.stop()


@app.command()
def relay(
    port: int = typer.Option(8765, "--port", "-p", help="Port to bind the WebSocket relay server"),
    secret: str = typer.Option("chikita", "--secret", "-s", help="Default room pre-shared secret"),
    tunnel: bool = typer.Option(
        False,
        "--tunnel",
        "-t",
        help="Expose relay via secure internet tunnel (cloudflared)"
    ),
    tunnel_provider: TunnelProvider = typer.Option(
        TunnelProvider.AUTO,
        "--tunnel-provider",
        help="Provider for tunnel (auto, cloudflared, tailscale)"
    )
):
    """Run standalone WebSocket relay server with optional zero-config Cloudflare/Tailscale tunnel."""
    try:
        effective_tunnel = tunnel_provider if tunnel else None
        asyncio.run(_serve_relay_async(port, secret, effective_tunnel))
    except KeyboardInterrupt:
        pass


@app.command()
def host(
    room: str = typer.Option("movie-night", "--room", "-r", help="Watch party room identifier"),
    secret: str = typer.Option("chikita", "--secret", "-s", help="Room password (defaults to 'chikita')"),
    port: int = typer.Option(8765, "--port", "-p", help="Embedded relay server port"),
    tunnel: bool = typer.Option(
        True,
        "--tunnel/--no-tunnel",
        "-t/-nt",
        help="Expose via internet tunnel (cloudflared)"
    ),
    tunnel_provider: TunnelProvider = typer.Option(
        TunnelProvider.AUTO,
        "--tunnel-provider",
        help="Provider for tunnel (auto, cloudflared, tailscale)"
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Force launch despite warnings"),
    raw: bool = typer.Option(False, "--raw", "--no-ui", help="Disable live UI and emit raw logs")
):
    """Quickstart: Host a watch party with automatic relay and internet tunnel."""
    relay_url = f"ws://localhost:{port}"
    _run_session(
        role=Role.HOST,
        room=room,
        secret=secret,
        relay=relay_url,
        port=port,
        tunnel=tunnel,
        tunnel_provider=tunnel_provider,
        auto_relay=True,
        force=force,
        raw=raw
    )


@app.command()
def join(
    room: str = typer.Argument("movie-night", help="Watch party room identifier"),
    secret: str = typer.Option("chikita", "--secret", "-s", help="Room pre-shared authorization secret"),
    relay: str = typer.Option(..., "--relay", help="Host relay WebSocket URL (wss://...)"),
    force: bool = typer.Option(False, "--force", "-f", help="Force launch despite warnings"),
    raw: bool = typer.Option(False, "--raw", "--no-ui", help="Disable live UI and emit raw logs")
):
    """Quickstart: Join an active watch party room as a guest."""
    _run_session(
        role=Role.GUEST,
        room=room,
        secret=secret,
        relay=relay,
        port=8765,
        tunnel=False,
        tunnel_provider=TunnelProvider.AUTO,
        auto_relay=False,
        force=force,
        raw=raw
    )


@app.command("controller", hidden=True)
def controller():
    """Internal daemon entry point for compiled/standalone runner."""
    from controller.main import main as controller_main
    controller_main()


@app.command("_controller", hidden=True)
def _controller():
    """Fallback alias for internal daemon."""
    controller()


def main():
    app()


if __name__ == "__main__":
    main()
