"""
Controller main entry point for Stremio Desktop Watch Party daemon.
"""

import asyncio
import os
import signal
import sys
import time
import uuid
from loguru import logger

from controller.config import load_config
from controller.mpv_ipc import MpvIpcClient
from controller.sync import SyncEngine
from controller.relay_client import RelayClient
from relay.protocol import Role, MessageType, SessionStatus, PlaybackState, RecentEvent


class WatchPartyController:
    def __init__(self, config: dict):
        self.config = config
        self.socket_path = config.get("socket")
        if not self.socket_path:
            raise ValueError("Socket path is required for controller")

        self.room = config.get("room")
        self.secret = config.get("secret")
        self.relay_url = config.get("relay")
        role_raw = config.get("role", "guest")
        self.role = Role(role_raw) if isinstance(role_raw, str) else role_raw
        self.sender = config.get("sender") or f"user-{uuid.uuid4().hex[:8]}"

        self.recent_events = []
        self._running = False
        self._status_task = None

        self.sync_engine = SyncEngine(on_local_event=self.handle_local_event)
        self.mpv_client = MpvIpcClient(
            socket_path=self.socket_path,
            on_property_change=self.handle_property_change
        )
        self.relay_client = RelayClient(
            url=self.relay_url,
            room=self.room,
            sender=self.sender,
            secret=self.secret,
            role=self.role,
            on_message_callback=self.handle_remote_message,
            get_state_callback=lambda: self.sync_engine.state
        )

    def _write_status(self, event_text: str = None):
        if not self.socket_path:
            return
        runtime_dir = os.path.dirname(self.socket_path)
        if not os.path.isdir(runtime_dir):
            return

        status_path = os.path.join(runtime_dir, "status.json")
        now = time.time()

        if event_text:
            self.recent_events.append({
                "time": now,
                "text": event_text
            })
            if len(self.recent_events) > 15:
                self.recent_events.pop(0)

        status_obj = SessionStatus(
            updated_at=now,
            role=self.role,
            room=self.room,
            relay_url=self.relay_url,
            relay_connected=self.relay_client.connected,
            mpv_connected=self.mpv_client.connected,
            clients=self.relay_client.clients,
            state=PlaybackState(**self.sync_engine.state),
            recent_events=[RecentEvent(**ev) for ev in self.recent_events]
        )

        tmp_path = status_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(status_obj.model_dump_json(indent=2))
            os.replace(tmp_path, status_path)
        except Exception:
            pass

    def handle_property_change(self, name, data):
        self.sync_engine.update_property(name, data)
        self._write_status()

    def handle_local_event(self, event_type, data):
        if self.role != Role.HOST:
            logger.debug(f"Ignoring local event {event_type} as guest role")
            return

        logger.info(f"Local event triggered: {event_type} with data {data}")
        if event_type == "local_pause":
            paused = data.get("paused")
            msg_type = MessageType.PAUSE if paused else MessageType.PLAY
            evt_msg = f"Local {'pause' if paused else 'play'} at {self.sync_engine.state['time_s']:.1f}s"
            self._write_status(evt_msg)
            asyncio.create_task(
                self.relay_client.send_control(msg_type, {
                    "room": self.room,
                    "sender": self.sender,
                    "time_s": self.sync_engine.state["time_s"]
                })
            )
        elif event_type == "local_seek":
            time_s = data.get("time_s")
            evt_msg = f"Local seek to {time_s:.1f}s"
            self._write_status(evt_msg)
            asyncio.create_task(
                self.relay_client.send_control(MessageType.SEEK, {
                    "room": self.room,
                    "sender": self.sender,
                    "time_s": time_s,
                    "paused": self.sync_engine.state["paused"]
                })
            )

    async def handle_remote_message(self, msg: dict):
        msg_type = msg.get("type")
        sender = msg.get("sender")
        if sender == self.sender:
            return  # ignore own messages

        logger.info(f"Received remote message [{msg_type}] from {sender}: {msg}")

        if msg_type == MessageType.JOIN.value:
            self._write_status(f"Peer '{sender}' joined room")
            return
        elif msg_type == MessageType.LEAVE.value:
            self._write_status(f"Peer '{sender}' left room")
            return
        elif msg_type == MessageType.WELCOME.value:
            self._write_status(f"Connected to room '{self.room}' as {self.role.value}")
            return

        if self.role == Role.HOST:
            # Host ignores incoming control commands from guests to maintain authority
            return

        # Guest handling host commands
        if msg_type in (MessageType.PLAY.value, MessageType.PAUSE.value, MessageType.SEEK.value, MessageType.STATE.value):
            host_time = msg.get("time_s", 0.0)
            host_paused = msg.get("paused", msg_type == MessageType.PAUSE.value)
            sent_at_ms = msg.get("sent_at_ms", int(time.time() * 1000))
            now_ms = int(time.time() * 1000)

            local_time = self.sync_engine.state["time_s"]
            local_paused = self.sync_engine.state["paused"]

            drift, expected_time, needs_seek = self.sync_engine.calculate_drift(
                local_time, host_time, sent_at_ms, now_ms, host_paused
            )
            self.sync_engine.state["drift_s"] = drift

            logger.info(f"Drift analysis: drift={drift:.2f}s, needs_seek={needs_seek}, host_paused={host_paused}, local_paused={local_paused}")

            if msg_type == MessageType.SEEK.value or needs_seek:
                self.sync_engine.register_remote_command("time-pos", expected_time)
                try:
                    await self.mpv_client.seek(expected_time)
                    action_msg = f"Host seek -> {expected_time:.1f}s (drift: {drift*1000:.0f}ms)"
                    self._write_status(action_msg)
                    logger.info(f"Enforced remote seek to {expected_time:.2f}s")
                except Exception as e:
                    logger.error(f"Failed to execute remote seek: {e}")

            if local_paused != host_paused:
                self.sync_engine.register_remote_command("pause", host_paused)
                try:
                    await self.mpv_client.set_pause(host_paused)
                    action_msg = f"Host {'paused' if host_paused else 'resumed'} playback"
                    self._write_status(action_msg)
                    logger.info(f"Enforced remote pause state: {host_paused}")
                except Exception as e:
                    logger.error(f"Failed to execute remote pause: {e}")

            self._write_status()

    async def _status_writer_loop(self):
        try:
            while self._running:
                await asyncio.sleep(0.5)
                self._write_status()
        except asyncio.CancelledError:
            pass

    async def start(self):
        self._running = True
        logger.info(f"Starting Watch Party controller daemon (role={self.role.value}, room={self.room})")
        self._write_status(f"Starting controller daemon ({self.role.value})")
        await self.mpv_client.start()
        await self.relay_client.start()
        self._status_task = asyncio.create_task(self._status_writer_loop())

    async def stop(self):
        self._running = False
        if self._status_task:
            self._status_task.cancel()
        logger.info("Stopping Watch Party controller daemon...")
        self._write_status("Stopped controller daemon")
        await self.relay_client.stop()
        await self.mpv_client.stop()


async def async_main(config=None):
    if not config:
        config = load_config()

    controller = WatchPartyController(config)
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def handle_signal():
        logger.info("Received stop signal")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_signal)
        except (NotImplementedError, RuntimeError):
            pass

    await controller.start()

    try:
        await stop_event.wait()
    finally:
        await controller.stop()


def main():
    config = load_config()
    try:
        asyncio.run(async_main(config))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
