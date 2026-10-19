"""Source changes: regressions from the review and bug injection."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import async_capture_events

from homeassistant.components.device_automation import DeviceAutomationType, async_get_device_automations
from homeassistant.helpers import device_registry as dr

from custom_components.better_media_player.const import DOMAIN, EVENT_SOURCE_CHANGED

from .conftest import FOLLOW, KITCHEN_OCC, KODI, LIVING_OCC, MUSIC, TV
from .test_source_event import ROOM, _data, _room, _tick

HOST = "switch.kodi_box_plug"


async def _call(hass, service, **data):
    await hass.services.async_call("media_player", service, {"entity_id": ROOM, **data}, blocking=True)
    await hass.async_block_till_done()


async def _music_plays(hass, home, freezer):
    home.music("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)


async def test_off_with_paused_music(hass, home, freezer):
    await _room(hass, home, freezer)
    await _music_plays(hass, home, freezer)
    home.music("paused")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    events = async_capture_events(hass, EVENT_SOURCE_CHANGED)
    await _call(hass, "turn_off")
    await _tick(hass, freezer, 5)
    assert _data(events) == [(None, "audio", "off")]


async def test_off_after_switching_to_video(hass, home, freezer):
    await _room(hass, home, freezer)
    home.kodi_boots = True
    await _music_plays(hass, home, freezer)
    events = async_capture_events(hass, EVENT_SOURCE_CHANGED)
    await _call(hass, "select_source", source="Kodi")
    await _tick(hass, freezer, 10)
    await _call(hass, "turn_off")
    await _tick(hass, freezer, 5)
    assert _data(events) == [("video", "audio", "selected"), (None, "video", "off")]


async def test_video_while_the_music_player_is_behind_its_host_plug(hass, home, freezer):
    hass.states.async_set(HOST, "off")
    home.music("unavailable")
    await _room(hass, home, freezer, audio_host=HOST)
    events = async_capture_events(hass, EVENT_SOURCE_CHANGED)
    home.kodi_boots = True
    await _call(hass, "select_source", source="Kodi")
    await _tick(hass, freezer, 10)
    assert _data(events) == [("video", None, "selected")]


async def test_selecting_music_fires_audio(hass, home, freezer):
    await _room(hass, home, freezer)
    home.kodi_boots = True
    await _call(hass, "turn_on")
    await _tick(hass, freezer, 5)
    events = async_capture_events(hass, EVENT_SOURCE_CHANGED)
    await _call(hass, "select_source", source="Office Player")
    await _tick(hass, freezer, 10)
    assert _data(events) == [("audio", "video", "selected")]


async def test_turning_on_with_music_from_off(hass, home, freezer):
    entry = await _room(hass, home, freezer)
    entry.runtime_data.data["last_source"] = "audio"
    events = async_capture_events(hass, EVENT_SOURCE_CHANGED)
    await _call(hass, "turn_on")
    await _tick(hass, freezer, 10)
    assert _data(events) == [("audio", None, "selected")]


async def test_device_triggers_of_a_room_that_is_not_loaded(hass, home, freezer):
    entry = await _room(hass, home, freezer)
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    triggers = await async_get_device_automations(hass, DeviceAutomationType.TRIGGER, [device.id])
    assert {t["type"] for t in triggers[device.id] if t["domain"] == DOMAIN} == {
        "source_changed", "source_video", "source_audio", "source_off"
    }


async def test_failed_start_leaves_no_stale_request(hass, home, freezer):
    await _room(hass, home, freezer)
    home.kodi_boots = False  # Kodi does not start
    await _call(hass, "turn_on")
    await _tick(hass, freezer, 120)
    events = async_capture_events(hass, EVENT_SOURCE_CHANGED)
    home.kodi("playing")  # a film cast later
    await hass.async_block_till_done()
    await _tick(hass, freezer, 10)
    assert [e[2] for e in _data(events)] == ["player"]


async def test_first_look_waits_for_the_sequence(hass, home, freezer):
    hass.states.async_set(HOST, "off")
    home.music("unavailable")
    await _room(hass, home, freezer, video_player=None, display=None, audio_host=HOST, audio_on_wait=5)
    events = async_capture_events(hass, EVENT_SOURCE_CHANGED)
    await _call(hass, "turn_on")
    home.music("idle")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 6)
    home.music("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 6)
    assert _data(events) == [("audio", None, "selected")]


# ------------------------------------------------------- from bug injection


async def test_a_suppressed_player_still_on_is_no_source(hass, home, freezer):
    await _room(hass, home, freezer)
    home.kodi_boots = True
    home.kodi_suspends = False  # Kodi stays idle after turn_off
    await _call(hass, "turn_on")
    await _tick(hass, freezer, 5)
    events = async_capture_events(hass, EVENT_SOURCE_CHANGED)
    await _call(hass, "select_source", source="Office Player")  # the music player stays idle
    await _tick(hass, freezer, 10)
    assert hass.states.get(KODI).state == "idle"
    assert _data(events) == [("audio", "video", "selected")]


async def test_follow_me_in_a_quiet_room_is_no_source(hass, house, freezer):
    from .test_follow import follow_from

    hass.states.async_set(KITCHEN_OCC, "on")
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.wohnzimmer_quiet"}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    await follow_from(hass, house, player="media_player.kitchen_player", room="media_player.kitchen")
    events = async_capture_events(hass, EVENT_SOURCE_CHANGED)
    original = house.handle

    def deaf(domain, service, entity_id, data, context):
        if service == "unjoin":
            return True  # Music Assistant does not take the quiet room out
        return original(domain, service, entity_id, data, context)

    house.handle = deaf
    # the living room's player is added to the group by hand while the room is quiet
    house.group("playing", [*house.members(FOLLOW), MUSIC], FOLLOW)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    assert [e.data for e in events if e.data["room"] == "Wohnzimmer"] == []


async def test_request_for_another_source_is_no_reason(hass, home, freezer):
    entry = await _room(hass, home, freezer)
    entry.runtime_data.source_watch.note_request("video")  # asked for video ...
    events = async_capture_events(hass, EVENT_SOURCE_CHANGED)
    await _music_plays(hass, home, freezer)  # ... but music started in its app
    assert _data(events) == [("audio", None, "player")]


async def test_play_through_the_room_player_is_selected(hass, home, freezer):
    await _room(hass, home, freezer)
    await _music_plays(hass, home, freezer)  # source audio
    home.kodi("idle")  # Kodi is on (in its menu) as well
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    events = async_capture_events(hass, EVENT_SOURCE_CHANGED)
    from custom_components.better_media_player.browser import make_id

    await _call(hass, "play_media", media_content_id=make_id("item", "video", "movie", "123"), media_content_type="movie")
    home.kodi("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 10)
    assert [e[2] for e in _data(events)] == ["selected"]


async def test_snooze_keeps_the_source(hass, home, freezer):
    from homeassistant.components.media_player import MediaPlayerEntityFeature as F

    from .conftest import MA_FEATURES
    from .test_wake import _settle

    await _room(hass, home, freezer)
    home.set(MUSIC, "idle", supported_features=int(MA_FEATURES & ~F.PAUSE))  # snooze stops it
    await hass.services.async_call(
        DOMAIN, "wake",
        {"entity_id": ROOM, "media_content_id": "library://radio/1", "media_content_type": "music"},
        blocking=True,
    )
    await _settle(hass)
    home.set(MUSIC, "playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    events = async_capture_events(hass, EVENT_SOURCE_CHANGED)
    await hass.services.async_call(DOMAIN, "snooze", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 10)
    assert hass.states.get(MUSIC).state == "idle"
    assert events == []


async def test_follow_wake_is_selected(hass, house, freezer):
    from .test_wake import _settle

    await _tick(hass, freezer, 3)
    events = async_capture_events(hass, EVENT_SOURCE_CHANGED)
    await hass.services.async_call(
        DOMAIN, "wake",
        {"entity_id": ROOM, "media_content_id": "library://radio/1", "media_content_type": "music", "follow": True},
        blocking=True,
    )
    await _settle(hass)
    await _tick(hass, freezer, 5)
    mine = [(e.data["source"], e.data["reason"]) for e in events if e.data["room"] == "Wohnzimmer"]
    assert mine == [("audio", "selected")]


async def test_a_long_switch_is_no_off(hass, home, freezer):
    await _room(hass, home, freezer, display_on_wait=8)
    await _music_plays(hass, home, freezer)
    events = async_capture_events(hass, EVENT_SOURCE_CHANGED)
    home.kodi_boots = True
    await _call(hass, "select_source", source="Kodi")  # the TV needs 8 s
    await _tick(hass, freezer, 15)
    assert _data(events) == [("video", "audio", "selected")]


async def test_a_start_longer_than_a_minute_is_still_selected(hass, home, freezer):
    await _room(hass, home, freezer, display_on_wait=70)
    events = async_capture_events(hass, EVENT_SOURCE_CHANGED)
    home.kodi_boots = True
    await _call(hass, "turn_on")
    await _tick(hass, freezer, 80, step=2)
    assert _data(events) == [("video", None, "selected")]
