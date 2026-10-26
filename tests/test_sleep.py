"""Sleep timer: fade out, room off, volume restore, restart."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import async_capture_events

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from custom_components.better_media_player.const import DOMAIN, EVENT_SLEEP_TIMER_FINISHED
from custom_components.better_media_player.hub import get_hub

from .conftest import BASS, FOLLOW, KODI, LIVING_OCC, MUSIC, SPEAKERS, TV, setup_room, start_follow
from .helpers import ROOM, SLEEP, SLEEP_END, SLEEP_PLUS, count_calls, tick, vol

async def _playing_music(hass, home, **overrides):
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
    await _playing_music(hass, home)
    await _set(hass, 10, fade=5)
    assert hass.states.get(SLEEP).state == "10.0"
    assert hass.states.get(SLEEP_END).state != "unknown"
    assert hass.states.get(ROOM).attributes["sleep_timer_ends_at"] is not None

    await tick(hass, freezer, 5 * 60)
    assert vol(hass) == 0.5  # fade not started yet
    await tick(hass, freezer, 150)
    assert 0.2 <= vol(hass) <= 0.3
    assert hass.states.get(ROOM).attributes["sleep_timer_fading"] is True
    assert hass.states.get(SLEEP).state == "3.0"

    await tick(hass, freezer, 160)
    # room off exactly once, music stopped, volume back for next time
    assert hass.states.get(MUSIC).state == "idle"
    await tick(hass, freezer, 5 * 60)  # speaker run-down is skipped (forced off)
    assert not home.on(SPEAKERS) and not home.on(BASS)
    assert count_calls(home, SPEAKERS, "turn_off") == 1
    assert count_calls(home, MUSIC, "media_stop") == 1
    assert vol(hass) == 0.5
    assert hass.states.get(SLEEP).state == "0.0"
    assert hass.states.get(SLEEP_END).state == "unknown"
    assert [e.data["reason"] for e in events] == ["expired"]
    assert events[0].data["entity_id"] == ROOM


async def test_manual_volume_change_stops_fade_only(hass, home, freezer):
    await _playing_music(hass, home)
    await _set(hass, 4, fade=4)
    await tick(hass, freezer, 60)
    assert vol(hass) < 0.5
    home.music("playing", volume_level=0.6)  # someone turns it up again
    await tick(hass, freezer, 30)
    assert vol(hass) == 0.6
    assert hass.states.get(ROOM).attributes["sleep_timer_fading"] is False
    await tick(hass, freezer, 160)
    assert hass.states.get(MUSIC).state == "idle"  # the timer still ended the room
    assert vol(hass) == 0.6  # the user's volume, nothing restored


async def test_plus_15_during_fade_restores_volume(hass, home, freezer):
    await _playing_music(hass, home)
    await _set(hass, 2, fade=2)
    await tick(hass, freezer, 60)
    assert vol(hass) < 0.5
    await hass.services.async_call("button", "press", {"entity_id": SLEEP_PLUS}, blocking=True)
    await hass.async_block_till_done()
    assert vol(hass) == 0.5
    assert hass.states.get(SLEEP).state == "16.0"
    await tick(hass, freezer, 10 * 60)
    assert vol(hass) == 0.5  # fade starts again only in the last 2 minutes
    assert hass.states.get(MUSIC).state == "playing"


async def test_plus_15_starts_timer(hass, home, freezer):
    await _playing_music(hass, home)
    await hass.services.async_call("button", "press", {"entity_id": SLEEP_PLUS}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(SLEEP).state == "15.0"


async def test_cancel_restores_volume(hass, home, freezer):
    events = async_capture_events(hass, EVENT_SLEEP_TIMER_FINISHED)
    await _playing_music(hass, home)
    await _set(hass, 3, fade=3)
    await tick(hass, freezer, 90)
    assert vol(hass) < 0.5
    await hass.services.async_call(DOMAIN, "cancel_sleep_timer", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert vol(hass) == 0.5
    await tick(hass, freezer, 5 * 60)
    assert hass.states.get(MUSIC).state == "playing"
    assert [e.data["reason"] for e in events] == ["cancelled"]


async def test_number_zero_cancels(hass, home, freezer):
    await _playing_music(hass, home)
    await hass.services.async_call("number", "set_value", {"entity_id": SLEEP, "value": 20}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(SLEEP).state == "20.0"
    await hass.services.async_call("number", "set_value", {"entity_id": SLEEP, "value": 0}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(SLEEP).state == "0.0"
    await tick(hass, freezer, 25 * 60)
    assert hass.states.get(MUSIC).state == "playing"


async def test_room_off_cancels_and_restores_after_stop(hass, home, freezer):
    events = async_capture_events(hass, EVENT_SLEEP_TIMER_FINISHED)
    await _playing_music(hass, home)
    await _set(hass, 2, fade=2)
    await tick(hass, freezer, 60)
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert [e.data["reason"] for e in events] == ["room_off"]
    assert vol(hass) == 0.5
    await tick(hass, freezer, 5 * 60)
    assert len(events) == 1
    assert count_calls(home, MUSIC, "media_stop") == 1


async def test_room_off_restores_only_once_stopped(hass, home, freezer):
    """A player still playing after 'off' gets its volume back only when quiet."""
    entry = await _playing_music(hass, home)
    ctrl = entry.runtime_data
    await _set(hass, 2, fade=2)
    await tick(hass, freezer, 60)
    low = vol(hass)
    ctrl.data["sleep"]["ends_at"] = dt_util.utcnow().timestamp()  # due now
    original = home.handle

    def no_stop(domain, service, entity_id, data, context):
        if service == "media_stop":
            home.calls.append((domain, service, entity_id))
            return True  # the player keeps playing for a moment
        return original(domain, service, entity_id, data, context)

    home.handle = no_stop
    await tick(hass, freezer, 10)
    assert hass.states.get(MUSIC).state == "playing"
    assert vol(hass) == low  # not loud while still playing
    home.handle = original
    home.music("idle")
    await hass.async_block_till_done()
    assert vol(hass) == 0.5


async def test_set_when_room_off_is_rejected(hass, home):
    await setup_room(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "set_sleep_timer", {"entity_id": ROOM, "minutes": 10}, blocking=True)


async def test_room_going_off_by_itself_ends_timer(hass, home, freezer):
    events = async_capture_events(hass, EVENT_SLEEP_TIMER_FINISHED)
    await _playing_music(hass, home)
    await _set(hass, 60)
    home.music("idle")
    await tick(hass, freezer, 3 * 60)  # speaker run-down 2 min
    assert not home.on(SPEAKERS)
    assert hass.states.get(SLEEP).state == "0.0"
    assert [e.data["reason"] for e in events] == ["room_off"]


async def test_kodi_volume_restored_when_woken(hass, home, freezer):
    """Kodi is suspended at the end; it gets its volume back at the next start."""
    entry = await setup_room(hass)
    home.kodi_boots = True
    home.kodi("playing", volume_level=0.8)
    await hass.async_block_till_done()
    assert home.on(TV)
    await _set(hass, 1, fade=1)
    await tick(hass, freezer, 70)
    assert hass.states.get(KODI).state == "off"
    assert entry.runtime_data.data["volume_restore"][KODI]["volume"] == 0.8
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert vol(hass, KODI) == 0.8
    assert entry.runtime_data.data["volume_restore"] == {}


async def test_survives_restart_mid_fade(hass, home, freezer):
    entry = await _playing_music(hass, home)
    await _set(hass, 4, fade=4)
    await tick(hass, freezer, 60)
    low = vol(hass)
    assert low < 0.5
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(SLEEP).state == "3.0"
    await tick(hass, freezer, 60)
    assert vol(hass) < low  # fade goes on
    await tick(hass, freezer, 130)
    assert hass.states.get(MUSIC).state == "idle"
    assert vol(hass) == 0.5
    assert count_calls(home, MUSIC, "media_stop") == 1


@pytest.mark.parametrize(("playing", "turned_off"), [(True, True), (False, False)])
async def test_expired_while_down(hass, home, freezer, playing, turned_off):
    events = async_capture_events(hass, EVENT_SLEEP_TIMER_FINISHED)
    entry = await _playing_music(hass, home)
    await _set(hass, 5, fade=0)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=10))
    if not playing:
        home.music("paused")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    assert hass.states.get(SLEEP).state == "0.0"
    assert (count_calls(home, MUSIC, "media_stop") == 1) is turned_off
    assert [e.data["reason"] for e in events] == ["expired"]


async def test_replace_timer(hass, home, freezer):
    await _playing_music(hass, home)
    await _set(hass, 10)
    await _set(hass, 30)
    assert hass.states.get(SLEEP).state == "30.0"
    await tick(hass, freezer, 11 * 60)
    assert hass.states.get(MUSIC).state == "playing"


async def test_default_fade_from_options(hass, home, freezer):
    await _playing_music(hass, home, sleep_fade=0)
    await _set(hass, 2)
    await tick(hass, freezer, 110)
    assert vol(hass) == 0.5  # no fade configured
    await tick(hass, freezer, 20)
    assert hass.states.get(MUSIC).state == "idle"


async def test_expired_during_downtime_waits_for_ha_start(hass, home, freezer):
    """At HA start the players are not known yet: decide only once HA has started."""
    from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
    from homeassistant.core import CoreState

    entry = await _playing_music(hass, home)
    await _set(hass, 5, fade=0)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=10))
    hass.set_state(CoreState.starting)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await tick(hass, freezer, 30)
    assert count_calls(home, MUSIC, "media_stop") == 0  # not yet
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    assert count_calls(home, MUSIC, "media_stop") == 1
    assert hass.states.get(SLEEP).state == "0.0"


# ------------------------------------------------------------ late volume report
async def test_late_volume_report_aborts_own_fade(hass, home, freezer):
    await _playing_music(hass, home)
    original = home.handle
    queued: list[float] = []

    def lagging(domain, service, entity_id, data, context):
        if service == "volume_set" and entity_id == MUSIC:
            home.calls.append((domain, service, entity_id))
            queued.append(data["volume_level"])  # the device reports it ~12 s later
            return True
        return original(domain, service, entity_id, data, context)

    def deliver():
        # reports arriving one step late
        while len(queued) > 1:
            home.music("playing", volume_level=queued.pop(0))

    home.handle = lagging
    await _set(hass, 2, fade=2)  # step = 0.5 * 10/120 = 0.042 > tolerance 0.02
    await tick(hass, freezer, 60, between=deliver)
    assert hass.states.get(ROOM).attributes["sleep_timer_fading"] is True, "own fade taken as external change"


# ------------------------------------------- user change right before cancel/+15/end
async def test_user_volume_then_cancel_keeps_user_volume(hass, home, freezer):
    await _playing_music(hass, home)
    await _set(hass, 3, fade=3)
    await tick(hass, freezer, 90)
    home.music("playing", volume_level=0.42)  # user turns it up
    await hass.async_block_till_done()
    await hass.services.async_call(DOMAIN, "cancel_sleep_timer", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert vol(hass) == 0.42, "external change must abort the fade without restore"


async def test_user_volume_then_plus15_keeps_user_volume(hass, home, freezer):
    await _playing_music(hass, home)
    await _set(hass, 3, fade=3)
    await tick(hass, freezer, 90)
    home.music("playing", volume_level=0.42)
    await hass.async_block_till_done()
    await hass.services.async_call("button", "press", {"entity_id": SLEEP_PLUS}, blocking=True)
    await hass.async_block_till_done()
    assert vol(hass) == 0.42


async def test_user_volume_in_last_step_then_expiry(hass, home, freezer):
    await _playing_music(hass, home)
    await _set(hass, 1, fade=1)
    await tick(hass, freezer, 50)
    freezer.tick(timedelta(seconds=5))
    home.music("playing", volume_level=0.4)  # at 55 s, after the last fade step
    await hass.async_block_till_done()
    await tick(hass, freezer, 20)
    assert hass.states.get(MUSIC).state == "idle"
    assert vol(hass) == 0.4, "user volume overwritten by restore"


# --------------------------------------------------- follow base volume polluted
async def test_fade_pollutes_follow_base_volume(hass, house, freezer):
    original = house.handle

    def avg(domain, service, entity_id, data, context):
        r = original(domain, service, entity_id, data, context)
        g = hass.states.get(FOLLOW)
        mem = list(g.attributes.get("group_members", []))
        if mem:
            vols = [hass.states.get(m).attributes.get("volume_level", 0) for m in mem]
            house.set(FOLLOW, g.state, volume_level=sum(vols) / len(vols))
        return r

    house.handle = avg
    hass.states.async_set(LIVING_OCC, "on")
    await hass.async_block_till_done()
    await start_follow(hass, house)
    await hass.async_block_till_done()
    assert house.members(FOLLOW) == [MUSIC]
    assert vol(hass) == pytest.approx(0.5)
    await _set(hass, 2, fade=2)
    await tick(hass, freezer, 140)
    assert house.members(FOLLOW) == []
    stored = get_hub(hass).data["volumes"].get(FOLLOW)
    assert stored == pytest.approx(60, abs=1), f"follow base volume now {stored}"


# ------------------------------------------------ auto power off: never cancelled
async def test_auto_power_off_room_off_keeps_stale_timer(hass, home, freezer):
    await setup_room(hass)
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.living_room_automatic_power"}, blocking=True
    )
    home.music("playing", volume_level=0.5)
    await hass.async_block_till_done()
    await _set(hass, 60, fade=0)
    home.music("idle")  # room is fully off now (no plug on, nothing playing)
    await tick(hass, freezer, 60)
    assert hass.states.get(SLEEP).state == "0.0", "timer survives a room that went fully off"


# ------------------------------------------- HA start with players not yet known
async def test_restart_with_unavailable_states_keeps_timer(hass, home, freezer):
    events = async_capture_events(hass, EVENT_SLEEP_TIMER_FINISHED)
    entry = await _playing_music(hass, home)
    await _set(hass, 60, fade=5)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    # HA restart: MA not connected yet, Zigbee plugs not reported yet
    for e in (MUSIC, SPEAKERS, BASS):
        hass.states.async_set(e, "unavailable", hass.states.get(e).attributes)
    hass.set_state(CoreState.starting)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    home.music("playing", volume_level=0.5)
    hass.states.async_set(SPEAKERS, "on")
    hass.states.async_set(BASS, "on")
    await tick(hass, freezer, 10)
    assert [e.data["reason"] for e in events] == [], "timer lost at HA start"
    assert hass.states.get(SLEEP).state != "0.0"


# ----------------------------------------------- fade start is saved right away
async def test_fade_start_saved_at_once(hass, home, freezer, hass_storage):
    entry = await _playing_music(hass, home)
    await _set(hass, 2, fade=2)
    await tick(hass, freezer, 20)  # fade steps keep coming; the start must be on disk
    stored = hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"]["sleep"]
    assert stored["fade_from"] == 0.5


# ------------------------------------------- someone set a volume: restore dropped
async def test_restore_dropped_when_volume_set_meanwhile(hass, home, freezer):
    entry = await setup_room(hass)
    home.kodi_boots = True
    home.kodi("playing", volume_level=0.8)
    await hass.async_block_till_done()
    await _set(hass, 1, fade=1)
    await tick(hass, freezer, 70)
    assert hass.states.get(KODI).state == "off"
    home.kodi("off", volume_level=0.3)  # e.g. an automation set a night volume
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert vol(hass, KODI) == 0.3
    assert entry.runtime_data.data["volume_restore"] == {}


# ------------------------------------------- timer end during a manual "off"
async def test_no_second_off_while_turning_off(hass, home, freezer):
    entry = await _playing_music(hass, home)
    ctrl = entry.runtime_data
    await _set(hass, 1, fade=0)
    calls = []
    original = ctrl._turn_off

    async def counting():
        calls.append(1)
        ctrl.data["sleep"]["ends_at"] = dt_util.utcnow().timestamp() - 1
        await ctrl.sleep._async_step()  # the timer ends while this "off" runs
        await original()

    ctrl._turn_off = counting
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert calls == [1]
