"""A player that plays on its own is no group member, even if an idle group
still lists it (e.g. Music Assistant's "All players")."""

from __future__ import annotations

from datetime import timedelta

from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.util import dt as dt_util

from custom_components.better_media_player.backend import groups_of

from .conftest import FOLLOW, GROUP, MUSIC, setup_room

PRESET = "select.wohnzimmer_volume_preset"


async def _tick(hass, freezer, seconds=3):
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()


async def _playing_on_its_own_in_an_idle_group(hass, home, freezer):
    entry = await setup_room(hass)
    entry.runtime_data.data["last_source"] = "audio"
    home.music("playing", volume_level=0.33)
    home.group("idle", [MUSIC], GROUP)  # still lists it, plays nothing
    home.set(MUSIC, "playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer)
    return entry


async def test_a_preset_is_set(hass, home, freezer):
    await _playing_on_its_own_in_an_idle_group(hass, home, freezer)
    await hass.services.async_call("select", "select_option", {"entity_id": PRESET, "option": "loud"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(MUSIC).attributes["volume_level"] == 0.5


async def test_a_volume_changed_on_the_player_is_learned(hass, home, freezer):
    entry = await _playing_on_its_own_in_an_idle_group(hass, home, freezer)
    await hass.services.async_call("select", "select_option", {"entity_id": PRESET, "option": "quiet"}, blocking=True)
    await hass.async_block_till_done()
    home.music("playing", volume_level=0.42)  # the Music Assistant app
    await hass.async_block_till_done()
    await _tick(hass, freezer)
    assert hass.states.get(PRESET).state == "manual"
    assert entry.runtime_data.volume.value == 0.42


async def test_the_rule(hass, home):
    home.group("idle", [MUSIC], GROUP)
    home.music("idle")
    assert groups_of(hass, MUSIC, idle=True) == [GROUP], "joined, the group does not play yet (follow me)"
    home.music("playing")
    assert groups_of(hass, MUSIC, idle=True) == [], "plays on its own"
    home.group("playing", [MUSIC], FOLLOW)
    assert groups_of(hass, MUSIC, idle=True) == [FOLLOW], "plays in the group"
    assert groups_of(hass, MUSIC) == [FOLLOW]
