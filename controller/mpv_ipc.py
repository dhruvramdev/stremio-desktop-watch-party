import asyncio
import json
from loguru import logger

class MpvIpcClient:
    def __init__(self, socket_path, on_property_change=None):
        self.socket_path = socket_path
        self.on_property_change = on_property_change # callback(name, data)
        self.reader = None
        self.writer = None
        self._request_id_counter = 0
        self._pending_requests = {} # request_id -> asyncio.Future
        self._running = False
        self._connect_task = None
        self._read_task = None
        self.connected = False

    async def start(self):
        self._running = True
        self._connect_task = asyncio.create_task(self._connection_loop())

    async def stop(self):
        self._running = False
        if self._connect_task:
            self._connect_task.cancel()
        if self._read_task:
            self._read_task.cancel()
        await self._close_socket()

    async def _close_socket(self):
        self.connected = False
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
            self.writer = None
            self.reader = None
        for fut in self._pending_requests.values():
            if not fut.done():
                fut.set_exception(ConnectionError("Socket disconnected"))
        self._pending_requests.clear()

    async def _connection_loop(self):
        while self._running:
            try:
                logger.info(f"Connecting to MPV IPC socket at {self.socket_path}")
                self.reader, self.writer = await asyncio.open_unix_connection(self.socket_path)
                self.connected = True
                logger.info("Connected to MPV IPC socket")
                
                # Start reading responses/events first
                self._read_task = asyncio.create_task(self._read_loop())

                # Then register observations
                await self._register_observations()

                await self._read_task
            except (asyncio.CancelledError, Exception) as e:
                if not self._running:
                    break
                logger.warning(f"MPV IPC connection error: {e}, retrying in 1s...")
                await self._close_socket()
                await asyncio.sleep(1.0)

    async def _register_observations(self):
        observations = [
            (1, "pause"),
            (2, "time-pos"),
            (3, "duration"),
            (4, "seeking"),
            (5, "paused-for-cache"),
            (6, "path"),
            (7, "media-title"),
        ]
        for obs_id, prop in observations:
            await self.command(["observe_property", obs_id, prop])

    async def _read_loop(self):
        try:
            while self._running and self.reader:
                line = await self.reader.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode().strip())
                except json.JSONDecodeError:
                    continue

                if "event" in msg:
                    event_type = msg.get("event")
                    if event_type == "property-change":
                        name = msg.get("name")
                        data = msg.get("data")
                        if self.on_property_change:
                            if asyncio.iscoroutinefunction(self.on_property_change):
                                asyncio.create_task(self.on_property_change(name, data))
                            else:
                                self.on_property_change(name, data)
                elif "request_id" in msg:
                    req_id = msg.get("request_id")
                    if req_id in self._pending_requests:
                        fut = self._pending_requests.pop(req_id)
                        if not fut.done():
                            error = msg.get("error")
                            if error and error != "success":
                                fut.set_exception(Exception(f"MPV error: {error}"))
                            else:
                                fut.set_result(msg.get("data"))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Error in MPV read loop: {e}")
            raise

    async def command(self, cmd_args):
        if not self.writer or not self.connected:
            raise ConnectionError("Not connected to MPV IPC socket")
        
        self._request_id_counter += 1
        req_id = self._request_id_counter
        
        payload = {
            "command": cmd_args,
            "request_id": req_id
        }
        
        fut = asyncio.get_running_loop().create_future()
        self._pending_requests[req_id] = fut

        self.writer.write((json.dumps(payload) + "\n").encode())
        await self.writer.drain()

        return await asyncio.wait_for(fut, timeout=5.0)

    async def set_pause(self, pause: bool):
        return await self.command(["set_property", "pause", pause])

    async def seek(self, time_s: float):
        return await self.command(["seek", str(time_s), "absolute"])

    async def get_time_pos(self):
        res = await self.command(["get_property", "time-pos"])
        return float(res) if res is not None else 0.0

    async def get_duration(self):
        res = await self.command(["get_property", "duration"])
        return float(res) if res is not None else 0.0
