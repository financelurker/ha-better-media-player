"""Night mode: no subwoofer, shorter video times, volume limit, fade back."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from custom_components.better_media_player.const import DOMAIN
from custom_components.better_media_player.hub import get_hub

from .conftest import BASS, FOLLOW, GROUP, KITCHEN, KITCHEN_OCC, KODI, LIVING_OCC, MUSIC, SPEAKERS, TV, setup_room

ROOM = "media_player.wohnzimmer"
NIGHT = "switch.wohnzimmer_night_mode"


async def _tick(hass, freezer, seconds: float, step: float = 1) -> None:
    left = seconds
    while left > 0:
        freezer.tick(timedelta(seconds=min(step, left)))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        left -= step


def _vol(hass, entity_id=MUSIC):
    return hass.states.get(entity_id).attributes.get("volume_level")


def _sent(home, entity_id):
    return [d["volume_level"] for s, e, d in home.played if s == "volume_set" and e == entity_id]


def _count(home, entity_id, service):
    return sum(1 for c in home.calls if c[2] == entity_id and c[1] == service)


async def _night(hass, on: bool = True):
    await hass.services.async_call("switch", "turn_on" if on else "turn_off", {"entity_id": NIGHT}, blocking=True)
    await hass.async_block_till_done()


async def _music_at(hass, home, freezer, volume=0.5, **overrides):
    entry = await setup_room(hass, **overrides)
    home.music("playing", volume_level=volume)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert home.on(SPEAKERS) and home.on(BASS)
    return entry


# ------------------------------------------------------------------ subwoofer


async def test_night_on_cuts_bass_at_once(hass, home, freezer):
    await _music_at(hass, home, freezer)
    await _night(hass)
    await _tick(hass, freezer, 3)
    assert not home.on(BASS)  # no run-down
    assert home.on(SPEAKERS)
    assert hass.states.get(NIGHT).state == "on"
    assert hass.states.get(ROOM).attributes["night_mode"] is True


async def test_night_off_brings_bass_back(hass, home, freezer):
    await _music_at(hass, home, freezer)
    await _night(hass)
    await _tick(hass, freezer, 3)
    await _night(hass, False)
    await _tick(hass, freezer, 3)
    assert home.on(BASS)


async def test_start_at_night_without_bass(hass, home, freezer):
    await setup_room(hass)
    await _night(hass)
    home.music("playing", volume_level=0.2)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert home.on(SPEAKERS)
    assert _count(home, BASS, "turn_on") == 0


async def test_video_at_night_turns_tv_on_once(hass, home, freezer):
    entry = await setup_room(hass)
    await _night(hass)
    home.kodi_boots = True
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert home.on(TV) and home.on(SPEAKERS)
    assert not home.on(BASS)
    # the room counts as powered without the subwoofer: no new power sequence
    await entry.runtime_data.async_play_on("video", {"media_content_type": "movie", "media_content_id": "1"})
    await hass.async_block_till_done()
    assert _count(home, TV, "turn_on") == 1
    assert _count(home, KODI, "turn_on") == 1


async def test_auto_power_off_keeps_bass(hass, home, freezer):
    await _music_at(hass, home, freezer)
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.wohnzimmer_automatic_power"}, blocking=True
    )
    await _night(hass)
    await _tick(hass, freezer, 3)
    assert home.on(BASS)


async def test_follow_prewarm_without_bass(hass, house, freezer):
    await hass.services.async_call("switch", "turn_on", {"entity_id": NIGHT}, blocking=True)
    hass.states.async_set(LIVING_OCC, "on")
    await hass.async_block_till_done()
    await hass.services.async_call(DOMAIN, "start_follow", {"volume": 0.6}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert house.on(SPEAKERS)
    assert _count(house, BASS, "turn_on") == 0


# ---------------------------------------------------------------- video times


async def test_night_video_idle_timeout(hass, home, freezer):
    await setup_room(hass)
    await _night(hass)
    home.kodi("idle")  # Kodi sits in its menu
    await hass.async_block_till_done()
    await _tick(hass, freezer, 6 * 60, step=10)
    assert hass.states.get(KODI).state == "off"  # 5 min instead of 30


async def test_night_display_run_down(hass, home, freezer):
    await setup_room(hass)
    home.kodi("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert home.on(TV)
    await _night(hass)
    home.kodi("off")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 150, step=10)
    # 1 min instead of 10 (the display still goes after the speakers, 2 min)
    assert not home.on(TV)


# ---------------------------------------------------------------- volume limit


async def test_limit_on_switch_and_on_raise(hass, home, freezer):
    await _music_at(hass, home, freezer, 0.5)
    await _night(hass)
    assert _vol(hass) == 0.3
    home.music("playing", volume_level=0.6)  # someone turns it up in the app
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert _vol(hass) == 0.3
    assert hass.states.get(ROOM).attributes["room_volume"] == 0.3  # what is heard
    home.music("playing", volume_level=0.2)  # lower is fine
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert _vol(hass) == 0.2
    assert hass.states.get(ROOM).attributes["room_volume"] == 0.2


async def test_limit_on_room_player_volume(hass, home, freezer):
    await _music_at(hass, home, freezer, 0.2)
    await _night(hass)
    await hass.services.async_call(
        "media_player", "volume_set", {"entity_id": ROOM, "volume_level": 0.9}, blocking=True
    )
    await hass.async_block_till_done()
    assert _vol(hass) == 0.3
    assert max(_sent(home, MUSIC)) <= 0.3  # limited before it is sent, never loud


async def test_limit_for_video_player_too(hass, home, freezer):
    await _music_at(hass, home, freezer, 0.5)
    await _night(hass)
    home.music("idle")
    home.kodi("playing", volume_level=0.8)  # cast to Kodi
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert _vol(hass, KODI) == 0.3


async def test_limit_zero_means_none(hass, home, freezer):
    await _music_at(hass, home, freezer, 0.5, night_max_volume=0)
    await _night(hass)
    home.music("playing", volume_level=0.9)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert _vol(hass) == 0.9


async def test_lowering_the_limit_holds_at_once(hass, home, freezer):
    await _music_at(hass, home, freezer, 0.5)
    await _night(hass)
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.wohnzimmer_night_maximum_volume", "value": 15}, blocking=True
    )
    await hass.async_block_till_done()
    assert _vol(hass) == 0.15


# ------------------------------------------------------------------ fade back


async def test_night_off_fades_back(hass, home, freezer):
    await _music_at(hass, home, freezer, 0.5)
    await _night(hass)
    assert _vol(hass) == 0.3
    await _night(hass, False)
    await _tick(hass, freezer, 60, step=5)
    assert 0.35 <= _vol(hass) <= 0.45  # half way (2 min)
    await _tick(hass, freezer, 70, step=5)
    assert _vol(hass) == 0.5


async def test_change_during_fade_back_stops_it(hass, home, freezer):
    await _music_at(hass, home, freezer, 0.5)
    await _night(hass)
    await _night(hass, False)
    await _tick(hass, freezer, 30, step=5)
    home.music("playing", volume_level=0.25)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 120, step=5)
    assert _vol(hass) == 0.25
    assert hass.states.get(ROOM).attributes["room_volume"] == 0.25


async def test_night_off_while_paused_sets_at_once(hass, home, freezer):
    await _music_at(hass, home, freezer, 0.5)
    await _night(hass)
    home.music("paused")
    await hass.async_block_till_done()
    await _night(hass, False)
    await hass.async_block_till_done()
    assert _vol(hass) == 0.5


async def test_room_volume_taken_along_limited(hass, home, freezer):
    await _music_at(hass, home, freezer, 0.5)
    home.music("idle")
    await hass.async_block_till_done()
    await _night(hass)
    home.kodi("playing", volume_level=0.1)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert _vol(hass, KODI) == 0.3  # room volume 0.5, limited
    assert _sent(home, KODI) == [0.3]
    assert hass.states.get(ROOM).attributes["room_volume"] == 0.5


# ------------------------------------------------------------- switch, extra


async def test_night_mode_survives_reload(hass, home, freezer):
    entry = await setup_room(hass)
    await _night(hass)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(NIGHT).state == "on"


async def test_extra_night_mode_switches_and_is_not_passed_on(hass, home, freezer):
    await _music_at(hass, home, freezer, 0.2)
    await hass.services.async_call(
        "media_player",
        "play_media",
        {
            "entity_id": ROOM,
            "media_content_type": "music",
            "media_content_id": "library://radio/1",
            "extra": {"night_mode": True},
        },
        blocking=True,
    )
    await hass.async_block_till_done()
    assert hass.states.get(NIGHT).state == "on"
    sent = [d for s, e, d in home.played if s == "play_media" and e == MUSIC]
    assert sent and "extra" not in sent[-1]


async def test_extra_night_mode_must_be_bool(hass, home, freezer):
    await _music_at(hass, home, freezer, 0.2)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "media_player",
            "play_media",
            {"entity_id": ROOM, "media_content_type": "music", "media_content_id": "x", "extra": {"night_mode": "yes"}},
            blocking=True,
        )


# ---------------------------------------------------------------- follow me


async def _follow(hass, house, freezer, volume=0.6):
    hass.states.async_set(LIVING_OCC, "on")
    hass.states.async_set(KITCHEN_OCC, "on")
    await hass.async_block_till_done()
    await hass.services.async_call(DOMAIN, "start_follow", {"volume": volume}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)


async def test_follow_join_volume_limited_and_not_remembered(hass, house, freezer):
    await hass.services.async_call("switch", "turn_on", {"entity_id": NIGHT}, blocking=True)
    await _follow(hass, house, freezer, 0.6)
    assert MUSIC in house.members(FOLLOW)
    assert _vol(hass) == 0.3  # 60 % - 10 offset, limited to 30 %
    assert max(_sent(house, MUSIC)) <= 0.3
    assert get_hub(hass).data["volumes"][FOLLOW] == 60


async def test_group_limited_only_when_all_rooms_at_night(hass, house, freezer):
    await hass.services.async_call("switch", "turn_on", {"entity_id": NIGHT}, blocking=True)
    await _follow(hass, house, freezer, 0.6)
    assert set(house.members(FOLLOW)) == {MUSIC, KITCHEN}
    house.set(FOLLOW, "playing", volume_level=0.55)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 12, step=4)
    assert _vol(hass, FOLLOW) == 0.55  # the kitchen is not in night mode

    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.kitchen_night_mode"}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 12, step=4)
    assert _vol(hass, FOLLOW) == 0.3

    # the kitchen leaves the night mode: its player fades back to follow me's
    # volume; the group is not raised (the living room is still at night)
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.kitchen_night_mode"}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 150, step=5)
    assert _vol(hass, KITCHEN) == pytest.approx(0.6, abs=0.01)
    assert _vol(hass, MUSIC) == 0.3
    assert GROUP  # the 'always' group plays no part here
