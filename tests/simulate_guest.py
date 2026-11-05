"""
Interactive guest simulator to test shared playback controls against a live Host Stremio session.

Usage:
    uv run python tests/simulate_guest.py --relay wss://generators-improved-violation-quick.trycloudflare.com
    # or against local relay:
    uv run python tests/simulate_guest.py --relay ws://localhost:8765
"""

import argparse
import asyncio
import json
import time
import websockets


async def run_guest_test(relay_url: str, room: str, secret: str):
    sender = "guest-simulator"
    print(f"[*] Connecting to {relay_url} (room={room}, sender={sender})...")
    async with websockets.connect(relay_url) as ws:
        await ws.send(json.dumps({
            "type": "hello",
            "room": room,
            "sender": sender,
            "secret": secret,
            "role": "guest",
            "version": 1
        }))
        welcome = json.loads(await ws.recv())
        print(f"[+] Connected! Welcome: {welcome}")

        print("[*] Waiting for 1 host state heartbeat to read current playback time...")
        host_time = 0.0
        while True:
            raw = await ws.recv()
            msg = json.loads(raw)
            print(f"    <- Received [{msg.get('type')}]: time_s={msg.get('time_s')}, paused={msg.get('paused')}")
            if msg.get("type") in ("state", "play", "pause", "seek"):
                host_time = float(msg.get("time_s", 0.0))
                break

        print(f"\n[1/3] Sending GUEST PAUSE at {host_time:.1f}s (watch your Stremio window pause and STAY paused for 6s)...")
        await ws.send(json.dumps({
            "type": "pause",
            "room": room,
            "sender": sender,
            "seq": 1,
            "sent_at_ms": int(time.time() * 1000),
            "time_s": host_time,
            "paused": True
        }))

        # Listen for 6 seconds (2 heartbeat cycles) to verify host stays paused
        end_wait = time.time() + 6.0
        while time.time() - end_wait < 0:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, end_wait - time.time()))
                msg = json.loads(raw)
                if msg.get("type") == "error":
                    print(f"    [!] Relay rejected command: {msg}")
                else:
                    print(f"    <- Host heartbeat during guest pause: type={msg.get('type')}, paused={msg.get('paused')}, time_s={msg.get('time_s')}")
            except asyncio.TimeoutError:
                break

        target_seek = host_time + 30.0
        print(f"\n[2/3] Sending GUEST SEEK +30s -> {target_seek:.1f}s...")
        await ws.send(json.dumps({
            "type": "seek",
            "room": room,
            "sender": sender,
            "seq": 2,
            "sent_at_ms": int(time.time() * 1000),
            "time_s": target_seek,
            "paused": True
        }))
        await asyncio.sleep(3.0)

        print(f"\n[3/3] Sending GUEST PLAY at {target_seek:.1f}s (watch your Stremio window resume)...")
        await ws.send(json.dumps({
            "type": "play",
            "room": room,
            "sender": sender,
            "seq": 3,
            "sent_at_ms": int(time.time() * 1000),
            "time_s": target_seek,
            "paused": False
        }))
        await asyncio.sleep(2.0)
        print("\n[+] Guest simulation complete!")


def main():
    parser = argparse.ArgumentParser(description="Simulate a guest pausing, seeking, and resuming your host Stremio.")
    parser.add_argument("--relay", default="ws://localhost:8765", help="Relay URL (ws:// or wss://)")
    parser.add_argument("--room", default="movie-night", help="Room name")
    parser.add_argument("--secret", default="chikita", help="Room secret")
    args = parser.parse_args()
    asyncio.run(run_guest_test(args.relay, args.room, args.secret))


if __name__ == "__main__":
    main()
