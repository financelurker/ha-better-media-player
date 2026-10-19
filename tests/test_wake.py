"""Wake up: start quietly, fade in, snooze, end."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import async_capture_events, async_fire_time_changed

from homeassistant.core import CoreState
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.better_media_player.browser import KIND_ITEM, make_id
from custom_components.better_media_player.const import (
    DOMAIN,
    EVENT_WAKE_FAILED,
    EVENT_WAKE_FINISHED,
    EVENT_WAKE_STARTED,
)

from .conftest import KODI, MUSIC, SPEAKERS, TV, setup_room, start_follow

ROOM = "media_player.wohnzimmer"
WAKE = "sensor.wohnzimmer_wake"
SNOOZE = "button.wohnzimmer_snooze"
STOP = "button.wohnzimmer_stop_wake"


async def _tick(hass, freezer, seconds: float, step: float = 5) -> None:
    left = seconds
    while left > 0:
        freezer.tick(timedelta(seconds=min(step, left)))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        left -= step


def _vol(hass, entity_id=MUSIC):
    return hass.states.get(entity_id).attributes.get("volume_level")


def _sent(home, entity_id):
    return [d["volume_level"] for s, e, d in home.played if s == "volume_set" and e == entity_id]


def _count(home, entity_id, service):
    return sum(1 for c in home.calls if c[2] == entity_id and c[1] == service)


async def _wake(hass, **data):
    payload = {"entity_id": ROOM, "media_content_id": "library://radio/1", "media_content_type": "music", **data}
    await hass.services.async_call(DOMAIN, "wake", payload, blocking=True)
    await _settle(hass)


async def _settle(hass):
    """Let the wake's background start run (it is not awaited by block_till_done)."""
    for _ in range(20):
        await asyncio.sleep(0)
        await hass.async_block_till_done()


async def _wake_playing(hass, home, freezer, **data):
    await _wake(hass, start_volume=0.05, target_volume=0.4, fade_minutes=2, **data)
    assert hass.states.get(WAKE).state == "starting"
    home.music("playing")  # the radio starts
    await hass.async_block_till_done()


async def _room(hass, home, volume=0.5):
    entry = await setup_room(hass)
    home.music("idle", volume_level=volume)
    await hass.async_block_till_done()
    entry.runtime_data.data["room_volume"] = volume
    return entry


# ----------------------------------------------------------------- the wake


async def test_wake_starts_quietly_and_fades_in(hass, home, freezer):
    started = async_capture_events(hass, EVENT_WAKE_STARTED)
    await _room(hass, home)
    await _wake(hass, start_volume=0.05, target_volume=0.4, fade_minutes=2)
    assert home.on(SPEAKERS)
    order = [(s, d.get("volume_level")) for s, e, d in home.played if e == MUSIC and s in ("volume_set", "play_media")]
    assert order[-2:] == [("volume_set", 0.05), ("play_media", None)]  # quiet from the first second
    home.music("playing")
    await hass.async_block_till_done()
    assert hass.states.get(WAKE).state == "fading"
    assert len(started) == 1
    await _tick(hass, freezer, 60)
    assert 0.18 <= _vol(hass) <= 0.27
    await _tick(hass, freezer, 70)
    assert _vol(hass) == 0.4
    assert hass.states.get(WAKE).state == "playing"
    assert hass.states.get(ROOM).attributes["room_volume"] == 0.5  # not a wish


async def test_nothing_plays_fails_and_turns_room_off(hass, home, freezer):
    failed = async_capture_events(hass, EVENT_WAKE_FAILED)
    await _room(hass, home)
    await _wake(hass)
    assert home.on(SPEAKERS)
    await _tick(hass, freezer, 65)
    assert [e.data["reason"] for e in failed] == ["not_playing"]
    assert hass.states.get(WAKE).state == "idle"
    await _tick(hass, freezer, 10)
    assert not home.on(SPEAKERS)


async def test_failure_keeps_a_room_that_was_on(hass, home, freezer):
    await _room(hass, home)
    home.music("playing")
    await hass.async_block_till_done()
    home.music("idle")
    await hass.async_block_till_done()
    assert home.on(SPEAKERS)  # run-down
    failed = async_capture_events(hass, EVENT_WAKE_FAILED)
    await _wake(hass)
    await _tick(hass, freezer, 65)
    assert [e.data["reason"] for e in failed] == ["not_playing"]
    assert home.on(SPEAKERS)  # not turned off: the normal run-down decides


async def test_start_failure(hass, home, freezer):
    failed = async_capture_events(hass, EVENT_WAKE_FAILED)
    await _room(hass, home)
    await _wake(hass, source="video")  # Kodi never wakes up
    await _tick(hass, freezer, 100)
    assert [e.data["reason"] for e in failed] == ["start_failed"]
    assert not home.on(TV)


async def test_media_from_room_browser_chooses_the_player(hass, home, freezer):
    await _room(hass, home)
    home.kodi_boots = True
    media = make_id(KIND_ITEM, "video", "movie", "123")
    await _wake(hass, media_content_id=media, media_content_type="movie", start_volume=0.1)
    assert home.on(TV)
    sent = [d for s, e, d in home.played if s == "play_media" and e == KODI]
    assert sent and sent[-1]["media_content_id"] == "123"
    assert _vol(hass, KODI) == 0.1


async def test_manual_volume_ends_only_the_fade(hass, home, freezer):
    await _room(hass, home)
    await _wake_playing(hass, home, freezer)
    await _tick(hass, freezer, 30)
    home.music("playing", volume_level=0.3)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 120)
    assert _vol(hass) == 0.3
    assert hass.states.get(WAKE).state == "playing"


# ------------------------------------------------------------------ snooze


async def test_snooze_pauses_keeps_power_and_fades_in_again(hass, home, freezer):
    await _room(hass, home)
    await _wake_playing(hass, home, freezer)
    await _tick(hass, freezer, 130)
    assert _vol(hass) == 0.4
    assert hass.states.get(SNOOZE).state != "unavailable"
    await hass.services.async_call("button", "press", {"entity_id": SNOOZE}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(MUSIC).state == "paused"
    assert hass.states.get(WAKE).state == "snoozed"
    assert hass.states.get(WAKE).attributes["snooze_until"] is not None
    await _tick(hass, freezer, 8 * 60, step=10)
    assert home.on(SPEAKERS)  # no run-down while snoozed
    await _tick(hass, freezer, 70, step=10)
    assert _count(home, MUSIC, "media_play") == 1  # resumed where it was
    assert _vol(hass) == 0.05
    assert hass.states.get(WAKE).state == "starting"
    home.music("playing")
    await hass.async_block_till_done()
    assert hass.states.get(WAKE).state == "fading"
    await _tick(hass, freezer, 130)
    assert _vol(hass) == 0.4


async def test_pause_by_hand_is_snooze(hass, home, freezer):
    await _room(hass, home)
    await _wake_playing(hass, home, freezer)
    home.music("paused")  # pause on the remote
    await hass.async_block_till_done()
    assert hass.states.get(WAKE).state == "snoozed"


async def test_snooze_action_with_minutes(hass, home, freezer):
    await _room(hass, home)
    await _wake_playing(hass, home, freezer)
    await hass.services.async_call(DOMAIN, "snooze", {"entity_id": ROOM, "minutes": 2}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 125, step=5)
    assert _count(home, MUSIC, "media_play") == 1


async def test_snooze_and_stop_without_wake_do_nothing(hass, home, freezer):
    await _room(hass, home)
    await hass.services.async_call(DOMAIN, "snooze", {"entity_id": ROOM}, blocking=True)
    await hass.services.async_call(DOMAIN, "stop_wake", {"entity_id": ROOM}, blocking=True)
    assert hass.states.get(WAKE).state == "idle"


# --------------------------------------------------------------------- end


async def test_stop_wake_keeps_playing(hass, home, freezer):
    finished = async_capture_events(hass, EVENT_WAKE_FINISHED)
    await _room(hass, home)
    await _wake_playing(hass, home, freezer)
    await hass.services.async_call("button", "press", {"entity_id": STOP}, blocking=True)
    await hass.async_block_till_done()
    assert [e.data["reason"] for e in finished] == ["stopped"]
    v = _vol(hass)
    await _tick(hass, freezer, 130)
    assert _vol(hass) == v  # the fade ended too
    assert hass.states.get(MUSIC).state == "playing"


async def test_stop_by_hand_ends_wake(hass, home, freezer):
    finished = async_capture_events(hass, EVENT_WAKE_FINISHED)
    await _room(hass, home)
    await _wake_playing(hass, home, freezer)
    home.music("idle")
    await hass.async_block_till_done()
    assert finished == []  # maybe just between two items
    await _tick(hass, freezer, 12)
    assert [e.data["reason"] for e in finished] == ["stopped"]


async def test_max_duration_hands_over_to_sleep_timer(hass, home, freezer):
    finished = async_capture_events(hass, EVENT_WAKE_FINISHED)
    await _room(hass, home)
    await _wake_playing(hass, home, freezer, max_duration_minutes=5)
    await _tick(hass, freezer, 5 * 60 + 5)
    assert [e.data["reason"] for e in finished] == ["max_duration"]
    assert hass.states.get("number.wohnzimmer_sleep_timer").state == "5.0"  # fade-out 5 min
    await _tick(hass, freezer, 5 * 60 + 20, step=10)
    assert hass.states.get(MUSIC).state == "idle"


async def test_sleep_timer_during_wake_ends_wake(hass, home, freezer):
    finished = async_capture_events(hass, EVENT_WAKE_FINISHED)
    await _room(hass, home)
    await _wake_playing(hass, home, freezer)
    await hass.services.async_call(DOMAIN, "set_sleep_timer", {"entity_id": ROOM, "minutes": 20}, blocking=True)
    await hass.async_block_till_done()
    assert [e.data["reason"] for e in finished] == ["sleep_timer"]
    assert hass.states.get(MUSIC).state == "playing"


async def test_wake_cancels_sleep_timer(hass, home, freezer):
    await _room(hass, home)
    home.music("playing")
    await hass.async_block_till_done()
    await hass.services.async_call(DOMAIN, "set_sleep_timer", {"entity_id": ROOM, "minutes": 20}, blocking=True)
    await hass.async_block_till_done()
    await _wake(hass)
    assert hass.states.get("number.wohnzimmer_sleep_timer").state == "0.0"
    assert hass.states.get(WAKE).state != "idle"


async def test_room_off_ends_wake(hass, home, freezer):
    finished = async_capture_events(hass, EVENT_WAKE_FINISHED)
    await _room(hass, home)
    await _wake_playing(hass, home, freezer)
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert [e.data["reason"] for e in finished] == ["room_off"]


async def test_new_wake_replaces(hass, home, freezer):
    finished = async_capture_events(hass, EVENT_WAKE_FINISHED)
    await _room(hass, home)
    await _wake_playing(hass, home, freezer)
    await _wake(hass, start_volume=0.1)
    assert [e.data["reason"] for e in finished] == ["replaced"]
    assert _vol(hass) == 0.1
    # it played before: only a new playback (or the play check) counts as started
    assert hass.states.get(WAKE).state == "starting"
    home.music("playing", media_title="Morning show")
    await hass.async_block_till_done()
    assert hass.states.get(WAKE).state == "fading"


# -------------------------------------------------------------- night mode


async def test_night_mode_on_limits_wake(hass, home, freezer):
    await _room(hass, home)
    await _wake_playing(hass, home, freezer, night_mode=True)
    assert hass.states.get("switch.wohnzimmer_night_mode").state == "on"
    await _tick(hass, freezer, 130)
    assert _vol(hass) == 0.3  # target 0.4, limited
    assert max(_sent(home, MUSIC)) <= 0.3


async def test_night_mode_off_does_not_fade_back(hass, home, freezer):
    await _room(hass, home, volume=0.2)
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.wohnzimmer_night_mode"}, blocking=True)
    await hass.async_block_till_done()
    await _wake_playing(hass, home, freezer, night_mode=False)
    await _tick(hass, freezer, 130)
    assert _vol(hass) == 0.4
    steps = [v for v in _sent(home, MUSIC)]
    assert steps == sorted(steps)  # one fade only (the wake's)


# ------------------------------------------------------------ hidden controls


async def test_controls_hidden_while_no_wake(hass, home, freezer):
    await _room(hass, home)
    reg = er.async_get(hass)
    assert reg.async_get(SNOOZE).hidden_by == er.RegistryEntryHider.INTEGRATION
    await _wake_playing(hass, home, freezer)
    assert reg.async_get(SNOOZE).hidden_by is None
    assert reg.async_get(STOP).hidden_by is None
    await hass.services.async_call(DOMAIN, "stop_wake", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert reg.async_get(SNOOZE).hidden_by == er.RegistryEntryHider.INTEGRATION


async def test_user_hidden_controls_stay_hidden(hass, home, freezer):
    await _room(hass, home)
    reg = er.async_get(hass)
    reg.async_update_entity(SNOOZE, hidden_by=er.RegistryEntryHider.USER)
    await _wake_playing(hass, home, freezer)
    assert reg.async_get(SNOOZE).hidden_by == er.RegistryEntryHider.USER


# ------------------------------------------------------------------ restart


async def test_restart_mid_fade_goes_on(hass, home, freezer):
    entry = await _room(hass, home)
    await _wake_playing(hass, home, freezer)
    await _tick(hass, freezer, 60)
    mid = _vol(hass)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(WAKE).state == "fading"
    await _tick(hass, freezer, 70)
    assert _vol(hass) == 0.4
    assert mid < 0.4


async def test_restart_while_snoozed(hass, home, freezer):
    entry = await _room(hass, home)
    await _wake_playing(hass, home, freezer)
    await hass.services.async_call(DOMAIN, "snooze", {"entity_id": ROOM, "minutes": 2}, blocking=True)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(WAKE).state == "snoozed"
    await _tick(hass, freezer, 125)
    assert _count(home, MUSIC, "media_play") == 1


async def test_restart_during_start_tries_again(hass, home, freezer):
    entry = await _room(hass, home)
    await _wake(hass)
    n = _count(home, MUSIC, "play_media")
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    hass.set_state(CoreState.starting)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await _settle(hass)
    assert _count(home, MUSIC, "play_media") == n + 1


async def test_follow_room_leaves_group_for_wake(hass, house, freezer):
    from .conftest import FOLLOW, LIVING_OCC

    hass.states.async_set(LIVING_OCC, "on")
    await hass.async_block_till_done()
    await start_follow(hass, house)
    await hass.async_block_till_done()
    assert MUSIC in house.members(FOLLOW)
    await _wake(hass, start_volume=0.05)
    assert MUSIC not in house.members(FOLLOW)
    assert _vol(hass) == 0.05


@pytest.mark.parametrize("bad", [{"source": "tv"}])
async def test_bad_source(hass, home, bad):
    await _room(hass, home)
    with pytest.raises(Exception):
        await _wake(hass, **bad)
