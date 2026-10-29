# Stremio Desktop Watch Party (macOS)

A lightweight macOS companion for synchronized watch parties in the **native Stremio Desktop player**. Rather than capturing video or streaming through an external web browser, it synchronizes remote playback directly through embedded `libmpv` using dynamic linker interposition.

---

## Key Invariants

1. **Zero Modifications to Stremio.app**: Never alters, patches, or re-signs `/Applications/Stremio.app`. Interposition is purely ephemeral at launch time.
2. **Absolute Privacy**: Zero media streams, debrid tokens, torrent hashes, subtitles, or addon details are ever sent over the network. Only playback timing (`time_s`, `paused`, `duration_s`) and room coordination metadata are exchanged.
3. **Fail-Closed Safety Gate**: Never launches an incompatible Stremio build or restricted environment without explicit user `--force`.
4. **Host Authority**: Only designated room hosts broadcast control events (`play`, `pause`, `seek`, `state`). Guests follow the host; rogue guest control attempts are rejected by the relay.

---

## Getting Started

### 1. Download Standalone Binary (Simplest)
Download the standalone executable from GitHub Releases. It requires **no Python, no Git, and no compilers** installed:
```bash
chmod +x watch-party
./watch-party doctor
```

### 2. Or Build from Source via `make`
If cloning the repository:
```bash
git clone https://github.com/<your-username>/stremio-desktop-watch-party.git
cd stremio-desktop-watch-party

# Compiles native Mach-O shim and standalone binary (dist/watch-party)
make build
```

---

## Usage

### 1. Run System Diagnostics
Verify your Stremio installation, codesign entitlements, architecture, and native Mach-O shim:
```bash
./dist/watch-party doctor
```

### 2. Start Watch Party as Host (Single Command)
Hosts can launch the watch party with automatic embedded relay and zero-config Cloudflare/Tailscale tunnel:

```bash
# Over the Internet (zero-config public tunnel, auto-starts relay)
./watch-party start --role host --room "movie-night" --secret "my-secret" --tunnel

# Or on local network (auto-starts local relay on port 8765)
./watch-party start --role host --room "movie-night" --secret "my-secret"
```
The host dashboard will automatically display the exact join command with the public `wss://` address for your friends to copy and paste.

### 3. Join Watch Party as Guest
Guests paste the command provided by the host:
```bash
./watch-party start --role guest --room "movie-night" --secret "my-secret" --relay "wss://..."
```

### 4. Optional: Dedicated Standalone Relay Server
If you prefer running a dedicated 24/7 relay on a server or separate machine:
```bash
# Local or VPS relay
./watch-party relay --port 8765 --secret "my-secret"

# Or with automatic Cloudflare Quick Tunnel
./watch-party relay --port 8765 --secret "my-secret" --tunnel
```
See [docs/TUNNELING.md](docs/TUNNELING.md) for Tailscale Funnel and reverse proxy setups.

### 5. Check Session Status & Stop
```bash
# Inspect active sessions, current media, position, drift, and connected peers
./dist/watch-party status

# Stop all background sessions and clean up temporary sockets
./dist/watch-party stop
```

---

## Live Terminal Dashboard

When running `watch-party start`, an interactive live dashboard displays playback state, real-time drift, progress, and connected peers:

```text
╭─────────────────────── 🎬 STREMIO DESKTOP WATCH PARTY ───────────────────────╮
│ Room: movie-night                               Role: HOST [Broadcasting]    │
│ Relay: wss://example.trycloudflare.com           Status: Connected (2 peers)  │
│ MPV IPC: Connected                                                           │
│                                                                              │
│ Media: Dune.Part.Two.2024.1080p.mkv                                          │
│ Playback: ▶ PLAYING ━━━━━━━━━━━           45.2%                              │
│ Position: 01:15:30 / 02:46:00                   Drift: 0 ms (Host Authority) │
│ Peers: host-alice (host), guest-bob (guest)                                  │
│                                                                              │
│ Recent Activity:                                                             │
│   [20:15:02] Connected to room 'movie-night' as host                         │
│   [20:15:10] Peer 'guest-bob' joined room                                    │
│   [20:18:24] Local seek to 01:15:30                                          │
│                                                                              │
╰──────────────── Press Ctrl+C to stop session and disconnect ─────────────────╯
```
*(Pass `--raw` to disable the live UI in headless CI or scripts).*

---

## Developer Commands

```bash
make build       # Build native shim and standalone dist/watch-party executable
make test        # Run unit and integration tests
make doctor      # Run diagnostic checks
make clean       # Remove build artifacts and temporary files
```

---

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): Mach-O dynamic interposition, MPV IPC protocol, drift algorithm, and echo suppression.
- [docs/TUNNELING.md](docs/TUNNELING.md): Remote watch parties using Cloudflare Tunnels, Tailscale Funnel, Caddy, or Nginx.
- [docs/RELEASES.md](docs/RELEASES.md): Automated standalone binary builds via GitHub Actions.
