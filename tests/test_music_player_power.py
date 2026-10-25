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


async def test_still_on_after_its_turn_off_stays_out(hass, home, freezer):
    await _room(hass, home)
    home.music("idle")
    await hass.async_block_till_done()
    home.ma_power = False  # (its turn_off does not get through)
    await _tick(hass, freezer, 135)
    await _tick(hass, freezer, 120)
    assert len(_calls(home, MUSIC, "turn_off")) == 1, "once"
    assert hass.states.get(MUSIC).state == "idle"
    await _tick(hass, freezer, 30)
    assert not home.on(SPEAKERS), "not back on until it really was off"


async def test_only_turn_on_is_no_power_control(hass, home, freezer):
    from homeassistant.components.media_player import MediaPlayerEntityFeature as F

    from .conftest import MA_FEATURES

    home.music("off")
    entry = await setup_room(hass, **MUSIC_ONLY)
    entry.runtime_data.data["last_source"] = "audio"
    hass.states.async_set(MUSIC, "idle", {"supported_features": MA_FEATURES | F.TURN_ON, "mass_player_type": "player"})
    await hass.async_block_till_done()
    await _tick(hass, freezer, 10)
    assert not home.on(SPEAKERS)


# --------------------------------------------------- with the room's features


async def test_a_snooze_keeps_it_on_and_resumes(hass, home, freezer):
    from .test_wake import SNOOZE, WAKE, _wake_playing

    await _room(hass, home)
    home.music("idle", volume_level=0.5)
    await hass.async_block_till_done()
    await _wake_playing(hass, home, freezer)
    await _tick(hass, freezer, 130)
    await hass.services.async_call("button", "press", {"entity_id": SNOOZE}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(WAKE).state == "snoozed"
    await _tick(hass, freezer, 8 * 60, step=10)
    assert hass.states.get(MUSIC).state == "paused", "not turned off while snoozed"
    await _tick(hass, freezer, 70, step=10)
    assert _calls(home, MUSIC, "media_play"), "resumed where it was"


async def test_a_paused_follow_group_keeps_its_members(hass, house, freezer):
    from .test_follow import follow_from

    house.ma_power = True
    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house)
    house.group("paused", house.members(FOLLOW), FOLLOW)  # a phone call
    for player in house.members(FOLLOW):
        house.set(player, "paused")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5 * 60)
    assert MUSIC in house.members(FOLLOW)
    assert not _calls(house, MUSIC, "turn_off")
    assert not _calls(house, MUSIC, "unjoin")


async def test_after_music_then_video_it_goes_off_with_the_speakers(hass, home, freezer):
    from .conftest import KODI

    home.ma_power = True
    home.music("off")
    home.kodi_boots = True
    await setup_room(hass)
    home.music("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 10)
    await hass.services.async_call("media_player", "select_source", {"entity_id": ROOM, "source": "Kodi"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(MUSIC).state == "paused"
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 15 * 60, step=30)
    assert hass.states.get(MUSIC).state == "off"
    assert hass.states.get(KODI).state == "off"


async def test_manual_mode_leaves_it_alone(hass, home, freezer):
    entry = await _room(hass, home)
    home.music("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 10)
    home.music("paused")
    await hass.async_block_till_done()
    entry.runtime_data.manual = True
    await _tick(hass, freezer, 300)
    assert hass.states.get(MUSIC).state == "paused"


async def test_played_after_turning_on_runs_down_from_the_pause(hass, home, freezer):
    await _room(hass, home)
    home.music("idle")  # on in the app
    await hass.async_block_till_done()
    await _tick(hass, freezer, 60)
    home.music("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 30)
    home.music("paused")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 90)
    assert home.on(SPEAKERS), "the run-down counts from the pause"
    await _tick(hass, freezer, 60)
    assert not home.on(SPEAKERS)


async def test_a_start_of_the_room_that_fails_turns_everything_off(hass, home, freezer):
    from .test_wake import WAKE, _wake

    from pytest_homeassistant_custom_component.common import async_capture_events

    from custom_components.better_media_player.const import EVENT_WAKE_FAILED

    failed = async_capture_events(hass, EVENT_WAKE_FAILED)
    entry = await _room(hass, home)  # off: the wake turns it on itself
    entry.runtime_data.data["room_volume"] = 0.5
    await _wake(hass)
    await _tick(hass, freezer, 65)
    assert [e.data["reason"] for e in failed] == ["not_playing"]
    assert hass.states.get(WAKE).state == "idle"
    await _tick(hass, freezer, 10)
    assert not home.on(SPEAKERS), "no run-down for a player the room turned on itself"


async def test_quiet_cuts_the_speakers_right_away(hass, house, freezer):
    from .test_follow import test_quiet_switch_leaves_and_cuts_speakers

    house.ma_power = True
    house.kitchen("off")
    await test_quiet_switch_leaves_and_cuts_speakers(hass, house)


async def test_without_speakers_of_the_room_it_is_left_alone(hass, home, freezer):
    home.ma_power = True
    home.music("playing")
    await setup_room(hass, video_player=None, display=None, speakers=None, bass=None)
    home.music("paused")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 300)
    assert not _calls(home, MUSIC, "turn_off"), "no devices to go off with"
