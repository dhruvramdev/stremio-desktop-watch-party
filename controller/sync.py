import time
from loguru import logger

class SyncEngine:
    def __init__(self, on_local_event=None):
        self.on_local_event = on_local_event # callback(event_type, data)
        self.state = {
            "paused": False,
            "time_s": 0.0,
            "duration_s": 0.0,
            "seeking": False,
            "buffering": False,
            "loaded": False,
            "media_title": "",
            "drift_s": 0.0
        }
        self.last_time_update_monotonic = time.monotonic()
        self.echo_suppressions = {}

    def update_property(self, name, data):
        now_mono = time.monotonic()
        if name == "pause":
            old_paused = self.state["paused"]
            new_paused = bool(data)
            self.state["paused"] = new_paused
            
            if self._is_suppressed("pause", new_paused):
                logger.debug(f"Echo suppressed local pause change: {new_paused}")
            elif old_paused != new_paused:
                if self.on_local_event:
                    self.on_local_event("local_pause", {"paused": new_paused})

        elif name == "time-pos":
            new_time = float(data) if data is not None else self.state["time_s"]
            dt = now_mono - self.last_time_update_monotonic
            old_time = self.state["time_s"]
            self.state["time_s"] = new_time
            self.last_time_update_monotonic = now_mono

            if self._is_suppressed("time-pos", new_time, tolerance=0.5):
                logger.debug(f"Echo suppressed local time-pos change: {new_time}")
            else:
                if self.state["paused"]:
                    diff = abs(new_time - old_time)
                    if diff > 2.0:
                        logger.info(f"Local seek (paused) detected! new_time={new_time}, old_time={old_time}, diff={diff}")
                        if self.on_local_event:
                            self.on_local_event("local_seek", {"time_s": new_time})
                elif dt > 0 and dt < 10.0:
                    expected_time = old_time + dt
                    diff = abs(new_time - expected_time)
                    if diff > 2.0:
                        logger.info(f"Local seek detected! new_time={new_time}, expected={expected_time}, diff={diff}")
                        if self.on_local_event:
                            self.on_local_event("local_seek", {"time_s": new_time})

        elif name == "duration":
            self.state["duration_s"] = float(data) if data is not None else 0.0
        elif name == "seeking":
            self.state["seeking"] = bool(data)
        elif name == "paused-for-cache":
            self.state["buffering"] = bool(data)
        elif name == "path":
            self.state["loaded"] = bool(data)
            if data and not self.state.get("media_title"):
                import os
                self.state["media_title"] = os.path.basename(str(data))
        elif name in ("media-title", "media_title"):
            if data:
                self.state["media_title"] = str(data)

    def register_remote_command(self, property_name, target_value, timeout=1.5):
        expire = time.monotonic() + timeout
        self.echo_suppressions[property_name] = {
            "value": target_value,
            "expire": expire
        }
        logger.debug(f"Registered echo suppression for {property_name} = {target_value} until {expire}")

    def _is_suppressed(self, property_name, value, tolerance=0.1):
        if property_name not in self.echo_suppressions:
            return False
        supp = self.echo_suppressions[property_name]
        if time.monotonic() > supp["expire"]:
            del self.echo_suppressions[property_name]
            return False
        
        target = supp["value"]
        if isinstance(target, (int, float)) and isinstance(value, (int, float)):
            if abs(target - value) <= tolerance:
                return True
        elif target == value:
            return True
        return False

    def calculate_drift(self, local_time_s, host_time_s, sent_at_ms, now_ms, host_paused):
        if not host_paused:
            transit_s = (now_ms - sent_at_ms) / 1000.0 if (now_ms and sent_at_ms) else 0.0
            # Clamp elapsed transit time to [0.0, 2.0] seconds to prevent cross-machine clock skew
            if transit_s < 0.0 or transit_s > 2.0:
                transit_s = 0.05
            expected_time = host_time_s + transit_s
        else:
            expected_time = host_time_s
        
        drift = local_time_s - expected_time
        needs_seek = abs(drift) > 1.5
        return drift, expected_time, needs_seek

