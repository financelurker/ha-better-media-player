"""Announcements in a room: the player, the room's devices, the film, the volume."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.components.media_player import MediaPlayerEntityFeature as F
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from custom_components.better_media_player.const import DOMAIN

from .conftest import BASS, KODI, KODI_FEATURES, MUSIC, SPEAKERS, TV, setup_room

ROOM = "media_player.wohnzimmer"
NIGHT = "switch.wohnzimmer_night_mode"
QUIET = "switch.wohnzimmer_quiet"
PRESET = "select.wohnzimmer_volume_preset"
VIDEO_MODE = "select.wohnzimmer_announcement_during_video"
LOWER_BY = "number.wohnzimmer_announcement_lower_film_by"


async def _tick(hass, freezer, seconds: float, step: float = 1) -> None:
    left = seconds
    while left > 0:
        freezer.tick(timedelta(seconds=min(step, left)))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        left -= step


def _calls(home, entity_id, service):
    return [c for c in home.calls if c[2] == entity_id and c[1] == service]


def _order(home, services):
    return [(c[1], c[2]) for c in home.calls if c[1] in services]


async def _speak(hass, **data):
    """tts.speak's call on the room player."""
    await hass.services.async_call(
        "media_player",
        "play_media",
        {"entity_id": ROOM, "media_content_id": "media-source://tts/x", "media_content_type": "music",
         "announce": True, **data},
        blocking=True,
    )
    await hass.async_block_till_done()


def _speak_later(hass):
    """tts.speak's call, running on while the test looks at the room (untracked:
    async_block_till_done does not wait for it)."""
    return asyncio.ensure_future(
        hass.services.async_call(
            "media_player",
            "play_media",
            {"entity_id": ROOM, "media_content_id": "media-source://tts/x", "media_content_type": "music",
             "announce": True},
            blocking=True,
        )
    )


async def _settle(hass, ticks: int = 50):
    """(no async_block_till_done while an announcement is held: it would wait for it)"""
    for _ in range(ticks):
        await asyncio.sleep(0)


async def _tick_held(hass, freezer, seconds: int):
    for _ in range(seconds):
        freezer.tick(timedelta(seconds=1))
        async_fire_time_changed(hass, dt_util.utcnow())
        await _settle(hass)


async def _announce(hass, entity_id=ROOM, **data):
    await hass.services.async_call(
        DOMAIN, "announce", {"entity_id": entity_id, "media_content_id": "media-source://tts/x", **data},
        blocking=True,
    )
    await hass.async_block_till_done()


async def _room(hass, home, freezer, playing=True, volume=0.4, **overrides):
    home.ma_announce = True
    home.music("playing" if playing else "idle", volume_level=volume)
    entry = await setup_room(hass, **overrides)
    entry.runtime_data.data["last_source"] = "audio"
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    return entry


# ------------------------------------------------------------------ the player


async def test_the_room_player_offers_announcements(hass, home, freezer):
    await _room(hass, home, freezer)
    assert hass.states.get(ROOM).attributes["supported_features"] & F.MEDIA_ANNOUNCE


async def test_no_announcements_without_a_player_for_them(hass, home, freezer):
    home.music("playing", volume_level=0.4)
    await setup_room(hass)
    await hass.async_block_till_done()
    assert not hass.states.get(ROOM).attributes["supported_features"] & F.MEDIA_ANNOUNCE
    with pytest.raises(ServiceValidationError):
        await _announce(hass)


async def test_tts_speak_goes_to_the_music_player(hass, home, freezer):
    await _room(hass, home, freezer)
    await _speak(hass)
    assert [a[0] for a in home.announcements] == [MUSIC]
    assert home.announcements[0][1]["announce"] is True
    assert hass.states.get(MUSIC).state == "playing", "Music Assistant resumed"


async def test_the_player_of_the_active_source_first(hass, home, freezer):
    entry = await _room(hass, home, freezer, playing=False)
    home.kodi_boots = True
    hass.states.async_set(KODI, "playing", {"supported_features": KODI_FEATURES | F.MEDIA_ANNOUNCE})
    entry.runtime_data.data["last_source"] = "video"
    await hass.async_block_till_done()
    await _speak(hass)
    assert [a[0] for a in home.announcements] == [KODI], "the film's player announces itself"
    assert not _calls(home, KODI, "media_pause"), "it pauses and resumes itself"


async def test_the_other_player_when_the_active_cannot(hass, home, freezer):
    entry = await _room(hass, home, freezer, playing=False)
    home.kodi("playing")
    entry.runtime_data.data["last_source"] = "video"
    await hass.async_block_till_done()
    await _speak(hass)
    assert [a[0] for a in home.announcements] == [MUSIC]


# --------------------------------------------------------------- room is off


async def test_a_room_that_is_off_comes_up_and_goes_off_again(hass, home, freezer):
    home.ma_announce = True
    home.music("off")
    await setup_room(hass)
    await hass.async_block_till_done()
    await _speak(hass)
    order = _order(home, {"turn_on", "announce"})
    assert ("turn_on", SPEAKERS) in order and order.index(("turn_on", SPEAKERS)) < order.index(("announce", MUSIC))
    assert not home.on(TV), "only what the music player needs"
    await _tick(hass, freezer, 10)
    assert not home.on(SPEAKERS), "off right away: no run-down"
    assert not home.on(BASS)


async def test_a_room_that_was_on_runs_down_as_usual(hass, home, freezer):
    await _room(hass, home, freezer)
    home.music("paused")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 30)
    await _speak(hass)
    await _tick(hass, freezer, 10)
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
    await _speak(hass)
    order = _order(home, {"media_pause", "announce", "media_play"})
    assert order == [("media_pause", KODI), ("announce", MUSIC), ("media_play", KODI)]


async def test_the_film_is_overlaid(hass, home, freezer):
    await _film(hass, home, freezer, mode="overlay")
    await _speak(hass)
    assert not _calls(home, KODI, "media_pause")
    assert not _calls(home, KODI, "volume_set")


async def test_the_film_is_turned_down_and_back(hass, home, freezer):
    await _film(hass, home, freezer, mode="lower", lower=25)
    levels = []
    home.announce_hold = asyncio.Event()
    task = _speak_later(hass)
    await _settle(hass)
    levels.append(hass.states.get(KODI).attributes["volume_level"])
    home.announce_hold.set()
    await task
    await hass.async_block_till_done()
    assert levels == [0.45], "25 % lower during the announcement"
    assert hass.states.get(KODI).attributes["volume_level"] == 0.6


# ---------------------------------------------------------------- the volume


async def test_volume_as_asked_and_as_a_preset(hass, home, freezer):
    await _room(hass, home, freezer)
    await _announce(hass, volume=0.25)
    await _announce(hass, volume="loud")
    levels = [a[1].get("extra", {}).get("announce_volume") for a in home.announcements]
    assert levels == [25, 50]


async def test_no_volume_by_default(hass, home, freezer):
    await _room(hass, home, freezer)
    await _speak(hass)
    assert "announce_volume" not in (home.announcements[0][1].get("extra") or {}), "Music Assistant's own"


async def test_at_night_at_most_the_limit(hass, home, freezer):
    await _room(hass, home, freezer, volume=0.2)
    await hass.services.async_call("switch", "turn_on", {"entity_id": NIGHT}, blocking=True)
    await hass.async_block_till_done()
    await _speak(hass)
    await _announce(hass, volume=0.8)
    await _announce(hass, volume=0.1)
    levels = [a[1]["extra"]["announce_volume"] for a in home.announcements]
    assert levels == [30, 30, 10]


async def test_the_room_volume_is_not_learned(hass, home, freezer):
    entry = await _room(hass, home, freezer, volume=0.3)
    await hass.services.async_call("select", "select_option", {"entity_id": PRESET, "option": "normal"}, blocking=True)
    await _tick(hass, freezer, 5)
    await _announce(hass, volume=0.7)
    await _tick(hass, freezer, 5)
    assert hass.states.get(PRESET).state == "normal"
    assert entry.runtime_data.volume.value == 0.3


# ------------------------------------------------------------------- quiet


async def test_a_quiet_room_is_left_out(hass, home, freezer):
    await _room(hass, home, freezer)
    await hass.services.async_call("switch", "turn_on", {"entity_id": QUIET}, blocking=True)
    await hass.async_block_till_done()
    await _speak(hass)
    await _announce(hass)
    assert home.announcements == []
    await _announce(hass, ignore_quiet=True)
    assert [a[0] for a in home.announcements] == [MUSIC], "an emergency gets through"


# ------------------------------------------------------------ holding still


async def test_no_source_change_and_no_snooze_meanwhile(hass, home, freezer):
    from custom_components.better_media_player.const import EVENT_SOURCE_CHANGED
    from pytest_homeassistant_custom_component.common import async_capture_events

    entry = await _film(hass, home, freezer, mode="overlay")
    await _tick(hass, freezer, 10)
    events = async_capture_events(hass, EVENT_SOURCE_CHANGED)
    home.announce_hold = asyncio.Event()
    task = _speak_later(hass)
    await _settle(hass)
    await _tick_held(hass, freezer, 10)  # two players play for a while
    home.announce_hold.set()
    await task
    await _tick(hass, freezer, 10)
    assert events == []
    assert entry.runtime_data.last_source == "video"


async def test_the_action_speaks_a_message(hass, home, freezer):
    await _room(hass, home, freezer)
    with patch(
        "homeassistant.components.tts.generate_media_source_id", return_value="media-source://tts/hello"
    ) as generate:
        await hass.services.async_call(DOMAIN, "announce", {"entity_id": ROOM, "message": "Hello"}, blocking=True)
    assert generate.call_args.args[1] == "Hello"
    assert home.announcements[0][1]["media_content_id"] == "media-source://tts/hello"


async def test_a_wake_is_not_snoozed_by_it(hass, home, freezer):
    from .test_wake import WAKE, _wake_playing

    await _room(hass, home, freezer, playing=False)
    await _wake_playing(hass, home, freezer)
    await _tick(hass, freezer, 10)
    await _announce(hass, volume=0.6)
    await _tick(hass, freezer, 5)
    assert hass.states.get(WAKE).state in ("fading", "playing"), "no snooze"


async def test_a_sleep_fade_goes_on_after_it(hass, home, freezer):
    entry = await _room(hass, home, freezer, volume=0.5)
    await hass.services.async_call(
        DOMAIN, "set_sleep_timer", {"entity_id": ROOM, "minutes": 10, "fade_minutes": 10}, blocking=True
    )
    await _tick(hass, freezer, 60, 10)
    assert entry.runtime_data.sleep.fading_player(MUSIC)
    await _announce(hass, volume=0.8)
    await _tick(hass, freezer, 60, 10)
    assert entry.runtime_data.sleep.fading_player(MUSIC), "not taken for a change by hand"
    assert hass.states.get(MUSIC).attributes["volume_level"] < 0.5


# -------------------------------------------------------------------- groups


async def _follow_both(hass, house, freezer):
    from .conftest import KITCHEN_OCC, LIVING_OCC
    from .test_follow import follow_from

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
    await _announce(hass, entity_id=[ROOM, "media_player.kitchen"])
    assert [a[0] for a in house.announcements] == [FOLLOW]


async def test_part_of_a_group_gets_it_on_its_own_players(hass, house, freezer):
    from .conftest import FOLLOW, KITCHEN

    await _follow_both(hass, house, freezer)
    await _announce(hass, entity_id="media_player.kitchen")
    assert [a[0] for a in house.announcements] == [KITCHEN]
    assert set(house.members(FOLLOW)) == {MUSIC, KITCHEN}, "the group plays on"


# ------------------------------------------------------------ bug injection


async def test_a_room_that_is_off_takes_the_music_player(hass, home, freezer):
    home.ma_announce = True
    home.music("off")
    await setup_room(hass)
    hass.states.async_set(KODI, "off", {"supported_features": KODI_FEATURES | F.MEDIA_ANNOUNCE})
    await hass.async_block_till_done()
    await _speak(hass)
    assert [a[0] for a in home.announcements] == [MUSIC]


async def test_no_source_change_for_a_room_that_was_off(hass, home, freezer):
    from pytest_homeassistant_custom_component.common import async_capture_events

    from custom_components.better_media_player.const import EVENT_SOURCE_CHANGED

    home.ma_announce = True
    home.music("off")
    await setup_room(hass)
    await _tick(hass, freezer, 10)
    events = async_capture_events(hass, EVENT_SOURCE_CHANGED)
    home.announce_hold = asyncio.Event()
    task = _speak_later(hass)
    await _settle(hass)
    await _tick_held(hass, freezer, 10)  # the music player plays it for a while
    home.announce_hold.set()
    await task
    await _tick(hass, freezer, 10)
    assert events == []


async def test_the_sleep_timer_waits_for_it(hass, home, freezer):
    await _room(hass, home, freezer)
    await hass.services.async_call(
        DOMAIN, "set_sleep_timer", {"entity_id": ROOM, "minutes": 1, "fade_minutes": 0}, blocking=True
    )
    await _tick(hass, freezer, 50, 10)
    home.announce_hold = asyncio.Event()
    task = _speak_later(hass)
    await _settle(hass)
    await _tick_held(hass, freezer, 20)  # the timer ends meanwhile
    assert not _calls(home, MUSIC, "media_stop"), "not in the middle of the announcement"
    home.announce_hold.set()
    await task
    await _tick(hass, freezer, 10)
    assert _calls(home, MUSIC, "media_stop"), "after it"


async def test_a_preset_fade_goes_on_after_it(hass, home, freezer):
    await _room(hass, home, freezer, volume=0.2)
    await hass.services.async_call(
        DOMAIN, "set_volume_preset", {"entity_id": ROOM, "preset": "party", "fade_seconds": 10}, blocking=True
    )
    await _tick(hass, freezer, 2, 0.5)
    await _announce(hass, volume=0.9)
    await _tick(hass, freezer, 15, 0.5)
    assert hass.states.get(MUSIC).attributes["volume_level"] == 0.7
    assert hass.states.get(PRESET).state == "party"


# ------------------------------------------------------------------- review


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
    first = _speak_later(hass)
    await _settle(hass)
    home.announce_hold = second_hold
    second = _speak_later(hass)
    await _settle(hass)
    first_hold.set()
    await first
    await _tick_held(hass, freezer, 5)  # the first one is over (and its settle time)
    assert entry.runtime_data.announce.active
    assert home.on(SPEAKERS), "the second one still plays"
    second_hold.set()
    await second
    await _tick(hass, freezer, 5)
    assert not entry.runtime_data.announce.active
    assert not home.on(SPEAKERS), "off once the last one is over"


async def test_a_paused_group_is_not_announced_through(hass, house, freezer):
    from .conftest import FOLLOW, KITCHEN

    await _follow_both(hass, house, freezer)
    house.group("paused", house.members(FOLLOW), FOLLOW)
    for player in house.members(FOLLOW):
        house.set(player, "paused")
    await hass.async_block_till_done()
    await _announce(hass, entity_id=[ROOM, "media_player.kitchen"])
    assert sorted(a[0] for a in house.announcements) == sorted([MUSIC, KITCHEN])


async def test_the_film_comes_back_up_although_its_lowering_was_late(hass, home, freezer):
    await _film(hass, home, freezer, mode="lower", lower=50)
    home.volume_lag = 1.0  # Kodi reports the lowered volume a second later
    await _speak(hass)
    await _tick(hass, freezer, 5)
    assert hass.states.get(KODI).attributes["volume_level"] == 0.6


async def test_a_film_that_cannot_pause_is_turned_down(hass, home, freezer):
    entry = await _room(hass, home, freezer, playing=False)
    hass.states.async_set(KODI, "playing", {"supported_features": KODI_FEATURES & ~F.PAUSE, "volume_level": 0.6})
    entry.runtime_data.data["last_source"] = "video"
    await hass.async_block_till_done()
    await _speak(hass)
    assert not _calls(home, KODI, "media_pause")
    assert _calls(home, KODI, "volume_set")


async def test_a_preset_not_offered_fails_before_anything(hass, home, freezer):
    home.ma_announce = True
    home.music("off")
    entry = await setup_room(hass)
    await entry.runtime_data.async_set_number("preset_party", 0)
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError):
        await _announce(hass, volume="party")
    assert not home.on(SPEAKERS)
    assert not entry.runtime_data.announce.active


async def test_a_room_that_cannot_announce_fails_the_action_after_the_others(hass, house, freezer):
    from .conftest import KITCHEN, MA_FEATURES

    house.ma_announce = True
    house.music("playing")
    hass.states.async_set(KITCHEN, "idle", {"supported_features": MA_FEATURES, "mass_player_type": "player"})
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError):
        await _announce(hass, entity_id=[ROOM, "media_player.kitchen"])
    assert [a[0] for a in house.announcements] == [MUSIC], "the room that can has it"


async def test_volume_true_is_no_volume(hass, home, freezer):
    import voluptuous as vol

    await _room(hass, home, freezer)
    with pytest.raises(vol.Invalid):
        await _announce(hass, volume=True)


async def test_a_group_that_cannot_announce_is_not_announced_through(hass, house, freezer):
    from .conftest import FOLLOW, KITCHEN, MA_FEATURES

    await _follow_both(hass, house, freezer)
    group = hass.states.get(FOLLOW)
    hass.states.async_set(FOLLOW, group.state, {**group.attributes, "supported_features": MA_FEATURES})
    await hass.async_block_till_done()
    await _announce(hass, entity_id=[ROOM, "media_player.kitchen"])
    assert sorted(a[0] for a in house.announcements) == sorted([MUSIC, KITCHEN])


async def test_a_wake_whose_fade_ended_meanwhile_plays_on(hass, home, freezer):
    from .test_wake import WAKE, _wake

    await _room(hass, home, freezer, playing=False)
    await _wake(hass, start_volume=0.05, target_volume=0.4, fade_minutes=1)
    home.music("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 10)
    home.announce_hold = asyncio.Event()
    task = _speak_later(hass)
    await _settle(hass)
    await _tick_held(hass, freezer, 70)  # longer than the fade
    home.announce_hold.set()
    await task
    await _tick(hass, freezer, 5)
    assert hass.states.get(WAKE).state == "playing"
    assert hass.states.get(MUSIC).attributes["volume_level"] == 0.4


async def test_the_nights_fade_back_goes_on_in_the_time_it_had_left(hass, home, freezer):
    entry = await _room(hass, home, freezer, volume=0.6)
    entry.runtime_data.data["room_volume"] = 0.6
    await hass.services.async_call("switch", "turn_on", {"entity_id": NIGHT}, blocking=True)
    await _tick(hass, freezer, 5)
    await hass.services.async_call("switch", "turn_off", {"entity_id": NIGHT}, blocking=True)
    await _tick(hass, freezer, 60, 5)  # half of the 2 min fade back
    await _announce(hass)
    await _tick(hass, freezer, 70, 5)
    assert hass.states.get(MUSIC).attributes["volume_level"] == 0.6, "done within the 2 min"


# ------------------------------------------------------------ second review


async def test_a_failed_start_keeps_another_announcements_protection(hass, home, freezer):
    entry = await _room(hass, home, freezer)
    announcer = entry.runtime_data.announce
    home.announce_hold = asyncio.Event()
    first = _speak_later(hass)
    await _settle(hass)

    async def _fails(*_args, **_kwargs):
        raise RuntimeError("devices")

    with patch.object(type(announcer), "_power_up", _fails), pytest.raises(RuntimeError):
        await announcer.async_announce("media-source://tts/y", "music")
    await _tick_held(hass, freezer, 4)
    assert announcer.protects(MUSIC), "the first one still plays"
    home.announce_hold.set()
    await first


async def test_turned_off_during_it_goes_off_after_it(hass, home, freezer):
    await _room(hass, home, freezer)
    home.music("paused")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 10)
    home.announce_hold = asyncio.Event()
    task = _speak_later(hass)
    await _settle(hass)
    off = asyncio.ensure_future(
        hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    )
    await _tick_held(hass, freezer, 3)
    home.announce_hold.set()
    await task
    await off
    await _tick(hass, freezer, 20)
    assert not home.on(SPEAKERS), "you said off"


async def test_a_room_that_is_off_and_announces_on_the_video_player_goes_off(hass, home, freezer):
    home.music("off")
    home.kodi_boots = True
    await setup_room(hass)
    hass.states.async_set(KODI, "off", {"supported_features": KODI_FEATURES | F.MEDIA_ANNOUNCE})
    await hass.async_block_till_done()
    await _speak(hass)
    await _tick(hass, freezer, 20)
    assert not home.on(TV)
    assert hass.states.get(KODI).state == "off"


async def test_a_room_that_is_off_with_music_assistant_power_goes_off(hass, home, freezer):
    home.ma_announce = True
    home.ma_power = True
    home.music("off")
    await setup_room(hass)
    await hass.async_block_till_done()
    await _speak(hass)
    await _tick(hass, freezer, 20)
    assert not home.on(SPEAKERS)
    assert hass.states.get(MUSIC).state == "off"


async def test_a_caller_that_gives_up_leaves_the_room_still_until_it_is_over(hass, home, freezer):
    entry = await _room(hass, home, freezer)
    home.announce_hold = asyncio.Event()
    task = _speak_later(hass)
    await _settle(hass)
    task.cancel()
    await _settle(hass)
    await _tick_held(hass, freezer, 5)
    assert entry.runtime_data.announce.protects(MUSIC), "the player still announces"
    home.announce_hold.set()
    await _settle(hass)
    await _tick(hass, freezer, 5)
    assert not entry.runtime_data.announce.active


async def test_the_room_players_announce_extra_is_checked(hass, home, freezer):
    await _room(hass, home, freezer)
    with pytest.raises(ServiceValidationError):
        await _speak(hass, extra={"announce_volume": "loud"})
    await hass.services.async_call("switch", "turn_on", {"entity_id": QUIET}, blocking=True)
    await _speak(hass, extra={"ignore_quiet": "false"})
    assert home.announcements == []
