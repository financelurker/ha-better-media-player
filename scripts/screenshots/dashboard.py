import asyncio, json, aiohttp
T = json.load(open("session.json"))["token"]
def tile(e, name=None, cols=6, **kw):
    c = {"type": "tile", "entity": e, "grid_options": {"columns": cols}, **kw}
    if name: c["name"] = name
    return c
cfg = {"title": "Home", "views": [{"title": "Family Room", "path": "family-room", "type": "sections", "max_columns": 3,
  "sections": [
    {"type": "grid", "cards": [
        {"type": "heading", "heading": "Family Room", "icon": "mdi:sofa"},
        {"type": "media-control", "entity": "media_player.family_room", "grid_options": {"columns": 12}},
        tile("sensor.family_room_status", "Room status", 12, icon="mdi:television-speaker"),
        tile("switch.family_room_night_mode", "Night mode"), tile("select.family_room_volume_preset", "Volume preset"),
        tile("number.family_room_sleep_timer", "Sleep timer"), tile("switch.family_room_quiet", "Quiet"),
    ]},
    {"type": "grid", "cards": [
        {"type": "heading", "heading": "Power", "icon": "mdi:power-plug"},
        tile("switch.tv_plug", "TV"), tile("sensor.tv_power", "TV power"),
        tile("switch.speaker_plug", "Speakers"), tile("sensor.speaker_power", "Speaker power"),
        tile("switch.subwoofer_plug", "Subwoofer"), tile("switch.family_room_automatic_power", "Automatic"),
    ]},
    {"type": "grid", "cards": [
        {"type": "heading", "heading": "Players", "icon": "mdi:play-network"},
        tile("media_player.kodi", "Kodi", 12), tile("media_player.walkman", "Music", 12),
        tile("binary_sensor.family_room_display_needed", "Display"),
        tile("binary_sensor.family_room_speakers_needed", "Speakers"),
    ]},
  ]}]}
async def main():
    async with aiohttp.ClientSession() as s:
        ws = await s.ws_connect("http://127.0.0.1:8123/api/websocket")
        await ws.receive_json(); await ws.send_json({"type": "auth", "access_token": T}); await ws.receive_json()
        # an own dashboard: since Home Assistant 2026.10, /lovelace shows the new overview
        await ws.send_json({"id": 1, "type": "lovelace/dashboards/create", "url_path": "dashboard-rooms", "title": "Rooms",
                            "icon": "mdi:sofa", "mode": "storage", "show_in_sidebar": True, "require_admin": False})
        print(await ws.receive_json())
        await ws.send_json({"id": 2, "type": "lovelace/config/save", "url_path": "dashboard-rooms", "config": cfg})
        print(await ws.receive_json())
asyncio.run(main())
