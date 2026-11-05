"""
Tunneling and NAT Traversal Integration for Stremio Desktop Watch Party Relay.
Supports Cloudflare Quick Tunnels (cloudflared) and Tailscale Funnel.
"""

import os
import re
import sys
import time
import shutil
import logging
import subprocess

logger = logging.getLogger(__name__)

CLOUDFLARED_URL_REGEX = re.compile(r"https://([a-zA-Z0-9-]+\.trycloudflare\.com)")


def is_tool_available(name: str) -> bool:
    """Check if an executable is in system PATH."""
    return shutil.which(name) is not None


def start_cloudflared_tunnel(port: int, timeout: float = 15.0) -> tuple[subprocess.Popen | None, str | None]:
    """
    Launch a zero-config Cloudflare Quick Tunnel to expose the local relay port.
    Returns (subprocess.Popen, wss_url).
    """
    cmd = shutil.which("cloudflared")
    if not cmd:
        return None, None

    logger.info(f"Starting Cloudflare Quick Tunnel for port {port}...")
    try:
        proc = subprocess.Popen(
            [cmd, "tunnel", "--url", f"http://127.0.0.1:{port}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True
        )
    except Exception as e:
        logger.error(f"Failed to spawn cloudflared: {e}")
        return None, None

    wss_url = None
    start_time = time.time()

    # Read stderr in a non-blocking way to find trycloudflare.com URL
    while time.time() - start_time < timeout:
        if proc.poll() is not None:
            # cloudflared exited unexpectedly
            stderr_out = proc.stderr.read() if proc.stderr else ""
            logger.error(f"cloudflared exited early (code {proc.returncode}): {stderr_out}")
            return None, None

        line = proc.stderr.readline()
        if not line:
            time.sleep(0.1)
            continue

        match = CLOUDFLARED_URL_REGEX.search(line)
        if match:
            https_url = match.group(0)
            wss_url = https_url.replace("https://", "wss://")
            logger.info(f"Cloudflare Tunnel active: {wss_url}")
            break

    if not wss_url:
        logger.warning("Timed out waiting for trycloudflare.com tunnel URL.")
        stop_tunnel(proc)
        return None, None

    return proc, wss_url


def start_tailscale_tunnel(port: int) -> tuple[subprocess.Popen | None, str | None]:
    """
    Launch a Tailscale Funnel for the local relay port if configured.
    """
    cmd = shutil.which("tailscale")
    if not cmd:
        return None, None

    try:
        # Check node status for MagicDNS domain
        status_proc = subprocess.run([cmd, "status", "--json"], capture_output=True, text=True, timeout=5)
        import json
        status_data = json.loads(status_proc.stdout)
        magic_dns = status_data.get("Self", {}).get("DNSName", "").rstrip(".")
        if not magic_dns:
            return None, None

        logger.info(f"Starting Tailscale Funnel on port {port} for {magic_dns}...")
        proc = subprocess.Popen(
            [cmd, "funnel", str(port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        wss_url = f"wss://{magic_dns}:{port}"
        return proc, wss_url
    except Exception as e:
        logger.warning(f"Tailscale Funnel failed: {e}")
        return None, None


def start_tunnel(port: int, provider: str = "auto") -> tuple[subprocess.Popen | None, str | None]:
    """
    Starts an internet tunnel for the relay server based on provider preference.
    provider: 'auto', 'cloudflared', or 'tailscale'.
    """
    if provider in ("cloudflared", "auto"):
        if is_tool_available("cloudflared"):
            proc, url = start_cloudflared_tunnel(port)
            if url:
                return proc, url
        elif provider == "cloudflared":
            logger.error("cloudflared binary not found in PATH. Install with: brew install cloudflared")
            return None, None

    if provider in ("tailscale", "auto"):
        if is_tool_available("tailscale"):
            proc, url = start_tailscale_tunnel(port)
            if url:
                return proc, url
        elif provider == "tailscale":
            logger.error("tailscale binary not found in PATH.")
            return None, None

    return None, None


def stop_tunnel(proc: subprocess.Popen | None):
    """Cleanly terminate a tunnel subprocess."""
    if not proc:
        return
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass

