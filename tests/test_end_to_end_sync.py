import os
import json
import asyncio
import tempfile
import unittest

from tests.fake_mpv import FakeMpvServer
from relay.server import RelayServer
from controller.main import WatchPartyController

class TestEndToEndSync(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.port = 8779
        self.relay = RelayServer(port=self.port, default_secret="e2e-secret")
        await self.relay.start()

        self.host_tmpdir = tempfile.TemporaryDirectory(dir="/tmp")
        self.guest_tmpdir = tempfile.TemporaryDirectory(dir="/tmp")

        self.host_sock = os.path.join(self.host_tmpdir.name, "mpv.sock")
        self.guest_sock = os.path.join(self.guest_tmpdir.name, "mpv.sock")

        self.host_mpv = FakeMpvServer(self.host_sock)
        self.guest_mpv = FakeMpvServer(self.guest_sock)
        await self.host_mpv.start()
        await self.guest_mpv.start()

        host_cfg = {
            "room": "e2e-room",
            "secret": "e2e-secret",
            "relay": f"ws://127.0.0.1:{self.port}",
            "role": "host",
            "socket": self.host_sock
        }
        guest_cfg = {
            "room": "e2e-room",
            "secret": "e2e-secret",
            "relay": f"ws://127.0.0.1:{self.port}",
            "role": "guest",
            "socket": self.guest_sock
        }

        self.host_ctrl = WatchPartyController(host_cfg)
        self.guest_ctrl = WatchPartyController(guest_cfg)

        await self.host_ctrl.start()
        await self.guest_ctrl.start()

        # Allow connections to establish
        await asyncio.sleep(0.5)

    async def asyncTearDown(self):
        await self.guest_ctrl.stop()
        await self.host_ctrl.stop()
        await self.guest_mpv.stop()
        await self.host_mpv.stop()
        await self.relay.stop()
        self.guest_tmpdir.cleanup()
        self.host_tmpdir.cleanup()

    async def test_end_to_end_playback_synchronization(self):
        # 1. Verify initial connections
        self.assertTrue(self.host_ctrl.relay_client.connected)
        self.assertTrue(self.guest_ctrl.relay_client.connected)
        self.assertTrue(self.host_ctrl.mpv_client.connected)
        self.assertTrue(self.guest_ctrl.mpv_client.connected)

        # 2. Host Pause Event
        # Host pauses local player
        await self.host_ctrl.mpv_client.set_pause(True)
        # Wait for propagation (Host MPV -> Host Controller -> Relay -> Guest Controller -> Guest MPV)
        await asyncio.sleep(0.3)
        self.assertTrue(self.host_mpv.properties["pause"])
        self.assertTrue(self.guest_mpv.properties["pause"])

        # 3. Host Resume Event
        await self.host_ctrl.mpv_client.set_pause(False)
        await asyncio.sleep(0.3)
        self.assertFalse(self.host_mpv.properties["pause"])
        self.assertFalse(self.guest_mpv.properties["pause"])

        # 4. Host Seek Event
        # Local seek jump > 2.0s
        await self.host_ctrl.mpv_client.seek(42.0)
        await asyncio.sleep(0.3)
        self.assertEqual(self.host_mpv.properties["time-pos"], 42.0)
        self.assertAlmostEqual(self.guest_mpv.properties["time-pos"], 42.0, delta=0.5)

        # 5. Shared Guest Control (default mode: guest pause & seek syncs to host)
        await self.guest_ctrl.mpv_client.set_pause(True)
        await asyncio.sleep(0.3)
        self.assertTrue(self.guest_mpv.properties["pause"])
        self.assertTrue(self.host_mpv.properties["pause"])

        await self.guest_ctrl.mpv_client.seek(99.0)
        await asyncio.sleep(0.3)
        self.assertAlmostEqual(self.host_mpv.properties["time-pos"], 99.0, delta=0.5)

        # 6. Status file persistence test
        host_status_file = os.path.join(self.host_tmpdir.name, "status.json")
        self.assertTrue(os.path.exists(host_status_file))
        with open(host_status_file, "r") as f:
            h_stat = json.load(f)
            self.assertEqual(h_stat["role"], "host")
            self.assertEqual(h_stat["room"], "e2e-room")
            self.assertTrue(h_stat["relay_connected"])
            self.assertTrue(h_stat["mpv_connected"])

        guest_status_file = os.path.join(self.guest_tmpdir.name, "status.json")
        self.assertTrue(os.path.exists(guest_status_file))
        with open(guest_status_file, "r") as f:
            g_stat = json.load(f)
            self.assertEqual(g_stat["role"], "guest")
            self.assertEqual(g_stat["room"], "e2e-room")

if __name__ == "__main__":
    unittest.main()

