"""Version 4: a room set up with roles (display, speakers, subwoofer) becomes devices
and cables; its setting entities go (set_setting / get_settings instead)."""

from __future__ import annotations

from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component

from custom_components.better_media_player.const import CONF_ENTRY_TYPE, DOMAIN, ENTRY_TYPE_HUB, ENTRY_TYPE_ROOM
from custom_components.better_media_player.devices.const import ACTION_ID
from custom_components.better_media_player.issues import START_DELAY
from custom_components.better_media_player.model import (
    REMOVED_CENTRAL_ENTITIES,
    REMOVED_ROOM_ENTITIES,
    options_v4,
)

from .conftest import AMP, AVR, BASS, FOLLOW, KODI, MUSIC, SPEAKERS, TV, TV_POWER, room_options
from .helpers import HOST, get_settings, tick

# a living room of version 1.2.0: Kodi with its box on a plug, a music player,
# TV, speakers and subwoofer on plugs, the TV's power sensor, an amplifier's volume
LIVING_V3 = room_options(
    audio_host=HOST,
    display_power=[TV_POWER],
    volume_control="device",
    volume_entity=AMP,
    occupancy=["binary_sensor.living_occupancy"],
    connections=[
        {"from": "video_player", "to": "display", "signals": ["picture"]},
        {"from": "video_player", "to": "speakers", "signals": ["sound"]},
        {"from": "audio_player", "to": "speakers", "signals": ["sound"]},
        {"from": "speakers", "to": "bass", "signals": ["sound"]},
    ],
)
STORED_V3 = {
    "display_run_down": 15,
    "audio_run_down": 4,
    "display_standby": True,
    "display_standby_w": 2.5,
    "host_run_down": 6,
    "video_idle_timeout": 40,
    "night_max_volume": 25,
    "prewarm": False,
    "room_volume": 0.3,
    "assumed": {f"{ACTION_ID}.audio": True, f"{ACTION_ID}.bass": False, "switch.x": True},
}


async def _room_v3(hass, hass_storage, options=LIVING_V3, stored=STORED_V3, setting_entities=True):
    entry = MockConfigEntry(
        domain=DOMAIN, title="Living Room", entry_id="room1", data={CONF_ENTRY_TYPE: ENTRY_TYPE_ROOM},
        options=options, version=3,
    )
    entry.add_to_hass(hass)
    hass_storage[f"{DOMAIN}.room1"] = {"version": 1, "key": f"{DOMAIN}.room1", "data": dict(stored)}
    if setting_entities:
        reg = er.async_get(hass)
        for domain, key in REMOVED_ROOM_ENTITIES:
            reg.async_get_or_create(domain, DOMAIN, f"room1_{key}", suggested_object_id=f"living_room_{key}", config_entry=entry)
    return entry


async def _setup(hass, entry):
    for entity_id in (TV_POWER, HOST, AMP, "binary_sensor.living_occupancy"):  # what the room is set up with
        hass.states.async_set(entity_id, "0" if entity_id.startswith(("sensor.", "number.")) else "off")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def test_roles_become_devices_and_cables():
    new = options_v4(LIVING_V3)
    assert [(s["id"], s["player"], s["host"]) for s in new["sources"]] == [("video", KODI, None), ("audio", MUSIC, HOST)]
    devices = {d["id"]: d for d in new["devices"]}
    assert list(devices) == ["display", "speakers", "subwoofer"]
    assert [d["kind"] for d in devices.values()] == ["display", "speakers", "subwoofer"]
    assert devices["display"]["power"] == {"plugs": [TV], "devices": [], "actions": None, "sensors": [TV_POWER]}
    assert devices["subwoofer"]["power"]["plugs"] == [BASS]
    assert [(c["from"], c["to"]) for c in new["cables"]] == [
        ("video", "display"), ("video", "speakers"), ("audio", "speakers"), ("speakers", "subwoofer"),
    ]
    # the amplifier's volume belongs to no device of the room: assumed on the speakers, and said so
    assert new["volume"] == {"where": "device", "device": "speakers"}
    assert devices["speakers"]["volume"] == AMP and devices["speakers"]["volume_migrated"] is True
    assert new["occupancy"] == ["binary_sensor.living_occupancy"]


def test_a_volume_on_a_device_of_the_room_stays_there():
    new = options_v4(room_options(speakers=[SPEAKERS, AVR], volume_control="device", volume_entity=AVR))
    speakers = next(d for d in new["devices"] if d["id"] == "speakers")
    assert speakers["power"] == {"plugs": [SPEAKERS], "devices": [AVR], "actions": None, "sensors": []}
    assert speakers["volume"] == AVR and "volume_migrated" not in speakers


def test_a_volume_entity_left_over_is_not_taken():
    new = options_v4(room_options(volume_control="players", volume_entity=AMP))
    assert new["volume"] == {"where": "players"}
    assert all(d["volume"] is None for d in new["devices"])


def test_a_players_only_room_gets_a_device_for_its_volume():
    new = options_v4(room_options(video_player=None, display=None, speakers=None, bass=None,
                                  volume_control="device", volume_entity=AMP))
    assert [(d["id"], d["kind"], d["volume"]) for d in new["devices"]] == [("volume", "amp", AMP)]
    assert new["volume"] == {"where": "device", "device": "volume"}


async def test_a_room_is_migrated(hass, home, hass_storage):
    entry = await _room_v3(hass, hass_storage)
    await _setup(hass, entry)
    assert (entry.version, entry.minor_version) == (4, 2)
    assert entry.options == options_v4(LIVING_V3)
    got = await get_settings(hass)
    assert "run_down" not in got["devices"]["display"], "no run-down any more: the idle timeout"
    assert got["settings"]["idle_timeout"] == 40 and "video_idle_timeout" not in entry.runtime_data.data
    assert got["devices"]["display"]["standby"] is True and got["devices"]["display"]["standby_w"] == 2.5
    assert got["devices"]["speakers"]["standby"] is False, "not set before: the default"
    assert got["devices"]["subwoofer"]["night_off"] is True, "off at night, as the subwoofer was"
    assert got["settings"]["night_max_volume"] == 25 and got["settings"]["prewarm"] is False
    assert got["sources"] == {"audio": {"off_wait": 6, "on_wait": 0}}, "the hardware plug's: to the player that has one"
    assert "host_run_down" not in got["settings"]
    data = entry.runtime_data.data
    assert data["room_volume"] == 0.3
    assert data["assumed"] == {f"{ACTION_ID}.speakers": True, f"{ACTION_ID}.subwoofer": False, "switch.x": True}
    assert not {k for k in STORED_V3 if k.startswith(("display_", "audio_"))} & set(data)


async def test_the_setting_entities_go(hass, home, hass_storage):
    entry = await _room_v3(hass, hass_storage)
    await _setup(hass, entry)
    reg = er.async_get(hass)
    ids = {e.entity_id for e in er.async_entries_for_config_entry(reg, entry.entry_id)}
    assert not [i for i in ids if i.startswith(("number.living_room_display", "select.living_room_power_on"))]
    assert "number.living_room_sleep_timer" in ids, "everyday controls stay"
    assert "switch.living_room_automatic_power" in ids
    assert len(entry.runtime_data.data["removed_entities"]) == len(REMOVED_ROOM_ENTITIES)


async def test_the_central_entry_is_migrated(hass, home, hass_storage):
    home.group("idle", [], FOLLOW)
    entry = MockConfigEntry(
        domain=DOMAIN, title="Better Media Player", entry_id="hub1", data={CONF_ENTRY_TYPE: ENTRY_TYPE_HUB},
        options={"follow_player": FOLLOW}, version=3,
    )
    entry.add_to_hass(hass)
    hass_storage[f"{DOMAIN}.hub1"] = {"version": 1, "key": f"{DOMAIN}.hub1", "data": {"default_volume": 35}}
    reg = er.async_get(hass)
    for domain, key in REMOVED_CENTRAL_ENTITIES:
        reg.async_get_or_create(domain, DOMAIN, f"hub1_{key}", suggested_object_id=f"better_media_player_{key}", config_entry=entry)
    await _setup(hass, entry)
    assert entry.version == 4 and dict(entry.options) == {"follow_player": FOLLOW}
    assert (await get_settings(hass, entity_id=None))["settings"]["default_volume"] == 35
    assert {e.domain for e in er.async_entries_for_config_entry(reg, entry.entry_id)} == {"sensor"}


async def test_automations_that_use_a_removed_entity_are_in_repairs(hass, home, hass_storage, freezer):
    entry = await _room_v3(hass, hass_storage)
    assert await async_setup_component(hass, "automation", {"automation": [
        {"id": "night", "alias": "Night limit", "triggers": [{"trigger": "time", "at": "22:00:00"}],
         "actions": [{"action": "number.set_value", "target": {"entity_id": "number.living_room_night_max_volume"},
                      "data": {"value": 20}}]},
        {"id": "other", "alias": "Other", "triggers": [{"trigger": "time", "at": "08:00:00"}],
         "actions": [{"action": "switch.turn_on", "target": {"entity_id": "switch.living_room_automatic_power"}}]},
    ]})
    await _setup(hass, entry)
    await tick(hass, freezer, START_DELAY + 3)
    issues = [i for (d, i) in ir.async_get(hass).issues if d == DOMAIN and i.startswith("removed_entities")]
    assert len(issues) == 1
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issues[0])
    assert issue.translation_placeholders["room"] == "Living Room"
    assert issue.translation_placeholders["entities"] == "- `number.living_room_night_max_volume`: automation.night_limit"
    # the automation changed to the action: the issue goes
    with patch("homeassistant.config.load_yaml_config_file", return_value={"automation": [
        {"id": "night", "alias": "Night limit", "triggers": [{"trigger": "time", "at": "22:00:00"}],
         "actions": [{"action": "better_media_player.set_setting",
                      "data": {"entity_id": "media_player.living_room", "setting": "night_max_volume", "value": 20}}]},
    ]}):
        await hass.services.async_call("automation", "reload", blocking=True)
    await tick(hass, freezer, 3)
    assert not [i for (d, i) in ir.async_get(hass).issues if d == DOMAIN]


async def test_no_issue_when_nothing_uses_them(hass, home, hass_storage, freezer):
    entry = await _room_v3(hass, hass_storage)
    await _setup(hass, entry)
    await tick(hass, freezer, START_DELAY + 3)
    assert not [i for (d, i) in ir.async_get(hass).issues if d == DOMAIN]


# a receiver in the speakers' role passes the picture on to the TV (Kodi → receiver → TV)
AVR_V3 = room_options(
    connections=[
        {"from": "video_player", "to": "speakers", "signals": ["picture", "sound"]},
        {"from": "speakers", "to": "display", "signals": ["picture"]},
        {"from": "audio_player", "to": "speakers", "signals": ["sound"]},
        {"from": "speakers", "to": "bass", "signals": ["sound"]},
    ],
)


async def test_a_receiver_that_passes_the_picture_on_stays_one_for_the_sound(hass, home, hass_storage):
    """As before: the TV before the player, the receiver after it; quiet and prewarm still take it."""
    entry = await _room_v3(hass, hass_storage, options=AVR_V3, stored={}, setting_entities=False)
    await _setup(hass, entry)
    power = entry.runtime_data.power
    assert power.on_order == ["display", "speakers", "subwoofer"]
    assert [n.key for n in power.sound_nodes()] == ["speakers", "subwoofer"]
    assert power.prewarms(power.nodes["speakers"])


async def test_a_room_of_a_pre_release_gets_the_idle_timeout(hass, home, hass_storage):
    """4.1 (only in pre-releases): the idle timeout's new names, the plug's run-down is its
    off_wait, the devices' run-downs go."""
    options = options_v4(LIVING_V3)
    entry = MockConfigEntry(
        domain=DOMAIN, title="Living Room", entry_id="room1", data={CONF_ENTRY_TYPE: ENTRY_TYPE_ROOM},
        options=options, version=4, minor_version=1,
    )
    entry.add_to_hass(hass)
    hass_storage[f"{DOMAIN}.room1"] = {"version": 1, "key": f"{DOMAIN}.room1", "data": {
        "video_idle_timeout": 45, "night_video_idle_timeout": 0, "night_max_volume": 25,
        "devices": {"display": {"run_down": 15, "night_run_down": 1, "on_wait": 3}, "speakers": {"run_down": 4}},
        "sources": {"audio": {"run_down": 6, "on_wait": 2}},
    }}
    await _setup(hass, entry)
    assert (entry.version, entry.minor_version) == (4, 2)
    got = await get_settings(hass)
    assert (got["settings"]["idle_timeout"], got["settings"]["night_idle_timeout"]) == (45, 0)
    assert got["settings"]["night_max_volume"] == 25
    assert got["devices"]["display"]["on_wait"] == 3 and "run_down" not in got["devices"]["display"]
    assert got["sources"] == {"audio": {"off_wait": 6, "on_wait": 2}}
    data = entry.runtime_data.data
    assert not {"video_idle_timeout", "night_video_idle_timeout"} & set(data)
    assert data["devices"]["display"] == {"on_wait": 3} and data["devices"]["speakers"] == {}


async def test_scripts_that_use_a_removed_entity_are_in_repairs_until_changed(hass, home, hass_storage, freezer):
    entry = await _room_v3(hass, hass_storage)
    uses = {"sequence": [{"action": "number.set_value", "target": {"entity_id": "number.living_room_night_max_volume"}, "data": {"value": 20}}]}
    assert await async_setup_component(hass, "script", {"script": {"night_limit": uses}})
    await _setup(hass, entry)
    await tick(hass, freezer, START_DELAY + 3)
    assert [i for (d, i) in ir.async_get(hass).issues if d == DOMAIN and i.startswith("removed_entities")]
    # the script changed to the action (saved in the editor: script.reload): the issue goes
    fixed = {"sequence": [{"action": "better_media_player.set_setting",
                           "data": {"entity_id": "media_player.living_room", "setting": "night_max_volume", "value": 20}}]}
    with patch("homeassistant.config.load_yaml_config_file", return_value={"script": {"night_limit": fixed}}):
        await hass.services.async_call("script", "reload", blocking=True)
    await tick(hass, freezer, 5)
    assert not [i for (d, i) in ir.async_get(hass).issues if d == DOMAIN]
