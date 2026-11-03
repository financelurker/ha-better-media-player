"""Editing in the panel: a room's draft checked, saved, created and deleted; the central entry."""

from __future__ import annotations

import copy

import pytest

from homeassistant import config_entries
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.media_player import MediaPlayerEntityFeature as F
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.better_media_player.const import CONF_ENTRY_TYPE, DOMAIN, ENTRY_TYPE_ROOM
from custom_components.better_media_player.model import options_v4
from custom_components.better_media_player.panel.edit import SETUP_SCHEMA, check_room, clean_setup

from .conftest import AMP, AVR, FOLLOW, GROUP, KITCHEN, KODI, LG, MUSIC, SPEAKERS, TV, TV_POWER, room_options, setup_hub, setup_room
from .helpers import ROOM, get_settings

BASE = {
    "sources": [{"id": "video", "player": KODI}, {"id": "audio", "player": MUSIC}],
    "devices": [
        {"id": "display", "kind": "display", "power": {"plugs": [TV]}},
        {"id": "speakers", "kind": "speakers", "power": {"plugs": [SPEAKERS]}},
    ],
    "cables": [],
    "volume": {"where": "players"},
}


def draft(**changes):
    out = copy.deepcopy(BASE)
    out.update(changes)
    return out


async def _ws(hass_ws_client, **msg):
    client = await hass_ws_client()
    await client.send_json_auto_id(msg)
    return await client.receive_json()


async def _validate(hass_ws_client, setup, **extra):
    res = await _ws(hass_ws_client, type="better_media_player/validate", setup=setup, **extra)
    assert res["success"], res
    return res["result"]["problems"]


async def _media_player_ready(hass):
    """Set up before the websocket client starts the web server (a room set up later needs it)."""
    assert await async_setup_component(hass, "media_player", {})


def codes(problems, error=True):
    return {p["code"] for p in problems if p["error"] is error}


async def _room(hass, hass_ws_client):
    """The Living Room as config/get gives it."""
    res = await _ws(hass_ws_client, type="better_media_player/config/get")
    return next(r for r in res["result"]["rooms"] if r["name"] == "Living Room")


def _setup_of(room):
    """The setup in a view, as the panel sends it back."""
    return {
        "sources": [{k: s[k] for k in ("id", "player", "host", "onoff", "on_action", "off_action")} for s in room["sources"]],
        "devices": [{k: v for k, v in d.items() if k not in ("settings", "inputs")} | {"inputs": None} for d in room["devices"]],
        "cables": room["cables"],
        "volume": room["volume"],
        "occupancy": room["occupancy"],
    }


# ------------------------------------------------------------------ the checks


async def test_a_good_draft_has_no_errors(hass, home, hass_ws_client):
    await setup_hub(hass)
    assert codes(await _validate(hass_ws_client, draft())) == set()


@pytest.mark.parametrize(
    ("setup", "code"),
    [
        (draft(sources=[]), "no_player"),
        (draft(sources=[{"id": "video", "player": KODI}, {"id": "audio", "player": KODI}]), "same_player"),
        (draft(sources=[{"id": "audio", "player": GROUP}]), "group_player"),
        (draft(sources=[{"id": "video", "player": KODI, "onoff": "actions"}]), "needs_actions"),
        (draft(devices=[{"id": "Display", "kind": "display", "power": {"plugs": [TV]}}]), "bad_id"),
        (draft(devices=[{"id": "video", "kind": "display", "power": {"plugs": [TV]}}]), "reserved_id"),
        (draft(devices=[{"id": "tv", "kind": "display", "power": {"plugs": [TV]}}, {"id": "tv", "kind": "speakers", "power": {"plugs": [SPEAKERS]}}]), "dup_id"),
        (draft(devices=[{"id": "tv", "kind": "display", "power": {"plugs": []}}]), "nothing_switches"),
        (draft(devices=[{"id": "tv", "kind": "display", "power": {"actions": ["script.on", None]}}]), "needs_both_actions"),
        (draft(devices=[{"id": "tv", "kind": "display", "power": {"devices": [KODI]}}]), "device_is_player"),
        (draft(devices=[{"id": "a", "kind": "display", "power": {"plugs": [TV]}}, {"id": "b", "kind": "speakers", "power": {"plugs": [TV]}}]), "used_twice"),
        (draft(volume={"where": "device", "device": "speakers"}), "volume_device_missing"),
        (draft(devices=[{"id": "spk", "kind": "speakers", "power": {"plugs": [SPEAKERS]}, "volume": MUSIC}]), "volume_is_player"),
        (draft(cables=[{"from": "video", "to": "video", "signals": ["sound"]}]), "cable_to_itself"),
        (draft(cables=[{"from": "video", "to": "beamer", "signals": ["picture"]}]), "cable_end_missing"),
        (draft(cables=[{"from": "display", "to": "speakers", "signals": ["sound"]}, {"from": "speakers", "to": "display", "signals": ["sound"]}]), "cycle"),
        (draft(cables=[
            {"from": "video", "to": "display", "signals": ["picture", "sound"], "input": "HDMI 1"},
            {"from": "display", "to": "speakers", "signals": ["sound"], "input": "ARC"},
            {"from": "video", "to": "speakers", "signals": ["sound"], "input": "BD"},
        ]), "input_conflict"),
    ],
)
async def test_what_stops_saving(hass, home, hass_ws_client, setup, code):
    home.group("idle", [], GROUP)
    await setup_hub(hass)
    assert code in codes(await _validate(hass_ws_client, setup))


async def test_a_room_player_or_another_room_s_plug(hass, home, hass_ws_client):
    entry = await setup_room(hass)  # switches the TV and the speakers
    other = draft(sources=[{"id": "audio", "player": ROOM}])  # (a room player would loop)
    problems = await _validate(hass_ws_client, other)
    assert "room_player" in codes(problems)
    taken = [p for p in problems if p["code"] == "taken"]
    assert {p["entity"] for p in taken} == {TV, SPEAKERS} and {p["room"] for p in taken} == {"Living Room"}
    assert not codes(await _validate(hass_ws_client, draft(), entry_id=entry.entry_id)) & {"taken"}, "its own"


async def test_hints_do_not_stop_saving(hass, home, hass_ws_client):
    await setup_hub(hass)
    hass.states.async_set(LG, "on", {"source_list": ["HDMI 1", "HDMI 2"]})
    hass.states.async_set("media_player.radio", "idle", {"supported_features": int(F.PLAY | F.TURN_ON)})
    setup = draft(
        sources=[{"id": "video", "player": KODI}, {"id": "audio", "player": "media_player.radio", "onoff": "player"}],
        devices=[
            {"id": "display", "kind": "display", "power": {"devices": [LG]}, "inputs": {"entity": LG}},
            {"id": "speakers", "kind": "speakers", "power": {"plugs": [SPEAKERS]}},
            {"id": "mix", "kind": "mixer"},  # (no cable: on no way)
        ],
        cables=[
            {"from": "video", "to": "display", "signals": ["picture"], "input": "SCART"},
            {"from": "video", "to": "speakers", "signals": ["sound"], "input": "TV"},
            {"from": "audio", "to": "speakers", "signals": ["sound"]},
        ],
    )
    problems = await _validate(hass_ws_client, setup)
    assert codes(problems) == set()
    assert codes(problems, error=False) == {"input_not_offered", "no_inputs", "off_route", "player_no_volume"}
    hass.states.async_set("media_player.radio", "idle", {"supported_features": int(F.PLAY | F.VOLUME_SET)})
    assert "player_no_onoff" in codes(await _validate(hass_ws_client, setup), error=False)


async def test_a_malformed_draft_is_refused(hass, home, hass_ws_client):
    await setup_hub(hass)
    for setup in (
        draft(devices=[{"id": "x", "kind": "beamer"}]),
        draft(devices=[{"id": "x", "kind": "display", "power": {"plugs": ["media_player.tv"]}}]),  # not a plug
        draft(devices=[{"id": "x", "kind": "display", "volume": "switch.amp"}]),
        draft(cables=[{"from": "video", "to": "display", "signals": []}]),
        draft(sources=[{"id": "audio", "player": "switch.music"}]),  # not a player
        draft(occupancy=["sensor.motion"]),  # not a binary sensor
        draft(devices=[{"id": "x", "kind": "display", "power": {"plugs": [TV]}, "inputs": {"entity": "script.inputs"}}]),
    ):
        res = await _ws(hass_ws_client, type="better_media_player/validate", setup=setup)
        assert not res["success"] and res["error"]["code"] == "invalid_format", setup


def test_what_a_kind_cannot_have_is_left_out():
    setup = clean_setup(draft(devices=[{
        "id": "mix", "kind": "mixer", "power": {"plugs": [TV]}, "volume": AMP,
        "inputs": {"entity": None, "actions": {}},
    }]))
    mixer = setup["devices"][0]
    assert (mixer["power"], mixer["control"], mixer["inputs"], mixer["volume"]) == (None, None, None, None)
    assert setup["sources"][0] == {"id": "video", "kind": "video", "player": KODI, "host": None, "onoff": "auto", "on_action": None, "off_action": None}
    SETUP_SCHEMA(setup)  # (stored as it is checked)


async def test_settings_are_checked_too(hass, home, hass_ws_client):
    await setup_hub(hass)
    problems = await _validate(
        hass_ws_client, draft(), settings={"room": {"night_max_volume": 120, "nonsense": 1}, "devices": {"display": {"on_wait": -1}}}
    )
    assert codes(problems) == {"bad_setting", "unknown_setting"}
    assert check_room(hass, None, SETUP_SCHEMA(draft()), {"room": {}, "devices": {}, "sources": {"video": {"standby": True}}})[0]["code"] == "unknown_setting"


def test_a_migrated_volume_only_device_can_be_saved_as_it_is(hass):
    """A players-only room got a device for its volume: passive, so it needs nothing to switch it."""
    old = options_v4(room_options(video_player=None, display=None, speakers=None, bass=None, volume_control="device", volume_entity=AMP))
    setup = clean_setup(SETUP_SCHEMA({k: old[k] for k in ("sources", "devices", "cables", "volume")}))
    assert setup["devices"][0]["power"] is None
    assert not [p for p in check_room(hass, None, setup, {}) if p["error"]]


# ------------------------------------------------------------------ saving


async def test_settings_only_take_effect_without_a_reload(hass, home, hass_ws_client):
    entry = await setup_room(hass)
    room = await _room(hass, hass_ws_client)
    controller = entry.runtime_data
    res = await _ws(
        hass_ws_client, type="better_media_player/room/save", entry_id=entry.entry_id, version=room["version"],
        name="Living Room", area_id=None, setup=_setup_of(room),
        settings={"room": {"night_max_volume": 20}, "devices": {"display": {"on_wait": 12}}},
    )
    assert res["result"]["saved"], res
    assert entry.runtime_data is controller, "no reload"
    got = await get_settings(hass)
    assert got["settings"]["night_max_volume"] == 20 and got["devices"]["display"]["on_wait"] == 12


async def test_only_what_was_changed_is_sent_and_written(hass, home, hass_ws_client):
    """A setting the panel did not change is not sent, so one changed meanwhile (by an action) stays."""
    entry = await setup_room(hass)
    room = await _room(hass, hass_ws_client)
    await hass.services.async_call(DOMAIN, "set_setting", {"entity_id": ROOM, "setting": "night_max_volume", "value": 11}, blocking=True)
    res = await _ws(hass_ws_client, type="better_media_player/room/save", entry_id=entry.entry_id, version=room["version"],
                    name="Living Room", setup=_setup_of(room), settings={"room": {"prewarm": False}})
    assert res["result"]["saved"], res
    got = (await get_settings(hass))["settings"]
    assert got["night_max_volume"] == 11 and got["prewarm"] is False


async def test_the_area_alone_does_not_set_the_room_up_again(hass, home, hass_ws_client):
    entry = await setup_room(hass)
    den = ar.async_get(hass).async_create("Den")
    controller = entry.runtime_data
    room = await _room(hass, hass_ws_client)
    res = await _ws(hass_ws_client, type="better_media_player/room/save", entry_id=entry.entry_id, version=room["version"],
                    name="Living Room", area_id=den.id, setup=_setup_of(room))
    assert res["result"]["saved"], res
    await hass.async_block_till_done()
    assert entry.runtime_data is controller, "no reload"
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    assert device.area_id == den.id
    # saved again without an area: the device keeps it
    room = await _room(hass, hass_ws_client)
    res = await _ws(hass_ws_client, type="better_media_player/room/save", entry_id=entry.entry_id, version=room["version"],
                    name="Living Room", setup=_setup_of(room), settings={"room": {"prewarm": False}})
    assert res["result"]["saved"]
    assert dr.async_get(hass).async_get_device(identifiers={(DOMAIN, entry.entry_id)}).area_id == den.id


async def test_a_room_not_loaded_is_set_up_with_the_saved_setup(hass, home, hass_ws_client):
    """E.g. its setup failed: saving does not wait for a reload that does not come, it sets it up."""
    await setup_hub(hass)
    await _media_player_ready(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Living Room", data={CONF_ENTRY_TYPE: ENTRY_TYPE_ROOM}, options=options_v4(room_options()), version=4)
    entry.add_to_hass(hass)
    res = await _ws(hass_ws_client, type="better_media_player/config/get")
    room = next(r for r in res["result"]["rooms"] if r["entry_id"] == entry.entry_id)
    setup = _setup_of(room)
    setup["devices"] = [d for d in setup["devices"] if d["id"] != "subwoofer"]
    res = await _ws(hass_ws_client, type="better_media_player/room/save", entry_id=entry.entry_id, version=room["version"],
                    name="Living Room", setup=setup)
    assert res["result"]["saved"], res
    await hass.async_block_till_done()
    assert entry.state is config_entries.ConfigEntryState.LOADED
    assert "subwoofer" not in [d["id"] for d in entry.options["devices"]]


async def test_a_changed_setup_reloads_the_room(hass, home, hass_ws_client):
    hass.states.async_set(TV_POWER, "0")
    entry = await setup_room(hass)
    room = await _room(hass, hass_ws_client)
    setup = _setup_of(room)
    setup["devices"].append({"id": "receiver", "kind": "receiver", "name": "AVR", "power": {"plugs": ["switch.avr_plug"]}, "volume": AVR})
    setup["cables"] = [{"from": "video", "to": "display", "signals": ["picture"]}, {"from": "video", "to": "receiver", "signals": ["sound"]},
                       {"from": "audio", "to": "receiver", "signals": ["sound"]}, {"from": "receiver", "to": "speakers", "signals": ["sound"]}]
    setup["volume"] = {"where": "device", "device": "receiver"}
    res = await _ws(
        hass_ws_client, type="better_media_player/room/save", entry_id=entry.entry_id, version=room["version"],
        name="Lounge", area_id=None, setup=setup,
        settings={"devices": {"receiver": {"on_wait": 7}}},
    )
    assert res["result"]["saved"], res
    await hass.async_block_till_done()
    assert entry.title == "Lounge"
    assert [d["id"] for d in entry.options["devices"]] == ["display", "speakers", "subwoofer", "receiver"]
    assert entry.runtime_data.power.on_order == ["display", "receiver", "subwoofer", "speakers"], "the subwoofer: fed directly"
    assert (await get_settings(hass, entity_id=ROOM))["devices"]["receiver"]["on_wait"] == 7, "kept over the reload"
    assert res["result"]["version"] != room["version"]


async def test_a_device_removed_takes_its_settings_along(hass, home, hass_ws_client):
    entry = await setup_room(hass)
    room = await _room(hass, hass_ws_client)
    setup = _setup_of(room)
    setup["devices"] = [d for d in setup["devices"] if d["id"] != "subwoofer"]
    res = await _ws(hass_ws_client, type="better_media_player/room/save", entry_id=entry.entry_id, version=room["version"],
                    name="Living Room", setup=setup)
    assert res["result"]["saved"]
    await hass.async_block_till_done()
    assert "subwoofer" not in entry.runtime_data.data["devices"]


async def test_errors_save_nothing(hass, home, hass_ws_client):
    entry = await setup_room(hass)
    room = await _room(hass, hass_ws_client)
    before = dict(entry.options)
    res = await _ws(hass_ws_client, type="better_media_player/room/save", entry_id=entry.entry_id, version=room["version"],
                    name="Living Room", setup={**_setup_of(room), "sources": []}, settings={"room": {"night_max_volume": 5}})
    assert res["result"]["saved"] is False and "no_player" in codes(res["result"]["problems"])
    assert dict(entry.options) == before
    assert (await get_settings(hass))["settings"]["night_max_volume"] == 30


async def test_a_room_changed_meanwhile_is_not_overwritten(hass, home, hass_ws_client):
    entry = await setup_room(hass)
    room = await _room(hass, hass_ws_client)
    hass.config_entries.async_update_entry(entry, title="Renamed elsewhere")
    await hass.async_block_till_done()
    res = await _ws(hass_ws_client, type="better_media_player/room/save", entry_id=entry.entry_id, version=room["version"],
                    name="Living Room", setup=_setup_of(room))
    assert not res["success"] and res["error"]["code"] == "changed"
    assert entry.title == "Renamed elsewhere"


async def test_the_area_is_the_room_device_s(hass, home, hass_ws_client):
    entry = await setup_room(hass)
    den = ar.async_get(hass).async_create("Den")
    room = await _room(hass, hass_ws_client)
    res = await _ws(hass_ws_client, type="better_media_player/room/save", entry_id=entry.entry_id, version=room["version"],
                    name="Living Room", area_id=den.id, setup=_setup_of(room))
    assert res["result"]["saved"]
    await hass.async_block_till_done()
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    assert device.area_id == den.id
    assert er.async_get(hass).async_get(ROOM) is not None, "the room player keeps its id"


async def test_create_and_delete_a_room(hass, home, hass_ws_client):
    await setup_hub(hass)
    await _media_player_ready(hass)
    hass.states.async_set("switch.kitchen_speakers", "off")
    res = await _ws(
        hass_ws_client, type="better_media_player/room/create", name="Kitchen", area_id=None,
        setup={"sources": [{"id": "audio", "player": KITCHEN}], "devices": [{"id": "speakers", "kind": "speakers", "power": {"plugs": ["switch.kitchen_speakers"]}}],
               "cables": [], "volume": {"where": "players"}},
        settings={"room": {"prewarm": False}, "devices": {"speakers": {"on_wait": 4}}},
    )
    assert res["result"]["saved"], res
    entry = hass.config_entries.async_get_entry(res["result"]["entry_id"])
    assert entry.title == "Kitchen" and entry.version == 4
    got = await get_settings(hass, entity_id="media_player.kitchen")
    assert got["settings"]["prewarm"] is False and got["devices"]["speakers"]["on_wait"] == 4
    res = await _ws(hass_ws_client, type="better_media_player/room/delete", entry_id=entry.entry_id)
    assert res["success"]
    assert hass.config_entries.async_get_entry(entry.entry_id) is None
    assert hass.states.get("media_player.kitchen") is None


async def test_a_new_room_with_errors_is_not_created(hass, home, hass_ws_client):
    await setup_hub(hass)
    res = await _ws(hass_ws_client, type="better_media_player/room/create", name="Empty",
                    setup={"sources": [], "devices": [], "cables": [], "volume": {"where": "players"}})
    assert res["result"]["saved"] is False
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_the_central_entry_cannot_be_deleted_as_a_room(hass, home, hass_ws_client):
    hub = await setup_hub(hass)
    res = await _ws(hass_ws_client, type="better_media_player/room/delete", entry_id=hub.entry_id)
    assert not res["success"] and res["error"]["code"] == "not_found"


async def test_central_save(hass, home, hass_ws_client):
    home.group("idle", [], FOLLOW)
    hass.states.async_set("media_player.radio", "idle")
    hub = await setup_hub(hass)
    res = await _ws(hass_ws_client, type="better_media_player/central/save", follow_player="media_player.radio", settings={})
    assert res["result"]["saved"] is False and codes(res["result"]["problems"]) == {"follow_not_group"}
    res = await _ws(hass_ws_client, type="better_media_player/central/save", follow_player=None, settings={"hide_delay": 9})
    assert res["result"]["saved"]
    await hass.async_block_till_done()
    assert dict(hub.options) == {}
    assert (await get_settings(hass, entity_id=None))["settings"]["hide_delay"] == 9


async def test_editing_needs_an_admin(hass, home, hass_ws_client, hass_read_only_access_token):
    await setup_hub(hass)
    client = await hass_ws_client(access_token=hass_read_only_access_token)
    for msg in ({"type": "better_media_player/validate", "setup": draft()}, {"type": "better_media_player/room/delete", "entry_id": "x"},
                {"type": "better_media_player/central/save", "settings": {}}):
        await client.send_json_auto_id(msg)
        res = await client.receive_json()
        assert not res["success"] and res["error"]["code"] == "unauthorized", msg


# ------------------------------------------------- how the player is switched


@pytest.mark.parametrize(
    ("onoff", "supports", "expected"),
    [
        ("auto", True, ("player",)),
        ("auto", False, ("action",)),
        ("player", True, ("player",)),
        ("player", False, ()),  # (it cannot)
        ("actions", True, ("action",)),
        ("none", True, ()),
    ],
)
async def test_how_the_player_is_switched(hass, home, freezer, onoff, supports, expected):
    features = int(F.PLAY | F.PAUSE | F.VOLUME_SET | (F.TURN_ON | F.TURN_OFF if supports else 0))
    home.set(MUSIC, "off", supported_features=features)
    options = options_v4(room_options(video_player=None, display=None, bass=None, audio_on_action="script.on", audio_off_action="script.off"))
    options["sources"][0]["onoff"] = onoff
    entry = MockConfigEntry(domain=DOMAIN, title="Living Room", data={CONF_ENTRY_TYPE: ENTRY_TYPE_ROOM}, options=options, version=4)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    ctrl = entry.runtime_data
    src = ctrl.sources["audio"]
    got = tuple(x for x in (ctrl.switched_by(src, True),) if x)
    assert got == expected
    assert ctrl.power_controlled(src) is (supports and onoff in ("auto", "player"))
    # a player the room does not switch on is ready as it is: it is woken by playing
    assert ctrl.source_ready(src) is (not expected or not supports)
