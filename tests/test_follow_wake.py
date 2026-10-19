"""Wake up with follow me: the music follows you out of the wake room."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.better_media_player.browser import KIND_ITEM, make_id
from custom_components.better_media_player.const import DOMAIN, EVENT_WAKE_FINISHED
from custom_components.better_media_player.hub import get_hub

from .conftest import (
    FOLLOW,
    KITCHEN,
    KITCHEN_OCC,
    LIVING_OCC,
    MUSIC,
    SPEAKERS,
    setup_hub,
    setup_room,
)

ROOM = "media_player.wohnzimmer"  # the wake room ("bedroom")
KROOM = "media_player.kitchen"
WAKE = "sensor.wohnzimmer_wake"
LSTATE = "sensor.wohnzimmer_follow"
KSTATE = "sensor.kitchen_follow"


async def _tick(hass, freezer, seconds: float, step: float = 5) -> None:
    left = seconds
    while left > 0:
        freezer.tick(timedelta(seconds=min(step, left)))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        left -= step


async def _settle(hass):
    for _ in range(20):
        await asyncio.sleep(0)
        await hass.async_block_till_done()


def _vol(hass, entity_id=MUSIC):
    return hass.states.get(entity_id).attributes.get("volume_level")


def _played(house, entity_id, service):
    return [d for s, e, d in house.played if s == service and e == entity_id]


async def _wake(hass, entity_id=ROOM, **data):
    payload = {
        "entity_id": entity_id,
        "media_content_id": "library://radio/1",
        "media_content_type": "music",
        "follow": True,
        "start_volume": 0.05,
        "target_volume": 0.4,
        "fade_minutes": 2,
        **data,
    }
    await hass.services.async_call(DOMAIN, "wake", payload, blocking=True)
    await _settle(hass)


async def test_wake_room_joins_and_fades(hass, house, freezer):
    await _wake(hass)
    assert MUSIC in house.members(FOLLOW)
    assert _played(house, FOLLOW, "play_media")[-1]["media_content_id"] == "library://radio/1"
    assert hass.states.get(LSTATE).state == "waking"
    assert hass.states.get(WAKE).state == "fading"
    assert _vol(hass) == 0.05
    assert [d["volume_level"] for d in _played(house, MUSIC, "volume_set")][0] == 0.05  # quiet from the join
    await _tick(hass, freezer, 130)
    assert _vol(hass) == 0.4
    assert house.on(SPEAKERS)
    assert get_hub(hass).data["volumes"].get(FOLLOW, 25) == 25  # the fade is not follow me's volume


async def test_occupied_rooms_are_held_out(hass, house, freezer):
    hass.states.async_set(KITCHEN_OCC, "on")  # the partner is already in the kitchen
    await hass.async_block_till_done()
    await _wake(hass)
    await _tick(hass, freezer, 10)
    assert KITCHEN not in house.members(FOLLOW)
    assert hass.states.get(KSTATE).state == "held_out"
    hass.states.async_set(KITCHEN_OCC, "off")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 10)
    hass.states.async_set(KITCHEN_OCC, "on")  # comes in again
    await hass.async_block_till_done()
    await _tick(hass, freezer, 10)
    assert KITCHEN in house.members(FOLLOW)
    assert _vol(hass, KITCHEN) == 0.25  # follow me's volume, not the wake's


async def test_rooms_joining_later_get_follow_volume(hass, house, freezer):
    await _wake(hass)
    await _tick(hass, freezer, 20)
    hass.states.async_set(KITCHEN_OCC, "on")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 10)
    assert KITCHEN in house.members(FOLLOW)
    assert _vol(hass, KITCHEN) == 0.25
    assert _vol(hass) < 0.25  # the wake room still fades


async def test_leaving_the_wake_room_ends_the_wake(hass, house, freezer):
    finished = async_capture_events(hass, EVENT_WAKE_FINISHED)
    await _wake(hass)
    hass.states.async_set(LIVING_OCC, "on")  # getting up
    await hass.async_block_till_done()
    await _tick(hass, freezer, 20)
    hass.states.async_set(KITCHEN_OCC, "on")
    hass.states.async_set(LIVING_OCC, "off")  # ... and off to the kitchen
    await hass.async_block_till_done()
    await _tick(hass, freezer, 60)
    assert MUSIC in house.members(FOLLOW)  # presence run-down (2 min)
    await _tick(hass, freezer, 80)
    assert [e.data["reason"] for e in finished] == ["left"]
    assert MUSIC not in house.members(FOLLOW)
    assert KITCHEN in house.members(FOLLOW)  # the music followed


async def test_sleeper_not_seen_stays(hass, house, freezer):
    """Motion sensors miss sleepers: presence elsewhere does not end the wake room."""
    finished = async_capture_events(hass, EVENT_WAKE_FINISHED)
    await _wake(hass)
    hass.states.async_set(KITCHEN_OCC, "on")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5 * 60, step=10)
    assert MUSIC in house.members(FOLLOW)
    assert finished == []
    assert hass.states.get(LSTATE).state == "waking"


async def test_snooze_only_this_room(hass, house, freezer):
    await _wake(hass)
    hass.states.async_set(KITCHEN_OCC, "on")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 10)
    assert KITCHEN in house.members(FOLLOW)
    await hass.services.async_call(DOMAIN, "snooze", {"entity_id": ROOM, "minutes": 2}, blocking=True)
    await _settle(hass)
    await _tick(hass, freezer, 20)
    assert MUSIC not in house.members(FOLLOW)
    assert KITCHEN in house.members(FOLLOW)
    assert hass.states.get(FOLLOW).state == "playing"
    assert hass.states.get(LSTATE).state == "snoozed"
    hass.states.async_set(LIVING_OCC, "on")  # presence does not bring it back while snoozed
    await hass.async_block_till_done()
    await _tick(hass, freezer, 20)
    assert MUSIC not in house.members(FOLLOW)
    assert house.on(SPEAKERS)  # the room stays ready
    await _tick(hass, freezer, 85)
    assert MUSIC in house.members(FOLLOW)
    assert _vol(hass) <= 0.07  # back at the start volume, fading in again
    assert hass.states.get(WAKE).state == "fading"


async def test_snooze_as_only_member_resumes_the_group(hass, house, freezer):
    await _wake(hass)
    await hass.services.async_call(DOMAIN, "snooze", {"entity_id": ROOM, "minutes": 2}, blocking=True)
    await _settle(hass)
    assert house.members(FOLLOW) == []
    assert hass.states.get(FOLLOW).state == "paused"
    await _tick(hass, freezer, 130)
    assert MUSIC in house.members(FOLLOW)
    assert _played(house, FOLLOW, "media_play")
    assert hass.states.get(WAKE).state in ("fading", "playing")


async def test_second_wake_room_joins_without_media_change(hass, house, freezer):
    await _wake(hass)
    n = len(_played(house, FOLLOW, "play_media"))
    await _tick(hass, freezer, 60)
    await _wake(hass, entity_id=KROOM, media_content_id="library://radio/2", start_volume=0.1)
    assert KITCHEN in house.members(FOLLOW)
    assert len(_played(house, FOLLOW, "play_media")) == n  # the group's media goes on
    assert _vol(hass, KITCHEN) == 0.1
    assert hass.states.get("sensor.kitchen_wake").state == "fading"


async def test_quiet_wake_room_still_wakes(hass, house, freezer):
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.wohnzimmer_quiet"}, blocking=True)
    await hass.async_block_till_done()
    await _wake(hass)
    await _tick(hass, freezer, 10)
    assert MUSIC in house.members(FOLLOW)
    assert house.on(SPEAKERS)


async def test_stop_wake_makes_it_a_normal_follow_room(hass, house, freezer):
    await _wake(hass)
    hass.states.async_set(LIVING_OCC, "on")
    await hass.async_block_till_done()
    await hass.services.async_call(DOMAIN, "stop_wake", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 10)
    assert MUSIC in house.members(FOLLOW)
    assert hass.states.get(LSTATE).state == "following"


async def test_max_duration_only_this_room_goes(hass, house, freezer):
    await _wake(hass, max_duration_minutes=3)
    hass.states.async_set(KITCHEN_OCC, "on")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3 * 60 + 5, step=10)
    assert hass.states.get("number.wohnzimmer_sleep_timer").state != "0.0"
    await _tick(hass, freezer, 6 * 60, step=10)
    assert MUSIC not in house.members(FOLLOW)
    assert KITCHEN in house.members(FOLLOW)


async def test_restart_mid_fade_keeps_waking(hass, house, freezer):
    await _wake(hass)
    await _tick(hass, freezer, 60)
    entry = next(e for e in hass.config_entries.async_entries(DOMAIN) if e.title == "Wohnzimmer")
    assert await hass.config_entries.async_reload(entry.entry_id)
    await _settle(hass)
    await _tick(hass, freezer, 70)
    assert _vol(hass) == 0.4
    assert hass.states.get(LSTATE).state == "waking"


async def test_needs_follow_capable_room(hass, home):
    await setup_room(hass)  # no occupancy sensors
    await setup_hub(hass)
    with pytest.raises(ServiceValidationError):
        await _wake(hass)


async def test_video_media_cannot_follow(hass, house):
    with pytest.raises(ServiceValidationError):
        await _wake(hass, media_content_id=make_id(KIND_ITEM, "video", "movie", "1"), media_content_type="movie")
