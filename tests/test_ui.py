import unittest
from launcher.ui import format_time, TerminalDashboard
from relay.protocol import Role
from rich.panel import Panel

class TestUI(unittest.TestCase):
    def test_format_time(self):
        self.assertEqual(format_time(0), "00:00")
        self.assertEqual(format_time(45.6), "00:45")
        self.assertEqual(format_time(125), "02:05")
        self.assertEqual(format_time(3600), "01:00:00")
        self.assertEqual(format_time(3665), "01:01:05")
        self.assertEqual(format_time(7325), "02:02:05")
        self.assertEqual(format_time(None), "--:--")
        self.assertEqual(format_time(-10), "--:--")

    def test_dashboard_creation_and_generate_view(self):
        dashboard = TerminalDashboard(
            room="test-room",
            role=Role.HOST,
            relay_url="ws://localhost:8765",
            public_url="wss://test.trycloudflare.com",
            secret="my-secret"
        )
        status = {
            "role": "host",
            "room": "test-room",
            "relay_connected": True,
            "mpv_connected": True,
            "clients": ["host1", "guest1"],
            "state": {
                "time_s": 120.0,
                "duration_s": 300.0,
                "paused": False,
                "media_title": "Test Movie 1080p",
                "drift_s": 0.01
            },
            "recent_events": [
                {"time": 1730000000.0, "text": "Peer connected"}
            ]
        }
        panel = dashboard.generate_view(status)
        self.assertIsInstance(panel, Panel)
        dashboard.render(status)
        dashboard.cleanup()

if __name__ == "__main__":
    unittest.main()
