# Remote Watch Party & Secure Tunneling Guide

When hosting a Stremio Desktop Watch Party with friends across different networks or over the internet, participants need to connect to the WebSocket relay server via a secure, public endpoint (`wss://`).

This guide outlines several zero-configuration, encrypted tunneling methods to expose the relay server securely without requiring router port-forwarding or public IP exposure.

---

## Method 1: Cloudflare Quick Tunnels (`cloudflared`) [Recommended]

Cloudflare Tunnels provide free, automatic TLS termination with zero setup or domain registration required.

### 1. Install `cloudflared`
If not already installed via Homebrew:
```bash
brew install cloudflared
```

### 2. Launch Host with Built-in Tunnel Flag (Single Command)
Hosts can automatically launch the embedded relay and tunnel in a single command:

```bash
watch-party start --role host --room my-room --secret mysecret --tunnel
```

Or when running a standalone relay:
```bash
watch-party relay --port 8765 --secret mysecret --tunnel
```

The CLI will:
1. Start the WebSocket relay server locally on port 8765.
2. Launch a temporary Cloudflare tunnel (`*.trycloudflare.com`).
3. Display the exact ready-to-share command for guests with the `wss://` URL:

```text
Room: my-room                             Role: HOST [Broadcasting]
Local Relay: ws://localhost:8765          Tunnel: wss://example-name.trycloudflare.com
MPV IPC: Connected                        Status: Connected (1 peers)
Share: watch-party start --role guest --room my-room --secret mysecret --relay wss://example-name.trycloudflare.com
```

Guests then simply run:
```bash
watch-party start --role guest --room my-room --secret mysecret --relay wss://example-name.trycloudflare.com
```

---

## Method 2: Tailscale Funnel / MagicDNS

If you and your friends use [Tailscale](https://tailscale.com):

### 1. Tailscale Funnel (Public Internet)
Expose the relay port to the public internet using Tailscale Funnel:
```bash
tailscale funnel 8765
```
Or start the relay with the Tailscale provider:
```bash
./bin/watch-party relay --port 8765 --secret mysecret --tunnel tailscale
```

### 2. Tailnet Mesh (Private VPN)
If all participants are on the same Tailscale tailnet:
1. Check host machine's MagicDNS address (e.g. `macbook.tailnet-xyz.ts.net`).
2. Run standard relay:
   ```bash
   ./bin/watch-party relay --port 8765 --secret mysecret
   ```
3. Connect using the MagicDNS hostname:
   ```bash
   ./bin/watch-party start --role guest --room my-room --secret mysecret --relay ws://macbook.tailnet-xyz.ts.net:8765
   ```

---

## Method 3: Self-Hosted Reverse Proxy (Caddy / Nginx)

For persistent watch parties on a VPS or cloud server:

### Caddyfile Example (`/etc/caddy/Caddyfile`)
```caddy
relay.yourdomain.com {
    reverse_proxy localhost:8765
}
```
Caddy automatically handles HTTPS/WSS Let's Encrypt certificates.

### Nginx Configuration
```nginx
server {
    server_name relay.yourdomain.com;
    listen 443 ssl http2;
    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "Upgrade";
        proxy_set_header Host $host;
    }
}
```

---

## Security & Privacy Considerations

1. **Secret Validation**: Rooms require constant-time preshared secret authentication (`hmac.compare_digest`). Never share room secrets publicly.
2. **End-to-End Privacy**: The relay server strictly forwards control events (`time_s`, `play`, `pause`, `seek`). Media tokens, streams, file paths, subtitles, and addon queries are strictly kept local on each machine and never traverse the relay.

