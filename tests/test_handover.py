"""Handover: what plays in one room continues in another."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.better_media_player.const import (
    CONF_DISPLAY,
    CONF_ENTRY_TYPE,
    CONF_VIDEO_PLAYER,
    DEFAULTS,
    DOMAIN,
    ENTRY_TYPE_ROOM,
)

from .conftest import (
    BED_TV,
    FOLLOW,
    KITCHEN,
    KITCHEN_OCC,
    KITCHEN_SPK,
    KODI,
    KODI2,
    KODI_FEATURES,
    LIVING_OCC,
    MUSIC,
    SPEAKERS,
    TV,
    setup_hub,
    setup_kitchen,
    setup_room,
)

LIVING = "media_player.wohnzimmer"
KROOM = "media_player.kitchen"
BEDROOM = "media_player.bedroom"
SELECT = "select.wohnzimmer_hand_over_to"


@pytest.fixture
def systems(hass):
    """The players come from Music Assistant and Better Kodi (registered first)."""
    reg = er.async_get(hass)
    reg.async_get_or_create("media_player", "music_assistant", "office", suggested_object_id="office_player")
    reg.async_get_or_create("media_player", "music_assistant", "kitchen", suggested_object_id="kitchen_player")
    reg.async_get_or_create("media_player", "better_kodi", "kodi1", suggested_object_id="kodi")
    reg.async_get_or_create("media_player", "better_kodi", "kodi2", suggested_object_id="kodi_bedroom")


async def _tick(hass, freezer, seconds: float = 3, step: float = 1) -> None:
    left = seconds
    while left > 0:
        freezer.tick(timedelta(seconds=min(step, left)))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        left -= step


async def _bedroom(hass, home):
    home.set(KODI2, "off", supported_features=KODI_FEATURES)
    hass.states.async_set(BED_TV, "off")
    entry = MockConfigEntry(
        domain=DOMAIN, title="Bedroom", data={CONF_ENTRY_TYPE: ENTRY_TYPE_ROOM},
        options={**DEFAULTS, CONF_VIDEO_PLAYER: KODI2, CONF_DISPLAY: [BED_TV]}, version=3,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.fixture
async def rooms(hass, systems, home):
    hass.states.async_set(KITCHEN_SPK, "off")
    hass.states.async_set(KITCHEN_OCC, "off")
    home.kitchen("idle")
    home.kodi_boots = True
    await setup_room(hass)
    await setup_kitchen(hass)
    await _bedroom(hass, home)
    return home


async def _handover(hass, source_room, destination, **data):
    await hass.services.async_call(
        DOMAIN, "handover", {"entity_id": source_room, "destination": destination, **data}, blocking=True
    )
    await hass.async_block_till_done()


async def _music(hass, home, freezer):
    home.music("playing", volume_level=0.3, media_content_id="t", media_position=42.0,
               media_position_updated_at=dt_util.utcnow())
    await hass.async_block_till_done()
    await _tick(hass, freezer)


async def _video(hass, home, freezer):
    await hass.services.async_call("media_player", "turn_on", {"entity_id": LIVING}, blocking=True)
    await hass.async_block_till_done()
    home.kodi("playing", media_title="Heat")
    await hass.async_block_till_done()
    await _tick(hass, freezer)


# ------------------------------------------------------------------ music


async def test_music_goes_to_the_kitchen(hass, rooms, freezer):
    home = rooms
    await _music(hass, home, freezer)
    await _handover(hass, LIVING, KROOM)
    assert home.on(KITCHEN_SPK), "the kitchen powered up first"
    assert home.transferred[-1] == (MUSIC, KITCHEN)
    assert hass.states.get(KITCHEN).state == "playing"
    await _tick(hass, freezer, 200, step=10)
    assert not home.on(SPEAKERS), "the living room went off"


async def test_music_cannot_play_on_while_moving(hass, rooms, freezer):
    await _music(hass, rooms, freezer)
    with pytest.raises(ServiceValidationError) as err:
        await _handover(hass, LIVING, KROOM, keep_source_playing=True)
    assert err.value.translation_key == "handover_keep_not_supported"


async def test_follow_me_ends_into_the_destination(hass, rooms, freezer):
    from custom_components.better_media_player.hub import get_hub

    home = rooms
    home.group("idle", [], FOLLOW)
    await setup_hub(hass)
    living = next(e for e in hass.config_entries.async_entries(DOMAIN) if e.title == "Wohnzimmer")
    hass.config_entries.async_update_entry(living, options={**living.options, "occupancy": [LIVING_OCC]})
    await hass.async_block_till_done()
    hass.states.async_set(LIVING_OCC, "on")
    await _music(hass, home, freezer)
    await hass.services.async_call(DOMAIN, "start_follow", {"entity_id": LIVING}, blocking=True)
    await hass.async_block_till_done()
    assert get_hub(hass).active and MUSIC in home.members(FOLLOW)
    await _handover(hass, LIVING, KROOM)
    assert not get_hub(hass).active, "follow me ended"
    assert home.transferred[-1] == (FOLLOW, KITCHEN), "the music went to the kitchen"
    assert hass.states.get(KITCHEN).state == "playing"
    assert home.members(FOLLOW) == []


# ------------------------------------------------------------------ video


async def test_video_goes_to_the_bedroom(hass, rooms, freezer):
    home = rooms
    await _video(hass, home, freezer)
    await _handover(hass, LIVING, BEDROOM)
    assert home.on(BED_TV), "the bedroom's TV first"
    assert home.handovers == [(KODI, KODI2, False)]
    assert hass.states.get(KODI2).state == "playing"
    await _tick(hass, freezer, 30, step=10)
    assert not home.on(TV) and hass.states.get(KODI).state == "off", "the living room went off"


async def test_video_can_play_on(hass, rooms, freezer):
    home = rooms
    await _video(hass, home, freezer)
    await _handover(hass, LIVING, BEDROOM, keep_source_playing=True)
    assert home.handovers == [(KODI, KODI2, True)]
    assert hass.states.get(KODI).state == "playing" and home.on(TV)


# ------------------------------------------------------------------ refused


@pytest.mark.parametrize(
    ("setup", "destination", "reason"),
    [
        ("music", LIVING, "handover_same_room"),
        ("none", KROOM, "handover_nothing_playing"),
        ("video", KROOM, "handover_no_player"),
        ("music", BEDROOM, "handover_no_player"),
        ("music", "media_player.office_player", "handover_not_a_room"),
    ],
)
async def test_refused(hass, rooms, freezer, setup, destination, reason):
    if setup == "music":
        await _music(hass, rooms, freezer)
    elif setup == "video":
        await _video(hass, rooms, freezer)
    with pytest.raises(ServiceValidationError) as err:
        await _handover(hass, LIVING, destination)
    assert err.value.translation_key == reason


async def test_not_for_a_system_without_handover(hass, home, freezer):
    """Players of no known system (plain backends) cannot hand over."""
    home.kitchen("idle")
    hass.states.async_set(KITCHEN_SPK, "off")
    await setup_room(hass)
    await setup_kitchen(hass)
    await _music(hass, home, freezer)
    with pytest.raises(ServiceValidationError) as err:
        await _handover(hass, LIVING, KROOM)
    assert err.value.translation_key == "handover_not_supported"


# ------------------------------------------------------------------ select


async def test_the_select(hass, rooms, freezer):
    home = rooms
    assert hass.states.get(SELECT).attributes["options"] == []
    await _music(hass, home, freezer)
    assert hass.states.get(SELECT).attributes["options"] == ["Kitchen"], "only where music can go on"
    await hass.services.async_call("select", "select_option", {"entity_id": SELECT, "option": "Kitchen"}, blocking=True)
    await hass.async_block_till_done()
    assert home.transferred[-1] == (MUSIC, KITCHEN)


async def test_destination_playing_something_else_is_replaced(hass, rooms, freezer):
    home = rooms
    await _video(hass, home, freezer)  # living room: Kodi plays
    home.kitchen("playing", volume_level=0.3)
    await hass.async_block_till_done()
    await _tick(hass, freezer)
    await _handover(hass, KROOM, LIVING)
    assert home.transferred[-1] == (KITCHEN, MUSIC)
    assert hass.states.get(KODI).state == "off", "the living room's video stopped for the music"
