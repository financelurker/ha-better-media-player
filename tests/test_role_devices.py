"""Roles with devices that turn themselves on/off, mixed with plugs, and own actions."""

from __future__ import annotations

from datetime import timedelta

from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.util import dt as dt_util

from .conftest import BASS, HARMONY, IR_OFF, IR_ON, KODI, LG, MUSIC, SPEAKERS, TV, TV_POWER, setup_room

ROOM = "media_player.wohnzimmer"


async def _tick(hass, freezer, seconds: float, step: float = 5) -> None:
    left = seconds
    while left > 0:
        freezer.tick(timedelta(seconds=min(step, left)))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        left -= step


def _calls(home, entity_id, service=None):
    return [c[1] for c in home.calls if c[2] == entity_id and (service is None or c[1] == service)]


def _order(home, entities, service):
    return [c[2] for c in home.calls if c[2] in entities and c[1] == service]


async def _turn_on(hass):
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()


async def _turn_off(hass):
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()


def _state(hass, entity_id):
    return hass.states.get(entity_id).state


# ------------------------------------------------------------------ devices


async def test_a_tv_as_display(hass, home, freezer):
    hass.states.async_set(LG, "off")
    await setup_room(hass, display=[LG])
    home.kodi_boots = True
    await _turn_on(hass)
    assert _state(hass, LG) == "on"
    assert _order(home, [LG, KODI, SPEAKERS], "turn_on") == [LG, KODI, SPEAKERS]
    await _turn_off(hass)
    assert _state(hass, LG) == "off"
    assert ("media_player", "turn_off", LG) in home.calls


async def test_a_remote_as_speakers(hass, home, freezer):
    hass.states.async_set(HARMONY, "off")
    entry = await setup_room(hass, speakers=[HARMONY], bass=None)
    entry.runtime_data.data["last_source"] = "audio"
    await _turn_on(hass)
    assert _state(hass, HARMONY) == "on"
    assert ("remote", "turn_on", HARMONY) in home.calls
    await _turn_off(hass)
    assert ("remote", "turn_off", HARMONY) in home.calls


async def test_plug_then_device(hass, home, freezer):
    home.lg_on_plug = True
    hass.states.async_set(LG, "unavailable")
    await setup_room(hass, display=[TV, LG])
    home.kodi_boots = True
    await _turn_on(hass)
    assert home.on(TV) and _state(hass, LG) == "on"
    assert _order(home, [TV, LG, KODI], "turn_on") == [TV, LG, KODI]


async def test_a_device_that_comes_up_late_is_waited_for(hass, home, freezer):
    hass.states.async_set(LG, "unavailable")
    await setup_room(hass, display=[TV, LG])
    home.kodi_boots = True
    hass.async_create_task(_turn_on(hass))
    await _tick(hass, freezer, 10)
    assert home.on(TV)
    assert not _calls(home, KODI, "turn_on"), "the TV comes first"
    hass.states.async_set(LG, "off")  # booted
    await hass.async_block_till_done()
    assert _state(hass, LG) == "on"
    assert _calls(home, KODI, "turn_on")


async def test_a_device_that_never_comes_is_left_out(hass, home, freezer):
    hass.states.async_set(LG, "unavailable")
    entry = await setup_room(hass, display=[TV, LG])
    home.kodi_boots = True
    hass.async_create_task(_turn_on(hass))
    await _tick(hass, freezer, 100)  # start timeout 90 s
    assert _calls(home, KODI, "turn_on"), "the start goes on without it"
    assert LG in entry.runtime_data.held
    assert _state(hass, "sensor.wohnzimmer_status") == "on"


async def test_off_device_first_then_its_plug(hass, home, freezer):
    home.lg_on_plug = True
    hass.states.async_set(LG, "unavailable")
    await setup_room(hass, display=[TV, LG])
    home.kodi_boots = True
    await _turn_on(hass)
    await _turn_off(hass)
    assert _order(home, [LG, TV], "turn_off") == [LG, TV]
    assert not home.on(TV)


async def test_the_plug_waits_for_the_device_to_go_off(hass, home, freezer):
    hass.states.async_set(LG, "off")
    await setup_room(hass, display=[TV, LG])
    home.kodi_boots = True
    await _turn_on(hass)
    home.lg_obeys = False  # the TV does not react to turn_off
    await _turn_off(hass)
    assert ("media_player", "turn_off", LG) in home.calls
    assert home.on(TV), "the TV still reports on"
    await _tick(hass, freezer, 70)
    assert not home.on(TV), "after 60 s the plug cuts it"


async def test_standby_wait_after_the_device_is_off(hass, home, freezer):
    hass.states.async_set(LG, "off")
    hass.states.async_set(TV_POWER, "80")
    entry = await setup_room(hass, display=[TV, LG], display_power=[TV_POWER])
    await entry.runtime_data.async_set_setting("display_standby", True)
    home.kodi_boots = True
    await _turn_on(hass)
    await _turn_off(hass)
    assert _state(hass, LG) == "off"
    await _tick(hass, freezer, 30)
    assert home.on(TV), "panel care: 80 W"
    hass.states.async_set(TV_POWER, "1")
    await _tick(hass, freezer, 40)
    assert not home.on(TV)


async def test_a_device_only_role_has_no_standby_wait(hass, home, freezer):
    hass.states.async_set(LG, "off")
    hass.states.async_set(TV_POWER, "80")
    entry = await setup_room(hass, display=[LG], display_power=[TV_POWER])
    await entry.runtime_data.async_set_setting("display_standby", True)
    home.kodi_boots = True
    await _turn_on(hass)
    await _turn_off(hass)
    assert _state(hass, LG) == "off"
    assert _state(hass, "sensor.wohnzimmer_status") != "stopping"


# ---------------------------------------------------------- outside changes


async def test_a_tv_turned_off_by_hand_stays_off_until_the_next_start(hass, home, freezer):
    hass.states.async_set(LG, "off")
    entry = await setup_room(hass, display=[LG])
    home.kodi_boots = True
    await _turn_on(hass)
    home.kodi("playing")
    await hass.async_block_till_done()
    hass.states.async_set(LG, "off")  # the TV's own remote
    await _tick(hass, freezer, 60)
    assert _state(hass, LG) == "off", "not turned back on"
    assert not entry.runtime_data.manual, "everything else stays automatic"
    assert home.on(SPEAKERS)
    await _turn_off(hass)
    await _tick(hass, freezer, 300)
    await _turn_on(hass)
    assert _state(hass, LG) == "on", "the next start switches it again"


async def test_a_device_turned_on_by_hand_is_manual(hass, home, freezer):
    hass.states.async_set(LG, "off")
    entry = await setup_room(hass, display=[LG])
    hass.states.async_set(LG, "on")  # live TV, without the room
    await _tick(hass, freezer, 900)
    assert entry.runtime_data.manual
    assert _state(hass, LG) == "on", "not run down"


async def test_a_plug_switched_by_hand_is_still_manual(hass, home, freezer):
    entry = await setup_room(hass)
    hass.states.async_set(TV, "on")
    await hass.async_block_till_done()
    assert entry.runtime_data.manual


# ------------------------------------------------------------ own actions


async def test_own_actions(hass, home, freezer):
    hass.states.async_set(IR_ON, "off")
    hass.states.async_set(IR_OFF, "off")
    entry = await setup_room(hass, bass=None, bass_on_action=IR_ON, bass_off_action=IR_OFF)
    entry.runtime_data.data["last_source"] = "audio"
    await _turn_on(hass)
    assert ("script", "turn_on", IR_ON) in home.calls
    assert _order(home, [SPEAKERS, IR_ON], "turn_on") == [SPEAKERS, IR_ON]
    calls = len(home.calls)
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert ("script", "turn_on", IR_ON) not in home.calls[calls:], "assumed on: not sent again"
    await _turn_off(hass)
    assert ("script", "turn_on", IR_OFF) in home.calls


async def test_the_assumed_state_survives_a_reload(hass, home, freezer):
    hass.states.async_set(IR_ON, "off")
    hass.states.async_set(IR_OFF, "off")
    entry = await setup_room(hass, bass=None, bass_on_action=IR_ON, bass_off_action=IR_OFF)
    entry.runtime_data.data["last_source"] = "audio"
    await _turn_on(hass)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await _turn_off(hass)
    assert ("script", "turn_on", IR_OFF) in home.calls


async def test_night_mode_switches_an_action_subwoofer_off(hass, home, freezer):
    hass.states.async_set(IR_ON, "off")
    hass.states.async_set(IR_OFF, "off")
    entry = await setup_room(hass, bass=None, bass_on_action=IR_ON, bass_off_action=IR_OFF)
    entry.runtime_data.data["last_source"] = "audio"
    await _turn_on(hass)
    home.music("playing")
    await hass.async_block_till_done()
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.wohnzimmer_night_mode"}, blocking=True)
    await _tick(hass, freezer, 10)
    assert ("script", "turn_on", IR_OFF) in home.calls
    assert home.on(SPEAKERS)


# --------------------------------------------------------- one device, two roles


async def test_a_tv_with_its_own_speakers(hass, home, freezer):
    hass.states.async_set(LG, "off")
    entry = await setup_room(hass, display=[LG], speakers=[LG], bass=[BASS])
    entry.runtime_data.data["last_source"] = "audio"
    await _turn_on(hass)
    home.music("playing")
    await hass.async_block_till_done()
    assert _state(hass, LG) == "on", "music needs its speakers"
    await _tick(hass, freezer, 60)
    assert _state(hass, LG) == "on", "the display's run-down does not cut what the speakers need"
    home.music("idle")
    await _tick(hass, freezer, 200)
    assert _state(hass, LG) == "off"
    assert MUSIC  # (the music player stays the room's)
