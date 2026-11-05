import os
import socket
import unittest
from launcher.runner import RuntimeEnvironment, is_port_in_use, is_local_url


class TestRunner(unittest.TestCase):
    def test_is_local_url(self):
        self.assertTrue(is_local_url("ws://localhost:8765"))
        self.assertTrue(is_local_url("ws://127.0.0.1:8765"))
        self.assertTrue(is_local_url("ws://0.0.0.0:8765"))
        self.assertTrue(is_local_url("ws://::1:8765"))
        self.assertFalse(is_local_url("wss://relay.example.com"))
        self.assertFalse(is_local_url("wss://party.trycloudflare.com"))
        self.assertFalse(is_local_url("http://192.168.1.50:8765"))

    def test_is_port_in_use(self):
        # Bind a temporary local socket to find an unused port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
            s.listen(1)
            # Port should be in use while listening
            self.assertTrue(is_port_in_use(port))
        
        # After closing, port should no longer be in use
        self.assertFalse(is_port_in_use(port))

    def test_runtime_environment_init(self):
        runner = RuntimeEnvironment(
            app_path="/Applications/Stremio.app",
            role="host",
            room="movie-night",
            secret="popcorn",
            relay_url="ws://localhost:9000",
            auto_relay=True,
            tunnel="cloudflared"
        )
        self.assertEqual(runner.role, "host")
        self.assertEqual(runner.room, "movie-night")
        self.assertEqual(runner.secret, "popcorn")
        self.assertEqual(runner.relay_port, 9000)
        self.assertTrue(runner.auto_relay)
        self.assertEqual(runner.tunnel, "cloudflared")

    def test_runtime_environment_prepare_and_cleanup(self):
        runner = RuntimeEnvironment(
            app_path="/Applications/Stremio.app",
            role="guest",
            room="test-cleanup"
        )
        runner.prepare()
        self.assertIsNotNone(runner.runtime_dir)
        self.assertTrue(runner.runtime_dir.startswith("/tmp/stremio-wp-test-cleanup"))
        self.assertTrue(os.path.exists(runner.runtime_dir))
        # Verify 0700 permissions
        stat_mode = os.stat(runner.runtime_dir).st_mode
        self.assertEqual(stat_mode & 0o777, 0o700)
        self.assertIsNotNone(runner.socket_path)
        self.assertIsNotNone(runner.pid_file)

        runner.stop()
        self.assertFalse(os.path.exists(runner.runtime_dir))

    def test_host_auto_relay_lifecycle(self):
        # Choose a random available port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            test_port = s.getsockname()[1]

        self.assertFalse(is_port_in_use(test_port))

        runner = RuntimeEnvironment(
            app_path="/Applications/Stremio.app",
            role="host",
            room="test-auto-relay",
            secret="test-secret",
            relay_url=f"ws://localhost:{test_port}",
            auto_relay=True,
            relay_port=test_port
        )
        runner.prepare()

        # Start only the auto-relay component without launching Stremio executable
        import sys, subprocess, time
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        env = os.environ.copy()
        env["PYTHONPATH"] = repo_root + (":" + env.get("PYTHONPATH", "") if env.get("PYTHONPATH") else "")
        relay_log_path = os.path.join(runner.runtime_dir, "relay.log")
        runner.relay_log_file = open(relay_log_path, "w", encoding="utf-8")

        runner.relay_proc = subprocess.Popen(
            [sys.executable, "-m", "launcher.cli", "relay", "--port", str(test_port), "--secret", "test-secret"],
            env=env,
            stdout=runner.relay_log_file,
            stderr=runner.relay_log_file
        )

        # Wait for port to open
        ready = False
        start_t = time.time()
        while time.time() - start_t < 4.0:
            if is_port_in_use(test_port):
                ready = True
                break
            time.sleep(0.05)

        self.assertTrue(ready, "Embedded relay server failed to bind and listen on port")
        self.assertTrue(is_port_in_use(test_port))

        # Stop runner and verify port is released
        runner.stop()
        time.sleep(0.2)
        self.assertFalse(is_port_in_use(test_port))


if __name__ == "__main__":
    unittest.main()
