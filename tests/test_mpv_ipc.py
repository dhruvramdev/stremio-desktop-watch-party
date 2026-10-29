import asyncio
import os
import tempfile
import unittest

from tests.fake_mpv import FakeMpvServer
from controller.mpv_ipc import MpvIpcClient

class TestMpvIpc(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(dir="/tmp")
        self.socket_path = os.path.join(self.tmpdir.name, "mpv.sock")
        self.fake_server = FakeMpvServer(self.socket_path)
        await self.fake_server.start()

    async def asyncTearDown(self):
        await self.fake_server.stop()
        self.tmpdir.cleanup()

    async def test_connect_and_observations(self):
        property_changes = []
        def on_change(name, data):
            property_changes.append((name, data))

        client = MpvIpcClient(self.socket_path, on_property_change=on_change)
        await client.start()

        await asyncio.sleep(0.2)
        self.assertTrue(client.connected)

        await client.set_pause(True)
        await asyncio.sleep(0.1)

        self.assertEqual(self.fake_server.properties["pause"], True)
        self.assertIn(("pause", True), property_changes)

        time_pos = await client.get_time_pos()
        self.assertEqual(time_pos, 0.0)

        await client.seek(45.0)
        await asyncio.sleep(0.1)
        self.assertEqual(self.fake_server.properties["time-pos"], 45.0)

        await client.stop()

if __name__ == "__main__":
    unittest.main()

