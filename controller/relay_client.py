"""
Relay Client for Stremio Desktop Watch Party.
"""

import asyncio
import json
import time
import websockets
from loguru import logger

from relay.protocol import Role, MessageType, HelloMessage, ControlMessage


class RelayClient:
    def __init__(
        self,
        url: str,
        room: str,
        sender: str,
        secret: str,
        role: str | Role = Role.GUEST,
        host_only: bool = False,
        on_message_callback=None,
        get_state_callback=None
    ):
        self.url = url
        self.room = room
        self.sender = sender
        self.secret = secret
        self.role = Role(role) if isinstance(role, str) else role
        self.host_only = bool(host_only)
        self.on_message_callback = on_message_callback
        self.get_state_callback = get_state_callback

        self.websocket = None
        self.seq = 0
        self.connected = False
        self._running = False
        self._heartbeat_task = None
        self._connect_task = None
        self.clients = []

    async def start(self):
        self._running = True
        self._connect_task = asyncio.create_task(self._connect_loop())

    async def stop(self):
        self._running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._connect_task:
            self._connect_task.cancel()
        if self.websocket:
            await self.websocket.close()
        logger.info("RelayClient stopped")

    async def _connect_loop(self):
        backoff = 0.5
        max_backoff = 10.0

        while self._running:
            try:
                logger.info(f"Connecting to relay at {self.url}...")
                async with websockets.connect(self.url) as websocket:
                    self.websocket = websocket
                    self.connected = True
                    backoff = 0.5  # reset backoff on success
                    logger.info(f"Connected to relay. Sending hello for room {self.room}...")

                    # Send hello handshake via Pydantic model
                    hello_msg = HelloMessage(
                        room=self.room,
                        sender=self.sender,
                        secret=self.secret,
                        role=self.role,
                        host_only=self.host_only
                    )
                    await websocket.send(hello_msg.model_dump_json())

                    # Start heartbeat task if host
                    if self.role == Role.HOST:
                        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

                    # Listen for incoming messages
                    async for raw_msg in websocket:
                        try:
                            msg = json.loads(raw_msg)
                            await self._handle_incoming(msg)
                        except json.JSONDecodeError:
                            logger.error(f"Received invalid JSON: {raw_msg}")

            except (websockets.exceptions.ConnectionClosed, ConnectionRefusedError, asyncio.TimeoutError) as e:
                logger.warning(f"Connection lost or failed: {e}. Reconnecting in {backoff}s...")
                self.connected = False
                self.websocket = None
                if self._heartbeat_task:
                    self._heartbeat_task.cancel()
                    self._heartbeat_task = None

                if not self._running:
                    break

                await asyncio.sleep(backoff)
                backoff = min(max_backoff, backoff * 2)
            except Exception as e:
                logger.error(f"Unexpected error in connect loop: {e}")
                self.connected = False
                self.websocket = None
                if self._heartbeat_task:
                    self._heartbeat_task.cancel()
                    self._heartbeat_task = None

                if not self._running:
                    break

                await asyncio.sleep(backoff)
                backoff = min(max_backoff, backoff * 2)

    async def _handle_incoming(self, msg: dict):
        msg_type = msg.get("type")
        if msg_type == MessageType.WELCOME.value:
            self.clients = msg.get("clients", [])
            logger.info(f"Welcome received. Role: {msg.get('role')}, Clients: {self.clients}")
        elif msg_type == MessageType.JOIN.value:
            client = msg.get("sender")
            if client and client not in self.clients:
                self.clients.append(client)
            logger.info(f"Client joined: {client}")
        elif msg_type == MessageType.LEAVE.value:
            client = msg.get("sender")
            if client in self.clients:
                self.clients.remove(client)
            logger.info(f"Client left: {client}")

        if self.on_message_callback:
            if asyncio.iscoroutinefunction(self.on_message_callback):
                await self.on_message_callback(msg)
            else:
                self.on_message_callback(msg)

    async def _heartbeat_loop(self):
        try:
            while self._running and self.connected:
                await asyncio.sleep(3.0)
                if self.role == Role.HOST and self.connected and self.websocket:
                    state_payload = {}
                    if self.get_state_callback:
                        state_payload = self.get_state_callback() or {}
                    await self.send_control(MessageType.STATE, {
                        "room": self.room,
                        "sender": self.sender,
                        "time_s": state_payload.get("time_s", 0.0),
                        "paused": state_payload.get("paused", False),
                        "duration_s": state_payload.get("duration_s", 0.0),
                        "status": "heartbeat"
                    })
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in heartbeat loop: {e}")

    async def send_control(self, msg_type: str | MessageType, payload: dict):
        if not self.connected or not self.websocket:
            logger.warning("Cannot send control message: not connected")
            return False

        type_str = msg_type.value if isinstance(msg_type, MessageType) else str(msg_type)
        self.seq += 1
        msg = {
            "type": type_str,
            "seq": self.seq,
            "sent_at_ms": int(time.time() * 1000),
            **payload
        }
        try:
            await self.websocket.send(json.dumps(msg))
            return True
        except Exception as e:
            logger.error(f"Failed to send control message {type_str}: {e}")
            return False
