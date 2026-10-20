"""Source changes: event and device triggers."""

from __future__ import annotations

from datetime import timedelta

from pytest_homeassistant_custom_component.common import async_capture_events, async_fire_time_changed

from homeassistant.components import automation
from homeassistant.components.device_automation import DeviceAutomationType
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from custom_components.better_media_player.const import DOMAIN, EVENT_SOURCE_CHANGED

from .conftest import GROUP, KODI, MUSIC, SPEAKERS, TV, setup_room

ROOM = "media_player.wohnzimmer"


async def _tick(hass, freezer, seconds: float, step: float = 1) -> None:
    left = seconds
    while left > 0:
        freezer.tick(timedelta(seconds=min(step, left)))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        left -= step


def _data(events):
    return [(e.data["source"], e.data["previous"], e.data["reason"]) for e in events]


async def _room(hass, home, freezer, **overrides):
    entry = await setup_room(hass, **overrides)
    await _tick(hass, freezer, 3)
    return entry


async def test_turn_on_fires_once_the_tv_has_power(hass, home, freezer):
    await _room(hass, home, freezer, display_on_wait=5)
    events = async_capture_events(hass, EVENT_SOURCE_CHANGED)
    tv_on = []
    hass.bus.async_listen(EVENT_SOURCE_CHANGED, lambda e: tv_on.append(home.on(TV)))
    home.kodi_boots = True
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 10)
    assert _data(events) == [("video", None, "selected")]
    e = events[0].data
    assert (e["entity_id"], e["room"], e["player"]) == (ROOM, "Wohnzimmer", KODI)
    assert tv_on == [True]


async def test_switching_the_source(hass, home, freezer):
    await _room(hass, home, freezer)
    home.kodi_boots = True
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    events = async_capture_events(hass, EVENT_SOURCE_CHANGED)
    await hass.services.async_call(
        "media_player", "select_source", {"entity_id": ROOM, "source": "Office Player"}, blocking=True
    )
    await hass.async_block_till_done()
    home.music("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    assert _data(events) == [("audio", "video", "selected")]


async def test_a_player_taking_over_by_itself(hass, home, freezer):
    await _room(hass, home, freezer)
    events = async_capture_events(hass, EVENT_SOURCE_CHANGED)
    home.music("playing")  # started in the Music Assistant app
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    home.kodi("playing")  # a film cast to Kodi while the music plays
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    assert _data(events) == [("audio", None, "player"), ("video", "audio", "player")]


async def test_room_off_fires_off(hass, home, freezer):
    await _room(hass, home, freezer)
    home.music("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    events = async_capture_events(hass, EVENT_SOURCE_CHANGED)
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    assert _data(events) == [(None, "audio", "off")]
    assert events[0].data["player"] is None


async def test_pause_new_media_and_dropouts_fire_nothing(hass, home, freezer):
    await _room(hass, home, freezer)
    home.music("playing", media_title="A")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    events = async_capture_events(hass, EVENT_SOURCE_CHANGED)
    home.music("paused")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    home.music("playing", media_title="B")
    await hass.async_block_till_done()
    home.music("unavailable")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    home.music("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    assert events == []


async def test_short_overlap_is_no_change(hass, home, freezer):
    await _room(hass, home, freezer)
    home.music("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    events = async_capture_events(hass, EVENT_SOURCE_CHANGED)
    home.kodi("idle")  # Kodi wakes for a moment and goes again
    await hass.async_block_till_done()
    home.kodi("off")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    assert events == []


async def test_nothing_after_a_restart_with_the_room_on(hass, home, freezer):
    home.music("playing")
    entry = await _room(hass, home, freezer)
    events = async_capture_events(hass, EVENT_SOURCE_CHANGED)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    assert events == []


async def test_group_playback_counts(hass, home, freezer):
    await _room(hass, home, freezer)
    events = async_capture_events(hass, EVENT_SOURCE_CHANGED)
    home.group("playing", [MUSIC], GROUP)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    assert _data(events) == [("audio", None, "player")]


# ------------------------------------------------------------ device triggers


async def test_device_triggers(hass, home, freezer):
    from homeassistant.components.device_automation import async_get_device_automations

    entry = await _room(hass, home, freezer)
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    triggers = await async_get_device_automations(hass, DeviceAutomationType.TRIGGER, [device.id])
    types = sorted(t["type"] for t in triggers[device.id] if t["domain"] == DOMAIN)
    assert types == ["night_mode_off", "night_mode_on", "source_audio", "source_changed", "source_off", "source_video"]
    types = [t for t in types if t.startswith("source")]  # (the night mode's: test_night_event.py)

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
                for t in types
            ]
        },
    )
    home.music("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    assert sorted(hits) == ["source_audio", "source_changed"]
    hits.clear()
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    assert sorted(hits) == ["source_changed", "source_off"]


async def test_music_only_room_offers_no_video_trigger(hass, home, freezer):
    from homeassistant.components.device_automation import async_get_device_automations

    entry = await _room(hass, home, freezer, video_player=None, display=None)
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    triggers = await async_get_device_automations(hass, DeviceAutomationType.TRIGGER, [device.id])
    assert "source_video" not in {t["type"] for t in triggers[device.id]}
    assert home.on(SPEAKERS) is False
