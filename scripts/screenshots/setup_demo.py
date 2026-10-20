"""Onboard a fresh HA and set up a Better Media Player room via the real config flow."""

import asyncio
import json
import sys

import aiohttp

BASE = "http://127.0.0.1:8123"
CLIENT = BASE + "/"
USER, PASSWORD = "demo", "demo-pass-2026"


async def main() -> None:
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{BASE}/api/onboarding/users", json={
            "client_id": CLIENT, "name": "Demo", "username": USER, "password": PASSWORD, "language": "en"})
        code = (await r.json())["auth_code"]
        r = await s.post(f"{BASE}/auth/token", data={
            "grant_type": "authorization_code", "code": code, "client_id": CLIENT})
        token = (await r.json())["access_token"]
        h = {"Authorization": f"Bearer {token}"}
        for step, body in (("core_config", {}), ("analytics", {}),
                           ("integration", {"client_id": CLIENT, "redirect_uri": CLIENT})):
            r = await s.post(f"{BASE}/api/onboarding/{step}", json=body, headers=h)
            print(step, r.status)

        ws = await s.ws_connect(f"{BASE}/api/websocket")
        await ws.receive_json()
        await ws.send_json({"type": "auth", "access_token": token})
        await ws.receive_json()
        n = 0

        async def call(msg):
            nonlocal n
            n += 1
            await ws.send_json({"id": n, **msg})
            while True:
                res = await ws.receive_json()
                if res.get("id") == n and res.get("type") == "result":
                    if not res["success"]:
                        print("WS error", msg, res, file=sys.stderr)
                    return res.get("result")

        area = await call({"type": "config/area_registry/create", "name": "Family Room", "icon": "mdi:sofa"})
        area_id = area["area_id"]
        for ent in ("switch.tv_plug", "switch.speaker_plug", "switch.subwoofer_plug",
                    "sensor.tv_power", "sensor.speaker_power",
                    "media_player.lounge_room", "media_player.walkman"):
            await call({"type": "config/entity_registry/update", "entity_id": ent, "area_id": area_id})
        await call({"type": "config/entity_registry/update", "entity_id": "media_player.lounge_room",
                    "name": "Kodi"})
        await call({"type": "config/entity_registry/update", "entity_id": "media_player.walkman",
                    "name": "Family Room Player"})

        async def flow(flow_id, data):
            r = await s.post(f"{BASE}/api/config/config_entries/flow/{flow_id}", json=data, headers=h)
            res = await r.json()
            print(res.get("step_id") or res.get("type"), res.get("errors"))
            return res

        r = await s.post(f"{BASE}/api/config/config_entries/flow", json={"handler": "better_media_player"}, headers=h)
        fid = (await r.json())["flow_id"]
        await flow(fid, {"next_step_id": "room"})
        await flow(fid, {"name": "Family Room", "area": area_id})
        await flow(fid, {"video_player": "media_player.lounge_room", "audio_player": "media_player.walkman", "special": {}})
        res = await flow(fid, {"display": ["switch.tv_plug"], "speakers": ["switch.speaker_plug"],
                               "bass": ["switch.subwoofer_plug"],
                               "standby": {"display_power": ["sensor.tv_power"], "audio_power": ["sensor.speaker_power"]},
                               "follow": {}})
        if res.get("step_id") == "volume_note":
            res = await flow(fid, {})
        print("entry", res.get("result", {}).get("entry_id"))

        devices = await call({"type": "config/device_registry/list"})
        dev = [d for d in devices if any(i[0] == "better_media_player" for i in d["identifiers"])]
        entries = await call({"type": "config_entries/get", "domain": "better_media_player"})
        json.dump({"token": token, "device_id": dev[0]["id"] if dev else None,
                   "entry_id": entries[0]["entry_id"] if entries else None},
                  open("session.json", "w"))
        print("device", dev[0]["id"] if dev else None)


asyncio.run(main())
