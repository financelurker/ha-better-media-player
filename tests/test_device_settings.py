"""How a room (and follow me) behaves is set on its device, not in the options."""

from __future__ import annotations

from datetime import timedelta

from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from homeassistant.const import EntityCategory
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.better_media_player.const import CONF_ENTRY_TYPE, DOMAIN, ENTRY_TYPE_HUB, ENTRY_TYPE_ROOM

from .conftest import FOLLOW, LIVING_OCC, SPEAKERS, TV, TV_POWER, room_options, setup_hub, setup_room

ROOM = "media_player.wohnzimmer"


async def _tick(hass, freezer, seconds: float, step: float = 1) -> None:
    left = seconds
    while left > 0:
        freezer.tick(timedelta(seconds=min(step, left)))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        left -= step


async def _set(hass, entity_id, value):
    if entity_id.startswith("number."):
        await hass.services.async_call("number", "set_value", {"entity_id": entity_id, "value": value}, blocking=True)
    elif entity_id.startswith("select."):
        await hass.services.async_call("select", "select_option", {"entity_id": entity_id, "option": value}, blocking=True)
    else:
        service = "turn_on" if value else "turn_off"
        await hass.services.async_call("switch", service, {"entity_id": entity_id}, blocking=True)
    await hass.async_block_till_done()


def _ids(hass, entry):
    return {e.entity_id for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)}


async def test_settings_are_config_entities(hass, home):
    entry = await setup_room(hass, occupancy=[LIVING_OCC], display_power=[TV_POWER])
    reg = er.async_get(hass)
    for entity_id in (
        "number.wohnzimmer_start_timeout",
        "number.wohnzimmer_wait_after_display_on",
        "number.wohnzimmer_wait_after_speakers_on",
        "number.wohnzimmer_sleep_timer_fade_out",
        "number.wohnzimmer_snooze_time",
        "number.wohnzimmer_night_fade_back_up",
        "number.wohnzimmer_display_standby_below",
        "number.wohnzimmer_display_wait_for_standby_at_most",
        "switch.wohnzimmer_display_wait_for_standby",
        "number.wohnzimmer_follow_me_leave_after",
        "number.wohnzimmer_follow_me_volume_offset",
        "switch.wohnzimmer_follow_me_prewarm_speakers",
        "switch.wohnzimmer_wake_detect_leaving_the_room",
        "select.wohnzimmer_power_on_preset",
    ):
        assert entity_id in _ids(hass, entry), entity_id
        assert reg.async_get(entity_id).entity_category is EntityCategory.CONFIG, entity_id
    # only what the room has: no speaker standby without speaker power sensors, no host wait
    assert "switch.wohnzimmer_speakers_wait_for_standby" not in _ids(hass, entry)
    assert "number.wohnzimmer_wait_after_player_plug_on" not in _ids(hass, entry)


async def test_follow_settings_only_in_follow_rooms(hass, home):
    entry = await setup_room(hass)
    assert "number.wohnzimmer_follow_me_leave_after" not in _ids(hass, entry)
    assert "switch.wohnzimmer_follow_me_prewarm_speakers" not in _ids(hass, entry)


async def test_a_wait_set_on_the_device_is_used(hass, home, freezer):
    await setup_room(hass)
    await _set(hass, "number.wohnzimmer_wait_after_display_on", 5)
    home.kodi_boots = True
    hass.async_create_task(hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True))
    await _tick(hass, freezer, 2)
    assert home.on(TV)
    assert not [c for c in home.calls if c[1] == "turn_on" and c[2] == "media_player.kodi"], "waits 5 s for the TV"
    await _tick(hass, freezer, 5)
    assert [c for c in home.calls if c[1] == "turn_on" and c[2] == "media_player.kodi"]


async def test_standby_wait_switched_on_the_device(hass, home, freezer):
    hass.states.async_set(TV_POWER, "80")
    await setup_room(hass, display_power=[TV_POWER])
    await _set(hass, "switch.wohnzimmer_display_wait_for_standby", True)
    home.kodi_boots = True
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 60, step=5)
    assert home.on(TV), "the TV draws 80 W: no standby yet"
    hass.states.async_set(TV_POWER, "1")
    await _tick(hass, freezer, 40, step=5)
    assert not home.on(TV)


async def test_power_on_preset_on_the_device(hass, home, freezer):
    entry = await setup_room(hass)
    await _set(hass, "select.wohnzimmer_power_on_preset", "quiet")
    entry.runtime_data.data["room_volume"] = 0.7
    home.music("playing", volume_level=0.7)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert hass.states.get("media_player.office_player").attributes["volume_level"] == 0.15


async def test_prewarm_switched_off_on_the_device(hass, house, freezer):
    from .test_follow import follow_from

    await _set(hass, "switch.wohnzimmer_follow_me_prewarm_speakers", False)
    hass.states.async_set("binary_sensor.kitchen_occupancy", "on")
    await follow_from(hass, house, player="media_player.kitchen_player", room="media_player.kitchen")
    await _tick(hass, freezer, 3)
    assert not house.on(SPEAKERS), "no prewarm in the living room"


async def test_settings_survive_a_reload(hass, home):
    entry = await setup_room(hass)
    await _set(hass, "number.wohnzimmer_snooze_time", 12)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert float(hass.states.get("number.wohnzimmer_snooze_time").state) == 12


# ------------------------------------------------------------- central device


async def test_central_settings_are_entities(hass, home):
    home.group("idle", [], FOLLOW)
    hub = await setup_hub(hass)
    assert dict(hub.options) == {"follow_player": FOLLOW}
    reg = er.async_get(hass)
    for entity_id in (
        "number.better_media_player_follow_me_default_volume",
        "number.better_media_player_hide_delay",
        "switch.better_media_player_dynamic_controls",
    ):
        assert reg.async_get(entity_id).entity_category is EntityCategory.CONFIG
    await _set(hass, "number.better_media_player_follow_me_default_volume", 40)
    assert hub.runtime_data.base_volume(FOLLOW) == 40  # nothing remembered yet: the default


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
        domain=DOMAIN, title="Wohnzimmer", entry_id="room1", data={CONF_ENTRY_TYPE: ENTRY_TYPE_ROOM},
        options=options, version=2,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.version == 3
    assert set(entry.options) == {"video_player", "audio_player", "display", "speakers", "bass", "occupancy", "volume_control"}
    state = hass.states.get
    assert float(state("number.wohnzimmer_speaker_run_down").state) == 4, "set on the entity: it stays"
    assert float(state("number.wohnzimmer_display_run_down").state) == 12, "changed in the options before: it wins"
    assert float(state("number.wohnzimmer_sleep_timer_fade_out").state) == 7
    assert float(state("number.wohnzimmer_wait_after_display_on").state) == 3
    assert state("switch.wohnzimmer_follow_me_prewarm_speakers").state == "off"
    assert state("select.wohnzimmer_power_on_preset").state == "normal"


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
    assert float(hass.states.get("number.better_media_player_follow_me_default_volume").state) == 35
    assert hass.states.get("switch.better_media_player_dynamic_controls").state == "off"
    assert float(hass.states.get("number.better_media_player_hide_delay").state) == 7


async def test_without_the_standby_switch_the_plug_goes_off_at_once(hass, home, freezer):
    hass.states.async_set(TV_POWER, "80")
    await setup_room(hass, display_power=[TV_POWER])
    assert hass.states.get("switch.wohnzimmer_display_wait_for_standby").state == "off"
    home.kodi_boots = True
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    assert not home.on(TV), "a power sensor alone does not make it wait"


async def test_migration_keeps_types_and_drops_old_markers(hass, home, hass_storage):
    hass_storage[f"{DOMAIN}.room1"] = {
        "version": 1, "key": f"{DOMAIN}.room1",
        "data": {"cfg_sleep_fade": 5.0, "cfg_prewarm": True},
    }
    entry = MockConfigEntry(
        domain=DOMAIN, title="Wohnzimmer", entry_id="room1", data={CONF_ENTRY_TYPE: ENTRY_TYPE_ROOM},
        options=room_options(prewarm=False, occupancy=[LIVING_OCC]), version=2,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    data = entry.runtime_data.data
    assert data["prewarm"] is False
    assert not [k for k in data if k.startswith("cfg_") and k != "cfg_volume"]
