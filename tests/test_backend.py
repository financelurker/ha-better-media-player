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

ROOM = "media_player.wohnzimmer"


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
    assert MusicAssistant(hass).can_transfer and not MusicBackend(hass).can_transfer
    assert await MusicBackend(hass).transfer("a", "b") is False


async def test_groups(hass, home):
    home.group("playing", [MUSIC], GROUP)
    home.group("idle", [MUSIC], FOLLOW)
    ma = MusicAssistant(hass)
    assert ma.is_group(GROUP) and not ma.is_group(MUSIC) and not MusicBackend(hass).is_group(GROUP)
    assert set(ma.groups()) == {GROUP, FOLLOW}
    assert groups_of(hass, MUSIC) == [GROUP], "playing groups only"
    assert set(groups_of(hass, MUSIC, idle=True)) == {GROUP, FOLLOW}, "also one that does not play"
    home.group("off", [MUSIC], GROUP)
    assert groups_of(hass, MUSIC, idle=True) == [FOLLOW], "groups that are off are left out"


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
