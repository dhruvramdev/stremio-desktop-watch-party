import asyncio
import json
import logging

logger = logging.getLogger("fake_mpv")

class FakeMpvServer:
    def __init__(self, socket_path):
        self.socket_path = socket_path
        self.server = None
        self.properties = {
            "pause": False,
            "time-pos": 0.0,
            "duration": 120.0,
            "seeking": False,
            "paused-for-cache": False,
            "path": "test.mp4"
        }
        self.observers = {} # obs_id -> property_name
        self.property_observers = {} # property_name -> set of (writer, obs_id)
        self.writers = set()

    async def start(self):
        self.server = await asyncio.start_unix_server(self._handle_client, self.socket_path)

    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def _handle_client(self, reader, writer):
        self.writers.add(writer)
        lock = asyncio.Lock()
        setattr(writer, '_lock', lock)
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode().strip())
                except json.JSONDecodeError:
                    continue
                
                cmd = msg.get("command", [])
                req_id = msg.get("request_id")

                if not cmd:
                    continue

                action = cmd[0]
                response = {"error": "success"}
                if req_id is not None:
                    response["request_id"] = req_id

                if action == "observe_property":
                    obs_id = cmd[1]
                    prop_name = cmd[2]
                    self.observers[obs_id] = prop_name
                    if prop_name not in self.property_observers:
                        self.property_observers[prop_name] = set()
                    self.property_observers[prop_name].add((writer, obs_id))
                    if req_id is None:
                        response["request_id"] = obs_id

                elif action == "get_property":
                    prop_name = cmd[1]
                    response["data"] = self.properties.get(prop_name)

                elif action == "set_property":
                    prop_name = cmd[1]
                    val = cmd[2]
                    self.properties[prop_name] = val
                    response["data"] = val
                    await self._broadcast_property_change(prop_name, val)

                elif action == "seek":
                    target = float(cmd[1])
                    self.properties["time-pos"] = target
                    response["data"] = target
                    await self._broadcast_property_change("time-pos", target)

                async with lock:
                    writer.write((json.dumps(response) + "\n").encode())
                    await writer.drain()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"FakeMpv client error: {e}")
        finally:
            self.writers.discard(writer)
            for prop_name, obs_set in list(self.property_observers.items()):
                self.property_observers[prop_name] = {(w, oid) for w, oid in obs_set if w != writer}
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _broadcast_property_change(self, prop_name, val):
        if prop_name in self.property_observers:
            for writer, obs_id in self.property_observers[prop_name]:
                event = {
                    "event": "property-change",
                    "id": obs_id,
                    "name": prop_name,
                    "data": val
                }
                try:
                    lock = getattr(writer, '_lock', None)
                    if lock:
                        async with lock:
                            writer.write((json.dumps(event) + "\n").encode())
                            await writer.drain()
                    else:
                        writer.write((json.dumps(event) + "\n").encode())
                        await writer.drain()
                except Exception:
                    pass

    async def mock_playback_tick(self, time_delta):
        if not self.properties.get("pause", False):
            self.properties["time-pos"] += time_delta
            if self.properties["time-pos"] > self.properties["duration"]:
                self.properties["time-pos"] = self.properties["duration"]
            await self._broadcast_property_change("time-pos", self.properties["time-pos"])

    async def mock_user_seek(self, new_pos):
        self.properties["time-pos"] = float(new_pos)
        await self._broadcast_property_change("time-pos", self.properties["time-pos"])
