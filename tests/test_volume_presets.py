"""Volume presets: room volumes by name."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from custom_components.better_media_player.const import (
    CONF_VOLUME_CONTROL,
    CONF_VOLUME_ENTITY,
    DOMAIN,
    VOLUME_DEVICE,
)

from .conftest import AVR, GROUP, KODI, MUSIC, setup_room

ROOM = "media_player.wohnzimmer"
SELECT = "select.wohnzimmer_volume_preset"
NIGHT = "switch.wohnzimmer_night_mode"
WAKE = "sensor.wohnzimmer_wake"


async def _tick(hass, freezer, seconds: float, step: float = 1) -> None:
    left = seconds
    while left > 0:
        freezer.tick(timedelta(seconds=min(step, left)))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        left -= step


async def _settle(hass):
    for _ in range(20):
        await asyncio.sleep(0)
        await hass.async_block_till_done()


def _vol(hass, entity_id=MUSIC):
    return hass.states.get(entity_id).attributes.get("volume_level")


def _room_volume(hass):
    return hass.states.get(ROOM).attributes["room_volume"]


async def _select(hass, option):
    await hass.services.async_call("select", "select_option", {"entity_id": SELECT, "option": option}, blocking=True)
    await hass.async_block_till_done()


async def _music_at(hass, home, freezer, volume=0.4, **overrides):
    entry = await setup_room(hass, **overrides)
    home.music("playing", volume_level=volume)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    return entry


async def test_select_offers_the_presets(hass, home, freezer):
    await _music_at(hass, home, freezer)
    st = hass.states.get(SELECT)
    assert st.attributes["options"] == ["quiet", "normal", "loud", "party", "manual"]
    assert st.state == "manual"


async def test_preset_sets_the_room_volume(hass, home, freezer):
    await _music_at(hass, home, freezer)
    await _select(hass, "loud")
    assert _vol(hass) == 0.5
    assert _room_volume(hass) == 0.5
    assert hass.states.get(SELECT).state == "loud"
    assert hass.states.get(ROOM).attributes["volume_preset"] == "loud"


async def test_other_volume_ends_the_preset(hass, home, freezer):
    await _music_at(hass, home, freezer)
    await _select(hass, "normal")
    home.music("playing", volume_level=0.35)  # Music Assistant app
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert hass.states.get(SELECT).state == "manual"
    assert _room_volume(hass) == 0.35


async def test_choosing_manual_keeps_the_volume(hass, home, freezer):
    await _music_at(hass, home, freezer)
    await _select(hass, "normal")
    await _select(hass, "manual")
    assert hass.states.get(SELECT).state == "manual"
    assert _vol(hass) == 0.3


async def test_action_and_not_offered_preset(hass, home, freezer):
    await _music_at(hass, home, freezer, preset_party=0)
    assert "party" not in hass.states.get(SELECT).attributes["options"]
    await hass.services.async_call(DOMAIN, "set_volume_preset", {"entity_id": ROOM, "preset": "quiet"}, blocking=True)
    await hass.async_block_till_done()
    assert _vol(hass) == 0.15
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "set_volume_preset", {"entity_id": ROOM, "preset": "party"}, blocking=True)


async def test_select_without_presets_offers_only_manual(hass, home):
    await setup_room(hass, preset_quiet=0, preset_normal=0, preset_loud=0, preset_party=0)
    assert hass.states.get(SELECT).attributes["options"] == ["manual"]


# ------------------------------------------------------------ preset numbers


async def _number(hass, name, value):
    await hass.services.async_call(
        "number", "set_value", {"entity_id": f"number.wohnzimmer_preset_{name}", "value": value}, blocking=True
    )
    await hass.async_block_till_done()


async def test_preset_values_are_number_entities(hass, home, freezer):
    from homeassistant.const import EntityCategory
    from homeassistant.helpers import entity_registry as er

    await _music_at(hass, home, freezer)
    st = hass.states.get("number.wohnzimmer_preset_loud")
    assert float(st.state) == 50 and st.attributes["unit_of_measurement"] == "%"
    assert er.async_get(hass).async_get("number.wohnzimmer_preset_loud").entity_category is EntityCategory.CONFIG
    await _number(hass, "loud", 60)
    await _select(hass, "loud")
    assert _vol(hass) == 0.6


async def test_changing_the_chosen_preset_sets_the_volume(hass, home, freezer):
    await _music_at(hass, home, freezer)
    await _select(hass, "normal")
    await _number(hass, "normal", 35)
    assert _vol(hass) == 0.35
    assert hass.states.get(SELECT).state == "normal"


async def test_preset_set_to_zero_is_no_longer_offered(hass, home, freezer):
    await _music_at(hass, home, freezer)
    await _select(hass, "party")
    await _number(hass, "party", 0)
    st = hass.states.get(SELECT)
    assert "party" not in st.attributes["options"]
    assert st.state == "manual"
    assert _vol(hass) == 0.7  # the volume stays
    await _number(hass, "party", 80)
    assert "party" in hass.states.get(SELECT).attributes["options"]
    assert hass.states.get(SELECT).state == "manual", "a preset dropped once is not chosen again by itself"


async def test_a_dropped_preset_does_not_come_back_after_a_reload(hass, home, freezer):
    entry = await _music_at(hass, home, freezer)
    await _select(hass, "party")
    await _number(hass, "party", 0)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await _number(hass, "party", 90)
    assert hass.states.get(SELECT).state == "manual"
    assert _vol(hass) == 0.7


async def test_another_preset_set_to_zero_leaves_the_select(hass, home, freezer):
    await _music_at(hass, home, freezer)
    await _select(hass, "normal")
    await _number(hass, "party", 0)
    assert hass.states.get(SELECT).attributes["options"] == ["quiet", "normal", "loud", "manual"]
    assert hass.states.get(SELECT).state == "normal"


async def test_number_value_survives_a_reload(hass, home, freezer):
    entry = await _music_at(hass, home, freezer)
    await _number(hass, "quiet", 10)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert float(hass.states.get("number.wohnzimmer_preset_quiet").state) == 10


# ---------------------------------------------------------- night / groups


async def test_preset_above_the_night_limit_comes_back_after_the_night(hass, home, freezer):
    await _music_at(hass, home, freezer)
    await hass.services.async_call("switch", "turn_on", {"entity_id": NIGHT}, blocking=True)
    await hass.async_block_till_done()
    await _select(hass, "party")
    assert _vol(hass) == 0.3  # limited
    assert hass.states.get(SELECT).state == "party"
    assert _room_volume(hass) == 0.7
    await hass.services.async_call("switch", "turn_off", {"entity_id": NIGHT}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 150, step=5)
    assert _vol(hass) == 0.7
    assert hass.states.get(SELECT).state == "party"


async def test_preset_in_a_group_is_only_remembered(hass, home, freezer):
    await _music_at(hass, home, freezer)
    home.group("playing", [MUSIC], GROUP)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    await _select(hass, "party")
    assert _vol(hass) == 0.4, "the group decides its members' volume"
    assert _room_volume(hass) == 0.7


async def test_preset_on_a_volume_device(hass, home, freezer):
    home.avr("off")
    await setup_room(hass, **{CONF_VOLUME_CONTROL: VOLUME_DEVICE, CONF_VOLUME_ENTITY: AVR})
    await _select(hass, "quiet")  # the AVR is off: remembered
    home.music("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert _vol(hass, AVR) == 0.15
    await _select(hass, "loud")
    assert _vol(hass, AVR) == 0.5
    assert hass.states.get(SELECT).state == "loud"


# ---------------------------------------------------------------- power on


async def test_power_on_preset(hass, home, freezer):
    entry = await setup_room(hass, power_on_preset="normal")
    entry.runtime_data.data["room_volume"] = 0.7  # last night's party
    home.music("playing", volume_level=0.7)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert _vol(hass) == 0.3
    assert hass.states.get(SELECT).state == "normal"


async def test_power_on_preset_not_on_a_restart(hass, home, freezer):
    home.music("playing", volume_level=0.6)  # the room is on already when Home Assistant starts
    entry = await setup_room(hass, power_on_preset="quiet")
    await _tick(hass, freezer, 3)
    assert _vol(hass) == 0.6
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert _vol(hass) == 0.6


async def test_power_on_preset_also_for_video(hass, home, freezer):
    entry = await setup_room(hass, power_on_preset="quiet")
    entry.runtime_data.data["room_volume"] = 0.7
    home.kodi_boots = True
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    assert _vol(hass, KODI) == 0.15


# -------------------------------------------------------------------- wake


async def _wake(hass, **data):
    payload = {"entity_id": ROOM, "media_content_id": "library://radio/1", "media_content_type": "music", **data}
    await hass.services.async_call(DOMAIN, "wake", payload, blocking=True)
    await _settle(hass)


async def test_wake_volumes_by_preset(hass, home, freezer):
    entry = await setup_room(hass)
    await _wake(hass, start_volume="quiet", target_volume="loud", fade_minutes=2)
    w = entry.runtime_data.data["wake"]
    assert (w["start_volume"], w["target_volume"]) == (0.15, 0.5)


async def test_wake_with_a_preset_not_offered(hass, home, freezer):
    await setup_room(hass, preset_loud=0)
    with pytest.raises(ServiceValidationError):
        await _wake(hass, target_volume="loud")


async def test_wake_target_is_the_power_on_preset(hass, home, freezer):
    entry = await setup_room(hass, power_on_preset="normal")
    entry.runtime_data.data["room_volume"] = 0.7
    await _wake(hass, start_volume=0.05, fade_minutes=2)
    assert entry.runtime_data.data["wake"]["target_volume"] == 0.3
