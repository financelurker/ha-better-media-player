"""Announcements on a player that cannot announce: played as media, then back."""

from __future__ import annotations

import asyncio

import pytest

from homeassistant.exceptions import ServiceValidationError

from custom_components.better_media_player.const import CONF_VOLUME_CONTROL, CONF_VOLUME_ENTITY, VOLUME_DEVICE

from .conftest import AVR, KODI, MUSIC, SPEAKERS, setup_room
from .helpers import NIGHT, PRESET, ROOM, announce, settle_held, speak, speak_later, tick, tick_held

TTS = "media-source://tts/x"


def _played(home, entity_id):
    """(service, data) the player got, in order."""
    return [(p[0], p[2]) for p in home.played if p[1] == entity_id]


async def _room(hass, home, freezer, state="playing", volume=0.4, **overrides):
    home.plays_media = True
    home.music(state, volume_level=volume, media_content_id="track-7", media_content_type="music",
               media_position=42.0, media_position_updated_at=home.now(), media_duration=200.0)
    entry = await setup_room(hass, **overrides)
    entry.runtime_data.data["last_source"] = "audio"
    entry.runtime_data.data["room_volume"] = 0.3
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    home.played.clear()
    return entry


async def test_what_plays_is_paused_and_comes_back_where_it_was(hass, home, freezer):
    await _room(hass, home, freezer)
    await speak(hass)
    calls = [(s, d.get("media_content_id") or d.get("seek_position")) for s, d in _played(home, MUSIC)
             if s in ("media_pause", "play_media", "media_seek")]
    assert calls[0] == ("media_pause", None)
    assert calls[1] == ("play_media", TTS)
    assert calls[2] == ("play_media", "track-7")
    assert calls[3][0] == "media_seek" and 42 <= calls[3][1] < 46
    assert hass.states.get(MUSIC).state == "playing"


async def test_at_the_room_volume_and_back(hass, home, freezer):
    await _room(hass, home, freezer, volume=0.4)
    await speak(hass)
    levels = [d["volume_level"] for s, d in _played(home, MUSIC) if s == "volume_set"]
    assert levels == [0.3, 0.4], "the room volume for it, then the player's own again"


async def test_a_volume_as_asked_and_the_night_limit(hass, home, freezer):
    await _room(hass, home, freezer)
    await announce(hass, volume="loud")
    await hass.services.async_call("switch", "turn_on", {"entity_id": NIGHT}, blocking=True)
    await hass.async_block_till_done()
    home.played.clear()
    await announce(hass, volume=0.9)
    levels = [d["volume_level"] for s, d in _played(home, MUSIC) if s == "volume_set"]
    assert levels[0] == 0.3, "at most the night's limit"


async def test_paused_stays_paused(hass, home, freezer):
    await _room(hass, home, freezer, state="paused")
    await speak(hass)
    calls = [(s, d.get("media_content_id")) for s, d in _played(home, MUSIC) if s in ("media_pause", "play_media")]
    assert calls == [("play_media", TTS), ("play_media", "track-7"), ("media_pause", None)], "not paused again first"
    assert hass.states.get(MUSIC).state == "paused"


async def test_nothing_played_before_it_stops(hass, home, freezer):
    entry = await _room(hass, home, freezer, state="idle")
    entry.runtime_data.data["last_source"] = "audio"
    await speak(hass)
    assert ("play_media", TTS) in [(s, d.get("media_content_id")) for s, d in _played(home, MUSIC)]
    assert not [d for s, d in _played(home, MUSIC) if s == "play_media" and d.get("media_content_id") != TTS]


async def test_a_room_that_is_off_comes_up_and_goes_off(hass, home, freezer):
    home.plays_media = True
    home.music("off")
    await setup_room(hass)
    await hass.async_block_till_done()
    await speak(hass)
    assert ("play_media", TTS) in [(s, d.get("media_content_id")) for s, d in _played(home, MUSIC)]
    await tick(hass, freezer, 10)
    assert not home.on(SPEAKERS)


async def _film(hass, home, freezer, music="idle"):
    entry = await _room(hass, home, freezer, state=music)
    home.kodi("playing", media_content_id="movie-3", media_content_type="movie", media_position=600.0,
              media_position_updated_at=home.now(), media_duration=5400.0, volume_level=0.6)
    entry.runtime_data.data["last_source"] = "video"
    await hass.async_block_till_done()
    home.played.clear()
    return entry


async def test_a_film_while_the_music_player_is_free_is_handled_as_usual(hass, home, freezer):
    await _film(hass, home, freezer)
    await speak(hass)
    assert ("play_media", TTS) in [(s, d.get("media_content_id")) for s, d in _played(home, MUSIC)]
    kodi = [s for s, _d in _played(home, KODI) if s in ("media_pause", "media_play", "play_media")]
    assert kodi == ["media_pause", "media_play"], "paused and on again (the setting), not replaced"


async def test_the_film_player_announces_when_the_music_player_is_off(hass, home, freezer):
    await _film(hass, home, freezer, music="off")
    await speak(hass)
    calls = [(s, d.get("media_content_id")) for s, d in _played(home, KODI) if s in ("media_pause", "play_media")]
    assert calls == [("media_pause", None), ("play_media", TTS), ("play_media", "movie-3")]
    assert not [p for p in _played(home, MUSIC) if p[0] == "play_media"]


async def test_the_room_volume_is_not_learned(hass, home, freezer):
    await _room(hass, home, freezer, volume=0.3)
    await hass.services.async_call("select", "select_option", {"entity_id": PRESET, "option": "normal"}, blocking=True)
    await tick(hass, freezer, 5)
    await announce(hass, volume=0.7)
    await tick(hass, freezer, 5)
    assert hass.states.get(PRESET).state == "normal"


async def test_with_a_volume_device_the_player_keeps_its_volume(hass, home, freezer):
    home.avr("on", volume_level=0.3)
    await _room(hass, home, freezer, **{CONF_VOLUME_CONTROL: VOLUME_DEVICE, CONF_VOLUME_ENTITY: AVR})
    await speak(hass)
    assert not [d for s, d in _played(home, MUSIC) if s == "volume_set"]
    await announce(hass, volume=0.5)
    levels = [d["volume_level"] for s, d in _played(home, MUSIC) if s == "volume_set"]
    assert levels[0] == 0.5, "a number asked for is set on the player"


# ------------------------------------------------------------------ review


async def test_a_real_player_plays_the_whole_clip_first(hass, home, freezer):
    await _room(hass, home, freezer)
    home.clip_seconds, home.report_lag = 6.0, 1.0  # reports late, shows the resolved url
    task = speak_later(hass)
    await settle_held(hass)
    await tick_held(hass, freezer, 5)
    assert not [d for s, d in _played(home, MUSIC) if s == "play_media" and d["media_content_id"] == "track-7"]
    await tick_held(hass, freezer, 6)
    await task
    await tick(hass, freezer, 3)
    calls = [s for s, d in _played(home, MUSIC) if s in ("play_media", "media_seek")]
    assert calls == ["play_media", "play_media", "media_seek"], "back after the clip, the seek after it plays"


async def test_turned_off_meanwhile_nothing_comes_back(hass, home, freezer):
    await _room(hass, home, freezer)
    home.clip_seconds = 6.0
    task = speak_later(hass)
    await settle_held(hass)
    off = hass.async_create_task(
        hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    )
    await tick_held(hass, freezer, 10)
    await task
    await off
    await tick(hass, freezer, 20)
    assert not [d for s, d in _played(home, MUSIC) if s == "play_media" and d["media_content_id"] == "track-7"]
    assert not home.on(SPEAKERS)


async def test_two_at_once_on_one_player_one_after_the_other(hass, home, freezer):
    await _room(hass, home, freezer)
    await asyncio.gather(speak(hass), announce(hass, volume=0.5))
    await tick(hass, freezer, 5)
    assert hass.states.get(MUSIC).attributes["media_content_id"] == "track-7"
    assert hass.states.get(MUSIC).attributes["volume_level"] == 0.4


async def test_without_an_id_to_play_again_it_is_not_started(hass, home, freezer):
    await _room(hass, home, freezer)
    home.set(MUSIC, "playing", media_content_id=None)
    await hass.async_block_till_done()
    home.played.clear()
    await speak(hass)
    services = [s for s, _d in _played(home, MUSIC)]
    assert services.count("play_media") == 1 and "media_play" not in services, "media_play would play the clip"


async def test_kodis_ids_are_not_played_again(hass, home, freezer):
    entry = await _room(hass, home, freezer, state="off")
    home.kodi("playing", media_content_id={"imdb": "tt01"}, media_content_type="movie", media_duration=5400.0,
              media_position=600.0, media_position_updated_at=home.now())
    entry.runtime_data.data["last_source"] = "video"
    await hass.async_block_till_done()
    home.played.clear()
    await speak(hass)
    services = [s for s, _d in _played(home, KODI)]
    assert services.count("play_media") == 1, "only the announcement"
    assert "media_play" not in services


async def test_the_volume_comes_back_before_what_played(hass, home, freezer):
    await _room(hass, home, freezer, volume=0.4)
    await speak(hass)
    order = [(s, d.get("volume_level") or d.get("media_content_id")) for s, d in _played(home, MUSIC)
             if s in ("volume_set", "play_media")]
    assert order == [("volume_set", 0.3), ("play_media", TTS), ("volume_set", 0.4), ("play_media", "track-7")]


async def test_a_player_in_a_group_is_left_alone(hass, home, freezer):
    from .conftest import GROUP

    entry = await _room(hass, home, freezer, state="idle")
    home.group("playing", [MUSIC], GROUP)
    home.set(MUSIC, "playing")
    entry.runtime_data.data["last_source"] = "audio"
    await hass.async_block_till_done()
    home.played.clear()
    with pytest.raises(ServiceValidationError):
        await speak(hass)
    assert not [p for p in _played(home, MUSIC) if p[0] == "play_media"]


# ------------------------------------------------------------ second review


async def test_music_is_announced_on_the_music_player_also_with_kodi_idle(hass, home, freezer):
    await _room(hass, home, freezer)
    home.kodi("idle")
    await hass.async_block_till_done()
    home.played.clear()
    await speak(hass)
    assert not [p for p in _played(home, KODI) if p[0] == "play_media"]
    assert ("play_media", TTS) in [(s, d.get("media_content_id")) for s, d in _played(home, MUSIC)]


async def test_a_film_with_paused_music_keeps_both(hass, home, freezer):
    await _film(hass, home, freezer, music="paused")
    await speak(hass)
    music = [(s, d.get("media_content_id")) for s, d in _played(home, MUSIC) if s in ("play_media", "media_pause")]
    assert music == [("play_media", TTS), ("play_media", "track-7"), ("media_pause", None)]
    kodi = [s for s, _d in _played(home, KODI) if s in ("media_pause", "media_play", "play_media")]
    assert kodi == ["media_pause", "media_play"], "the film is paused and goes on, not replaced"


async def test_at_night_the_volume_comes_back_too(hass, home, freezer):
    await _room(hass, home, freezer, volume=0.25)
    await hass.services.async_call("switch", "turn_on", {"entity_id": NIGHT}, blocking=True)
    await hass.async_block_till_done()
    home.played.clear()
    await announce(hass, volume=0.9)
    levels = [d["volume_level"] for s, d in _played(home, MUSIC) if s == "volume_set"]
    assert levels == [0.3, 0.25]


async def test_something_started_meanwhile_is_not_replaced(hass, home, freezer):
    await _room(hass, home, freezer)
    home.clip_seconds = 6.0
    task = speak_later(hass)
    await settle_held(hass)
    await tick_held(hass, freezer, 2)
    home.set(MUSIC, "playing", media_content_id="playlist-9")  # picked in the app meanwhile
    await settle_held(hass)
    await task
    await tick(hass, freezer, 10)
    assert not [d for s, d in _played(home, MUSIC) if s == "play_media" and d["media_content_id"] == "track-7"]


async def test_two_over_a_film_resume_it_after_the_last(hass, home, freezer):
    await _film(hass, home, freezer)
    home.clip_seconds = 6.0
    first = speak_later(hass)
    await settle_held(hass)
    await tick_held(hass, freezer, 1)
    second = speak_later(hass)
    await settle_held(hass)
    await tick_held(hass, freezer, 8)  # the first is over, the second plays
    assert "media_play" not in [s for s, _d in _played(home, KODI)], "still paused"
    await tick_held(hass, freezer, 10)
    await first
    await second
    await tick(hass, freezer, 5)
    assert [s for s, _d in _played(home, KODI)].count("media_play") == 1


async def test_the_old_media_reported_playing_for_a_moment_is_not_the_clip(hass, home, freezer):
    await _room(hass, home, freezer, state="paused")
    home.clip_seconds, home.report_lag = 6.0, 1.0
    task = speak_later(hass)
    await settle_held(hass, 200)
    assert ("play_media", TTS) in [(s, d.get("media_content_id")) for s, d in _played(home, MUSIC)]
    home.set(MUSIC, "playing")  # still shows the paused track, as playing, for a moment
    await tick_held(hass, freezer, 5)
    assert not [d for s, d in _played(home, MUSIC) if s == "play_media" and d["media_content_id"] == "track-7"]
    await tick_held(hass, freezer, 6)
    await task
    await tick(hass, freezer, 3)
    assert [d for s, d in _played(home, MUSIC) if s == "play_media" and d["media_content_id"] == "track-7"], (
        "back after the clip"
    )


async def test_a_stream_starts_anew_without_a_seek(hass, home, freezer):
    await _room(hass, home, freezer)
    home.set(MUSIC, "playing", media_duration=None)  # a radio stream
    await hass.async_block_till_done()
    home.played.clear()
    await speak(hass)
    services = [s for s, _d in _played(home, MUSIC)]
    assert services.count("play_media") == 2 and "media_seek" not in services
