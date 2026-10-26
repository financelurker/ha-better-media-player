"""Put the demo rooms into a state for the screenshots.

    python scene.py movie   # Family Room: Kodi plays (TV, speakers, subwoofer on)
    python scene.py music   # Family Room: music plays, Kodi and the TV off
    python scene.py off     # all rooms off
"""

import asyncio
import json
import sys

import aiohttp

BASE = "http://127.0.0.1:8123"


async def main(scene: str) -> None:
    token = json.load(open("session.json"))["token"]
    h = {"Authorization": f"Bearer {token}"}
    async with aiohttp.ClientSession() as s:
        async def call(domain, service, data):
            r = await s.post(f"{BASE}/api/services/{domain}/{service}", json=data, headers=h)
            print(domain, service, r.status)

        room = {"entity_id": "media_player.family_room"}
        if scene == "off":
            for e in ("media_player.family_room", "media_player.bedroom"):
                await call("media_player", "turn_off", {"entity_id": e})
            return
        await call("number", "set_value", {"entity_id": "number.family_room_display_run_down",
                                           "value": 0 if scene == "music" else 10})
        await call("media_player", "select_source", {**room, "source": "Kodi" if scene == "movie" else "Family Room Player"})
        await asyncio.sleep(3)
        await call("media_player", "media_play", room)
        await asyncio.sleep(2)
        await call("better_media_player", "set_volume_preset", {**room, "preset": "normal", "fade_seconds": 0})
        await call("number", "set_value", {"entity_id": "number.family_room_sleep_timer", "value": 45})


asyncio.run(main(sys.argv[1]))
