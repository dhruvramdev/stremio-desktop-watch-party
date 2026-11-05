"""
Tests for Relay Server and Relay Client.
"""

import asyncio
import unittest
import websockets
import json

from relay.server import RelayServer
from controller.relay_client import RelayClient


class TestRelay(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.port = 8766
        self.secret = "test-secret-123"
        self.server = RelayServer(host="127.0.0.1", port=self.port, default_secret=self.secret)
        await self.server.start()
        # Give server a moment to bind
        await asyncio.sleep(0.1)

    async def asyncTearDown(self):
        await self.server.stop()
        await asyncio.sleep(0.1)

    async def test_room_authentication_valid(self):
        uri = f"ws://127.0.0.1:{self.port}"
        async with websockets.connect(uri) as ws:
            # Send hello
            await ws.send(json.dumps({
                "type": "hello",
                "room": "room1",
                "sender": "host_user",
                "secret": self.secret,
                "role": "host",
                "version": 1
            }))
            resp = await asyncio.wait_for(ws.recv(), timeout=2.0)
            data = json.loads(resp)
            self.assertEqual(data["type"], "welcome")
            self.assertEqual(data["role"], "host")
            self.assertIn("host_user", data["clients"])

    async def test_room_authentication_invalid(self):
        uri = f"ws://127.0.0.1:{self.port}"
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({
                "type": "hello",
                "room": "room1",
                "sender": "bad_user",
                "secret": "wrong-secret",
                "role": "guest",
                "version": 1
            }))
            resp = await asyncio.wait_for(ws.recv(), timeout=2.0)
            data = json.loads(resp)
            self.assertEqual(data["type"], "error")
            self.assertEqual(data["message"], "unauthorized")

    async def test_host_control_broadcasting_to_guest(self):
        uri = f"ws://127.0.0.1:{self.port}"
        
        guest_messages = []
        async def on_guest_message(msg):
            guest_messages.append(msg)

        # Connect host via websockets
        async with websockets.connect(uri) as host_ws:
            await host_ws.send(json.dumps({
                "type": "hello",
                "room": "room_ctrl",
                "sender": "host1",
                "secret": self.secret,
                "role": "host",
                "version": 1
            }))
            welcome = json.loads(await host_ws.recv())
            self.assertEqual(welcome["type"], "welcome")

            # Connect guest via RelayClient
            guest_client = RelayClient(
                url=uri,
                room="room_ctrl",
                sender="guest1",
                secret=self.secret,
                role="guest",
                on_message_callback=on_guest_message
            )
            await guest_client.start()
            # Wait for connection and welcome
            await asyncio.sleep(0.5)

            # Host sends play control
            play_msg = {
                "type": "play",
                "seq": 1,
                "sent_at_ms": 123456789,
                "time": 10.5
            }
            await host_ws.send(json.dumps(play_msg))

            # Wait for guest to receive
            await asyncio.sleep(0.5)
            await guest_client.stop()

            # Verify guest received play message
            play_received = [m for m in guest_messages if m.get("type") == "play"]
            self.assertTrue(len(play_received) > 0)
            self.assertEqual(play_received[0]["time"], 10.5)
            self.assertEqual(play_received[0]["sender"], "host1")

    async def test_shared_control_and_host_only_lock(self):
        uri = f"ws://127.0.0.1:{self.port}"

        # 1. Shared mode (default): guest CAN broadcast play/pause/seek to host
        async with websockets.connect(uri) as host_ws:
            await host_ws.send(json.dumps({
                "type": "hello",
                "room": "room_shared",
                "sender": "host_s",
                "secret": self.secret,
                "role": "host",
                "host_only": False,
                "version": 1
            }))
            await host_ws.recv()  # welcome

            async with websockets.connect(uri) as guest_ws:
                await guest_ws.send(json.dumps({
                    "type": "hello",
                    "room": "room_shared",
                    "sender": "guest_s",
                    "secret": self.secret,
                    "role": "guest",
                    "version": 1
                }))
                await guest_ws.recv()  # welcome
                await host_ws.recv()   # join notification

                await guest_ws.send(json.dumps({
                    "type": "pause",
                    "seq": 1,
                    "sent_at_ms": 123456789,
                    "time_s": 42.0,
                    "paused": True
                }))
                relayed = json.loads(await asyncio.wait_for(host_ws.recv(), timeout=2.0))
                self.assertEqual(relayed["type"], "pause")
                self.assertEqual(relayed["sender"], "guest_s")

        # 2. Host-only mode (host_only=True): guest play/pause/seek is rejected
        async with websockets.connect(uri) as host_ws:
            await host_ws.send(json.dumps({
                "type": "hello",
                "room": "room_locked",
                "sender": "host_locked",
                "secret": self.secret,
                "role": "host",
                "host_only": True,
                "version": 1
            }))
            await host_ws.recv()  # welcome

            async with websockets.connect(uri) as guest_ws:
                await guest_ws.send(json.dumps({
                    "type": "hello",
                    "room": "room_locked",
                    "sender": "guest_rogue",
                    "secret": self.secret,
                    "role": "guest",
                    "version": 1
                }))
                welcome = json.loads(await guest_ws.recv())
                self.assertEqual(welcome["type"], "welcome")
                self.assertTrue(welcome["host_only"])

                await guest_ws.send(json.dumps({
                    "type": "play",
                    "seq": 1,
                    "sent_at_ms": 123456789,
                    "time_s": 0.0
                }))

                resp = json.loads(await asyncio.wait_for(guest_ws.recv(), timeout=2.0))
                self.assertEqual(resp["type"], "error")
                self.assertEqual(resp["message"], "only host can broadcast controls")

    async def test_disconnection_and_room_state_cleanup(self):
        uri = f"ws://127.0.0.1:{self.port}"
        
        host_leave_received = []

        async with websockets.connect(uri) as host_ws:
            await host_ws.send(json.dumps({
                "type": "hello",
                "room": "room_leave",
                "sender": "host_l",
                "secret": self.secret,
                "role": "host",
                "version": 1
            }))
            await host_ws.recv() # welcome

            async with websockets.connect(uri) as guest_ws:
                await guest_ws.send(json.dumps({
                    "type": "hello",
                    "room": "room_leave",
                    "sender": "guest_l",
                    "secret": self.secret,
                    "role": "guest",
                    "version": 1
                }))
                await guest_ws.recv() # welcome
                # host also receives join message
                join_msg = json.loads(await host_ws.recv())
                self.assertEqual(join_msg["type"], "join")

                # Guest disconnects by closing ws
                await guest_ws.close()

                # Host should receive leave message
                leave_msg = json.loads(await asyncio.wait_for(host_ws.recv(), timeout=2.0))
                self.assertEqual(leave_msg["type"], "leave")
                self.assertEqual(leave_msg["sender"], "guest_l")

        # Verify room cleaned up
        await asyncio.sleep(0.1)
        self.assertNotIn("room_leave", self.server.rooms)


if __name__ == "__main__":
    unittest.main()
