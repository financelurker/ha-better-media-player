"""Sleep timer: fade out, room off, volume restore, restart."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import async_capture_events, async_fire_time_changed

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from custom_components.better_media_player.const import DOMAIN, EVENT_SLEEP_TIMER_FINISHED

from .conftest import BASS, KODI, MUSIC, SPEAKERS, TV, setup_room

ROOM = "media_player.wohnzimmer"
NUMBER = "number.wohnzimmer_sleep_timer"
BUTTON = "button.wohnzimmer_sleep_timer_15_min"
END = "sensor.wohnzimmer_sleep_timer_end"


async def _tick(hass: HomeAssistant, freezer, seconds: float) -> None:
    step = 10.0
    left = seconds
    while left > 0:
        freezer.tick(timedelta(seconds=min(step, left)))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        left -= step


def _volume(hass, entity_id=MUSIC) -> float:
    return hass.states.get(entity_id).attributes["volume_level"]


def _count(home, entity_id: str, service: str) -> int:
    return sum(1 for c in home.calls if c[2] == entity_id and c[1] == service)


async def _playing_music(hass, home, freezer, **overrides):
    entry = await setup_room(hass, **overrides)
    home.music("playing", volume_level=0.5)
    await hass.async_block_till_done()
    assert home.on(SPEAKERS)
    return entry


async def _set(hass, minutes, fade=None):
    data = {"entity_id": ROOM, "minutes": minutes}
    if fade is not None:
        data["fade_minutes"] = fade
    await hass.services.async_call(DOMAIN, "set_sleep_timer", data, blocking=True)
    await hass.async_block_till_done()


async def test_fade_then_room_off_and_restore(hass, home, freezer):
    events = async_capture_events(hass, EVENT_SLEEP_TIMER_FINISHED)
    await _playing_music(hass, home, freezer)
    await _set(hass, 10, fade=5)
    assert hass.states.get(NUMBER).state == "10.0"
    assert hass.states.get(END).state != "unknown"
    assert hass.states.get(ROOM).attributes["sleep_timer_ends_at"] is not None

    await _tick(hass, freezer, 5 * 60)
    assert _volume(hass) == 0.5  # fade not started yet
    await _tick(hass, freezer, 150)
    assert 0.2 <= _volume(hass) <= 0.3
    assert hass.states.get(ROOM).attributes["sleep_timer_fading"] is True
    assert hass.states.get(NUMBER).state == "3.0"

    await _tick(hass, freezer, 160)
    # room off exactly once, music stopped, volume back for next time
    assert hass.states.get(MUSIC).state == "idle"
    await _tick(hass, freezer, 5 * 60)  # speaker run-down is skipped (forced off)
    assert not home.on(SPEAKERS) and not home.on(BASS)
    assert _count(home, SPEAKERS, "turn_off") == 1
    assert _count(home, MUSIC, "media_stop") == 1
    assert _volume(hass) == 0.5
    assert hass.states.get(NUMBER).state == "0.0"
    assert hass.states.get(END).state == "unknown"
    assert [e.data["reason"] for e in events] == ["expired"]
    assert events[0].data["entity_id"] == ROOM


async def test_manual_volume_change_stops_fade_only(hass, home, freezer):
    await _playing_music(hass, home, freezer)
    await _set(hass, 4, fade=4)
    await _tick(hass, freezer, 60)
    assert _volume(hass) < 0.5
    home.music("playing", volume_level=0.6)  # someone turns it up again
    await _tick(hass, freezer, 30)
    assert _volume(hass) == 0.6
    assert hass.states.get(ROOM).attributes["sleep_timer_fading"] is False
    await _tick(hass, freezer, 160)
    assert hass.states.get(MUSIC).state == "idle"  # the timer still ended the room
    assert _volume(hass) == 0.6  # the user's volume, nothing restored


async def test_plus_15_during_fade_restores_volume(hass, home, freezer):
    await _playing_music(hass, home, freezer)
    await _set(hass, 2, fade=2)
    await _tick(hass, freezer, 60)
    assert _volume(hass) < 0.5
    await hass.services.async_call("button", "press", {"entity_id": BUTTON}, blocking=True)
    await hass.async_block_till_done()
    assert _volume(hass) == 0.5
    assert hass.states.get(NUMBER).state == "16.0"
    await _tick(hass, freezer, 10 * 60)
    assert _volume(hass) == 0.5  # fade starts again only in the last 2 minutes
    assert hass.states.get(MUSIC).state == "playing"


async def test_plus_15_starts_timer(hass, home, freezer):
    await _playing_music(hass, home, freezer)
    await hass.services.async_call("button", "press", {"entity_id": BUTTON}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(NUMBER).state == "15.0"


async def test_cancel_restores_volume(hass, home, freezer):
    events = async_capture_events(hass, EVENT_SLEEP_TIMER_FINISHED)
    await _playing_music(hass, home, freezer)
    await _set(hass, 3, fade=3)
    await _tick(hass, freezer, 90)
    assert _volume(hass) < 0.5
    await hass.services.async_call(DOMAIN, "cancel_sleep_timer", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert _volume(hass) == 0.5
    await _tick(hass, freezer, 5 * 60)
    assert hass.states.get(MUSIC).state == "playing"
    assert [e.data["reason"] for e in events] == ["cancelled"]


async def test_number_zero_cancels(hass, home, freezer):
    await _playing_music(hass, home, freezer)
    await hass.services.async_call("number", "set_value", {"entity_id": NUMBER, "value": 20}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(NUMBER).state == "20.0"
    await hass.services.async_call("number", "set_value", {"entity_id": NUMBER, "value": 0}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(NUMBER).state == "0.0"
    await _tick(hass, freezer, 25 * 60)
    assert hass.states.get(MUSIC).state == "playing"


async def test_room_off_cancels_and_restores_after_stop(hass, home, freezer):
    events = async_capture_events(hass, EVENT_SLEEP_TIMER_FINISHED)
    await _playing_music(hass, home, freezer)
    await _set(hass, 2, fade=2)
    await _tick(hass, freezer, 60)
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert [e.data["reason"] for e in events] == ["room_off"]
    assert _volume(hass) == 0.5
    await _tick(hass, freezer, 5 * 60)
    assert len(events) == 1
    assert _count(home, MUSIC, "media_stop") == 1


async def test_room_off_restores_only_once_stopped(hass, home, freezer):
    """A player still playing after 'off' gets its volume back only when quiet."""
    entry = await _playing_music(hass, home, freezer)
    ctrl = entry.runtime_data
    await _set(hass, 2, fade=2)
    await _tick(hass, freezer, 60)
    low = _volume(hass)
    ctrl.data["sleep"]["ends_at"] = dt_util.utcnow().timestamp()  # due now
    original = home.handle

    def no_stop(domain, service, entity_id, data, context):
        if service == "media_stop":
            home.calls.append((domain, service, entity_id))
            return True  # the player keeps playing for a moment
        return original(domain, service, entity_id, data, context)

    home.handle = no_stop
    await _tick(hass, freezer, 10)
    assert hass.states.get(MUSIC).state == "playing"
    assert _volume(hass) == low  # not loud while still playing
    home.handle = original
    home.music("idle")
    await hass.async_block_till_done()
    assert _volume(hass) == 0.5


async def test_set_when_room_off_is_rejected(hass, home):
    await setup_room(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "set_sleep_timer", {"entity_id": ROOM, "minutes": 10}, blocking=True)


async def test_room_going_off_by_itself_ends_timer(hass, home, freezer):
    events = async_capture_events(hass, EVENT_SLEEP_TIMER_FINISHED)
    await _playing_music(hass, home, freezer)
    await _set(hass, 60)
    home.music("idle")
    await _tick(hass, freezer, 3 * 60)  # speaker run-down 2 min
    assert not home.on(SPEAKERS)
    assert hass.states.get(NUMBER).state == "0.0"
    assert [e.data["reason"] for e in events] == ["room_off"]


async def test_kodi_volume_restored_when_woken(hass, home, freezer):
    """Kodi is suspended at the end; it gets its volume back at the next start."""
    entry = await setup_room(hass)
    home.kodi_boots = True
    home.kodi("playing", volume_level=0.8)
    await hass.async_block_till_done()
    assert home.on(TV)
    await _set(hass, 1, fade=1)
    await _tick(hass, freezer, 70)
    assert hass.states.get(KODI).state == "off"
    assert entry.runtime_data.data["volume_restore"][KODI]["volume"] == 0.8
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert _volume(hass, KODI) == 0.8
    assert entry.runtime_data.data["volume_restore"] == {}


async def test_survives_restart_mid_fade(hass, home, freezer):
    entry = await _playing_music(hass, home, freezer)
    await _set(hass, 4, fade=4)
    await _tick(hass, freezer, 60)
    low = _volume(hass)
    assert low < 0.5
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(NUMBER).state == "3.0"
    await _tick(hass, freezer, 60)
    assert _volume(hass) < low  # fade goes on
    await _tick(hass, freezer, 130)
    assert hass.states.get(MUSIC).state == "idle"
    assert _volume(hass) == 0.5
    assert _count(home, MUSIC, "media_stop") == 1


@pytest.mark.parametrize(("playing", "turned_off"), [(True, True), (False, False)])
async def test_expired_while_down(hass, home, freezer, playing, turned_off):
    events = async_capture_events(hass, EVENT_SLEEP_TIMER_FINISHED)
    entry = await _playing_music(hass, home, freezer)
    await _set(hass, 5, fade=0)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=10))
    if not playing:
        home.music("paused")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 10)
    assert hass.states.get(NUMBER).state == "0.0"
    assert (_count(home, MUSIC, "media_stop") == 1) is turned_off
    assert [e.data["reason"] for e in events] == ["expired"]


async def test_replace_timer(hass, home, freezer):
    await _playing_music(hass, home, freezer)
    await _set(hass, 10)
    await _set(hass, 30)
    assert hass.states.get(NUMBER).state == "30.0"
    await _tick(hass, freezer, 11 * 60)
    assert hass.states.get(MUSIC).state == "playing"


async def test_default_fade_from_options(hass, home, freezer):
    await _playing_music(hass, home, freezer, sleep_fade=0)
    await _set(hass, 2)
    await _tick(hass, freezer, 110)
    assert _volume(hass) == 0.5  # no fade configured
    await _tick(hass, freezer, 20)
    assert hass.states.get(MUSIC).state == "idle"


async def test_expired_during_downtime_waits_for_ha_start(hass, home, freezer):
    """At HA start the players are not known yet: decide only once HA has started."""
    from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
    from homeassistant.core import CoreState

    entry = await _playing_music(hass, home, freezer)
    await _set(hass, 5, fade=0)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=10))
    hass.set_state(CoreState.starting)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 30)
    assert _count(home, MUSIC, "media_stop") == 0  # not yet
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 10)
    assert _count(home, MUSIC, "media_stop") == 1
    assert hass.states.get(NUMBER).state == "0.0"
