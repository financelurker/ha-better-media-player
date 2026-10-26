"""What a player's system can do comes from its backend (backend.py)."""

from __future__ import annotations

import pytest

from homeassistant.helpers import entity_registry as er

from custom_components.better_media_player.backend import (
    MusicBackend,
    VideoBackend,
    backend_for,
    groups_of,
    suggested_role,
)
from custom_components.better_media_player.const import SOURCE_AUDIO, SOURCE_VIDEO
from custom_components.better_media_player.kodi import BetterKodi, Kodi
from custom_components.better_media_player.music_assistant import MusicAssistant

from .conftest import FOLLOW, GROUP, KODI, MUSIC, setup_room


def _register(hass, platform, object_id):
    return er.async_get(hass).async_get_or_create("media_player", platform, object_id, suggested_object_id=object_id)


async def test_backend_by_platform(hass, home):
    kodi = _register(hass, "kodi", "kodi_a")
    better = _register(hass, "better_kodi", "kodi_b")
    ma = _register(hass, "music_assistant", "ma_a")
    other = _register(hass, "cast", "tv_cast")
    assert type(backend_for(hass, kodi.entity_id, SOURCE_VIDEO)) is Kodi
    assert type(backend_for(hass, better.entity_id, SOURCE_VIDEO)) is BetterKodi
    assert type(backend_for(hass, ma.entity_id, SOURCE_AUDIO)) is MusicAssistant
    assert type(backend_for(hass, other.entity_id, SOURCE_VIDEO)) is VideoBackend
    assert type(backend_for(hass, other.entity_id, SOURCE_AUDIO)) is MusicBackend
    assert type(backend_for(hass, ma.entity_id, SOURCE_VIDEO)) is VideoBackend, "a music system as video player: the plain role"


async def test_what_the_systems_can(hass, home):
    plain_video, kodi, better = VideoBackend(hass), Kodi(hass), BetterKodi(hass)
    assert not plain_video.can_browse("off") and not kodi.can_browse("off") and kodi.can_browse("idle")
    assert MusicAssistant(hass).can_browse("off") and MusicBackend(hass).can_browse("off")
    assert plain_video.media_sources is not None and MusicAssistant(hass).media_sources is None
    assert kodi.enqueue_mode("better_kodi://enqueue/next/abc") is None
    assert better.enqueue_mode("better_kodi://enqueue/next/abc") == "next"
    assert better.enqueue_mode("better_kodi://enqueue/bogus/abc") is None
    assert not plain_video.room_choices and MusicBackend(hass).room_choices
    assert MusicAssistant(hass).has_queue and not MusicBackend(hass).has_queue
    assert await MusicBackend(hass).transfer("a", "b") is False
    assert not plain_video.lifts_queue and not kodi.lifts_queue and better.lifts_queue
    assert plain_video.is_active("idle") and not MusicBackend(hass).is_active("idle")
    assert MusicBackend(hass).is_active("playing") and not MusicBackend(hass).is_active("paused")


async def test_groups(hass, home):
    home.group("playing", [MUSIC], GROUP)
    home.group("idle", [MUSIC], FOLLOW)
    ma = MusicAssistant(hass)
    assert ma.is_group(GROUP) and not ma.is_group(MUSIC) and not MusicBackend(hass).is_group(GROUP)
    assert set(ma.groups()) == {GROUP, FOLLOW}
    assert groups_of(hass, MUSIC) == [GROUP], "playing groups only"
    assert set(groups_of(hass, MUSIC, idle=True)) == {GROUP}, "it plays (in GROUP): an idle group does not count"
    home.group("off", [MUSIC], GROUP)
    home.music("idle")
    assert groups_of(hass, MUSIC, idle=True) == [FOLLOW], "groups that are off are left out; an idle one counts"


async def test_suggested_role(hass, home):
    reg = er.async_get(hass)
    assert suggested_role(hass, _register(hass, "kodi", "k")) == SOURCE_VIDEO
    assert suggested_role(hass, _register(hass, "better_kodi", "bk")) == SOURCE_VIDEO
    assert suggested_role(hass, _register(hass, "music_assistant", "m")) == SOURCE_AUDIO
    assert suggested_role(hass, _register(hass, "cast", "c")) is None
    group = _register(hass, "music_assistant", "all_players")
    hass.states.async_set(group.entity_id, "idle", {"mass_player_type": "group"})
    assert suggested_role(hass, reg.async_get(group.entity_id)) is None, "a group is no room's own player"


@pytest.fixture
def ma_office(hass):
    """(registered before the fake home sets the states)"""
    _register(hass, "music_assistant", "office_player")


async def test_a_room_takes_its_players_backends(hass, ma_office, home):
    entry = await setup_room(hass)
    ctrl = entry.runtime_data
    assert type(ctrl.sources[SOURCE_AUDIO].backend) is MusicAssistant
    assert type(ctrl.sources[SOURCE_VIDEO].backend) is VideoBackend  # (media_player.kodi of no known system here)
    assert KODI == ctrl.sources[SOURCE_VIDEO].player


async def test_better_kodi_lifts_only_its_queue_entry(hass, home):
    from homeassistant.components.media_player import BrowseMedia, MediaClass

    def item(content_type):
        return BrowseMedia(
            media_class=MediaClass.DIRECTORY, media_content_id=content_type, media_content_type=content_type,
            title=content_type, can_play=False, can_expand=True,
        )

    root = item("directory")
    root.children = [item("movies"), item("better_kodi_queue"), item("tvshows")]
    assert BetterKodi(hass).queue_entry(root).media_content_type == "better_kodi_queue"
    root.children = [item("movies")]
    assert BetterKodi(hass).queue_entry(root) is None
    assert BetterKodi(hass).queue_entry(None) is None


async def test_no_position_watch_after_the_stop(hass, home):
    from custom_components.better_media_player.position import Position

    home.group("idle", [MUSIC], FOLLOW)
    home.music("playing", media_content_id="t", media_position=10.0)
    ma = MusicAssistant(hass)
    ma.async_stop()
    assert await ma.transfer(MUSIC, FOLLOW, position=Position("t", 10.0))
    assert ma._watch is None, "unloaded meanwhile: the track is not set any more"


async def test_a_group_of_any_known_system_is_no_room_player(hass, home):
    from custom_components.better_media_player.backend import is_group

    home.group("idle", [], GROUP)
    assert is_group(hass, GROUP) and not is_group(hass, MUSIC)


async def test_unloading_a_room_stops_its_backends(hass, home):
    from unittest.mock import patch

    entry = await setup_room(hass)
    with patch.object(MusicBackend, "async_stop") as stop_music, patch.object(VideoBackend, "async_stop") as stop_video:
        assert await hass.config_entries.async_unload(entry.entry_id)
    stop_music.assert_called_once()
    stop_video.assert_called_once()
