"""Describe the Family Room's cabling through the real options flow (for the panel's screenshots).

Kodi's picture to the display, Kodi's and the music player's sound to the speakers,
the speakers on to the subwoofer. Connections set before are replaced.
"""

import asyncio
import json

import aiohttp

BASE = "http://127.0.0.1:8123"
CABLES = [
    {"from": "video_player", "to": "display", "signals": ["picture"]},
    {"from": "video_player", "to": "speakers", "signals": ["sound"]},
    {"from": "audio_player", "to": "speakers", "signals": ["sound"]},
    {"from": "speakers", "to": "bass", "signals": ["sound"]},
]


async def main() -> None:
    session = json.load(open("session.json"))
    h = {"Authorization": f"Bearer {session['token']}"}
    async with aiohttp.ClientSession(headers=h) as s:
        r = await s.post(f"{BASE}/api/config/config_entries/options/flow", json={"handler": session["entry_id"]})
        flow = await r.json()
        url = f"{BASE}/api/config/config_entries/options/flow/{flow['flow_id']}"

        async def step(data):
            res = await (await s.post(url, json=data)).json()
            print(res.get("step_id") or res.get("type"), res.get("errors") or "")
            return res

        res = await step({"next_step_id": "cabling"})
        if "connection_remove" in res.get("menu_options", []):
            form = await step({"next_step_id": "connection_remove"})
            options = form["data_schema"][0]["selector"]["select"]["options"]
            await step({"connections": [o["value"] for o in options]})
        for cable in CABLES:
            await step({"next_step_id": "connection_add"})
            res = await step(cable)
            if res.get("step_id") == "connection_input":
                res = await step({"mode": "bmp"})
        await step({"next_step_id": "init"})
        await step({"next_step_id": "save"})


asyncio.run(main())
