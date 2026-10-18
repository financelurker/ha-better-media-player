"""Sleep timer: regressions from the review."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import async_capture_events, async_fire_time_changed

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState
from homeassistant.util import dt as dt_util

from custom_components.better_media_player.const import DOMAIN, EVENT_SLEEP_TIMER_FINISHED
from custom_components.better_media_player.hub import get_hub

from .conftest import BASS, FOLLOW, KODI, LIVING_OCC, MUSIC, SPEAKERS, setup_room

ROOM = "media_player.wohnzimmer"
NUMBER = "number.wohnzimmer_sleep_timer"
BUTTON = "button.wohnzimmer_sleep_timer_15_min"


async def _tick(hass, freezer, seconds, between=None):
    left = seconds
    while left > 0:
        freezer.tick(timedelta(seconds=min(10, left)))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        if between:
            between()
            await hass.async_block_till_done()
        left -= 10


def _vol(hass, e=MUSIC):
    return hass.states.get(e).attributes["volume_level"]


async def _set(hass, minutes, fade=None):
    data = {"entity_id": ROOM, "minutes": minutes}
    if fade is not None:
        data["fade_minutes"] = fade
    await hass.services.async_call(DOMAIN, "set_sleep_timer", data, blocking=True)
    await hass.async_block_till_done()


async def _playing(hass, home, **kw):
    entry = await setup_room(hass, **kw)
    home.music("playing", volume_level=0.5)
    await hass.async_block_till_done()
    assert home.on(SPEAKERS)
    return entry


# 1 ------------------------------------------------------------ late volume report
async def test_late_volume_report_aborts_own_fade(hass, home, freezer):
    await _playing(hass, home)
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
    await _tick(hass, freezer, 60, between=deliver)
    assert hass.states.get(ROOM).attributes["sleep_timer_fading"] is True, "own fade taken as external change"


# 2 ------------------------------------------- user change right before cancel/+15/end
async def test_user_volume_then_cancel_keeps_user_volume(hass, home, freezer):
    await _playing(hass, home)
    await _set(hass, 3, fade=3)
    await _tick(hass, freezer, 90)
    home.music("playing", volume_level=0.42)  # user turns it up
    await hass.async_block_till_done()
    await hass.services.async_call(DOMAIN, "cancel_sleep_timer", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert _vol(hass) == 0.42, "external change must abort the fade without restore"


async def test_user_volume_then_plus15_keeps_user_volume(hass, home, freezer):
    await _playing(hass, home)
    await _set(hass, 3, fade=3)
    await _tick(hass, freezer, 90)
    home.music("playing", volume_level=0.42)
    await hass.async_block_till_done()
    await hass.services.async_call("button", "press", {"entity_id": BUTTON}, blocking=True)
    await hass.async_block_till_done()
    assert _vol(hass) == 0.42


async def test_user_volume_in_last_step_then_expiry(hass, home, freezer):
    await _playing(hass, home)
    await _set(hass, 1, fade=1)
    await _tick(hass, freezer, 50)
    freezer.tick(timedelta(seconds=5))
    home.music("playing", volume_level=0.4)  # at 55 s, after the last fade step
    await hass.async_block_till_done()
    await _tick(hass, freezer, 20)
    assert hass.states.get(MUSIC).state == "idle"
    assert _vol(hass) == 0.4, "user volume overwritten by restore"


# 3 --------------------------------------------------- follow base volume polluted
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
    await hass.services.async_call(DOMAIN, "start_follow", {"volume": 0.6}, blocking=True)
    await hass.async_block_till_done()
    assert house.members(FOLLOW) == [MUSIC]
    assert _vol(hass) == pytest.approx(0.5)
    await _set(hass, 2, fade=2)
    await _tick(hass, freezer, 140)
    assert house.members(FOLLOW) == []
    stored = get_hub(hass).data["volumes"].get(FOLLOW)
    assert stored == pytest.approx(60, abs=1), f"follow base volume now {stored}"


# 4 ------------------------------------------------ auto power off: never cancelled
async def test_auto_power_off_room_off_keeps_stale_timer(hass, home, freezer):
    await setup_room(hass)
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.wohnzimmer_automatic_power"}, blocking=True
    )
    home.music("playing", volume_level=0.5)
    await hass.async_block_till_done()
    await _set(hass, 60, fade=0)
    home.music("idle")  # room is fully off now (no plug on, nothing playing)
    await _tick(hass, freezer, 60)
    assert hass.states.get(NUMBER).state == "0.0", "timer survives a room that went fully off"


# 5 ------------------------------------------- HA start with players not yet known
async def test_restart_with_unavailable_states_keeps_timer(hass, home, freezer):
    events = async_capture_events(hass, EVENT_SLEEP_TIMER_FINISHED)
    entry = await _playing(hass, home)
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
    await _tick(hass, freezer, 10)
    home.music("playing", volume_level=0.5)
    hass.states.async_set(SPEAKERS, "on")
    hass.states.async_set(BASS, "on")
    await _tick(hass, freezer, 10)
    assert [e.data["reason"] for e in events] == [], "timer lost at HA start"
    assert hass.states.get(NUMBER).state != "0.0"


# 6 ----------------------------------------------- fade start is saved right away
async def test_fade_start_saved_at_once(hass, home, freezer, hass_storage):
    entry = await _playing(hass, home)
    await _set(hass, 2, fade=2)
    await _tick(hass, freezer, 20)  # fade steps keep coming; the start must be on disk
    stored = hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"]["sleep"]
    assert stored["fade_from"] == 0.5


# 7 ------------------------------------------- someone set a volume: restore dropped
async def test_restore_dropped_when_volume_set_meanwhile(hass, home, freezer):
    entry = await setup_room(hass)
    home.kodi_boots = True
    home.kodi("playing", volume_level=0.8)
    await hass.async_block_till_done()
    await _set(hass, 1, fade=1)
    await _tick(hass, freezer, 70)
    assert hass.states.get(KODI).state == "off"
    home.kodi("off", volume_level=0.3)  # e.g. an automation set a night volume
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert _vol(hass, KODI) == 0.3
    assert entry.runtime_data.data["volume_restore"] == {}


# 8 ------------------------------------------- timer end during a manual "off"
async def test_no_second_off_while_turning_off(hass, home, freezer):
    entry = await _playing(hass, home)
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
