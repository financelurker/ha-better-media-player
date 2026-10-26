"""A music player with an on/off of its own (Music Assistant's power control,
also its fake one): handled like the video player.
"""

from __future__ import annotations

from .conftest import BASS, FOLLOW, KITCHEN, KITCHEN_OCC, LIVING_OCC, MUSIC, SPEAKERS, setup_room
from .helpers import (
    ROOM,
    SNOOZE,
    WAKE,
    calls_to,
    follow_from,
    quiet_leaves_and_cuts_speakers,
    start_wake,
    tick,
    wake_playing,
)

MUSIC_ONLY = {"video_player": None, "display": None}


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
    await tick(hass, freezer, 10)
    assert home.on(SPEAKERS)


async def test_on_but_nothing_plays_goes_off_after_the_run_down(hass, home, freezer):
    await _room(hass, home)
    home.music("idle")
    await hass.async_block_till_done()
    await tick(hass, freezer, 60)
    assert home.on(SPEAKERS)
    await tick(hass, freezer, 75)  # speaker run-down: 2 min
    assert hass.states.get(MUSIC).state == "off", "turned off with the room"
    await tick(hass, freezer, 15)
    assert not home.on(SPEAKERS), "no second run-down"
    assert not home.on(BASS)


async def test_paused_goes_off_after_the_run_down(hass, home, freezer):
    await _room(hass, home)
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    assert home.on(SPEAKERS)
    home.music("paused")
    await hass.async_block_till_done()
    await tick(hass, freezer, 150)
    assert hass.states.get(MUSIC).state == "off"
    assert not home.on(SPEAKERS)


async def test_playing_again_in_time_keeps_everything_on(hass, home, freezer):
    await _room(hass, home)
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    home.music("paused")
    await hass.async_block_till_done()
    await tick(hass, freezer, 60)
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 300)
    assert home.on(SPEAKERS)
    assert not calls_to(home, MUSIC, "turn_off")


async def test_turned_off_in_music_assistant_runs_down(hass, home, freezer):
    await _room(hass, home)
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    home.music("off")  # off in the app: Music Assistant stops it
    await hass.async_block_till_done()
    await tick(hass, freezer, 60)
    assert home.on(SPEAKERS), "the speakers' run-down"
    await tick(hass, freezer, 90)
    assert not home.on(SPEAKERS)


async def test_the_room_turns_it_on_and_off(hass, home, freezer):
    await _room(hass, home)
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert calls_to(home, MUSIC, "turn_on")
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
    await tick(hass, freezer, 10)
    assert not home.on(SPEAKERS), "as before: active only while it plays"


async def test_follow_me_turns_it_on_before_joining(hass, house, freezer):
    house.ma_power = True
    house.kitchen("off")
    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house)
    hass.states.async_set(KITCHEN_OCC, "on")
    await hass.async_block_till_done()
    assert calls_to(house, KITCHEN, "turn_on"), "on first"
    assert KITCHEN in house.members(FOLLOW)
    on = next(i for i, c in enumerate(house.calls) if c[2] == KITCHEN and c[1] == "turn_on")
    assert any(c[1] == "join" and c[2] == FOLLOW for c in house.calls[on:]), "then the join"


async def test_still_on_after_its_turn_off_stays_out(hass, home, freezer):
    await _room(hass, home)
    home.music("idle")
    await hass.async_block_till_done()
    home.ma_power = False  # (its turn_off does not get through)
    await tick(hass, freezer, 135)
    await tick(hass, freezer, 120)
    assert len(calls_to(home, MUSIC, "turn_off")) == 1, "once"
    assert hass.states.get(MUSIC).state == "idle"
    await tick(hass, freezer, 30)
    assert not home.on(SPEAKERS), "not back on until it really was off"


async def test_only_turn_on_is_no_power_control(hass, home, freezer):
    from homeassistant.components.media_player import MediaPlayerEntityFeature as F

    from .conftest import MA_FEATURES

    home.music("off")
    entry = await setup_room(hass, **MUSIC_ONLY)
    entry.runtime_data.data["last_source"] = "audio"
    hass.states.async_set(MUSIC, "idle", {"supported_features": MA_FEATURES | F.TURN_ON, "mass_player_type": "player"})
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    assert not home.on(SPEAKERS)


# --------------------------------------------------- with the room's features


async def test_a_snooze_keeps_it_on_and_resumes(hass, home, freezer):
    await _room(hass, home)
    home.music("idle", volume_level=0.5)
    await hass.async_block_till_done()
    await wake_playing(hass, home, freezer)
    await tick(hass, freezer, 130)
    await hass.services.async_call("button", "press", {"entity_id": SNOOZE}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(WAKE).state == "snoozed"
    await tick(hass, freezer, 8 * 60, step=10)
    assert hass.states.get(MUSIC).state == "paused", "not turned off while snoozed"
    await tick(hass, freezer, 70, step=10)
    assert calls_to(home, MUSIC, "media_play"), "resumed where it was"


async def test_a_paused_follow_group_keeps_its_members(hass, house, freezer):
    house.ma_power = True
    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house)
    house.group("paused", house.members(FOLLOW), FOLLOW)  # a phone call
    for player in house.members(FOLLOW):
        house.set(player, "paused")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5 * 60)
    assert MUSIC in house.members(FOLLOW)
    assert not calls_to(house, MUSIC, "turn_off")
    assert not calls_to(house, MUSIC, "unjoin")


async def test_after_music_then_video_it_goes_off_with_the_speakers(hass, home, freezer):
    from .conftest import KODI

    home.ma_power = True
    home.music("off")
    home.kodi_boots = True
    await setup_room(hass)
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    await hass.services.async_call("media_player", "select_source", {"entity_id": ROOM, "source": "Kodi"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(MUSIC).state == "paused"
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 15 * 60, step=30)
    assert hass.states.get(MUSIC).state == "off"
    assert hass.states.get(KODI).state == "off"


async def test_manual_mode_leaves_it_alone(hass, home, freezer):
    entry = await _room(hass, home)
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    home.music("paused")
    await hass.async_block_till_done()
    entry.runtime_data.manual = True
    await tick(hass, freezer, 300)
    assert hass.states.get(MUSIC).state == "paused"


async def test_played_after_turning_on_runs_down_from_the_pause(hass, home, freezer):
    await _room(hass, home)
    home.music("idle")  # on in the app
    await hass.async_block_till_done()
    await tick(hass, freezer, 60)
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 30)
    home.music("paused")
    await hass.async_block_till_done()
    await tick(hass, freezer, 90)
    assert home.on(SPEAKERS), "the run-down counts from the pause"
    await tick(hass, freezer, 60)
    assert not home.on(SPEAKERS)


async def test_a_start_of_the_room_that_fails_turns_everything_off(hass, home, freezer):
    from pytest_homeassistant_custom_component.common import async_capture_events

    from custom_components.better_media_player.const import EVENT_WAKE_FAILED

    failed = async_capture_events(hass, EVENT_WAKE_FAILED)
    entry = await _room(hass, home)  # off: the wake turns it on itself
    entry.runtime_data.data["room_volume"] = 0.5
    await start_wake(hass)
    await tick(hass, freezer, 65)
    assert [e.data["reason"] for e in failed] == ["not_playing"]
    assert hass.states.get(WAKE).state == "idle"
    await tick(hass, freezer, 10)
    assert not home.on(SPEAKERS), "no run-down for a player the room turned on itself"


async def test_quiet_cuts_the_speakers_right_away(hass, house, freezer):
    house.ma_power = True
    house.kitchen("off")
    await quiet_leaves_and_cuts_speakers(hass, house)


async def test_without_speakers_of_the_room_it_is_left_alone(hass, home, freezer):
    home.ma_power = True
    home.music("playing")
    await setup_room(hass, video_player=None, display=None, speakers=None, bass=None)
    home.music("paused")
    await hass.async_block_till_done()
    await tick(hass, freezer, 300)
    assert not calls_to(home, MUSIC, "turn_off"), "no devices to go off with"


# ------------------------------------------------------------------ review


async def test_speakers_not_reported_yet_after_a_restart(hass, home, freezer):
    entry = await _room(hass, home)
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    home.music("paused")
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set(SPEAKERS, "unavailable")  # the plug has not reported yet
    hass.states.async_set(BASS, "unavailable")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    assert not calls_to(home, MUSIC, "turn_off"), "no off to follow before they reported"


async def test_turned_off_again_after_a_failed_off(hass, home, freezer):
    await _room(hass, home)
    home.music("idle")
    await hass.async_block_till_done()
    home.ma_power = False  # its first turn_off does not get through
    await tick(hass, freezer, 135)
    assert len(calls_to(home, MUSIC, "turn_off")) == 1
    home.ma_power = True
    home.music("playing")  # in use again
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    home.music("paused")
    await hass.async_block_till_done()
    await tick(hass, freezer, 150)
    assert hass.states.get(MUSIC).state == "off", "the next run-down turns it off again"


async def test_automatic_power_off_cuts_nothing(hass, home, freezer):
    await _room(hass, home)
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.living_room_automatic_power"}, blocking=True
    )
    await hass.async_block_till_done()
    for plug in (SPEAKERS, BASS):
        hass.states.async_set(plug, "on")
    home.music("idle")  # on in the app
    await hass.async_block_till_done()
    await tick(hass, freezer, 300)
    assert home.on(SPEAKERS), "no automatic switching"
    assert not calls_to(home, MUSIC, "turn_off")


async def test_it_goes_off_before_its_hardware_plug(hass, home, freezer):
    host = "switch.player_plug_2"
    hass.states.async_set(host, "on")  # the player's hardware is up
    home.ma_power = True
    home.music("off")
    entry = await setup_room(hass, video_player=None, display=None, audio_host=host)
    entry.runtime_data.data["last_source"] = "audio"
    await entry.runtime_data.async_set_setting("host_run_down", 10)
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    home.music("paused")
    await hass.async_block_till_done()
    await tick(hass, freezer, 135)  # speakers ran down; the hardware plug's run-down goes on
    assert not home.on(SPEAKERS)
    assert home.on(host)
    assert hass.states.get(MUSIC).state == "off", "with its speakers, before its plug"


async def test_a_run_down_of_zero_still_brings_the_speakers_up(hass, home, freezer):
    entry = await _room(hass, home)
    await entry.runtime_data.async_set_setting("audio_run_down", 0)
    home.music("idle")
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    assert home.on(SPEAKERS)
