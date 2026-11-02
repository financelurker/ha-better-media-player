"""Describe the Family Room's cabling as the panel does (room/save; for the panel's screenshots).

Kodi's picture to the display, Kodi's and the music player's sound to the speakers,
the speakers on to the subwoofer (its sub out). Cables described before are replaced.
"""

import asyncio
import json

import aiohttp

BASE = "http://127.0.0.1:8123"
CABLES = [
    {"from": "video", "to": "display", "signals": ["picture"]},
    {"from": "video", "to": "speakers", "signals": ["sound"]},
    {"from": "audio", "to": "speakers", "signals": ["sound"]},
    {"from": "speakers", "to": "subwoofer", "signals": ["sound"]},
]


async def main() -> None:
    session = json.load(open("session.json"))
    async with aiohttp.ClientSession() as s:
        ws = await s.ws_connect(f"{BASE}/api/websocket")
        await ws.receive_json()
        await ws.send_json({"type": "auth", "access_token": session["token"]})
        await ws.receive_json()
        await ws.send_json({"id": 1, "type": "better_media_player/config/get"})
        config = (await ws.receive_json())["result"]
        room = next(r for r in config["rooms"] if r["entry_id"] == session["entry_id"])
        setup = {
            "sources": [{k: s.get(k) for k in ("id", "player", "host", "onoff", "on_action", "off_action")} for s in room["sources"]],
            "devices": [{k: v for k, v in d.items() if k != "settings"} | {"inputs": None} for d in room["devices"]],
            "cables": CABLES,
            "volume": room["volume"],
            "occupancy": room["occupancy"],
        }
        await ws.send_json({"id": 2, "type": "better_media_player/room/save", "entry_id": room["entry_id"],
                            "version": room["version"], "name": room["name"], "area_id": room["area_id"], "setup": setup})
        print(await ws.receive_json())


asyncio.run(main())
