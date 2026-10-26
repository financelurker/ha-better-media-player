"""Announcements in a room: the player, the room's devices, the film, the volume."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from homeassistant.components.media_player import MediaPlayerEntityFeature as F
from homeassistant.exceptions import ServiceValidationError

from custom_components.better_media_player.const import DOMAIN

from .conftest import BASS, KODI, KODI_FEATURES, MUSIC, SPEAKERS, TV, setup_room
from .helpers import (
    NIGHT,
    PRESET,
    QUIET,
    ROOM,
    WAKE,
    announce,
    calls_to,
    follow_from,
    settle_held,
    speak,
    speak_later,
    start_wake,
    tick,
    tick_held,
    wake_playing,
)

VIDEO_MODE = "select.living_room_announcement_during_video"
LOWER_BY = "number.living_room_announcement_lower_film_by"


def _order(home, services):
    return [(c[1], c[2]) for c in home.calls if c[1] in services]


async def _room(hass, home, freezer, playing=True, volume=0.4, **overrides):
    home.ma_announce = True
    home.music("playing" if playing else "idle", volume_level=volume)
    entry = await setup_room(hass, **overrides)
    entry.runtime_data.data["last_source"] = "audio"
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    return entry


# ------------------------------------------------------------------ the player


async def test_the_room_player_offers_announcements(hass, home, freezer):
    await _room(hass, home, freezer)
    assert hass.states.get(ROOM).attributes["supported_features"] & F.MEDIA_ANNOUNCE


async def test_without_a_player_that_announces_the_room_still_offers_them(hass, home, freezer):
    home.music("playing", volume_level=0.4)
    await setup_room(hass)
    await hass.async_block_till_done()
    assert hass.states.get(ROOM).attributes["supported_features"] & F.MEDIA_ANNOUNCE, "the fallback"


async def test_tts_speak_goes_to_the_music_player(hass, home, freezer):
    await _room(hass, home, freezer)
    await speak(hass)
    assert [a[0] for a in home.announcements] == [MUSIC]
    assert home.announcements[0][1]["announce"] is True
    assert hass.states.get(MUSIC).state == "playing", "Music Assistant resumed"


async def test_the_player_of_the_active_source_first(hass, home, freezer):
    entry = await _room(hass, home, freezer, playing=False)
    home.kodi_boots = True
    hass.states.async_set(KODI, "playing", {"supported_features": KODI_FEATURES | F.MEDIA_ANNOUNCE})
    entry.runtime_data.data["last_source"] = "video"
    await hass.async_block_till_done()
    await speak(hass)
    assert [a[0] for a in home.announcements] == [KODI], "the film's player announces itself"
    assert not calls_to(home, KODI, "media_pause"), "it pauses and resumes itself"


async def test_the_other_player_when_the_active_cannot(hass, home, freezer):
    entry = await _room(hass, home, freezer, playing=False)
    home.kodi("playing")
    entry.runtime_data.data["last_source"] = "video"
    await hass.async_block_till_done()
    await speak(hass)
    assert [a[0] for a in home.announcements] == [MUSIC]


# --------------------------------------------------------------- room is off


async def test_a_room_that_is_off_comes_up_and_goes_off_again(hass, home, freezer):
    home.ma_announce = True
    home.music("off")
    await setup_room(hass)
    await hass.async_block_till_done()
    await speak(hass)
    order = _order(home, {"turn_on", "announce"})
    assert ("turn_on", SPEAKERS) in order and order.index(("turn_on", SPEAKERS)) < order.index(("announce", MUSIC))
    assert not home.on(TV), "only what the music player needs"
    await tick(hass, freezer, 10)
    assert not home.on(SPEAKERS), "off right away: no run-down"
    assert not home.on(BASS)


async def test_a_room_that_was_on_runs_down_as_usual(hass, home, freezer):
    await _room(hass, home, freezer)
    home.music("paused")
    await hass.async_block_till_done()
    await tick(hass, freezer, 30)
    await speak(hass)
    await tick(hass, freezer, 10)
    assert home.on(SPEAKERS), "its run-down goes on"


# ------------------------------------------------------------------ the film


async def _film(hass, home, freezer, mode=None, lower=None):
    entry = await _room(hass, home, freezer, playing=False)
    home.kodi("playing", volume_level=0.6)
    entry.runtime_data.data["last_source"] = "video"
    await hass.async_block_till_done()
    if mode is not None:
        await hass.services.async_call("select", "select_option", {"entity_id": VIDEO_MODE, "option": mode}, blocking=True)
    if lower is not None:
        await hass.services.async_call("number", "set_value", {"entity_id": LOWER_BY, "value": lower}, blocking=True)
    await hass.async_block_till_done()
    return entry


async def test_the_film_pauses_and_goes_on(hass, home, freezer):
    await _film(hass, home, freezer)
    assert hass.states.get(VIDEO_MODE).state == "pause"
    await speak(hass)
    order = _order(home, {"media_pause", "announce", "media_play"})
    assert order == [("media_pause", KODI), ("announce", MUSIC), ("media_play", KODI)]


async def test_the_film_is_overlaid(hass, home, freezer):
    await _film(hass, home, freezer, mode="overlay")
    await speak(hass)
    assert not calls_to(home, KODI, "media_pause")
    assert not calls_to(home, KODI, "volume_set")


async def test_the_film_is_turned_down_and_back(hass, home, freezer):
    await _film(hass, home, freezer, mode="lower", lower=25)
    levels = []
    home.announce_hold = asyncio.Event()
    task = speak_later(hass)
    await settle_held(hass)
    levels.append(hass.states.get(KODI).attributes["volume_level"])
    home.announce_hold.set()
    await task
    await hass.async_block_till_done()
    assert levels == [0.45], "25 % lower during the announcement"
    assert hass.states.get(KODI).attributes["volume_level"] == 0.6


# ---------------------------------------------------------------- the volume


async def test_volume_as_asked_and_as_a_preset(hass, home, freezer):
    await _room(hass, home, freezer)
    await announce(hass, volume=0.25)
    await announce(hass, volume="loud")
    levels = [a[1].get("extra", {}).get("announce_volume") for a in home.announcements]
    assert levels == [25, 50]


async def test_no_volume_by_default(hass, home, freezer):
    await _room(hass, home, freezer)
    await speak(hass)
    assert "announce_volume" not in (home.announcements[0][1].get("extra") or {}), "Music Assistant's own"


async def test_at_night_at_most_the_limit(hass, home, freezer):
    await _room(hass, home, freezer, volume=0.2)
    await hass.services.async_call("switch", "turn_on", {"entity_id": NIGHT}, blocking=True)
    await hass.async_block_till_done()
    await speak(hass)
    await announce(hass, volume=0.8)
    await announce(hass, volume=0.1)
    levels = [a[1]["extra"]["announce_volume"] for a in home.announcements]
    assert levels == [30, 30, 10]


async def test_the_room_volume_is_not_learned(hass, home, freezer):
    entry = await _room(hass, home, freezer, volume=0.3)
    await hass.services.async_call("select", "select_option", {"entity_id": PRESET, "option": "normal"}, blocking=True)
    await tick(hass, freezer, 5)
    await announce(hass, volume=0.7)
    await tick(hass, freezer, 5)
    assert hass.states.get(PRESET).state == "normal"
    assert entry.runtime_data.volume.value == 0.3


# ------------------------------------------------------------------- quiet


async def test_a_quiet_room_is_left_out(hass, home, freezer):
    await _room(hass, home, freezer)
    await hass.services.async_call("switch", "turn_on", {"entity_id": QUIET}, blocking=True)
    await hass.async_block_till_done()
    await speak(hass)
    await announce(hass)
    assert home.announcements == []
    await announce(hass, ignore_quiet=True)
    assert [a[0] for a in home.announcements] == [MUSIC], "an emergency gets through"


# ------------------------------------------------------------ holding still


async def test_no_source_change_and_no_snooze_meanwhile(hass, home, freezer):
    from custom_components.better_media_player.const import EVENT_SOURCE_CHANGED
    from pytest_homeassistant_custom_component.common import async_capture_events

    entry = await _film(hass, home, freezer, mode="overlay")
    await tick(hass, freezer, 10)
    events = async_capture_events(hass, EVENT_SOURCE_CHANGED)
    home.announce_hold = asyncio.Event()
    task = speak_later(hass)
    await settle_held(hass)
    await tick_held(hass, freezer, 10)  # two players play for a while
    home.announce_hold.set()
    await task
    await tick(hass, freezer, 10)
    assert events == []
    assert entry.runtime_data.last_source == "video"


async def test_the_action_speaks_a_message(hass, home, freezer):
    import sys
    import types
    from unittest.mock import MagicMock

    await _room(hass, home, freezer)
    tts = types.ModuleType("homeassistant.components.tts")  # (the real one needs extra packages)
    tts.generate_media_source_id = MagicMock(return_value="media-source://tts/hello")
    with patch.dict(sys.modules, {"homeassistant.components.tts": tts}):
        await hass.services.async_call(DOMAIN, "announce", {"entity_id": ROOM, "message": "Hello"}, blocking=True)
    assert tts.generate_media_source_id.call_args.args[1] == "Hello"
    assert home.announcements[0][1]["media_content_id"] == "media-source://tts/hello"

async def test_a_wake_is_not_snoozed_by_it(hass, home, freezer):
    await _room(hass, home, freezer, playing=False)
    await wake_playing(hass, home, freezer)
    await tick(hass, freezer, 10)
    await announce(hass, volume=0.6)
    await tick(hass, freezer, 5)
    assert hass.states.get(WAKE).state in ("fading", "playing"), "no snooze"


async def test_a_sleep_fade_goes_on_after_it(hass, home, freezer):
    entry = await _room(hass, home, freezer, volume=0.5)
    await hass.services.async_call(
        DOMAIN, "set_sleep_timer", {"entity_id": ROOM, "minutes": 10, "fade_minutes": 10}, blocking=True
    )
    await tick(hass, freezer, 60, step=10)
    assert entry.runtime_data.sleep.fading_player(MUSIC)
    await announce(hass, volume=0.8)
    await tick(hass, freezer, 60, step=10)
    assert entry.runtime_data.sleep.fading_player(MUSIC), "not taken for a change by hand"
    assert hass.states.get(MUSIC).attributes["volume_level"] < 0.5


# -------------------------------------------------------------------- groups


async def _follow_both(hass, house, freezer):
    from .conftest import KITCHEN_OCC, LIVING_OCC

    house.ma_announce = True
    house.kitchen("idle")
    hass.states.async_set(LIVING_OCC, "on")
    hass.states.async_set(KITCHEN_OCC, "on")
    await follow_from(hass, house)
    await hass.async_block_till_done()


async def test_a_whole_group_gets_it_once(hass, house, freezer):
    from .conftest import FOLLOW, KITCHEN

    await _follow_both(hass, house, freezer)
    assert set(house.members(FOLLOW)) == {MUSIC, KITCHEN}
    await announce(hass, entity_id=[ROOM, "media_player.kitchen"])
    assert [a[0] for a in house.announcements] == [FOLLOW]


async def test_part_of_a_group_gets_it_on_its_own_players(hass, house, freezer):
    from .conftest import FOLLOW, KITCHEN

    await _follow_both(hass, house, freezer)
    await announce(hass, entity_id="media_player.kitchen")
    assert [a[0] for a in house.announcements] == [KITCHEN]
    assert set(house.members(FOLLOW)) == {MUSIC, KITCHEN}, "the group plays on"


# ---------------------------------------------- the room holds still meanwhile


async def test_a_room_that_is_off_takes_the_music_player(hass, home, freezer):
    home.ma_announce = True
    home.music("off")
    await setup_room(hass)
    hass.states.async_set(KODI, "off", {"supported_features": KODI_FEATURES | F.MEDIA_ANNOUNCE})
    await hass.async_block_till_done()
    await speak(hass)
    assert [a[0] for a in home.announcements] == [MUSIC]


async def test_no_source_change_for_a_room_that_was_off(hass, home, freezer):
    from pytest_homeassistant_custom_component.common import async_capture_events

    from custom_components.better_media_player.const import EVENT_SOURCE_CHANGED

    home.ma_announce = True
    home.music("off")
    await setup_room(hass)
    await tick(hass, freezer, 10)
    events = async_capture_events(hass, EVENT_SOURCE_CHANGED)
    home.announce_hold = asyncio.Event()
    task = speak_later(hass)
    await settle_held(hass)
    await tick_held(hass, freezer, 10)  # the music player plays it for a while
    home.announce_hold.set()
    await task
    await tick(hass, freezer, 10)
    assert events == []


async def test_the_sleep_timer_waits_for_it(hass, home, freezer):
    await _room(hass, home, freezer)
    await hass.services.async_call(
        DOMAIN, "set_sleep_timer", {"entity_id": ROOM, "minutes": 1, "fade_minutes": 0}, blocking=True
    )
    await tick(hass, freezer, 50, step=10)
    home.announce_hold = asyncio.Event()
    task = speak_later(hass)
    await settle_held(hass)
    await tick_held(hass, freezer, 20)  # the timer ends meanwhile
    assert not calls_to(home, MUSIC, "media_stop"), "not in the middle of the announcement"
    home.announce_hold.set()
    await task
    await tick(hass, freezer, 10)
    assert calls_to(home, MUSIC, "media_stop"), "after it"


async def test_a_preset_fade_goes_on_after_it(hass, home, freezer):
    await _room(hass, home, freezer, volume=0.2)
    await hass.services.async_call(
        DOMAIN, "set_volume_preset", {"entity_id": ROOM, "preset": "party", "fade_seconds": 10}, blocking=True
    )
    await tick(hass, freezer, 2, step=0.5)
    await announce(hass, volume=0.9)
    await tick(hass, freezer, 15, step=0.5)
    assert hass.states.get(MUSIC).attributes["volume_level"] == 0.7
    assert hass.states.get(PRESET).state == "party"


# ----------------------------------------------------- groups, films, failures


async def test_a_device_target_announces_once(hass, home, freezer):
    from homeassistant.helpers import device_registry as dr

    entry = await _room(hass, home, freezer)
    device = next(iter(dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)))
    await hass.services.async_call(
        DOMAIN, "announce", {"device_id": device.id, "media_content_id": "media-source://tts/x"}, blocking=True
    )
    assert len(home.announcements) == 1


async def test_overlapping_announcements_keep_the_room_up(hass, home, freezer):
    home.ma_announce = True
    home.music("off")
    entry = await setup_room(hass)
    await hass.async_block_till_done()
    first_hold, second_hold = asyncio.Event(), asyncio.Event()
    home.announce_hold = first_hold
    first = speak_later(hass)
    await settle_held(hass)
    home.announce_hold = second_hold
    second = speak_later(hass)
    await settle_held(hass)
    first_hold.set()
    await first
    await tick_held(hass, freezer, 5)  # the first one is over (and its settle time)
    assert entry.runtime_data.announce.active
    assert home.on(SPEAKERS), "the second one still plays"
    second_hold.set()
    await second
    await tick(hass, freezer, 5)
    assert not entry.runtime_data.announce.active
    assert not home.on(SPEAKERS), "off once the last one is over"


async def test_a_paused_group_is_not_announced_through(hass, house, freezer):
    from .conftest import FOLLOW, KITCHEN

    await _follow_both(hass, house, freezer)
    house.group("paused", house.members(FOLLOW), FOLLOW)
    for player in house.members(FOLLOW):
        house.set(player, "paused")
    await hass.async_block_till_done()
    await announce(hass, entity_id=[ROOM, "media_player.kitchen"])
    assert sorted(a[0] for a in house.announcements) == sorted([MUSIC, KITCHEN])


async def test_the_film_comes_back_up_although_its_lowering_was_late(hass, home, freezer):
    await _film(hass, home, freezer, mode="lower", lower=50)
    home.volume_lag = 1.0  # Kodi reports the lowered volume a second later
    await speak(hass)
    await tick(hass, freezer, 5)
    assert hass.states.get(KODI).attributes["volume_level"] == 0.6


async def test_a_film_that_cannot_pause_is_turned_down(hass, home, freezer):
    entry = await _room(hass, home, freezer, playing=False)
    hass.states.async_set(KODI, "playing", {"supported_features": KODI_FEATURES & ~F.PAUSE, "volume_level": 0.6})
    entry.runtime_data.data["last_source"] = "video"
    await hass.async_block_till_done()
    await speak(hass)
    assert not calls_to(home, KODI, "media_pause")
    assert calls_to(home, KODI, "volume_set")


async def test_a_preset_not_offered_fails_before_anything(hass, home, freezer):
    home.ma_announce = True
    home.music("off")
    entry = await setup_room(hass)
    await entry.runtime_data.async_set_number("preset_party", 0)
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError):
        await announce(hass, volume="party")
    assert not home.on(SPEAKERS)
    assert not entry.runtime_data.announce.active


async def test_a_room_that_fails_fails_the_action_after_the_others(hass, house, freezer):
    from custom_components.better_media_player.hub import room_of

    house.ma_announce = True
    house.music("playing")
    await hass.async_block_till_done()
    await room_of(hass, "media_player.kitchen").async_set_number("preset_party", 0)
    with pytest.raises(ServiceValidationError):
        await announce(hass, entity_id=[ROOM, "media_player.kitchen"], volume="party")
    assert [a[0] for a in house.announcements] == [MUSIC], "the room that can has it"


async def test_volume_true_is_no_volume(hass, home, freezer):
    import voluptuous as vol

    await _room(hass, home, freezer)
    with pytest.raises(vol.Invalid):
        await announce(hass, volume=True)


async def test_a_group_that_cannot_announce_is_not_announced_through(hass, house, freezer):
    from .conftest import FOLLOW, KITCHEN, MA_FEATURES

    await _follow_both(hass, house, freezer)
    group = hass.states.get(FOLLOW)
    hass.states.async_set(FOLLOW, group.state, {**group.attributes, "supported_features": MA_FEATURES})
    await hass.async_block_till_done()
    await announce(hass, entity_id=[ROOM, "media_player.kitchen"])
    assert sorted(a[0] for a in house.announcements) == sorted([MUSIC, KITCHEN])


async def test_a_wake_whose_fade_ended_meanwhile_plays_on(hass, home, freezer):
    await _room(hass, home, freezer, playing=False)
    await start_wake(hass, start_volume=0.05, target_volume=0.4, fade_minutes=1)
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    home.announce_hold = asyncio.Event()
    task = speak_later(hass)
    await settle_held(hass)
    await tick_held(hass, freezer, 70)  # longer than the fade
    home.announce_hold.set()
    await task
    await tick(hass, freezer, 5)
    assert hass.states.get(WAKE).state == "playing"
    assert hass.states.get(MUSIC).attributes["volume_level"] == 0.4


async def test_the_nights_fade_back_goes_on_in_the_time_it_had_left(hass, home, freezer):
    entry = await _room(hass, home, freezer, volume=0.6)
    entry.runtime_data.data["room_volume"] = 0.6
    await hass.services.async_call("switch", "turn_on", {"entity_id": NIGHT}, blocking=True)
    await tick(hass, freezer, 5)
    await hass.services.async_call("switch", "turn_off", {"entity_id": NIGHT}, blocking=True)
    await tick(hass, freezer, 60, step=5)  # half of the 2 min fade back
    await announce(hass)
    await tick(hass, freezer, 70, step=5)
    assert hass.states.get(MUSIC).attributes["volume_level"] == 0.6, "done within the 2 min"


# ---------------------------------------------- protection, rooms that are off


async def test_a_failed_start_keeps_another_announcements_protection(hass, home, freezer):
    entry = await _room(hass, home, freezer)
    announcer = entry.runtime_data.announce
    home.announce_hold = asyncio.Event()
    first = speak_later(hass)
    await settle_held(hass)

    async def _fails(*_args, **_kwargs):
        raise RuntimeError("devices")

    with patch.object(type(announcer), "_power_up", _fails), pytest.raises(RuntimeError):
        await announcer.async_announce("media-source://tts/y", "music")
    await tick_held(hass, freezer, 4)
    assert announcer.protects(MUSIC), "the first one still plays"
    home.announce_hold.set()
    await first


async def test_turned_off_during_it_goes_off_after_it(hass, home, freezer):
    await _room(hass, home, freezer)
    home.music("paused")
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    home.announce_hold = asyncio.Event()
    task = speak_later(hass)
    await settle_held(hass)
    off = asyncio.ensure_future(
        hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    )
    await tick_held(hass, freezer, 3)
    home.announce_hold.set()
    await task
    await off
    await tick(hass, freezer, 20)
    assert not home.on(SPEAKERS), "you said off"


async def test_a_room_that_is_off_and_announces_on_the_video_player_goes_off(hass, home, freezer):
    home.music("off")
    home.kodi_boots = True
    await setup_room(hass)
    hass.states.async_set(KODI, "off", {"supported_features": KODI_FEATURES | F.MEDIA_ANNOUNCE})
    await hass.async_block_till_done()
    await speak(hass)
    await tick(hass, freezer, 20)
    assert not home.on(TV)
    assert hass.states.get(KODI).state == "off"


async def test_a_room_that_is_off_with_music_assistant_power_goes_off(hass, home, freezer):
    home.ma_announce = True
    home.ma_power = True
    home.music("off")
    await setup_room(hass)
    await hass.async_block_till_done()
    await speak(hass)
    await tick(hass, freezer, 20)
    assert not home.on(SPEAKERS)
    assert hass.states.get(MUSIC).state == "off"


async def test_a_caller_that_gives_up_leaves_the_room_still_until_it_is_over(hass, home, freezer):
    entry = await _room(hass, home, freezer)
    home.announce_hold = asyncio.Event()
    task = speak_later(hass)
    await settle_held(hass)
    task.cancel()
    await settle_held(hass)
    await tick_held(hass, freezer, 5)
    assert entry.runtime_data.announce.protects(MUSIC), "the player still announces"
    home.announce_hold.set()
    await settle_held(hass)
    await tick(hass, freezer, 5)
    assert not entry.runtime_data.announce.active


async def test_the_room_players_announce_extra_is_checked(hass, home, freezer):
    await _room(hass, home, freezer)
    with pytest.raises(ServiceValidationError):
        await speak(hass, extra={"announce_volume": "loud"})
    await hass.services.async_call("switch", "turn_on", {"entity_id": QUIET}, blocking=True)
    await speak(hass, extra={"ignore_quiet": "false"})
    assert home.announcements == []


async def test_a_room_that_is_off_announces_once_its_speakers_are_ready(hass, home, freezer):
    home.ma_announce = True
    home.music("off")
    entry = await setup_room(hass, bass=None)  # the speakers are the last device
    await entry.runtime_data.async_set_setting("audio_on_wait", 5)  # they need a moment
    await hass.async_block_till_done()
    task = speak_later(hass)
    await settle_held(hass)
    await tick_held(hass, freezer, 3)
    assert home.on(SPEAKERS)
    assert home.announcements == [], "not before the speakers' wait is over"
    await tick_held(hass, freezer, 4)
    await task
    assert [a[0] for a in home.announcements] == [MUSIC]
