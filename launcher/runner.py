"""
Runner and Runtime Environment manager for Stremio Desktop Watch Party.
"""

import os
import re
import sys
import json
import socket
import subprocess
import tempfile
import shutil
import time
import signal
from urllib.parse import urlparse
from typing import Optional
from loguru import logger

from launcher.compatibility import find_shim_path
from launcher.tunnel import start_tunnel, stop_tunnel


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Check if a TCP port is currently listening."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex((host, port)) == 0
    except Exception:
        return False


def is_local_url(url: str) -> bool:
    """Check if a WebSocket or HTTP URL targets the local machine."""
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        return hostname in ("localhost", "127.0.0.1", "0.0.0.0", "::1", "")
    except Exception:
        return False


class RuntimeEnvironment:
    def __init__(
        self,
        app_path: str,
        role: str = "guest",
        room: str = "default-room",
        secret: str = "default-secret",
        relay_url: str = "ws://localhost:8765",
        auto_relay: bool = True,
        relay_port: int = 8765,
        tunnel: Optional[str] = None,
        host_only: bool = False
    ):
        self.app_path = app_path
        self.role = role
        self.room = room
        self.secret = secret
        self.relay_url = relay_url
        self.auto_relay = auto_relay
        self.relay_port = relay_port
        self.tunnel = tunnel
        self.host_only = host_only

        # Synchronize port with relay_url
        try:
            parsed = urlparse(self.relay_url)
            if parsed.port:
                self.relay_port = parsed.port
            elif relay_port != 8765 and is_local_url(self.relay_url):
                self.relay_url = f"ws://localhost:{relay_port}"
        except Exception:
            pass

        self.runtime_dir = None
        self.socket_path = None
        self.stremio_proc = None
        self.controller_proc = None
        self.relay_proc = None
        self.tunnel_proc = None
        self.public_url = None
        self.pid_file = None
        self.stremio_log_file = None
        self.controller_log_file = None
        self.relay_log_file = None

    def _is_dir_active(self, dir_path: str) -> bool:
        """Check if an existing runtime directory belongs to an active session."""
        pid_file = os.path.join(dir_path, "watch-party.pid")
        if not os.path.exists(pid_file):
            return False
        try:
            with open(pid_file, "r") as f:
                for line in f.read().splitlines():
                    if line.strip():
                        pid = int(line.strip())
                        try:
                            os.kill(pid, 0)
                            return True
                        except ProcessLookupError:
                            pass
                        except PermissionError:
                            return True
        except Exception:
            pass
        return False

    def prepare(self):
        # Create secure 0700 runtime directory in /tmp (or STREMIO_WP_RUNTIME_DIR)
        custom_dir = os.environ.get("STREMIO_WP_RUNTIME_DIR")
        if custom_dir:
            self.runtime_dir = os.path.abspath(custom_dir)
            os.makedirs(self.runtime_dir, mode=0o700, exist_ok=True)
        else:
            safe_room = re.sub(r"[^a-zA-Z0-9_-]", "-", self.room or "default").strip("-") or "default"
            candidate = f"/tmp/stremio-wp-{safe_room}"
            pid_path = os.path.join(candidate, "watch-party.pid")
            if not os.path.exists(candidate):
                os.makedirs(candidate, mode=0o700, exist_ok=True)
                self.runtime_dir = candidate
            elif os.path.exists(pid_path) and not self._is_dir_active(candidate):
                shutil.rmtree(candidate, ignore_errors=True)
                os.makedirs(candidate, mode=0o700, exist_ok=True)
                self.runtime_dir = candidate
            else:
                self.runtime_dir = tempfile.mkdtemp(prefix=f"stremio-wp-{safe_room}-", dir="/tmp")

        os.chmod(self.runtime_dir, 0o700)
        self.socket_path = os.path.join(self.runtime_dir, "mpv.sock")
        
        if os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except Exception:
                pass

        self.pid_file = os.path.join(self.runtime_dir, "watch-party.pid")

    def start(self):
        self.prepare()
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

        # Locate shim dylib
        shim_path = find_shim_path()
        if not os.path.exists(shim_path):
            shim_dir = os.path.join(repo_root, "shim")
            if os.path.exists(os.path.join(shim_dir, "Makefile")):
                logger.info("Shim dylib not found. Auto-building via clang/make...")
                try:
                    subprocess.run(["make", "-C", shim_dir], check=True, capture_output=True)
                    logger.info("Shim dylib built successfully.")
                except Exception as e:
                    logger.warning(f"Auto-build of shim failed: {e}")

        if not os.path.exists(shim_path):
            logger.warning(f"Shim dylib not found at {shim_path}. Interposition may fail.")

        env = os.environ.copy()
        if repo_root not in env.get("PYTHONPATH", ""):
            env["PYTHONPATH"] = repo_root + (":" + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        env["STREMIO_WP_SOCKET"] = self.socket_path
        env["STREMIO_WP_ROOM"] = self.room
        env["STREMIO_WP_SECRET"] = self.secret
        env["STREMIO_WP_RELAY"] = self.relay_url
        env["STREMIO_WP_ROLE"] = self.role
        env["STREMIO_WP_HOST_ONLY"] = "1" if self.host_only else "0"
        env["STREMIO_PATH"] = self.app_path

        # 1. If role is host and auto_relay is enabled, ensure relay is up
        tunnel_state_path = "/tmp/stremio-wp-tunnel.json"
        existing_tunnel_state = {}
        if os.path.exists(tunnel_state_path):
            try:
                with open(tunnel_state_path, "r", encoding="utf-8") as f:
                    existing_tunnel_state = json.load(f)
            except Exception:
                existing_tunnel_state = {}

        if self.role == "host" and self.auto_relay and is_local_url(self.relay_url):
            if is_port_in_use(self.relay_port):
                logger.info(f"Relay already listening on port {self.relay_port}; connecting to existing instance.")
            else:
                logger.info(f"Auto-launching embedded relay server on port {self.relay_port}...")
                relay_log_path = os.path.join(self.runtime_dir, "relay.log")
                self.relay_log_file = open(relay_log_path, "w", encoding="utf-8")

                if getattr(sys, "frozen", False):
                    relay_cmd = [sys.executable, "relay", "--port", str(self.relay_port), "--secret", self.secret]
                else:
                    relay_cmd = [sys.executable, "-m", "launcher.cli", "relay", "--port", str(self.relay_port), "--secret", self.secret]

                self.relay_proc = subprocess.Popen(
                    relay_cmd,
                    env=env,
                    stdout=self.relay_log_file,
                    stderr=self.relay_log_file,
                    start_new_session=True
                )

                # Wait up to 3 seconds for relay port to bind
                start_wait = time.time()
                relay_ready = False
                while time.time() - start_wait < 3.0:
                    if self.relay_proc.poll() is not None:
                        logger.error(f"Embedded relay exited prematurely with code {self.relay_proc.returncode}")
                        break
                    if is_port_in_use(self.relay_port):
                        relay_ready = True
                        break
                    time.sleep(0.05)

                if relay_ready:
                    logger.info(f"Embedded relay server is ready on port {self.relay_port}")
                else:
                    logger.warning(f"Embedded relay server port {self.relay_port} not ready in time; proceeding anyway.")

            # Optional tunnel creation (reuse existing active tunnel if alive so URL stays fixed)
            if self.tunnel:
                reused_tunnel = False
                saved_t_pid = existing_tunnel_state.get("tunnel_pid")
                saved_url = existing_tunnel_state.get("public_url")
                saved_port = existing_tunnel_state.get("port")
                if saved_t_pid and saved_url and saved_port == self.relay_port:
                    try:
                        os.kill(int(saved_t_pid), 0)
                        self.public_url = saved_url
                        reused_tunnel = True
                        logger.info(f"Reusing existing active tunnel ({saved_t_pid}): {self.public_url}")
                    except Exception:
                        pass

                if not reused_tunnel:
                    logger.info(f"Setting up secure tunnel ({self.tunnel}) for relay port {self.relay_port}...")
                    self.tunnel_proc, self.public_url = start_tunnel(self.relay_port, provider=self.tunnel)
                    if self.public_url:
                        logger.info(f"Secure internet tunnel established: {self.public_url}")
                    else:
                        logger.warning("Tunnel could not be established. Falling back to local relay address.")

                try:
                    new_state = {
                        "port": self.relay_port,
                        "relay_pid": self.relay_proc.pid if self.relay_proc else existing_tunnel_state.get("relay_pid"),
                        "tunnel_pid": self.tunnel_proc.pid if self.tunnel_proc else existing_tunnel_state.get("tunnel_pid"),
                        "public_url": self.public_url
                    }
                    with open(tunnel_state_path, "w", encoding="utf-8") as f:
                        json.dump(new_state, f)
                except Exception:
                    pass

        stremio_env = env.copy()
        stremio_env["DYLD_INSERT_LIBRARIES"] = shim_path

        # 2. Spawn Stremio via direct executable
        # If no other watch-party session is active on this machine, ensure any pre-existing
        # un-shimmed Stremio instance is closed first so Stremio's single-instance lock does not exit(0).
        import glob
        other_active = any(
            d != self.runtime_dir and os.path.isdir(d) and self._is_dir_active(d)
            for d in glob.glob("/tmp/stremio-wp-*")
        )
        if not other_active:
            try:
                chk = subprocess.run(["pgrep", "-x", "Stremio"], capture_output=True, text=True)
                if chk.returncode != 0:
                    chk = subprocess.run(["pgrep", "-xi", "stremio"], capture_output=True, text=True)
                if chk.returncode == 0 and chk.stdout.strip():
                    logger.info("Closing existing Stremio instance to relaunch with Watch Party shim...")
                    subprocess.run(["killall", "Stremio"], capture_output=True)
                    subprocess.run(["killall", "stremio"], capture_output=True)
                    time.sleep(1.0)
            except Exception:
                pass

        executable_path = os.path.join(self.app_path, "Contents", "MacOS", "stremio")
        if not os.path.exists(executable_path):
            import plistlib
            info_plist = os.path.join(self.app_path, "Contents", "Info.plist")
            exec_name = "Stremio"
            if os.path.exists(info_plist):
                try:
                    with open(info_plist, "rb") as f:
                        p = plistlib.load(f)
                        exec_name = p.get("CFBundleExecutable", "Stremio")
                except Exception:
                    pass
            executable_path = os.path.join(self.app_path, "Contents", "MacOS", exec_name)

        logger.info(f"Spawning Stremio from {executable_path}")
        logger.info(f"Using IPC socket path: {self.socket_path}")

        # Prepare log files to prevent pipe buffer deadlocks
        stremio_log_path = os.path.join(self.runtime_dir, "stremio.log")
        self.stremio_log_file = open(stremio_log_path, "w", encoding="utf-8")

        try:
            self.stremio_proc = subprocess.Popen(
                [executable_path],
                env=stremio_env,
                stdout=self.stremio_log_file,
                stderr=self.stremio_log_file
            )
        except Exception as e:
            logger.error(f"Failed to spawn Stremio executable directly: {e}. Trying 'open' command...")
            self.stremio_proc = subprocess.Popen(
                ["open", "-n", self.app_path],
                env=stremio_env,
                stdout=self.stremio_log_file,
                stderr=self.stremio_log_file
            )

        # 3. Spawn controller daemon process
        controller_script = os.path.join(repo_root, "controller", "main.py")
        controller_log_path = os.path.join(self.runtime_dir, "controller.log")
        self.controller_log_file = open(controller_log_path, "w", encoding="utf-8")

        if getattr(sys, "frozen", False):
            controller_cmd = [sys.executable, "controller"]
        elif os.path.exists(controller_script):
            controller_cmd = [sys.executable, controller_script]
        else:
            controller_cmd = [sys.executable, "-m", "controller.main"]

        logger.info(f"Spawning controller daemon with {controller_cmd}...")
        self.controller_proc = subprocess.Popen(
            controller_cmd,
            env=env,
            stdout=self.controller_log_file,
            stderr=self.controller_log_file
        )

        # Write PID file
        try:
            pids = [self.stremio_proc.pid, self.controller_proc.pid]
            if self.relay_proc:
                pids.append(self.relay_proc.pid)
            if self.tunnel_proc:
                pids.append(self.tunnel_proc.pid)
            with open(self.pid_file, "w") as f:
                f.write("\n".join(str(p) for p in pids) + "\n")
        except Exception:
            pass

        logger.info("Watch Party started successfully.")

    def get_status(self) -> dict | None:
        if not self.runtime_dir:
            return None
        status_path = os.path.join(self.runtime_dir, "status.json")
        if os.path.exists(status_path):
            try:
                with open(status_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if self.public_url:
                        data["public_url"] = self.public_url
                    return data
            except Exception:
                pass
        return None

    def stop(self):
        logger.info("Stopping Watch Party processes...")
        if not self.tunnel:
            if self.tunnel_proc:
                stop_tunnel(self.tunnel_proc)
                self.tunnel_proc = None

            if self.relay_proc:
                try:
                    self.relay_proc.terminate()
                    self.relay_proc.wait(timeout=2)
                except Exception:
                    try:
                        self.relay_proc.kill()
                    except Exception:
                        pass
                self.relay_proc = None

        if self.controller_proc:
            try:
                self.controller_proc.terminate()
                self.controller_proc.wait(timeout=2)
            except Exception:
                try:
                    self.controller_proc.kill()
                except Exception:
                    pass

        if self.stremio_proc:
            try:
                self.stremio_proc.terminate()
                self.stremio_proc.wait(timeout=2)
            except Exception:
                try:
                    self.stremio_proc.kill()
                except Exception:
                    pass

        # Close log files
        if self.relay_log_file and not self.relay_log_file.closed:
            try:
                self.relay_log_file.close()
            except Exception:
                pass

        if self.controller_log_file and not self.controller_log_file.closed:
            try:
                self.controller_log_file.close()
            except Exception:
                pass

        if self.stremio_log_file and not self.stremio_log_file.closed:
            try:
                self.stremio_log_file.close()
            except Exception:
                pass

        # Cleanup runtime dir
        if self.runtime_dir and os.path.exists(self.runtime_dir):
            try:
                shutil.rmtree(self.runtime_dir)
            except Exception:
                pass

        logger.info("Watch Party stopped and cleaned up.")
