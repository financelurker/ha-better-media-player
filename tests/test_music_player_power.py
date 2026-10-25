"""A music player with an on/off of its own (Music Assistant's power control,
also its fake one): handled like the video player."""

from __future__ import annotations

from datetime import timedelta

from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.util import dt as dt_util

from .conftest import BASS, FOLLOW, KITCHEN, KITCHEN_OCC, LIVING_OCC, MUSIC, SPEAKERS, setup_room

ROOM = "media_player.wohnzimmer"
MUSIC_ONLY = {"video_player": None, "display": None}


async def _tick(hass, freezer, seconds: float, step: float = 5) -> None:
    left = seconds
    while left > 0:
        freezer.tick(timedelta(seconds=min(step, left)))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        left -= step


def _calls(home, entity_id, service):
    return [c for c in home.calls if c[2] == entity_id and c[1] == service]


async def _room(hass, home, state="off"):
    home.ma_power = True
    home.music(state)
    entry = await setup_room(hass, **MUSIC_ONLY)
    entry.runtime_data.data["last_source"] = "audio"
    return entry


async def test_turned_on_in_music_assistant_brings_the_speakers_up(hass, home, freezer):
    await _room(hass, home)
    home.music("idle")  # on in the Music Assistant app
    await hass.async_block_till_done()
    await _tick(hass, freezer, 10)
    assert home.on(SPEAKERS)


async def test_on_but_nothing_plays_goes_off_after_the_run_down(hass, home, freezer):
    await _room(hass, home)
    home.music("idle")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 60)
    assert home.on(SPEAKERS)
    await _tick(hass, freezer, 75)  # speaker run-down: 2 min
    assert hass.states.get(MUSIC).state == "off", "turned off with the room"
    await _tick(hass, freezer, 15)
    assert not home.on(SPEAKERS), "no second run-down"
    assert not home.on(BASS)


async def test_paused_goes_off_after_the_run_down(hass, home, freezer):
    await _room(hass, home)
    home.music("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 10)
    assert home.on(SPEAKERS)
    home.music("paused")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 150)
    assert hass.states.get(MUSIC).state == "off"
    assert not home.on(SPEAKERS)


async def test_playing_again_in_time_keeps_everything_on(hass, home, freezer):
    await _room(hass, home)
    home.music("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 10)
    home.music("paused")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 60)
    home.music("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 300)
    assert home.on(SPEAKERS)
    assert not _calls(home, MUSIC, "turn_off")


async def test_turned_off_in_music_assistant_runs_down(hass, home, freezer):
    await _room(hass, home)
    home.music("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 10)
    home.music("off")  # off in the app: Music Assistant stops it
    await hass.async_block_till_done()
    await _tick(hass, freezer, 60)
    assert home.on(SPEAKERS), "the speakers' run-down"
    await _tick(hass, freezer, 90)
    assert not home.on(SPEAKERS)


async def test_the_room_turns_it_on_and_off(hass, home, freezer):
    await _room(hass, home)
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert _calls(home, MUSIC, "turn_on")
    assert hass.states.get(MUSIC).state == "idle"
    assert home.on(SPEAKERS)
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(MUSIC).state == "off"


async def test_without_power_control_idle_is_not_in_use(hass, home, freezer):
    home.music("off")
    entry = await setup_room(hass, **MUSIC_ONLY)
    entry.runtime_data.data["last_source"] = "audio"
    home.music("idle")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 10)
    assert not home.on(SPEAKERS), "as before: active only while it plays"


async def test_follow_me_turns_it_on_before_joining(hass, house, freezer):
    from .test_follow import follow_from

    house.ma_power = True
    house.kitchen("off")
    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house)
    hass.states.async_set(KITCHEN_OCC, "on")
    await hass.async_block_till_done()
    assert _calls(house, KITCHEN, "turn_on"), "on first"
    assert KITCHEN in house.members(FOLLOW)
    on = next(i for i, c in enumerate(house.calls) if c[2] == KITCHEN and c[1] == "turn_on")
    assert any(c[1] == "join" and c[2] == FOLLOW for c in house.calls[on:]), "then the join"
