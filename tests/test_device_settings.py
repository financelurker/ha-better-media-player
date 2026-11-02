"""How a room (and follow me) behaves: its settings, set with the action set_setting
and read with get_settings (the panel shows them)."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.exceptions import ServiceValidationError

from custom_components.better_media_player.const import (
    CENTRAL_SETTINGS,
    CONF_ENTRY_TYPE,
    DOMAIN,
    ENTRY_TYPE_HUB,
    ENTRY_TYPE_ROOM,
)
from custom_components.better_media_player.model import DEVICE_SETTINGS, ROOM_SETTINGS

from .conftest import FOLLOW, KODI, LIVING_OCC, SPEAKERS, TV, TV_POWER, room_options, setup_hub, setup_room
from .helpers import ROOM, follow_from, get_settings, set_setting, tick


async def test_get_settings_of_a_room_and_its_devices(hass, home):
    await setup_room(hass)
    got = await get_settings(hass)
    assert got["settings"] == ROOM_SETTINGS
    assert set(got["devices"]) == {"display", "speakers", "subwoofer"}
    assert all(set(d) == set(DEVICE_SETTINGS) for d in got["devices"].values())
    assert got["devices"]["display"]["on_wait"] == 0 and got["devices"]["speakers"]["standby_w"] == 3.0
    assert got["devices"]["subwoofer"]["night_off"] is True and got["devices"]["display"]["night_off"] is False
    assert (await get_settings(hass, device="display"))["devices"].keys() == {"display"}


async def test_a_device_by_its_name(hass, home):
    entry = await setup_room(hass)
    devices = [dict(d) for d in entry.options["devices"]]
    devices[0]["name"] = "TV"
    hass.config_entries.async_update_entry(entry, options={**entry.options, "devices": devices})
    await hass.async_block_till_done()
    await set_setting(hass, "on_wait", 20, device="TV")
    assert (await get_settings(hass, device=devices[0]["id"]))["devices"][devices[0]["id"]]["on_wait"] == 20


@pytest.mark.parametrize(
    ("data", "key"),
    [
        ({"setting": "nonsense", "value": 1}, "settings_unknown"),
        ({"setting": "on_wait", "value": 1}, "settings_needs_device"),
        ({"setting": "night_max_volume", "value": 1, "device": "display"}, "settings_unknown"),
        ({"setting": "on_wait", "value": 1, "device": "beamer"}, "settings_no_device"),
        ({"setting": "run_down", "value": 1, "device": "display"}, "settings_unknown"),  # gone: idle_timeout
        ({"setting": "video_idle_timeout", "value": 10}, "settings_unknown"),  # now idle_timeout
        ({"setting": "night_max_volume", "value": 101}, "settings_bad_value"),
        ({"setting": "night_max_volume", "value": "loud"}, "settings_bad_value"),
        ({"setting": "prewarm", "value": 1}, "settings_bad_value"),
        ({"setting": "power_on_preset", "value": "deafening"}, "settings_bad_value"),
        ({"setting": "night_max_volume", "value": 20, "entity_id": "media_player.somewhere"}, "settings_not_a_room"),
        ({"setting": "night_max_volume", "value": 20, "entity_id": None}, "settings_unknown"),
    ],
)
async def test_what_the_action_refuses(hass, home, data, key):
    await setup_room(hass)
    data = {"entity_id": ROOM, **data}
    if data["entity_id"] is None:
        data.pop("entity_id")
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "set_setting", data, blocking=True)
    assert err.value.translation_key == key
    assert (await get_settings(hass))["settings"] == ROOM_SETTINGS, "nothing changed"


async def test_the_idle_timeout(hass, home):
    await setup_room(hass)
    await set_setting(hass, "idle_timeout", 45)
    await set_setting(hass, "night_idle_timeout", 0)
    got = (await get_settings(hass))["settings"]
    assert (got["idle_timeout"], got["night_idle_timeout"]) == (45, 0)


async def test_central_settings_need_the_central_entry(hass, home):
    await setup_room(hass)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "get_settings", {}, blocking=True, return_response=True)
    assert err.value.translation_key == "settings_no_central"


async def test_a_wait_set_on_the_device_is_used(hass, home, freezer):
    await setup_room(hass)
    await set_setting(hass, "on_wait", 5, device="display")
    home.kodi_boots = True
    hass.async_create_task(hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True))
    await tick(hass, freezer, 2)
    assert home.on(TV)
    assert not [c for c in home.calls if c[1] == "turn_on" and c[2] == "media_player.kodi"], "waits 5 s for the TV"
    await tick(hass, freezer, 5)
    assert [c for c in home.calls if c[1] == "turn_on" and c[2] == "media_player.kodi"]


async def test_standby_wait_switched_on_the_device(hass, home, freezer):
    hass.states.async_set(TV_POWER, "80")
    await setup_room(hass, display_power=[TV_POWER])
    await set_setting(hass, "standby", True, device="display")
    home.kodi_boots = True
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 60, step=5)
    assert home.on(TV), "the TV draws 80 W: no standby yet"
    hass.states.async_set(TV_POWER, "1")
    await tick(hass, freezer, 40, step=5)
    assert not home.on(TV)


async def test_power_on_preset_on_the_device(hass, home, freezer):
    entry = await setup_room(hass)
    await set_setting(hass, "power_on_preset", "quiet")
    entry.runtime_data.data["room_volume"] = 0.7
    home.music("playing", volume_level=0.7)
    await hass.async_block_till_done()
    await tick(hass, freezer, 6)  # settle, then the preset fade
    assert hass.states.get("media_player.office_player").attributes["volume_level"] == 0.15


async def test_prewarm_switched_off_on_the_device(hass, house, freezer):
    await set_setting(hass, "prewarm", False)
    hass.states.async_set("binary_sensor.kitchen_occupancy", "on")
    await follow_from(hass, house, player="media_player.kitchen_player", room="media_player.kitchen")
    await tick(hass, freezer, 3)
    assert not house.on(SPEAKERS), "no prewarm in the living room"


async def test_settings_survive_a_reload(hass, home):
    entry = await setup_room(hass)
    await set_setting(hass, "snooze_minutes", 12)
    await set_setting(hass, "standby_w", 1.5, device="speakers")
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    got = await get_settings(hass)
    assert got["settings"]["snooze_minutes"] == 12
    assert got["devices"]["speakers"]["standby_w"] == 1.5


# ------------------------------------------------------------- central device


async def test_central_settings(hass, home):
    home.group("idle", [], FOLLOW)
    hub = await setup_hub(hass)
    assert dict(hub.options) == {"follow_player": FOLLOW}
    assert (await get_settings(hass, entity_id=None)) == {"settings": CENTRAL_SETTINGS}
    await set_setting(hass, "default_volume", 40, entity_id=None)
    assert hub.runtime_data.volume.base_volume(FOLLOW) == 40  # nothing remembered yet: the default


# ------------------------------------------------------------------ migration


async def test_room_options_move_to_the_device(hass, home, hass_storage):
    hass_storage[f"{DOMAIN}.room1"] = {
        "version": 1, "key": f"{DOMAIN}.room1",
        "data": {"audio_run_down": 4.0, "cfg_audio_run_down": 2.0, "display_run_down": 15.0, "cfg_display_run_down": 10.0},
    }
    options = room_options(
        audio_run_down=2, display_run_down=12, sleep_fade=7, display_on_wait=3, prewarm=False,
        power_on_preset="normal", occupancy=[LIVING_OCC],
    )
    entry = MockConfigEntry(
        domain=DOMAIN, title="Living Room", entry_id="room1", data={CONF_ENTRY_TYPE: ENTRY_TYPE_ROOM},
        options=options, version=2,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.version == 4
    assert set(entry.options) == {"sources", "devices", "cables", "volume", "occupancy"}
    got = await get_settings(hass)
    assert entry.minor_version == 2
    assert "run_down" not in got["devices"]["speakers"], "no run-down any more: the idle timeout"
    assert got["devices"]["display"]["on_wait"] == 3
    assert got["settings"]["sleep_fade"] == 7
    assert got["settings"]["prewarm"] is False
    assert got["settings"]["power_on_preset"] == "normal"


async def test_central_options_move_to_the_device(hass, home):
    home.group("idle", [], FOLLOW)
    entry = MockConfigEntry(
        domain=DOMAIN, title="Better Media Player", data={CONF_ENTRY_TYPE: ENTRY_TYPE_HUB},
        options={"follow_player": FOLLOW, "default_volume": 35, "dynamic_controls": False, "hide_delay": 7}, version=2,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert dict(entry.options) == {"follow_player": FOLLOW}
    got = (await get_settings(hass, entity_id=None))["settings"]
    assert (got["default_volume"], got["dynamic_controls"], got["hide_delay"]) == (35, False, 7)


async def test_without_the_standby_switch_the_plug_goes_off_at_once(hass, home, freezer):
    hass.states.async_set(TV_POWER, "80")
    await setup_room(hass, display_power=[TV_POWER])
    assert (await get_settings(hass))["devices"]["display"]["standby"] is False
    home.kodi_boots = True
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert not home.on(TV), "a power sensor alone does not make it wait"


async def test_migration_keeps_types_and_drops_old_markers(hass, home, hass_storage):
    hass_storage[f"{DOMAIN}.room1"] = {
        "version": 1, "key": f"{DOMAIN}.room1",
        "data": {"cfg_sleep_fade": 5.0, "cfg_prewarm": True},
    }
    entry = MockConfigEntry(
        domain=DOMAIN, title="Living Room", entry_id="room1", data={CONF_ENTRY_TYPE: ENTRY_TYPE_ROOM},
        options=room_options(prewarm=False, occupancy=[LIVING_OCC]), version=2,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    data = entry.runtime_data.data
    assert data["prewarm"] is False
    assert not [k for k in data if k.startswith("cfg_") and k != "cfg_volume"]


async def test_the_room_as_a_target(hass, home):
    """As a target the room player comes as a list (of one)."""
    await setup_room(hass)
    await hass.services.async_call(
        DOMAIN, "set_setting", {"setting": "night_max_volume", "value": 22}, target={"entity_id": ROOM}, blocking=True
    )
    got = await hass.services.async_call(DOMAIN, "get_settings", {}, target={"entity_id": ROOM}, blocking=True, return_response=True)
    assert got["settings"]["night_max_volume"] == 22


async def test_the_settings_of_a_player_s_hardware_plug(hass, home, freezer):
    hass.states.async_set("switch.kodi_box_plug", "off")
    await setup_room(hass, video_host="switch.kodi_box_plug")
    got = await get_settings(hass)
    assert got["sources"] == {"video": {"off_wait": 2, "on_wait": 0}}, "only a player with a plug for its hardware"
    await set_setting(hass, "on_wait", 4, device="video")
    await set_setting(hass, "off_wait", 7, device=hass.states.get(KODI).attributes.get("friendly_name") or "video")
    assert (await get_settings(hass, device="video")) == {
        "settings": ROOM_SETTINGS, "devices": {}, "sources": {"video": {"off_wait": 7, "on_wait": 4}},
    }
    for data, key in (
        ({"setting": "standby", "value": True, "device": "video"}, "settings_unknown"),
        ({"setting": "off_wait", "value": 1, "device": "audio"}, "settings_no_device"),  # no plug for its hardware
    ):
        with pytest.raises(ServiceValidationError) as err:
            await hass.services.async_call(DOMAIN, "set_setting", {"entity_id": ROOM, **data}, blocking=True)
        assert err.value.translation_key == key


async def test_values_as_automations_give_them(hass, home):
    """A device needs its room; on / off as text (an input_boolean's state); whole numbers stay whole."""
    await setup_room(hass)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "set_setting", {"device": "display", "setting": "on_wait", "value": 3}, blocking=True)
    assert err.value.translation_key == "settings_needs_room"
    await set_setting(hass, "prewarm", "off")
    await set_setting(hass, "night_max_volume", "25")  # a template's result
    got = (await get_settings(hass))["settings"]
    assert got["prewarm"] is False and got["night_max_volume"] == 25 and isinstance(got["night_max_volume"], int)


async def test_off_at_night_set_during_the_night_goes_at_once(hass, home, freezer):
    await setup_room(hass)
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.living_room_night_mode"}, blocking=True)
    await hass.async_block_till_done()
    assert home.on(SPEAKERS)
    await set_setting(hass, "night_off", True, device="speakers")
    await tick(hass, freezer, 3)
    assert not home.on(SPEAKERS), "as when night mode comes on: no run-down"
