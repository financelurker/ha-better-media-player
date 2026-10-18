"""Room media browser, search and playing from them."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.components.media_player import BrowseMedia, MediaClass
from homeassistant.components.media_player import MediaPlayerEntityFeature as F
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.better_media_player.browser import is_own_id, parse_id

from .conftest import BASS, GROUP, KODI, MUSIC, SPEAKERS, TV, setup_room

ROOM = "media_player.wohnzimmer"


@pytest.fixture
def ma_registered(hass: HomeAssistant):
    """The music player comes from the Music Assistant integration."""
    reg = er.async_get(hass)
    reg.async_get_or_create("media_player", "music_assistant", "office", suggested_object_id="office_player")


async def _browse(hass, ws, media_type=None, media_id=None) -> dict:
    msg = {"id": _browse.n, "type": "media_player/browse_media", "entity_id": ROOM}
    _browse.n += 1
    if media_id is not None:
        msg["media_content_id"] = media_id
        msg["media_content_type"] = media_type or ""
    await ws.send_json(msg)
    response = await ws.receive_json()
    assert response["success"], response
    return response["result"]


_browse.n = 1


def _titles(node: dict) -> list[str]:
    return [c["title"] for c in node["children"]]


def _child(node: dict, title: str) -> dict:
    return next(c for c in node["children"] if c["title"] == title)


async def _play(hass, item: dict, **extra) -> None:
    await hass.services.async_call(
        "media_player",
        "play_media",
        {"entity_id": ROOM, "media_content_type": item["media_content_type"],
         "media_content_id": item["media_content_id"], **extra},
        blocking=True,
    )
    await hass.async_block_till_done()


def _plays(home, entity_id):
    return [
        {k: v for k, v in d.items() if k != "entity_id"}
        for s, e, d in home.played
        if s == "play_media" and e == entity_id
    ]


async def _tick(hass, freezer, seconds):
    left = seconds
    while left > 0:
        freezer.tick(timedelta(seconds=min(10, left)))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        left -= 10


# --------------------------------------------------------------------- root


async def test_features_offered_while_room_is_off(hass, home):
    await setup_room(hass)
    features = hass.states.get(ROOM).attributes["supported_features"]
    for feature in (F.BROWSE_MEDIA, F.SEARCH_MEDIA, F.PLAY_MEDIA, F.MEDIA_ENQUEUE):
        assert features & feature, feature


async def test_root_has_queues_first_then_players(hass, ma_registered, home, hass_ws_client):
    await setup_room(hass)
    home.kodi("idle")
    await hass.async_block_till_done()
    ws = await hass_ws_client(hass)
    root = await _browse(hass, ws)
    assert _titles(root) == ["Queue: Kodi", "Queue: Office Player", "Kodi", "Office Player"]
    assert all(is_own_id(c["media_content_id"]) for c in root["children"])

    kodi_queue = await _browse(hass, ws, *(_child(root, "Queue: Kodi")[k] for k in ("media_content_type", "media_content_id")))
    assert _titles(kodi_queue) == ["▶ Heat"]

    music_queue = await _browse(hass, ws, "playlist", _child(root, "Queue: Office Player")["media_content_id"])
    assert _titles(music_queue) == ["Now playing: All Summer Long", "Up next: Bawitdaba"]
    assert not any(c["can_play"] for c in music_queue["children"])


async def test_no_music_queue_without_music_assistant(hass, home, hass_ws_client):
    await setup_room(hass)
    ws = await hass_ws_client(hass)
    root = await _browse(hass, ws)
    assert "Queue: Office Player" not in _titles(root)


async def test_only_players_that_can_browse(hass, home, hass_ws_client):
    from .conftest import KODI_FEATURES

    await setup_room(hass)
    hass.states.async_set(KODI, "idle", {"supported_features": KODI_FEATURES & ~F.BROWSE_MEDIA, "friendly_name": "Kodi"})
    await hass.async_block_till_done()
    ws = await hass_ws_client(hass)
    root = await _browse(hass, ws)
    assert _titles(root) == ["Office Player"]


# ------------------------------------------------------------------- browse


async def test_browse_into_player_keeps_routing(hass, home, hass_ws_client):
    await setup_room(hass)
    home.kodi("idle")
    await hass.async_block_till_done()
    ws = await hass_ws_client(hass)
    root = await _browse(hass, ws)
    kodi = await _browse(hass, ws, "directory", _child(root, "Kodi")["media_content_id"])
    movies = await _browse(hass, ws, "library", _child(kodi, "Movies")["media_content_id"])
    heat = _child(movies, "Heat")
    kind, source, inner_type, inner_id = parse_id(heat["media_content_id"])
    assert (source, inner_type, inner_id) == ("video", "movie", "123")
    assert heat["thumbnail"] == "/thumb/123", "thumbnails of the player are kept"


async def test_kodi_off_shows_media_sources_and_hint(hass, home, hass_ws_client):
    await setup_room(hass)
    ms_root = BrowseMedia(
        media_class=MediaClass.DIRECTORY, media_content_id="media-source://", media_content_type="",
        title="Media", can_play=False, can_expand=True,
        children=[BrowseMedia(media_class=MediaClass.DIRECTORY, media_content_id="media-source://jellyfin",
                              media_content_type="", title="Jellyfin", can_play=False, can_expand=True)],
    )
    with patch(
        "custom_components.better_media_player.browser.media_source.async_browse_media", return_value=ms_root
    ):
        ws = await hass_ws_client(hass)
        root = await _browse(hass, ws)
        assert "Queue: Kodi" not in _titles(root), "Kodi off: no queue"
        kodi = await _browse(hass, ws, "directory", _child(root, "Kodi")["media_content_id"])
    assert kodi["title"] == "Kodi"
    assert _titles(kodi)[0].startswith("Kodi is off")
    assert "Jellyfin" in _titles(kodi)
    assert parse_id(_child(kodi, "Jellyfin")["media_content_id"])[2:] == ("", "media-source://jellyfin")


async def test_music_items_offer_choices(hass, home, hass_ws_client):
    await setup_room(hass)
    ws = await hass_ws_client(hass)
    root = await _browse(hass, ws)
    music = await _browse(hass, ws, "directory", _child(root, "Office Player")["media_content_id"])
    playlists = await _browse(hass, ws, "playlists", _child(music, "Playlists")["media_content_id"])
    track = _child(playlists, "All Summer Long")
    assert track["can_expand"] and track["can_play"], "track opens the choices, still playable"
    assert not _child(playlists, "Road trip")["media_content_id"].startswith("better_media_player://choose"), \
        "folders (playlists) are not wrapped"
    choices = await _browse(hass, ws, track["media_content_type"], track["media_content_id"])
    assert _titles(choices) == ["▶ Play now", "⏭ Play next", "➕ Add to queue", "⟲ Replace queue"]


# --------------------------------------------------------------------- play


async def test_movie_from_browser_powers_up_then_plays(hass, home):
    """Kodi asleep: TV on, wake Kodi, wait, speakers, then play on Kodi."""
    from custom_components.better_media_player.browser import make_id

    home.kodi_boots = True
    await setup_room(hass)
    item = {"media_content_type": "movie", "media_content_id": make_id("item", "video", "movie", "123")}
    await _play(hass, item)
    assert home.on(TV) and home.on(SPEAKERS) and home.on(BASS)
    assert _plays(home, KODI) == [{"media_content_type": "movie", "media_content_id": "123"}]
    order = [c[2] for c in home.calls if c[2] in (TV, KODI, SPEAKERS) and c[1] in ("turn_on", "play_media")]
    assert order == [TV, KODI, SPEAKERS, KODI], "play only after the power sequence"


async def test_media_source_item_played_on_kodi(hass, home):
    from custom_components.better_media_player.browser import make_id

    home.kodi_boots = True
    await setup_room(hass)
    item = {"media_content_type": "video/mp4",
            "media_content_id": make_id("media", "video", "video/mp4", "media-source://jellyfin/abc")}
    await _play(hass, item)
    assert _plays(home, KODI)[0]["media_content_id"] == "media-source://jellyfin/abc"


async def test_start_failure_raises_and_does_not_play(hass, home, freezer):
    from custom_components.better_media_player.browser import make_id

    await setup_room(hass)  # Kodi never boots
    item = {"media_content_type": "movie", "media_content_id": make_id("item", "video", "movie", "123")}
    task = hass.async_create_background_task(
        hass.services.async_call(
            "media_player", "play_media",
            {"entity_id": ROOM, "media_content_type": "movie", "media_content_id": item["media_content_id"]},
            blocking=True,
        ),
        "test play",
    )
    # the waiting service call is a tracked task, so time is moved without
    # async_block_till_done (which would wait for that very call)
    for _ in range(12):
        freezer.tick(timedelta(seconds=10))
        async_fire_time_changed(hass, dt_util.utcnow())
        for _ in range(20):
            await asyncio.sleep(0)
    with pytest.raises(HomeAssistantError):
        await asyncio.wait_for(task, 5)
    await hass.async_block_till_done()
    assert _plays(home, KODI) == []
    assert not home.on(TV)


async def test_play_now_music_stops_kodi(hass, home):
    from custom_components.better_media_player.browser import make_id

    await setup_room(hass)
    home.kodi("playing")
    await hass.async_block_till_done()
    item = {"media_content_type": "track", "media_content_id": make_id("enqueue-play", "audio", "track", "library://track/9")}
    await _play(hass, item)
    assert ("media_player", "turn_off", KODI) in home.calls
    assert _plays(home, MUSIC) == [
        {"media_content_type": "track", "media_content_id": "library://track/9", "enqueue": "play"}
    ]


async def test_enqueue_next_changes_nothing_else(hass, home):
    from custom_components.better_media_player.browser import make_id

    await setup_room(hass)
    home.kodi("playing")
    await hass.async_block_till_done()
    home.calls.clear()
    item = {"media_content_type": "track", "media_content_id": make_id("enqueue-next", "audio", "track", "library://track/9")}
    await _play(hass, item)
    assert ("media_player", "turn_off", KODI) not in home.calls
    assert _plays(home, MUSIC)[0]["enqueue"] == "next"


async def test_plain_ids_still_go_to_the_active_player(hass, home):
    await setup_room(hass)
    home.music("playing")
    await hass.async_block_till_done()
    await _play(hass, {"media_content_type": "music", "media_content_id": "library://album/5"})
    assert _plays(home, MUSIC)[0]["media_content_id"] == "library://album/5"


async def test_grouped_music_player_is_left_to_music_assistant(hass, home):
    """Music Assistant releases a grouped player itself when it gets play_media."""
    from custom_components.better_media_player.browser import make_id

    await setup_room(hass)
    home.group("playing", [MUSIC], GROUP)
    await hass.async_block_till_done()
    item = {"media_content_type": "track", "media_content_id": make_id("item", "audio", "track", "library://track/9")}
    await _play(hass, item)
    assert _plays(home, MUSIC), "played on the room player"
    assert ("media_player", "unjoin", MUSIC) not in home.calls


# ------------------------------------------------------------------- search


async def test_search_goes_to_players_that_can_search(hass, home):
    await setup_room(hass)
    response = await hass.services.async_call(
        "media_player", "search_media", {"entity_id": ROOM, "search_query": "kid rock"},
        blocking=True, return_response=True,
    )
    result = response[ROOM]
    items = result.result if hasattr(result, "result") else result["result"]
    assert len(items) == 1
    item = items[0] if isinstance(items[0], dict) else items[0].as_dict(parent=False)
    assert parse_id(item["media_content_id"])[1:] == ("audio", "track", "library://track/9")
    searched = [c[2] for c in home.calls if c[1] == "search_media"]
    assert searched == [MUSIC], "Kodi cannot search, so it is not asked"

    await _play(hass, item)
    assert _plays(home, MUSIC)[0]["media_content_id"] == "library://track/9"
    assert home.on(SPEAKERS)


async def test_no_choices_when_player_cannot_enqueue(hass, home, hass_ws_client):
    from .conftest import MA_FEATURES

    await setup_room(hass)
    hass.states.async_set(
        MUSIC, "idle", {"supported_features": MA_FEATURES & ~F.MEDIA_ENQUEUE, "friendly_name": "Office Player"}
    )
    await hass.async_block_till_done()
    ws = await hass_ws_client(hass)
    root = await _browse(hass, ws)
    music = await _browse(hass, ws, "directory", _child(root, "Office Player")["media_content_id"])
    playlists = await _browse(hass, ws, "playlists", _child(music, "Playlists")["media_content_id"])
    track = _child(playlists, "All Summer Long")
    assert not track["can_expand"], "no play next / add without MEDIA_ENQUEUE"
    assert parse_id(track["media_content_id"])[0] == "item"
