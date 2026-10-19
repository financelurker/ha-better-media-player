"""Room volume: one volume per room, taken along between the players."""

from __future__ import annotations

from datetime import timedelta

from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant import config_entries
from homeassistant.components.media_player import MediaPlayerEntityFeature as F
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util

from custom_components.better_media_player.const import DOMAIN

from .conftest import FOLLOW, GROUP, KODI, LIVING_OCC, MUSIC, setup_room

ROOM = "media_player.wohnzimmer"


async def _tick(hass, freezer, seconds: float) -> None:
    left = seconds
    while left > 0:
        freezer.tick(timedelta(seconds=min(1, left)))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        left -= 1


def _vol(hass, entity_id=MUSIC):
    return hass.states.get(entity_id).attributes.get("volume_level")


def _room_volume(hass):
    return hass.states.get(ROOM).attributes["room_volume"]


def _sets(home, entity_id):
    return [d["volume_level"] for s, e, d in home.played if s == "volume_set" and e == entity_id]


async def _music_at(hass, home, freezer, volume=0.3):
    entry = await setup_room(hass)
    home.music("playing", volume_level=volume)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    return entry


async def test_first_start_learns_room_volume(hass, home, freezer):
    await _music_at(hass, home, freezer, 0.3)
    assert _room_volume(hass) == 0.3
    assert _sets(home, MUSIC) == []  # nothing to take along yet


async def test_change_on_player_is_learned(hass, home, freezer):
    await _music_at(hass, home, freezer)
    home.music("playing", volume_level=0.22)  # e.g. Music Assistant app
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)  # learned after a short moment (group check)
    assert _room_volume(hass) == 0.22


async def test_change_on_room_player_is_learned(hass, home, freezer):
    await _music_at(hass, home, freezer)
    await hass.services.async_call(
        "media_player", "volume_set", {"entity_id": ROOM, "volume_level": 0.4}, blocking=True
    )
    await hass.async_block_till_done()
    assert _vol(hass) == 0.4
    assert _room_volume(hass) == 0.4


async def test_switch_to_video_takes_room_volume(hass, home, freezer):
    await _music_at(hass, home, freezer, 0.25)
    home.kodi("off", volume_level=0.6)  # Kodi still has yesterday's volume
    home.kodi_boots = True
    await hass.services.async_call(
        "media_player", "select_source", {"entity_id": ROOM, "source": "Kodi"}, blocking=True
    )
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert _vol(hass, KODI) == 0.25
    assert _room_volume(hass) == 0.25  # our own set is not a new wish


async def test_cast_to_kodi_takes_room_volume(hass, home, freezer):
    await _music_at(hass, home, freezer, 0.25)
    home.music("idle")
    home.kodi("playing", volume_level=0.6)  # started on Kodi itself
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert _vol(hass, KODI) == 0.25
    assert _sets(home, KODI) == [0.25]  # once


async def test_own_play_now_order(hass, home, freezer):
    """From the room browser: volume first, then play."""
    entry = await _music_at(hass, home, freezer, 0.25)
    home.music("idle", volume_level=0.5)
    await hass.async_block_till_done()
    entry.runtime_data.data["room_volume"] = 0.2
    await entry.runtime_data.async_play_on("audio", {"media_content_type": "track", "media_content_id": "x"})
    await hass.async_block_till_done()
    order = [s for s, e, _ in home.played if e == MUSIC and s in ("volume_set", "play_media")]
    assert order[-2:] == ["volume_set", "play_media"]
    assert _vol(hass) == 0.2


async def test_sleep_fade_is_not_learned(hass, home, freezer):
    await _music_at(hass, home, freezer, 0.5)
    await hass.services.async_call(DOMAIN, "set_sleep_timer", {"entity_id": ROOM, "minutes": 1, "fade_minutes": 1}, blocking=True)
    await _tick(hass, freezer, 40)
    assert _vol(hass) < 0.5
    assert _room_volume(hass) == 0.5


async def test_group_playback_neither_learned_nor_applied(hass, home, freezer):
    await _music_at(hass, home, freezer, 0.3)
    home.music("idle")
    await hass.async_block_till_done()
    home.group("playing", [MUSIC], GROUP)  # started from somewhere else
    home.music("playing", volume_level=0.8)  # the group sets the member's volume
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert _vol(hass) == 0.8
    assert _room_volume(hass) == 0.3
    assert _sets(home, MUSIC) == []

    # leaves the group, plays on its own again: back to the room's volume
    home.group("idle", [], GROUP)
    home.music("idle")
    await hass.async_block_till_done()
    home.music("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert _vol(hass) == 0.3


async def test_follow_join_volume_is_not_learned(hass, house, freezer):
    house.music("playing", volume_level=0.3)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    house.music("idle")
    await hass.async_block_till_done()
    assert _room_volume(hass) == 0.3
    hass.states.async_set(LIVING_OCC, "on")
    await hass.async_block_till_done()
    await hass.services.async_call(DOMAIN, "start_follow", {"volume": 0.6}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert MUSIC in house.members(FOLLOW)
    assert _vol(hass) == 0.5  # 60 % - 10 offset
    assert _room_volume(hass) == 0.3


async def test_room_volume_survives_reload(hass, home, freezer):
    entry = await _music_at(hass, home, freezer, 0.33)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert _room_volume(hass) == 0.33


async def test_player_without_volume_gets_a_note(hass, home):
    home.set("media_player.radio", "idle", supported_features=int(F.PLAY | F.PAUSE), friendly_name="Radio")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "room"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"name": "Bad"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"audio_player": "media_player.radio"})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "volume_note"
    assert result["description_placeholders"] == {"players": "Radio"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "devices"


async def test_player_with_volume_gets_no_note(hass, home):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "room"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"name": "Good"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"audio_player": MUSIC})
    assert result["step_id"] == "devices"
