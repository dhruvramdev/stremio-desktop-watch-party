"""
WebSocket Relay Server for Stremio Watch Party.
"""

import asyncio
import hmac
import json
import websockets
from loguru import logger
from websockets.server import WebSocketServerProtocol

from relay.protocol import (
    Role,
    MessageType,
    HelloMessage,
    WelcomeMessage,
    PeerEventMessage,
    ControlMessage,
    ErrorMessage
)


class Room:
    def __init__(self, room_id: str, secret: str):
        self.room_id = room_id
        self.secret = secret
        self.host_websocket: WebSocketServerProtocol | None = None
        self.host_sender: str | None = None
        # map websocket -> client info dict {"sender": str, "role": Role}
        self.clients: dict[WebSocketServerProtocol, dict] = {}

    def is_authenticated_host(self, websocket: WebSocketServerProtocol) -> bool:
        return self.host_websocket == websocket


class RelayServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 8765, default_secret: str = "secret"):
        self.host = host
        self.port = port
        self.default_secret = default_secret
        # room_id -> Room
        self.rooms: dict[str, Room] = {}
        self.server = None

    def verify_secret(self, provided_secret: str, expected_secret: str) -> bool:
        if not provided_secret or not expected_secret:
            return False
        return hmac.compare_digest(provided_secret.encode("utf-8"), expected_secret.encode("utf-8"))

    async def register_room(self, room_id: str, secret: str) -> Room:
        if room_id not in self.rooms:
            self.rooms[room_id] = Room(room_id, secret)
        return self.rooms[room_id]

    async def handle_connection(self, websocket: WebSocketServerProtocol):
        room: Room | None = None
        sender: str | None = None
        role: Role = Role.GUEST

        try:
            # First message must be hello handshake
            raw_msg = await asyncio.wait_for(websocket.recv(), timeout=10.0)
            try:
                data = json.loads(raw_msg)
            except json.JSONDecodeError:
                await websocket.send(ErrorMessage(message="invalid json").model_dump_json())
                await websocket.close()
                return

            if data.get("type") != MessageType.HELLO.value:
                await websocket.send(ErrorMessage(message="expected hello handshake").model_dump_json())
                await websocket.close()
                return

            try:
                hello = HelloMessage.model_validate(data)
            except Exception as e:
                await websocket.send(ErrorMessage(message=f"invalid hello: {e}").model_dump_json())
                await websocket.close()
                return

            room_id = hello.room
            sender = hello.sender
            provided_secret = hello.secret
            requested_role = hello.role

            # Get or create room (hosts establish room secret if new)
            if room_id not in self.rooms:
                room_secret = provided_secret if (requested_role == Role.HOST and provided_secret) else self.default_secret
                room = await self.register_room(room_id, room_secret)
            else:
                room = self.rooms[room_id]

            # Verify secret
            if not self.verify_secret(provided_secret, room.secret):
                await websocket.send(ErrorMessage(message="unauthorized").model_dump_json())
                await websocket.close()
                return

            # Determine role
            if requested_role == Role.HOST:
                if room.host_websocket is not None and room.host_websocket != websocket:
                    await websocket.send(ErrorMessage(message="host already exists").model_dump_json())
                    await websocket.close()
                    return
                else:
                    room.host_websocket = websocket
                    room.host_sender = sender
                    role = Role.HOST
            else:
                role = Role.GUEST

            # Register client
            room.clients[websocket] = {"sender": sender, "role": role}

            # Send welcome message
            client_senders = [c["sender"] for c in room.clients.values()]
            welcome_msg = WelcomeMessage(
                room=room_id,
                role=role,
                clients=client_senders
            )
            await websocket.send(welcome_msg.model_dump_json())

            # Broadcast join to others in room
            join_msg = PeerEventMessage(
                type=MessageType.JOIN,
                room=room_id,
                sender=sender,
                role=role
            )
            await self.broadcast_to_room(room, join_msg.model_dump(), exclude=websocket)

            logger.info(f"Client {sender} ({role.value}) joined room {room_id}")

            # Message loop
            async for raw_msg in websocket:
                try:
                    msg = json.loads(raw_msg)
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get("type")

                # Handle ping/pong if needed
                if msg_type == MessageType.PING.value:
                    await websocket.send(json.dumps({"type": MessageType.PONG.value}))
                    continue

                # Control message forwarding: play, pause, seek, state
                if msg_type in (MessageType.PLAY.value, MessageType.PAUSE.value, MessageType.SEEK.value, MessageType.STATE.value):
                    if not room.is_authenticated_host(websocket):
                        await websocket.send(ErrorMessage(message="only host can broadcast controls").model_dump_json())
                        continue
                    
                    # Broadcast valid host message to all other clients in the room
                    msg["room"] = room_id
                    msg["sender"] = sender
                    await self.broadcast_to_room(room, msg, exclude=websocket)

        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as e:
            logger.error(f"Error handling connection: {e}")
        finally:
            if room and websocket in room.clients:
                client_info = room.clients.pop(websocket)
                left_sender = client_info["sender"]
                is_host = (room.host_websocket == websocket)
                if is_host:
                    room.host_websocket = None
                    room.host_sender = None

                logger.info(f"Client {left_sender} left room {room.room_id}")

                # Broadcast leave
                leave_msg = PeerEventMessage(
                    type=MessageType.LEAVE,
                    room=room.room_id,
                    sender=left_sender
                )
                await self.broadcast_to_room(room, leave_msg.model_dump())

                # Cleanup empty rooms
                if not room.clients:
                    self.rooms.pop(room.room_id, None)

    async def broadcast_to_room(self, room: Room, message: dict, exclude: WebSocketServerProtocol | None = None):
        if not room.clients:
            return
        payload = json.dumps(message)
        coros = [
            client.send(payload)
            for client in room.clients.keys()
            if client != exclude
        ]
        if coros:
            await asyncio.gather(*coros, return_exceptions=True)

    async def start(self):
        self.server = await websockets.serve(self.handle_connection, self.host, self.port, reuse_address=True)
        logger.info(f"RelayServer started on ws://{self.host}:{self.port}")

    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            logger.info("RelayServer stopped")


async def _main_async():
    server = RelayServer()
    await server.start()
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    import signal
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):
            pass
    try:
        await stop_event.wait()
    finally:
        await server.stop()


if __name__ == "__main__":
    try:
        asyncio.run(_main_async())
    except KeyboardInterrupt:
        pass
