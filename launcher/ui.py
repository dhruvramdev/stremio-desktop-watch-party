"""
Terminal UI Dashboard for Stremio Desktop Watch Party powered by Rich.
"""

import sys
import time
from typing import Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.live import Live
from rich.progress_bar import ProgressBar

from relay.protocol import Role


def format_time(seconds: float | None) -> str:
    """Format seconds into HH:MM:SS or MM:SS."""
    if seconds is None or seconds < 0:
        return "--:--"
    total_sec = int(seconds)
    hours = total_sec // 3600
    minutes = (total_sec % 3600) // 60
    secs = total_sec % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class TerminalDashboard:
    def __init__(
        self,
        room: str,
        role: str | Role,
        relay_url: str,
        public_url: Optional[str] = None,
        secret: Optional[str] = None,
        runtime_dir: Optional[str] = None
    ):
        self.room = room
        self.role = Role(role) if isinstance(role, str) else role
        self.relay_url = relay_url
        self.public_url = public_url
        self.secret = secret
        self.runtime_dir = runtime_dir
        self.console = Console()
        self.live: Optional[Live] = None

    def setup(self):
        if sys.stdout.isatty():
            self.live = Live(console=self.console, refresh_per_second=4, transient=False)
            self.live.start()

    def cleanup(self):
        if self.live:
            try:
                self.live.stop()
            except Exception:
                pass
            self.live = None

    def generate_view(self, status: dict | None = None) -> Panel:
        status = status or {}
        role_val = status.get("role", self.role.value if isinstance(self.role, Role) else self.role).upper()
        room = status.get("room", self.room)
        public_url = status.get("public_url", self.public_url)
        relay_connected = status.get("relay_connected", False)
        mpv_connected = status.get("mpv_connected", False)
        clients = status.get("clients", [])
        state = status.get("state", {})
        recent_events = status.get("recent_events", [])

        time_s = state.get("time_s", 0.0)
        duration_s = state.get("duration_s", 0.0)
        paused = state.get("paused", True)
        seeking = state.get("seeking", False)
        buffering = state.get("buffering", False)
        loaded = state.get("loaded", False)
        media_title = state.get("media_title", "")
        drift_s = state.get("drift_s", 0.0)

        # Status Badge
        if not mpv_connected:
            badge = "[yellow]○ WAITING FOR MPV[/yellow]"
        elif not loaded and duration_s == 0:
            badge = "[dim]⏹ IDLE (NO MEDIA)[/dim]"
        elif buffering:
            badge = "[magenta]⏳ BUFFERING[/magenta]"
        elif seeking:
            badge = "[cyan]⏩ SEEKING[/cyan]"
        elif paused:
            badge = "[yellow]⏸ PAUSED[/yellow]"
        else:
            badge = "[bold green]▶ PLAYING[/bold green]"

        # Drift text
        if role_val == "HOST":
            drift_text = "[cyan]0 ms (Host Authority)[/cyan]"
        else:
            drift_ms = int(drift_s * 1000)
            if abs(drift_s) < 0.2:
                drift_text = f"[green]{drift_ms:+d} ms [IN SYNC][/green]"
            elif abs(drift_s) < 1.5:
                drift_text = f"[yellow]{drift_ms:+d} ms [SYNCING][/yellow]"
            else:
                drift_text = f"[bold red]{drift_ms:+d} ms [SEEKING][/bold red]"

        mpv_status = "[green]Connected[/green]" if mpv_connected else "[red]Waiting for Stremio video...[/red]"
        relay_status = f"[green]Connected[/green] ({len(clients)} peers)" if relay_connected else "[red]Connecting...[/red]"

        host_only = status.get("host_only", False)
        mode_tag = "Host-Only Lock" if host_only else "Shared Control"
        role_label = (
            f"[bold green]HOST [{mode_tag}][/bold green]"
            if role_val == "HOST"
            else f"[bold blue]GUEST [{mode_tag}][/bold blue]"
        )

        # Main Layout Grid Table
        grid = Table.grid(expand=True, padding=(0, 1))

        # Row 1: Session Details
        grid.add_row(f"[bold]Room:[/bold] {room}", f"[bold]Role:[/bold] {role_label}")
        if public_url:
            grid.add_row(f"[bold]Local Relay:[/bold] {self.relay_url}", f"[bold]Tunnel:[/bold] [green]{public_url}[/green]")
            grid.add_row(f"[bold]MPV IPC:[/bold] {mpv_status}", f"[bold]Status:[/bold] {relay_status}")
        else:
            grid.add_row(f"[bold]Relay:[/bold] {self.relay_url}", f"[bold]Status:[/bold] {relay_status}")
            grid.add_row(f"[bold]MPV IPC:[/bold] {mpv_status}", "")

        if role_val == "HOST" and self.secret:
            invite_target = public_url or self.relay_url
            grid.add_row(f"[bold]Share:[/bold] [cyan]watch-party join {room} --secret {self.secret} --relay {invite_target}[/cyan]", "")
        if self.runtime_dir:
            grid.add_row(f"[bold]Logs:[/bold] [dim]{self.runtime_dir}[/dim]", "")
        grid.add_row("", "")

        # Row 2: Media Title
        title_display = media_title if media_title else "[dim]Waiting for playback in Stremio...[/dim]"
        grid.add_row(f"[bold]Media:[/bold] [italic]{title_display}[/italic]", "")

        # Row 3: Progress & Position
        pct = max(0.0, min(1.0, time_s / duration_s)) if duration_s > 0 else 0.0
        bar = ProgressBar(total=100, completed=pct * 100, width=22)
        pos_str = f"{format_time(time_s)} / {format_time(duration_s)}"

        sub_table = Table.grid(expand=True, padding=(0, 1))
        sub_table.add_row(f"[bold]Playback:[/bold] {badge}", bar, f"{pct*100:5.1f}%")
        grid.add_row(sub_table, "")
        grid.add_row(f"[bold]Position:[/bold] {pos_str}", f"[bold]Drift:[/bold] {drift_text}")

        # Row 4: Peers
        peers_display = ", ".join(clients[:6]) if clients else "None"
        if len(clients) > 6:
            peers_display += f" (+{len(clients) - 6} more)"
        grid.add_row(f"[bold]Peers:[/bold] [dim]{peers_display}[/dim]", "")
        grid.add_row("", "")

        # Row 5: Recent Events Feed
        events_text = Text()
        events_text.append("Recent Activity:\n", style="bold")
        if not recent_events:
            events_text.append("  • Awaiting session activity...", style="dim")
        else:
            for ev in recent_events[-4:]:
                ev_time = time.strftime("%H:%M:%S", time.localtime(ev.get("time", time.time())))
                ev_txt = ev.get("text", "")
                events_text.append(f"  [{ev_time}] ", style="dim")
                events_text.append(f"{ev_txt}\n")
        grid.add_row(events_text, "")

        return Panel(
            grid,
            title="[bold cyan]🎬 STREMIO DESKTOP WATCH PARTY[/bold cyan]",
            subtitle="[dim]Press Ctrl+C to stop session and disconnect[/dim]",
            border_style="cyan"
        )

    def render(self, status: dict | None = None):
        """Render update through Rich Live."""
        panel = self.generate_view(status)
        if self.live:
            self.live.update(panel)
        elif sys.stdout.isatty():
            self.console.print(panel)
