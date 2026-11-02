"""The configuration panel: what it is shown (websocket commands) and its registration."""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component

from custom_components.better_media_player.const import (
    CENTRAL_SETTINGS,
    CONF_CONNECTIONS,
    CONF_DISPLAY,
    CONF_ENTRY_TYPE,
    CONF_SPEAKERS,
    DOMAIN,
    ENTRY_TYPE_ROOM,
)
from custom_components.better_media_player.model import DEVICE_SETTINGS, ROOM_SETTINGS, SPECS, options_v4
from custom_components.better_media_player.panel import PANEL_URL, async_setup_panel
from custom_components.better_media_player.panel.view import routes_of

from .conftest import AVR, BASS, KODI, LG, MUSIC, SPEAKERS, TV, TV_POWER, room_options, setup_hub, setup_kitchen, setup_room
from .helpers import set_setting

CEC_ROOM = {
    CONF_DISPLAY: [LG],
    CONF_SPEAKERS: [SPEAKERS],
    "display_inputs": LG,
    "display_power_on": "cec",
    "display_cec_fallback": True,
    "speakers_inputs": AVR,
    CONF_CONNECTIONS: [
        {"from": "video_player", "to": "display", "signals": ["picture", "sound"], "input": "HDMI 2", "mode": "cec"},
        {"from": "display", "to": "speakers", "signals": ["sound"], "input": "TV/ARC", "mode": "cec"},
        {"from": "audio_player", "to": "speakers", "signals": ["sound"], "input": "NET"},
    ],
}


async def _get(hass_ws_client) -> dict:
    client = await hass_ws_client()
    await client.send_json_auto_id({"type": "better_media_player/config/get"})
    msg = await client.receive_json()
    assert msg["success"], msg
    return msg["result"]


async def test_a_room_from_today_s_options(hass, home, hass_ws_client):
    """Players are the sources, the roles one device each; without cables everything is fed directly."""
    await setup_room(hass, display_power=[TV_POWER])
    result = await _get(hass_ws_client)
    assert result["central"] is None
    (room,) = result["rooms"]
    assert room["name"] == "Living Room" and room["loaded"] is True
    assert [(s["id"], s["player"], s["needs"]) for s in room["sources"]] == [
        ("video", KODI, ["picture", "sound"]),
        ("audio", MUSIC, ["sound"]),
    ]
    devices = {d["id"]: d for d in room["devices"]}
    assert [(d["id"], d["kind"]) for d in room["devices"]] == [
        ("display", "display"),
        ("speakers", "speakers"),
        ("subwoofer", "subwoofer"),
    ]
    assert devices["display"]["power"] == {"plugs": [TV], "devices": [], "actions": None, "sensors": [TV_POWER]}
    assert devices["subwoofer"]["power"]["plugs"] == [BASS] and devices["subwoofer"]["control"] is None
    assert devices["display"]["control"] == {"on": "bmp", "off": "bmp", "fallback": False, "timeout": 15.0}
    assert room["cables"] == []
    assert room["routes"]["video"] == {
        "devices": ["display", "speakers", "subwoofer"],
        "cables": [],
        "direct": ["display", "speakers", "subwoofer"],
        "order": ["display", "speakers", "subwoofer"],
        "picture": ["display"],
        "inputs": {},
    }
    assert room["routes"]["audio"]["devices"] == ["speakers", "subwoofer"]
    assert room["volume"] == {"where": "players"} and room["layout"] == {}


async def test_the_room_s_and_its_devices_settings(hass, home, hass_ws_client):
    await setup_room(hass)
    await set_setting(hass, "night_max_volume", 25)
    await set_setting(hass, "run_down", 15, device="display")
    (room,) = (await _get(hass_ws_client))["rooms"]
    assert room["settings"] == {**ROOM_SETTINGS, "night_max_volume": 25}
    devices = {d["id"]: d for d in room["devices"]}
    assert devices["display"]["settings"]["run_down"] == 15
    assert devices["speakers"]["settings"]["run_down"] == 2
    assert set(devices["subwoofer"]["settings"]) == set(DEVICE_SETTINGS)


async def test_cabled_room_routes_inputs_and_cec(hass, home, hass_ws_client):
    hass.states.async_set(LG, "off", {"source_list": ["HDMI 1", "HDMI 2"]})
    hass.states.async_set(AVR, "off", {"source_list": ["TV/ARC", "NET"]})
    await setup_room(hass, **CEC_ROOM, bass=None)
    (room,) = (await _get(hass_ws_client))["rooms"]
    assert [(c["from"], c["to"], c["input"], c["mode"]) for c in room["cables"]] == [
        ("video", "display", "HDMI 2", "cec"),
        ("display", "speakers", "TV/ARC", "cec"),
        ("audio", "speakers", "NET", "bmp"),
    ]
    devices = {d["id"]: d for d in room["devices"]}
    assert devices["display"]["power"]["devices"] == [LG] and devices["display"]["control"]["on"] == "cec"
    assert devices["display"]["inputs"]["list"] == ["HDMI 1", "HDMI 2"]
    assert devices["speakers"]["inputs"]["entity"] == AVR
    video, audio = room["routes"]["video"], room["routes"]["audio"]
    assert video["devices"] == ["display", "speakers"] and video["cables"] == [0, 1] and video["direct"] == []
    assert video["inputs"] == {"display": {"input": "HDMI 2", "mode": "cec"}, "speakers": {"input": "TV/ARC", "mode": "cec"}}
    assert video["order"] == ["display", "speakers"] and video["picture"] == ["display"]
    assert audio == {"devices": ["speakers"], "cables": [2], "direct": [], "order": ["speakers"], "picture": [],
                     "inputs": {"speakers": {"input": "NET", "mode": "bmp"}}}


async def test_central_entry_and_rooms_by_name(hass, home, hass_ws_client):
    await setup_hub(hass)
    await setup_room(hass)
    await setup_kitchen(hass)
    result = await _get(hass_ws_client)
    assert result["central"]["follow_player"] == "media_player.all_players_follow"
    assert result["central"]["settings"] == CENTRAL_SETTINGS
    assert [r["name"] for r in result["rooms"]] == ["Kitchen", "Living Room"]


def test_route_of_a_draft_with_passive_devices():
    """The user's living room: Kodi's picture to the TV, its sound and the music
    through a mixer, a splitter feeds speakers and subwoofer."""
    sources = [{"id": "video", "needs": ["picture", "sound"]}, {"id": "audio", "needs": ["sound"]}]
    devices = [
        {"id": "tv", "kind": "display"},
        {"id": "mix", "kind": "mixer"},
        {"id": "split", "kind": "splitter"},
        {"id": "spk", "kind": "speakers"},
        {"id": "sub", "kind": "subwoofer"},
    ]
    cables = [
        {"from": "video", "to": "tv", "signals": ["picture"]},
        {"from": "video", "to": "mix", "signals": ["sound"]},
        {"from": "audio", "to": "mix", "signals": ["sound"]},
        {"from": "mix", "to": "split", "signals": ["sound"]},
        {"from": "split", "to": "spk", "signals": ["sound"]},
        {"from": "split", "to": "sub", "signals": ["sound"]},
        {"from": "gone", "to": "spk", "signals": ["sound"]},  # an end that no longer exists
    ]
    routes = routes_of(sources, devices, cables)
    assert routes["video"]["devices"] == ["mix", "spk", "split", "sub", "tv"]
    assert routes["video"]["cables"] == [0, 1, 3, 4, 5] and routes["video"]["direct"] == []
    assert routes["audio"]["devices"] == ["mix", "spk", "split", "sub"]  # the TV stays off
    assert routes["video"]["order"] == ["tv", "spk", "sub"], "the picture first; mixer and splitter are not switched"
    assert routes["audio"]["cables"] == [2, 3, 4, 5]


async def test_route_command(hass, hass_ws_client):
    assert await async_setup_component(hass, DOMAIN, {})
    client = await hass_ws_client()
    await client.send_json_auto_id(
        {
            "type": "better_media_player/route",
            "sources": [{"id": "audio", "needs": ["sound"]}],
            "devices": [{"id": "spk", "kind": "speakers"}, {"id": "tv", "kind": "display"}],
            "cables": [],
        }
    )
    msg = await client.receive_json()
    assert msg["success"]
    assert msg["result"]["routes"]["audio"] == {"devices": ["spk"], "cables": [], "direct": ["spk"], "order": ["spk"], "picture": [], "inputs": {}}


async def test_layout_is_kept_in_the_room(hass, home, hass_ws_client, hass_storage):
    entry = await setup_room(hass)
    client = await hass_ws_client()
    await client.send_json_auto_id(
        {"type": "better_media_player/layout/save", "entry_id": entry.entry_id, "layout": {"display": [360.4, 120]}}
    )
    assert (await client.receive_json())["success"]
    await hass.async_block_till_done()
    assert hass_storage[f"better_media_player.{entry.entry_id}"]["data"]["layout"] == {"display": [360, 120]}
    (room,) = (await _get(hass_ws_client))["rooms"]
    assert room["layout"] == {"display": [360, 120]}
    # empty: arranged automatically again
    await client.send_json_auto_id({"type": "better_media_player/layout/save", "entry_id": entry.entry_id, "layout": {}})
    assert (await client.receive_json())["success"]
    assert (await _get(hass_ws_client))["rooms"][0]["layout"] == {}


async def test_layout_of_a_room_not_loaded(hass, hass_ws_client):
    assert await async_setup_component(hass, DOMAIN, {})
    client = await hass_ws_client()
    await client.send_json_auto_id({"type": "better_media_player/layout/save", "entry_id": "nope", "layout": {}})
    msg = await client.receive_json()
    assert not msg["success"] and msg["error"]["code"] == "not_loaded"


@pytest.mark.parametrize(
    "message",
    [
        {"type": "better_media_player/config/get"},
        {"type": "better_media_player/route", "sources": [], "devices": [], "cables": []},
        {"type": "better_media_player/layout/save", "entry_id": "x", "layout": {}},
    ],
)
async def test_admins_only(hass, home, hass_ws_client, hass_admin_user, message):
    await setup_room(hass)
    hass_admin_user.groups = []
    client = await hass_ws_client()
    await client.send_json_auto_id(message)
    msg = await client.receive_json()
    assert not msg["success"] and msg["error"]["code"] == "unauthorized"


async def test_panel_registered_where_the_frontend_runs(hass):
    hass.config.components.add("frontend")
    hass.http = MagicMock(async_register_static_paths=AsyncMock())
    with patch("homeassistant.components.panel_custom.async_register_panel", AsyncMock()) as register:
        await async_setup_panel(hass, "1.4.0")
    (paths,), _ = hass.http.async_register_static_paths.call_args
    assert paths[0].url_path == "/better_media_player_panel/1.4.0" and paths[0].cache_headers is True
    assert paths[0].path.endswith("better_media_player/frontend")
    kw = register.call_args.kwargs
    assert kw["frontend_url_path"] == PANEL_URL and kw["require_admin"] is True
    assert kw["module_url"] == "/better_media_player_panel/1.4.0/bmp-panel.js"
    # view only: the integration's gear still opens its options (no config panel)
    assert "config_panel_domain" not in kw


async def test_no_panel_without_the_frontend(hass):
    hass.http = MagicMock(async_register_static_paths=AsyncMock())
    with patch("homeassistant.components.panel_custom.async_register_panel", AsyncMock()) as register:
        await async_setup_panel(hass, "1.4.0")
    register.assert_not_called()
    hass.http.async_register_static_paths.assert_not_called()


def test_panel_texts_exist_in_every_language():
    """Every text the panel uses is in English, and German has the same keys."""
    front = Path(__file__).parent.parent / "custom_components" / "better_media_player" / "frontend"
    en = json.loads((front / "i18n" / "en.json").read_text())
    de = json.loads((front / "i18n" / "de.json").read_text())
    assert set(de) == set(en)
    code = (front / "bmp-panel.js").read_text()
    used = set(re.findall(r'\bt\(\s*"([a-z_.]+)"', code))
    used |= {k for pair in re.findall(r'"(value\.\w+)"\s*:\s*"(value\.\w+)"', code) for k in pair}  # t(on ? a : b)
    used |= {f"count.{w}{s}" for w in re.findall(r'_count\("(\w+)"', code) for s in ("", "_one")}
    # built keys: kinds, sources, signals and modes by their values
    for prefix, values in {
        "kind": ("player", "display", "speakers", "subwoofer", "receiver", "amp", "mixer", "splitter", "switch"),
        "source": ("video", "audio"),
        "legend": ("picture", "sound"),
        "mode": ("bmp", "cec", "never"),
        "tab": ("wiring", "settings"),
        "setting": (*ROOM_SETTINGS, *DEVICE_SETTINGS, *CENTRAL_SETTINGS),
        "choice": tuple(x for spec in SPECS.values() if isinstance(spec, list) for x in spec),
    }.items():
        used |= {f"{prefix}.{v}" for v in values}
    assert used - set(en) == set()
    for key, text in en.items():  # same placeholders in both languages
        assert set(re.findall(r"\{(\w+)\}", text)) == set(re.findall(r"\{(\w+)\}", de[key])), key


async def test_area_of_the_room_s_device_else_the_one_chosen(hass, home, hass_ws_client):
    areas = ar.async_get(hass)
    lounge, den = areas.async_create("Lounge"), areas.async_create("Den")
    entry = await setup_room(hass, area=den.id)
    assert (await _get(hass_ws_client))["rooms"][0]["area"] == "Den"
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    dr.async_get(hass).async_update_device(device.id, area_id=lounge.id)  # moved in Home Assistant
    assert (await _get(hass_ws_client))["rooms"][0]["area"] == "Lounge"


async def test_a_room_not_loaded_is_shown_from_its_options(hass, home, hass_ws_client):
    assert await async_setup_component(hass, DOMAIN, {})
    MockConfigEntry(domain=DOMAIN, title="Attic", data={CONF_ENTRY_TYPE: ENTRY_TYPE_ROOM}, options=options_v4(room_options()), version=4).add_to_hass(hass)
    (room,) = (await _get(hass_ws_client))["rooms"]
    assert room["loaded"] is False and room["layout"] == {}
    assert room["settings"] is None and room["devices"][0]["settings"] is None
    assert [d["id"] for d in room["devices"]] == ["display", "speakers", "subwoofer"]


async def test_cables_to_a_device_the_room_no_longer_has(hass, home, hass_ws_client):
    await setup_room(
        hass,
        bass=None,
        connections=[
            {"from": "video_player", "to": "display", "signals": ["picture"]},
            {"from": "speakers", "to": "bass", "signals": ["sound"]},  # no subwoofer any more
        ],
    )
    (room,) = (await _get(hass_ws_client))["rooms"]
    assert [(c["from"], c["to"]) for c in room["cables"]] == [("video", "display")]
    assert room["routes"]["video"]["cables"] == [0]


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({"volume_control": "device", "volume_entity": AVR}, {"where": "device", "device": "speakers"}),
        ({"volume_control": "players", "volume_entity": AVR}, {"where": "players"}),  # an entity left over
    ],
)
async def test_where_the_room_volume_is(hass, home, hass_ws_client, options, expected):
    await setup_room(hass, **options)
    assert (await _get(hass_ws_client))["rooms"][0]["volume"] == expected


async def test_devices_link_to_the_panel(hass, home):
    entry = await setup_room(hass)
    hub = await setup_hub(hass)
    devices = dr.async_get(hass)
    room = devices.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    central = devices.async_get_device(identifiers={(DOMAIN, hub.entry_id)})
    assert room.configuration_url == f"homeassistant://better-media-player/{entry.entry_id}"
    assert central.configuration_url == "homeassistant://better-media-player"


def test_fed_directly_for_the_signal_no_cable_brings():
    """A cable carries only the sound to the display: the picture still comes directly."""
    routes = routes_of(
        [{"id": "video", "needs": ["picture", "sound"]}],
        [{"id": "tv", "kind": "display"}, {"id": "spk", "kind": "speakers"}],
        [{"from": "video", "to": "tv", "signals": ["sound"]}],
    )
    assert routes["video"]["direct"] == ["spk", "tv"] and routes["video"]["cables"] == [0]


@pytest.mark.parametrize("place", [["nan", 1], ["1e400", 0], [1, 2, 3]])
async def test_layout_places_are_checked(hass, home, hass_ws_client, place):
    entry = await setup_room(hass)
    client = await hass_ws_client()
    await client.send_json_auto_id({"type": "better_media_player/layout/save", "entry_id": entry.entry_id, "layout": {"display": place}})
    msg = await client.receive_json()
    assert not msg["success"] and msg["error"]["code"] == "invalid_format"
