"""The night mode's event (and device triggers): when the room's devices are up."""

from __future__ import annotations

from datetime import timedelta

from pytest_homeassistant_custom_component.common import async_capture_events, async_fire_time_changed

from homeassistant.components import automation
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from custom_components.better_media_player.const import DOMAIN, EVENT_NIGHT_MODE, EVENT_SOURCE_CHANGED

from .conftest import SPEAKERS, setup_room

ROOM = "media_player.wohnzimmer"
NIGHT = "switch.wohnzimmer_night_mode"


async def _tick(hass, freezer, seconds: float, step: float = 1) -> None:
    left = seconds
    while left > 0:
        freezer.tick(timedelta(seconds=min(step, left)))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        left -= step


def _data(events):
    return [(e.data["night_mode"], e.data["reason"]) for e in events]


async def _night(hass, on: bool) -> None:
    await hass.services.async_call("switch", "turn_on" if on else "turn_off", {"entity_id": NIGHT}, blocking=True)
    await hass.async_block_till_done()


async def _room(hass, freezer, **overrides):
    entry = await setup_room(hass, **overrides)
    await _tick(hass, freezer, 3)
    return entry


async def _music(hass, home, freezer, state="playing"):
    home.music(state)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)


async def test_switched_while_the_room_is_on(hass, home, freezer):
    await _room(hass, freezer)
    await _music(hass, home, freezer)
    events = async_capture_events(hass, EVENT_NIGHT_MODE)
    await _night(hass, True)
    await _night(hass, False)
    assert _data(events) == [(True, "switched"), (False, "switched")]
    e = events[0].data
    assert (e["entity_id"], e["room"]) == (ROOM, "Wohnzimmer")


async def test_switched_while_the_room_is_off_waits_for_the_room(hass, home, freezer):
    await _room(hass, freezer)
    events = async_capture_events(hass, EVENT_NIGHT_MODE)
    await _night(hass, True)
    assert events == []
    order: list[str] = []
    hass.bus.async_listen(EVENT_SOURCE_CHANGED, callback(lambda e: order.append("source")))
    hass.bus.async_listen(EVENT_NIGHT_MODE, callback(lambda e: order.append(f"night:{home.on(SPEAKERS)}")))
    await _music(hass, home, freezer)
    assert _data(events) == [(True, "room_on")]
    assert order == ["source", "night:True"], "after the source event, with the speakers on"


async def test_every_power_on_at_night(hass, home, freezer):
    await _room(hass, freezer)
    await _night(hass, True)
    events = async_capture_events(hass, EVENT_NIGHT_MODE)
    for _ in range(2):
        await _music(hass, home, freezer)
        await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
        await _tick(hass, freezer, 300, step=10)
    assert _data(events) == [(True, "room_on"), (True, "room_on")]


async def test_off_while_the_room_was_off_is_told_once(hass, home, freezer):
    await _room(hass, freezer)
    await _music(hass, home, freezer)
    await _night(hass, True)
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await _tick(hass, freezer, 300, step=10)
    events = async_capture_events(hass, EVENT_NIGHT_MODE)
    await _night(hass, False)
    assert events == []
    await _music(hass, home, freezer)
    assert _data(events) == [(False, "room_on")]
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await _tick(hass, freezer, 300, step=10)
    await _music(hass, home, freezer)
    assert _data(events) == [(False, "room_on")], "by day nothing more"


async def test_switched_during_the_start_comes_after_it(hass, home, freezer):
    await _room(hass, freezer, display_on_wait=5)
    events = async_capture_events(hass, EVENT_NIGHT_MODE)
    home.kodi_boots = True
    hass.async_create_task(
        hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    )
    await _tick(hass, freezer, 2)
    await _night(hass, True)
    assert events == []
    await _tick(hass, freezer, 10)
    assert _data(events) == [(True, "room_on")]


async def test_play_media_with_night_mode(hass, home, freezer):
    await _room(hass, freezer)
    await _music(hass, home, freezer)
    events = async_capture_events(hass, EVENT_NIGHT_MODE)
    await hass.services.async_call(
        "media_player", "play_media",
        {"entity_id": ROOM, "media_content_id": "x", "media_content_type": "music", "extra": {"night_mode": True}},
        blocking=True,
    )
    await _tick(hass, freezer, 3)
    assert _data(events) == [(True, "switched")]


async def test_a_restart_with_the_room_on_tells_nothing(hass, home, freezer):
    entry = await _room(hass, freezer)
    await _music(hass, home, freezer)
    await _night(hass, True)
    events = async_capture_events(hass, EVENT_NIGHT_MODE)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 10)
    assert events == []


async def test_device_triggers(hass, home, freezer):
    entry = await _room(hass, freezer)
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    hits: list[str] = []
    hass.services.async_register("test", "hit", lambda call: hits.append(call.data["type"]))
    assert await async_setup_component(
        hass,
        automation.DOMAIN,
        {
            automation.DOMAIN: [
                {
                    "trigger": {"platform": "device", "domain": DOMAIN, "device_id": device.id, "type": t},
                    "action": {"service": "test.hit", "data": {"type": t}},
                }
                for t in ("night_mode_on", "night_mode_off")
            ]
        },
    )
    await _music(hass, home, freezer)
    await _night(hass, True)
    await _night(hass, False)
    await hass.async_block_till_done()
    assert hits == ["night_mode_on", "night_mode_off"]


# --------------------------------------------------------------- from review


async def test_switched_during_a_source_switch_comes_when_it_is_done(hass, home, freezer):
    await _room(hass, freezer, display_on_wait=8)
    await _music(hass, home, freezer)
    events = async_capture_events(hass, EVENT_NIGHT_MODE)
    home.kodi_boots = True
    hass.async_create_task(
        hass.services.async_call("media_player", "select_source", {"entity_id": ROOM, "source": "Kodi"}, blocking=True)
    )
    await _tick(hass, freezer, 2)
    await _night(hass, True)
    assert events == [], "the TV is not up yet"
    await _tick(hass, freezer, 15)
    assert _data(events) == [(True, "source")]
    assert hass.states.get("media_player.kodi").state != "off"


async def test_the_tv_after_music_is_told_at_night(hass, home, freezer):
    await _room(hass, freezer)
    await _music(hass, home, freezer)
    await _night(hass, True)
    events = async_capture_events(hass, EVENT_NIGHT_MODE)
    home.kodi_boots = True
    await hass.services.async_call("media_player", "select_source", {"entity_id": ROOM, "source": "Kodi"}, blocking=True)
    await _tick(hass, freezer, 10)
    assert _data(events) == [(True, "source")]


async def test_an_update_with_the_room_on_at_night_tells_nothing(hass, home, freezer):
    entry = await _room(hass, freezer)
    await _music(hass, home, freezer)
    await _night(hass, True)
    entry.runtime_data.data.pop("night_announced")  # (stored before this version)
    events = async_capture_events(hass, EVENT_NIGHT_MODE)
    await _tick(hass, freezer, 20)
    assert events == []
