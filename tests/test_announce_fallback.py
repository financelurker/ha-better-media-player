"""Announcements on a player that cannot announce: played as media, then back."""

from __future__ import annotations

from datetime import timedelta

from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.util import dt as dt_util

from custom_components.better_media_player.const import CONF_VOLUME_CONTROL, CONF_VOLUME_ENTITY, VOLUME_DEVICE

from .conftest import AVR, KODI, MUSIC, SPEAKERS, setup_room
from .test_announce import NIGHT, PRESET, _announce, _speak

TTS = "media-source://tts/x"


async def _tick(hass, freezer, seconds: float, step: float = 1) -> None:
    left = seconds
    while left > 0:
        freezer.tick(timedelta(seconds=min(step, left)))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        left -= step


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
    await _tick(hass, freezer, 3)
    home.played.clear()
    return entry


async def test_what_plays_is_paused_and_comes_back_where_it_was(hass, home, freezer):
    await _room(hass, home, freezer)
    await _speak(hass)
    calls = [(s, d.get("media_content_id") or d.get("seek_position")) for s, d in _played(home, MUSIC)
             if s in ("media_pause", "play_media", "media_seek")]
    assert calls[0] == ("media_pause", None)
    assert calls[1] == ("play_media", TTS)
    assert calls[2] == ("play_media", "track-7")
    assert calls[3][0] == "media_seek" and 42 <= calls[3][1] < 46
    assert hass.states.get(MUSIC).state == "playing"


async def test_at_the_room_volume_and_back(hass, home, freezer):
    await _room(hass, home, freezer, volume=0.4)
    await _speak(hass)
    levels = [d["volume_level"] for s, d in _played(home, MUSIC) if s == "volume_set"]
    assert levels == [0.3, 0.4], "the room volume for it, then the player's own again"


async def test_a_volume_as_asked_and_the_night_limit(hass, home, freezer):
    await _room(hass, home, freezer)
    await _announce(hass, volume="loud")
    await hass.services.async_call("switch", "turn_on", {"entity_id": NIGHT}, blocking=True)
    await hass.async_block_till_done()
    home.played.clear()
    await _announce(hass, volume=0.9)
    levels = [d["volume_level"] for s, d in _played(home, MUSIC) if s == "volume_set"]
    assert levels[0] == 0.3, "at most the night's limit"


async def test_paused_stays_paused(hass, home, freezer):
    await _room(hass, home, freezer, state="paused")
    await _speak(hass)
    calls = [(s, d.get("media_content_id")) for s, d in _played(home, MUSIC) if s in ("media_pause", "play_media")]
    assert calls == [("play_media", TTS), ("play_media", "track-7"), ("media_pause", None)], "not paused again first"
    assert hass.states.get(MUSIC).state == "paused"


async def test_nothing_played_before_it_stops(hass, home, freezer):
    entry = await _room(hass, home, freezer, state="idle")
    entry.runtime_data.data["last_source"] = "audio"
    await _speak(hass)
    assert ("play_media", TTS) in [(s, d.get("media_content_id")) for s, d in _played(home, MUSIC)]
    assert not [d for s, d in _played(home, MUSIC) if s == "play_media" and d.get("media_content_id") != TTS]


async def test_a_room_that_is_off_comes_up_and_goes_off(hass, home, freezer):
    home.plays_media = True
    home.music("off")
    await setup_room(hass)
    await hass.async_block_till_done()
    await _speak(hass)
    assert ("play_media", TTS) in [(s, d.get("media_content_id")) for s, d in _played(home, MUSIC)]
    await _tick(hass, freezer, 10)
    assert not home.on(SPEAKERS)


async def test_the_film_player_announces_when_it_plays(hass, home, freezer):
    entry = await _room(hass, home, freezer, state="idle")
    home.kodi("playing", media_content_id="movie-3", media_content_type="movie", media_position=600.0,
              media_position_updated_at=home.now(), media_duration=5400.0, volume_level=0.6)
    entry.runtime_data.data["last_source"] = "video"
    await hass.async_block_till_done()
    await _speak(hass)
    calls = [(s, d.get("media_content_id")) for s, d in _played(home, KODI) if s in ("media_pause", "play_media")]
    assert calls == [("media_pause", None), ("play_media", TTS), ("play_media", "movie-3")]
    assert not [p for p in _played(home, MUSIC) if p[0] == "play_media"]


async def test_the_room_volume_is_not_learned(hass, home, freezer):
    await _room(hass, home, freezer, volume=0.3)
    await hass.services.async_call("select", "select_option", {"entity_id": PRESET, "option": "normal"}, blocking=True)
    await _tick(hass, freezer, 5)
    await _announce(hass, volume=0.7)
    await _tick(hass, freezer, 5)
    assert hass.states.get(PRESET).state == "normal"


async def test_with_a_volume_device_the_player_keeps_its_volume(hass, home, freezer):
    home.avr("on", volume_level=0.3)
    await _room(hass, home, freezer, **{CONF_VOLUME_CONTROL: VOLUME_DEVICE, CONF_VOLUME_ENTITY: AVR})
    await _speak(hass)
    assert not [d for s, d in _played(home, MUSIC) if s == "volume_set"]
    await _announce(hass, volume=0.5)
    levels = [d["volume_level"] for s, d in _played(home, MUSIC) if s == "volume_set"]
    assert levels[0] == 0.5, "a number asked for is set on the player"
