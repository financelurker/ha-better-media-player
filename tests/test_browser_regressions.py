"""Regression tests for issues found in the review of the media browser."""

from __future__ import annotations

import asyncio


from homeassistant.components.media_player import MediaPlayerEntityFeature as F

from custom_components.better_media_player.browser import ROOT_ID, make_id

from .conftest import KODI, MA_FEATURES, MUSIC, setup_room
from .test_browser import ROOM, _browse, _child, _play, _plays, _titles


async def test_music_browsable_while_ma_player_off(hass, home, hass_ws_client):
    """Room off turned the MA player off (it supports TURN_OFF): library must stay browsable."""
    await setup_room(hass)
    hass.states.async_set(MUSIC, "off", {"supported_features": MA_FEATURES | F.TURN_ON | F.TURN_OFF,
                                         "friendly_name": "Office Player"})
    await hass.async_block_till_done()
    ws = await hass_ws_client(hass)
    root = await _browse(hass, ws)
    assert "Office Player" in _titles(root)
    music = await _browse(hass, ws, "directory", _child(root, "Office Player")["media_content_id"])
    assert _titles(music) == ["Playlists"]


async def test_search_with_root_id(hass, home, hass_ws_client):
    await setup_room(hass)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "media_player/search_media", "entity_id": ROOM,
                        "media_content_type": "directory", "media_content_id": ROOT_ID,
                        "search_query": "kid rock"})
    resp = await ws.receive_json()
    assert resp["success"], resp


async def test_root_can_search(hass, home, hass_ws_client):
    await setup_room(hass)
    ws = await hass_ws_client(hass)
    root = await _browse(hass, ws)
    assert root["can_search"], "room supports SEARCH_MEDIA but root is not searchable in the UI"


async def test_kodi_off_without_media_source(hass, home, hass_ws_client):
    """media_source not loaded: the Kodi folder should still show the hint, not fail."""
    await setup_room(hass)
    ws = await hass_ws_client(hass)
    root = await _browse(hass, ws)
    msg = {"id": 99, "type": "media_player/browse_media", "entity_id": ROOM,
           "media_content_type": "directory", "media_content_id": _child(root, "Kodi")["media_content_id"]}
    await ws.send_json(msg)
    resp = await ws.receive_json()
    assert resp["success"], resp


async def test_cancelled_caller_cancels_power_sequence(hass, home, freezer):
    await setup_room(hass)  # Kodi never boots
    ctrl = hass.config_entries.async_entries("better_media_player")[0].runtime_data
    item = make_id("item", "video", "movie", "123")
    task = hass.async_create_background_task(
        hass.services.async_call("media_player", "play_media",
                                 {"entity_id": ROOM, "media_content_type": "movie", "media_content_id": item},
                                 blocking=True),
        "t",
    )
    for _ in range(30):
        await asyncio.sleep(0)
    assert ctrl.sequence_running
    task.cancel()
    for _ in range(30):
        await asyncio.sleep(0)
    await asyncio.gather(task, return_exceptions=True)
    assert ctrl.sequence_running, "cancelling the play_media caller killed the room's power sequence"


async def test_cancel_swallowed(hass, home, freezer):
    """Caller cancellation is converted into HomeAssistantError (cancellation swallowed)."""
    await setup_room(hass)
    item = make_id("item", "video", "movie", "123")
    task = hass.async_create_background_task(
        hass.services.async_call("media_player", "play_media",
                                 {"entity_id": ROOM, "media_content_type": "movie", "media_content_id": item},
                                 blocking=True),
        "t",
    )
    for _ in range(30):
        await asyncio.sleep(0)
    task.cancel()
    res = await asyncio.gather(task, return_exceptions=True)
    assert isinstance(res[0], asyncio.CancelledError), repr(res[0])


async def test_enqueue_add_enum_from_service(hass, home):
    await setup_room(hass)
    home.kodi("playing")
    await hass.async_block_till_done()
    item = {"media_content_type": "track", "media_content_id": make_id("item", "audio", "track", "library://track/9")}
    await _play(hass, item, enqueue="add")
    assert ("media_player", "turn_off", KODI) not in home.calls
    assert str(_plays(home, MUSIC)[0]["enqueue"]) == "add"


async def test_play_now_music_in_follow_group(hass, house):
    """Room is a member of the playing follow group; picking a track 'play now'."""
    from .conftest import FOLLOW, LIVING_OCC

    home = house
    hass.states.async_set(LIVING_OCC, "on")
    home.group("playing", [MUSIC], FOLLOW)
    await hass.async_block_till_done()
    item = {"media_content_type": "track", "media_content_id": make_id("enqueue-play", "audio", "track", "library://track/9")}
    await _play(hass, item)
    assert ("media_player", "unjoin", MUSIC) in home.calls, "play now on a follow member replaces the whole group's music"
