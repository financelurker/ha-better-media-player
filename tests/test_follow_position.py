"""Follow me moves the queue; the track goes on where it was."""

from __future__ import annotations

from datetime import timedelta

from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.util import dt as dt_util

from custom_components.better_media_player.const import DOMAIN

from .conftest import FOLLOW, KITCHEN, KITCHEN_OCC, LIVING_OCC, MUSIC
from .test_follow import KROOM, LIVING, stop_follow

TRACK = "library://track/42"


def _seeks(house, entity_id):
    return [d["seek_position"] for s, e, d in house.played if s == "media_seek" and e == entity_id]


async def _tick(hass, freezer, seconds):
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()


async def _start(hass, house, freezer, position=90.0):
    hass.states.async_set(LIVING_OCC, "on")
    house.music(
        "playing", volume_level=0.25, media_content_id=TRACK, media_duration=240,
        media_position=position - 5, media_position_updated_at=dt_util.utcnow(),
    )
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)  # it played on for 5 s since its last report
    await hass.services.async_call(DOMAIN, "start_follow", {"entity_id": LIVING}, blocking=True)
    await hass.async_block_till_done()


async def test_start_goes_on_where_the_track_was(hass, house, freezer):
    house.transfer_restarts = True
    await _start(hass, house, freezer)
    assert _seeks(house, FOLLOW) == [90.0]
    assert hass.states.get(FOLLOW).attributes["media_position"] == 90.0


async def test_no_seek_when_the_move_kept_the_position(hass, house, freezer):
    await _start(hass, house, freezer)
    house.set(FOLLOW, "playing", media_position=120.0, media_position_updated_at=dt_util.utcnow())
    await hass.async_block_till_done()
    await stop_follow(hass)  # the move back keeps the position
    assert hass.states.get(MUSIC).attributes["media_position"] == 120.0
    assert _seeks(house, MUSIC) == []


async def test_stop_goes_on_where_the_track_was(hass, house, freezer):
    await _start(hass, house, freezer)
    house.transfer_restarts = True
    house.set(FOLLOW, "playing", media_position=120.0, media_position_updated_at=dt_util.utcnow())
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    await stop_follow(hass)
    assert house.transferred[-1] == (FOLLOW, MUSIC)
    assert _seeks(house, MUSIC) == [123.0]


async def test_stop_in_another_room(hass, house, freezer):
    await _start(hass, house, freezer)
    hass.states.async_set(KITCHEN_OCC, "on")
    await hass.async_block_till_done()
    house.transfer_restarts = True
    await stop_follow(hass, KROOM)
    assert house.transferred[-1] == (FOLLOW, KITCHEN)
    assert len(_seeks(house, KITCHEN)) == 1


async def test_a_paused_stop_is_not_started(hass, house, freezer):
    await _start(hass, house, freezer)
    house.transfer_restarts = True
    house.group("paused", house.members(FOLLOW), FOLLOW)
    await hass.async_block_till_done()
    await stop_follow(hass)
    assert hass.states.get(MUSIC).state == "paused"
    assert _seeks(house, MUSIC) == []


async def test_near_the_end_of_the_track_nothing_is_set(hass, house, freezer):
    house.transfer_restarts = True
    await _start(hass, house, freezer, position=237)
    assert _seeks(house, FOLLOW) == []


async def test_another_track_by_then_is_left_alone(hass, house, freezer):
    house.transfer_restarts = True
    house.transfer_track = "library://track/43"
    await _start(hass, house, freezer)
    assert _seeks(house, FOLLOW) == []


async def test_without_a_position_report_nothing_is_set(hass, house, freezer):
    house.transfer_restarts = True
    hass.states.async_set(LIVING_OCC, "on")
    house.music("playing", volume_level=0.25, media_content_id=TRACK)
    await hass.async_block_till_done()
    await hass.services.async_call(DOMAIN, "start_follow", {"entity_id": LIVING}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(FOLLOW).state == "playing"
    assert _seeks(house, FOLLOW) == []


async def test_a_late_report_is_waited_for(hass, house, freezer):
    house.transfer_reports = False
    await _start(hass, house, freezer)
    assert _seeks(house, FOLLOW) == []
    await _tick(hass, freezer, 2)
    house.set(FOLLOW, "playing", media_position=2.0, media_position_updated_at=dt_util.utcnow())
    await hass.async_block_till_done()
    assert _seeks(house, FOLLOW) == [90.0]


async def test_gives_up_without_a_report(hass, house, freezer):
    house.transfer_reports = False
    await _start(hass, house, freezer)
    await _tick(hass, freezer, 30)
    house.set(FOLLOW, "playing", media_position=2.0, media_position_updated_at=dt_util.utcnow())
    await hass.async_block_till_done()
    assert _seeks(house, FOLLOW) == []


async def test_an_old_report_of_the_same_track_is_not_taken(hass, house, freezer):
    house.transfer_reports = False
    house.set(FOLLOW, "idle", media_content_id=TRACK, media_position=0.0, media_position_updated_at=dt_util.utcnow())
    await _start(hass, house, freezer)  # the old report is 5 s old by then
    assert _seeks(house, FOLLOW) == []


async def test_unloading_stops_the_watch(hass, house, freezer):
    house.transfer_reports = False
    await _start(hass, house, freezer)
    hub = next(e for e in hass.config_entries.async_entries(DOMAIN) if e.data.get("entry_type") == "hub")
    assert await hass.config_entries.async_unload(hub.entry_id)
    await hass.async_block_till_done()
    house.set(FOLLOW, "playing", media_position=2.0, media_position_updated_at=dt_util.utcnow())
    await hass.async_block_till_done()
    assert _seeks(house, FOLLOW) == []
