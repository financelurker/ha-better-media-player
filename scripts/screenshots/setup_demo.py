"""Onboard a fresh HA and set up the Better Media Player rooms as the panel does (room/create)."""

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
        # (the demo's players have no unique id: their name comes from customize)
        for ent in ("switch.tv_plug", "switch.speaker_plug", "switch.subwoofer_plug",
                    "sensor.tv_power", "sensor.speaker_power", "media_player.kodi"):
            await call({"type": "config/entity_registry/update", "entity_id": ent, "area_id": area_id})

        # onboarding creates a Bedroom area
        bedroom = next(a for a in await call({"type": "config/area_registry/list"}) if a["area_id"] == "bedroom")
        for ent in ("switch.bedroom_tv_plug", "media_player.bedroom_kodi"):
            await call({"type": "config/entity_registry/update", "entity_id": ent, "area_id": bedroom["area_id"]})

        # the integration as "Add integration" sets it up (its central entry): then the panel is there
        r = await s.post(f"{BASE}/api/config/config_entries/flow", json={"handler": "better_media_player"}, headers=h)
        flow = await r.json()
        r = await s.post(f"{BASE}/api/config/config_entries/flow/{flow['flow_id']}", json={}, headers=h)
        print("central", (await r.json()).get("type"))

        def plugged(dev_id, kind, plugs, sensors=()):
            return {"id": dev_id, "kind": kind, "power": {"plugs": plugs, "sensors": list(sensors)}}

        async def room(name, area, sources, devices):
            # as the panel creates a room (its area is set afterwards: since Home Assistant 2026.6
            # an entity id gets the area's name in front, and the docs show <domain>.<room>_...)
            res = await call({
                "type": "better_media_player/room/create", "name": name, "area_id": area,
                "setup": {"sources": sources, "devices": devices, "cables": [], "volume": {"where": "players"}},
            })
            print(name, res)

        await room("Family Room", area_id,
                   [{"id": "video", "player": "media_player.kodi"}, {"id": "audio", "player": "media_player.walkman"}],
                   [plugged("display", "display", ["switch.tv_plug"], ["sensor.tv_power"]),
                    plugged("speakers", "speakers", ["switch.speaker_plug"], ["sensor.speaker_power"]),
                    plugged("subwoofer", "subwoofer", ["switch.subwoofer_plug"])])
        await room("Bedroom", bedroom["area_id"], [{"id": "video", "player": "media_player.bedroom_kodi"}],
                   [plugged("display", "display", ["switch.bedroom_tv_plug"])])
        guest = await call({"type": "config/area_registry/create", "name": "Guest Room", "icon": "mdi:bed-outline"})
        await call({"type": "config/entity_registry/update", "entity_id": "media_player.guest_room_kodi",
                    "area_id": guest["area_id"]})
        await room("Guest Room", guest["area_id"], [{"id": "video", "player": "media_player.guest_room_kodi"}], [])

        devices = await call({"type": "config/device_registry/list"})
        dev = [d for d in devices if any(i[0] == "better_media_player" for i in d["identifiers"])
               and d["name"] == "Family Room"]
        entries = [e for e in await call({"type": "config_entries/get", "domain": "better_media_player"})
                   if e["title"] == "Family Room"]
        json.dump({"token": token, "device_id": dev[0]["id"] if dev else None,
                   "entry_id": entries[0]["entry_id"] if entries else None},
                  open("session.json", "w"))
        print("device", dev[0]["id"] if dev else None)


asyncio.run(main())
