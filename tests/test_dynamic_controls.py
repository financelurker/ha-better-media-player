"""Dynamic controls: only what makes sense right now is shown."""

from __future__ import annotations

from datetime import timedelta

from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.better_media_player.const import DOMAIN

from .conftest import LIVING_OCC, MUSIC, SPEAKERS, setup_hub, setup_room

ROOM = "media_player.wohnzimmer"
ALL_OFF = "button.wohnzimmer_all_off"
SLEEP = "number.wohnzimmer_sleep_timer"
SLEEP_PLUS = "button.wohnzimmer_sleep_timer_15_min"
SLEEP_END = "sensor.wohnzimmer_sleep_timer_end"
NIGHT = "switch.wohnzimmer_night_mode"
QUIET = "switch.wohnzimmer_quiet"
WAKE = "sensor.wohnzimmer_wake"
PRESET = "select.wohnzimmer_volume_preset"
FOLLOW_ROOMS = "sensor.better_media_player_follow_rooms"
ROOM_CONTROLS = [ALL_OFF, SLEEP, SLEEP_PLUS, SLEEP_END, NIGHT, QUIET, WAKE, PRESET]
ALWAYS = [ROOM, "switch.wohnzimmer_automatic_power", "number.wohnzimmer_night_maximum_volume",
          "number.wohnzimmer_preset_normal", "sensor.wohnzimmer_status"]


async def _tick(hass, freezer, seconds: float, step: float = 1) -> None:
    left = seconds
    while left > 0:
        freezer.tick(timedelta(seconds=min(step, left)))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        left -= step


def _hidden(hass, entity_id) -> bool:
    return er.async_get(hass).async_get(entity_id).hidden_by == er.RegistryEntryHider.INTEGRATION


def _shown(hass) -> set[str]:
    return {e for e in ROOM_CONTROLS if not _hidden(hass, e)}


async def _music(hass, home, freezer, state="playing"):
    home.music(state)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 1)


async def test_nothing_plays_only_the_room_player(hass, home, freezer):
    await setup_room(hass)
    await _tick(hass, freezer, 5)
    assert _shown(hass) == set()
    assert not any(_hidden(hass, e) for e in ALWAYS)


async def test_playback_shows_its_controls_at_once(hass, home, freezer):
    await setup_room(hass)
    await _tick(hass, freezer, 5)
    await _music(hass, home, freezer)
    assert _shown(hass) == {ALL_OFF, SLEEP, SLEEP_PLUS, NIGHT, PRESET}


async def test_controls_go_after_the_hide_delay(hass, home, freezer):
    await setup_room(hass)
    await _music(hass, home, freezer)
    await _tick(hass, freezer, 5)
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 1)
    assert PRESET in _shown(hass), "not before the delay"
    await _tick(hass, freezer, 6)
    assert _shown(hass) == set()


async def test_all_off_stays_while_something_has_power(hass, home, freezer):
    await setup_room(hass)
    await _music(hass, home, freezer)
    await _tick(hass, freezer, 5)
    await _music(hass, home, freezer, "idle")  # music stopped: speakers run down (2 min)
    await _tick(hass, freezer, 10)
    assert home.on(SPEAKERS)
    assert _shown(hass) == {ALL_OFF}
    await _tick(hass, freezer, 130, step=5)
    assert not home.on(SPEAKERS)
    assert _shown(hass) == set()


async def test_a_short_stop_between_two_items_hides_nothing(hass, home, freezer):
    await setup_room(hass)
    await _music(hass, home, freezer)
    await _tick(hass, freezer, 5)
    home.music("idle")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 1)
    await _music(hass, home, freezer)
    await _tick(hass, freezer, 10)
    assert PRESET in _shown(hass)


async def test_sleep_timer_shows_its_end_and_stays(hass, home, freezer):
    await setup_room(hass)
    await _music(hass, home, freezer)
    await hass.services.async_call(DOMAIN, "set_sleep_timer", {"entity_id": ROOM, "minutes": 30}, blocking=True)
    await hass.async_block_till_done()
    assert SLEEP_END in _shown(hass)


async def test_night_mode_stays_while_on(hass, home, freezer):
    await setup_room(hass)
    await _tick(hass, freezer, 5)
    await hass.services.async_call("switch", "turn_on", {"entity_id": NIGHT}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    assert _shown(hass) == {NIGHT}


async def test_wake_shows_its_sensor(hass, home, freezer):
    import asyncio

    await setup_room(hass)
    await _tick(hass, freezer, 5)
    await hass.services.async_call(
        DOMAIN, "wake", {"entity_id": ROOM, "media_content_id": "x", "media_content_type": "music"}, blocking=True
    )
    for _ in range(10):
        await asyncio.sleep(0)
        await hass.async_block_till_done()
    assert WAKE in _shown(hass)


# ---------------------------------------------------------------- follow me


async def test_follow_me_shows_quiet_and_night_in_follow_rooms(hass, house, freezer):
    from .test_follow import follow_from

    await _tick(hass, freezer, 5)
    assert _hidden(hass, QUIET) and _hidden(hass, NIGHT) and _hidden(hass, FOLLOW_ROOMS)
    hass.states.async_set("binary_sensor.kitchen_occupancy", "on")
    await follow_from(hass, house, player="media_player.kitchen_player", room="media_player.kitchen")
    await hass.async_block_till_done()
    assert not _hidden(hass, QUIET), "follow me runs: the living room may get the music"
    assert not _hidden(hass, NIGHT)
    assert not _hidden(hass, FOLLOW_ROOMS)


async def test_quiet_stays_while_on(hass, house, freezer):
    await _tick(hass, freezer, 5)
    await hass.services.async_call("switch", "turn_on", {"entity_id": QUIET}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    assert not _hidden(hass, QUIET)


# ---------------------------------------------------------- central options


async def test_dynamic_controls_off_shows_everything(hass, home, freezer):
    await setup_room(hass)
    await _tick(hass, freezer, 5)
    assert _shown(hass) == set()
    await setup_hub(hass, dynamic_controls=False)
    await _tick(hass, freezer, 5)
    assert _shown(hass) == set(ROOM_CONTROLS)
    assert not _hidden(hass, FOLLOW_ROOMS)


async def test_hide_delay_from_the_central_settings(hass, home, freezer):
    await setup_hub(hass, hide_delay=10)
    await setup_room(hass)
    await _music(hass, home, freezer)
    await _tick(hass, freezer, 5)
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 6)
    assert PRESET in _shown(hass)
    await _tick(hass, freezer, 8)
    assert PRESET not in _shown(hass)


async def test_hub_without_follow_player(hass, home, freezer):
    from homeassistant import config_entries

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "hub"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"dynamic_controls": False})
    assert result["type"].value == "create_entry"
    assert result["options"]["dynamic_controls"] is False
    assert "follow_player" not in result["options"]


async def test_user_shown_control_stays_shown(hass, home, freezer):
    await setup_room(hass)
    await _tick(hass, freezer, 5)
    er.async_get(hass).async_update_entity(PRESET, hidden_by=None)  # the user shows it
    await _music(hass, home, freezer)
    await _tick(hass, freezer, 5)
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 10)
    assert PRESET in _shown(hass)
