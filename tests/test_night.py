"""Night mode: no subwoofer, shorter video times, volume limit, fade back; in groups; its event."""

from __future__ import annotations

import asyncio
import statistics

import pytest
from pytest_homeassistant_custom_component.common import async_capture_events

from homeassistant.components import automation
from homeassistant.core import callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component

from custom_components.better_media_player.const import DOMAIN, EVENT_NIGHT_MODE, EVENT_SOURCE_CHANGED
from custom_components.better_media_player.hub import get_hub

from .conftest import (
    BASS,
    FOLLOW,
    GROUP,
    GROUPS,
    KITCHEN,
    KITCHEN_OCC,
    KODI,
    LIVING_OCC,
    MUSIC,
    SPEAKERS,
    TV,
    setup_room,
    start_follow,
)
from .helpers import NIGHT, ROOM, STATUS, count_calls, music_at, set_night, tick, vol, volume_sets

KNIGHT = "switch.kitchen_night_mode"


# ------------------------------------------------------------------ subwoofer


async def test_night_on_cuts_bass_at_once(hass, home, freezer):
    await music_at(hass, home, freezer, 0.5)
    await set_night(hass)
    await tick(hass, freezer, 3)
    assert not home.on(BASS)  # no run-down
    assert home.on(SPEAKERS)
    assert hass.states.get(NIGHT).state == "on"
    assert hass.states.get(ROOM).attributes["night_mode"] is True


async def test_night_off_brings_bass_back(hass, home, freezer):
    await music_at(hass, home, freezer, 0.5)
    await set_night(hass)
    await tick(hass, freezer, 3)
    await set_night(hass, False)
    await tick(hass, freezer, 3)
    assert home.on(BASS)


async def test_start_at_night_without_bass(hass, home, freezer):
    await setup_room(hass)
    await set_night(hass)
    home.music("playing", volume_level=0.2)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert home.on(SPEAKERS)
    assert count_calls(home, BASS, "turn_on") == 0


async def test_video_at_night_turns_tv_on_once(hass, home, freezer):
    entry = await setup_room(hass)
    await set_night(hass)
    home.kodi_boots = True
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert home.on(TV) and home.on(SPEAKERS)
    assert not home.on(BASS)
    # the room counts as powered without the subwoofer: no new power sequence
    await entry.runtime_data.async_play_on("video", {"media_content_type": "movie", "media_content_id": "1"})
    await hass.async_block_till_done()
    assert count_calls(home, TV, "turn_on") == 1
    assert count_calls(home, KODI, "turn_on") == 1


async def test_auto_power_off_keeps_bass(hass, home, freezer):
    await music_at(hass, home, freezer, 0.5)
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.living_room_automatic_power"}, blocking=True
    )
    await set_night(hass)
    await tick(hass, freezer, 3)
    assert home.on(BASS)


async def test_follow_prewarm_without_bass(hass, house, freezer):
    await hass.services.async_call("switch", "turn_on", {"entity_id": NIGHT}, blocking=True)
    hass.states.async_set(LIVING_OCC, "on")
    await hass.async_block_till_done()
    await start_follow(hass, house)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert house.on(SPEAKERS)
    assert count_calls(house, BASS, "turn_on") == 0


# ---------------------------------------------------------------- video times


async def test_night_video_idle_timeout(hass, home, freezer):
    await setup_room(hass)
    await set_night(hass)
    home.kodi("idle")  # Kodi sits in its menu
    await hass.async_block_till_done()
    await tick(hass, freezer, 6 * 60, step=10)
    assert hass.states.get(KODI).state == "off"  # 5 min instead of 30


async def test_night_display_run_down(hass, home, freezer):
    await setup_room(hass)
    home.kodi("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert home.on(TV)
    await set_night(hass)
    home.kodi("off")
    await hass.async_block_till_done()
    await tick(hass, freezer, 150, step=10)
    # 1 min instead of 10 (the display still goes after the speakers, 2 min)
    assert not home.on(TV)


# ---------------------------------------------------------------- volume limit


async def test_limit_on_switch_and_on_raise(hass, home, freezer):
    await music_at(hass, home, freezer, 0.5)
    await set_night(hass)
    assert vol(hass) == 0.3
    home.music("playing", volume_level=0.6)  # someone turns it up in the app
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert vol(hass) == 0.3
    assert hass.states.get(ROOM).attributes["room_volume"] == 0.3  # what is heard
    home.music("playing", volume_level=0.2)  # lower is fine
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert vol(hass) == 0.2
    assert hass.states.get(ROOM).attributes["room_volume"] == 0.2


async def test_limit_on_room_player_volume(hass, home, freezer):
    await music_at(hass, home, freezer, 0.2)
    await set_night(hass)
    await hass.services.async_call(
        "media_player", "volume_set", {"entity_id": ROOM, "volume_level": 0.9}, blocking=True
    )
    await hass.async_block_till_done()
    assert vol(hass) == 0.3
    assert max(volume_sets(home, MUSIC)) <= 0.3  # limited before it is sent, never loud


async def test_limit_for_video_player_too(hass, home, freezer):
    await music_at(hass, home, freezer, 0.5)
    await set_night(hass)
    home.music("idle")
    home.kodi("playing", volume_level=0.8)  # cast to Kodi
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert vol(hass, KODI) == 0.3


async def test_limit_zero_means_none(hass, home, freezer):
    await music_at(hass, home, freezer, 0.5, night_max_volume=0)
    await set_night(hass)
    home.music("playing", volume_level=0.9)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert vol(hass) == 0.9


async def test_lowering_the_limit_holds_at_once(hass, home, freezer):
    await music_at(hass, home, freezer, 0.5)
    await set_night(hass)
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.living_room_night_maximum_volume", "value": 15}, blocking=True
    )
    await hass.async_block_till_done()
    assert vol(hass) == 0.15


# ------------------------------------------------------------------ fade back


async def test_night_off_fades_back(hass, home, freezer):
    await music_at(hass, home, freezer, 0.5)
    await set_night(hass)
    assert vol(hass) == 0.3
    await set_night(hass, False)
    await tick(hass, freezer, 60, step=5)
    assert 0.35 <= vol(hass) <= 0.45  # half way (2 min)
    await tick(hass, freezer, 70, step=5)
    assert vol(hass) == 0.5


async def test_change_during_fade_back_stops_it(hass, home, freezer):
    await music_at(hass, home, freezer, 0.5)
    await set_night(hass)
    await set_night(hass, False)
    await tick(hass, freezer, 30, step=5)
    home.music("playing", volume_level=0.25)
    await hass.async_block_till_done()
    await tick(hass, freezer, 120, step=5)
    assert vol(hass) == 0.25
    assert hass.states.get(ROOM).attributes["room_volume"] == 0.25


async def test_night_off_while_paused_sets_at_once(hass, home, freezer):
    await music_at(hass, home, freezer, 0.5)
    await set_night(hass)
    home.music("paused")
    await hass.async_block_till_done()
    await set_night(hass, False)
    await hass.async_block_till_done()
    assert vol(hass) == 0.5


async def test_room_volume_taken_along_limited(hass, home, freezer):
    await music_at(hass, home, freezer, 0.5)
    home.music("idle")
    await hass.async_block_till_done()
    await set_night(hass)
    home.kodi("playing", volume_level=0.1)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert vol(hass, KODI) == 0.3  # room volume 0.5, limited
    assert volume_sets(home, KODI) == [0.3]
    assert hass.states.get(ROOM).attributes["room_volume"] == 0.5


# ------------------------------------------------------------- switch, extra


async def test_night_mode_survives_reload(hass, home, freezer):
    entry = await setup_room(hass)
    await set_night(hass)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(NIGHT).state == "on"


async def test_extra_night_mode_switches_and_is_not_passed_on(hass, home, freezer):
    await music_at(hass, home, freezer, 0.2)
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
    await music_at(hass, home, freezer, 0.2)
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
    await start_follow(hass, house)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)


async def test_follow_join_volume_limited_and_not_remembered(hass, house, freezer):
    await _follow(hass, house, freezer, 0.6)
    await hass.services.async_call("switch", "turn_on", {"entity_id": NIGHT}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert MUSIC in house.members(FOLLOW)
    assert vol(hass) == 0.3  # 60 % - 10 offset, limited to 30 %
    assert max(volume_sets(house, MUSIC)) <= 0.3
    assert get_hub(hass).data["volumes"][FOLLOW] == 60


async def test_group_limited_only_when_all_rooms_at_night(hass, house, freezer):
    await _follow(hass, house, freezer, 0.6)
    await hass.services.async_call("switch", "turn_on", {"entity_id": NIGHT}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert set(house.members(FOLLOW)) == {MUSIC, KITCHEN}
    house.set(FOLLOW, "playing", volume_level=0.55)
    await hass.async_block_till_done()
    await tick(hass, freezer, 12, step=4)
    assert vol(hass, FOLLOW) == 0.55  # the kitchen is not in night mode

    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.kitchen_night_mode"}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 12, step=4)
    assert vol(hass, FOLLOW) == 0.3

    # the kitchen leaves the night mode: its player fades back to follow me's
    # volume; the group is not raised (the living room is still at night)
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.kitchen_night_mode"}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 150, step=5)
    assert vol(hass, KITCHEN) == pytest.approx(0.6, abs=0.01)
    assert vol(hass, MUSIC) == 0.3
    assert GROUP  # the 'always' group plays no part here


async def _follow_at_night(hass, house, freezer, kitchen_night=True):
    hass.states.async_set(LIVING_OCC, "on")
    hass.states.async_set(KITCHEN_OCC, "on")
    await hass.async_block_till_done()
    await start_follow(hass, house)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    await set_night(hass, True, entity_id=NIGHT)
    if kitchen_night:
        await set_night(hass, True, entity_id=KNIGHT)
    await tick(hass, freezer, 3)


# the room leaves the night mode: its member gets follow me's volume back
async def test_single_room_member_fades_back(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    await hass.async_block_till_done()
    await start_follow(hass, house)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    await set_night(hass, True, entity_id=NIGHT)
    assert house.members(FOLLOW) == [MUSIC]
    assert vol(hass) == 0.3
    n = len(volume_sets(house, MUSIC))
    living = next(e for e in hass.config_entries.async_entries(DOMAIN) if e.title == "Living Room")
    living.runtime_data.data["room_volume"] = 0.4  # the room's own volume plays no part in the group
    await set_night(hass, False, entity_id=NIGHT)
    await tick(hass, freezer, 60, step=5)
    assert 0.3 < vol(hass) < 0.5  # fading
    await tick(hass, freezer, 90, step=5)
    assert get_hub(hass).data["volumes"][FOLLOW] == 60  # never remembered limited
    assert vol(hass) == 0.5  # 60 % - 10 offset
    steps = volume_sets(house, MUSIC)[n:]
    assert steps == sorted(steps)  # one ramp only: the hub's (not also the room's)
    

async def test_both_rooms_leave_together(hass, house, freezer):
    await _follow_at_night(hass, house, freezer)
    await set_night(hass, False, entity_id=NIGHT)
    await set_night(hass, False, entity_id=KNIGHT)
    await tick(hass, freezer, 150, step=5)
    assert get_hub(hass).data["volumes"][FOLLOW] == 60
    assert vol(hass, MUSIC) == 0.5
    assert vol(hass, KITCHEN) == 0.6


async def test_paused_group_member_back_at_once(hass, house, freezer):
    await _follow_at_night(hass, house, freezer)
    house.group("paused", house.members(FOLLOW), FOLLOW)
    await hass.async_block_till_done()
    await set_night(hass, False, entity_id=KNIGHT)
    await tick(hass, freezer, 6, step=2)
    assert vol(hass, KITCHEN) == 0.6
    assert vol(hass, MUSIC) == 0.3  # still in night mode


async def test_other_group_member_back(hass, house, freezer):
    house.group("playing", [MUSIC, KITCHEN], GROUP, volume_level=0.6)
    house.music("playing", volume_level=0.6)
    house.kitchen("playing", volume_level=0.6)
    await hass.async_block_till_done()
    await tick(hass, freezer, 12, step=4)  # the group's volume while nobody limits it
    await set_night(hass, True, entity_id=NIGHT)
    await set_night(hass, True, entity_id=KNIGHT)
    await tick(hass, freezer, 12, step=4)
    assert vol(hass, GROUP) == 0.3
    await set_night(hass, False, entity_id=KNIGHT)
    await tick(hass, freezer, 150, step=5)
    assert vol(hass, KITCHEN) == 0.6


def _ma_like(house):
    """Group volume = members' average; a group volume_set moves every member by the delta."""
    orig = house.handle

    def handle(domain, service, entity_id, data, context):
        if domain == "media_player" and service == "volume_set":
            if entity_id in GROUPS:
                members = house.members(entity_id)
                old = house.hass.states.get(entity_id).attributes.get("volume_level") or 0
                delta = data["volume_level"] - old
                house.calls.append((domain, service, entity_id))
                house.played.append((service, entity_id, dict(data)))
                for m in members:
                    mv = house.hass.states.get(m).attributes.get("volume_level") or 0
                    house.set(m, house.hass.states.get(m).state, volume_level=round(min(1, max(0, mv + delta)), 3))
                house.set(entity_id, house.hass.states.get(entity_id).state, volume_level=data["volume_level"])
                return True
            r = orig(domain, service, entity_id, data, context)
            for g in GROUPS:
                ms = house.members(g)
                if entity_id in ms:
                    vols = [house.hass.states.get(m).attributes.get("volume_level") or 0 for m in ms]
                    house.set(g, house.hass.states.get(g).state, volume_level=round(statistics.fmean(vols), 3))
            return r
        return orig(domain, service, entity_id, data, context)

    house.handle = handle


async def test_mixed_group_no_ping_pong(hass, house, freezer):
    _ma_like(house)
    hass.states.async_set(LIVING_OCC, "on")
    hass.states.async_set(KITCHEN_OCC, "on")
    await hass.async_block_till_done()
    await start_follow(hass, house)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    await hass.services.async_call("switch", "turn_on", {"entity_id": NIGHT}, blocking=True)
    await hass.services.async_call("switch", "turn_on", {"entity_id": KNIGHT}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    ms = house.members(FOLLOW)
    vols = [vol(hass, m) for m in ms]
    house.set(FOLLOW, "playing", volume_level=round(statistics.fmean(vols), 3))
    await hass.async_block_till_done()
    await tick(hass, freezer, 12, step=4)
    n = len(volume_sets(house, MUSIC))
    await hass.services.async_call("switch", "turn_off", {"entity_id": KNIGHT}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 150, step=5)
    assert vol(hass, KITCHEN) == pytest.approx(0.6, abs=0.01)
    assert vol(hass, MUSIC) == 0.3
    assert len(volume_sets(house, MUSIC)) - n <= 1  # no fight with the living room's limit
    assert volume_sets(house, FOLLOW) == [] or max(volume_sets(house, FOLLOW)) <= 0.3


async def test_night_on_during_start_keeps_bass_off(hass, home, freezer):
    await setup_room(hass, audio_on_wait=5)
    home.music("playing", volume_level=0.2)
    await hass.async_block_till_done()
    await asyncio.sleep(0)
    assert hass.states.get(STATUS).state == "starting"
    await set_night(hass)
    await tick(hass, freezer, 18)
    assert not home.on(BASS)


# night mode switched on during the sleep fade: the fade must go on
async def test_night_on_during_sleep_fade_keeps_fading(hass, home, freezer):
    await music_at(hass, home, freezer, 0.5)
    await hass.services.async_call(
        DOMAIN, "set_sleep_timer", {"entity_id": ROOM, "minutes": 4, "fade_minutes": 3}, blocking=True
    )
    await hass.async_block_till_done()
    await tick(hass, freezer, 90, step=10)  # fade running, ~0.42
    v = vol(hass)
    assert 0.3 < v < 0.5
    await set_night(hass)
    assert vol(hass) == 0.3
    await tick(hass, freezer, 60, step=10)
    assert vol(hass) < 0.25  # still fading down


# restart (reload) in the middle of the fade back
async def test_restart_mid_fade_back_goes_on(hass, home, freezer):
    entry = await music_at(hass, home, freezer, 0.5)
    await set_night(hass)
    await set_night(hass, False)
    await tick(hass, freezer, 40, step=5)
    mid = vol(hass)
    assert 0.3 < mid < 0.5
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await tick(hass, freezer, 200, step=5)
    assert vol(hass) == 0.5


# quick on/off: subwoofer back
async def test_quick_on_off_bass_back(hass, home, freezer):
    await music_at(hass, home, freezer, 0.5)
    await set_night(hass)
    await set_night(hass, False)
    await tick(hass, freezer, 40)
    assert home.on(BASS)


# duplicate commands on night on
async def test_one_volume_command_on_switch(hass, home, freezer):
    await music_at(hass, home, freezer, 0.5)
    n = len(volume_sets(home, MUSIC))
    await set_night(hass)
    await tick(hass, freezer, 10)
    assert volume_sets(home, MUSIC)[n:] == [0.3]


async def test_lower_limit_holds_at_once(hass, home, freezer):
    await music_at(hass, home, freezer, 0.5)
    await set_night(hass)
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.living_room_night_maximum_volume", "value": 15}, blocking=True
    )
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert vol(hass) == 0.15


async def test_raised_while_down_is_limited(hass, home, freezer):
    entry = await music_at(hass, home, freezer, 0.5)
    await set_night(hass)
    await hass.config_entries.async_unload(entry.entry_id)
    home.music("playing", volume_level=0.8)
    await hass.async_block_till_done()
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert vol(hass) == 0.3


def _data(events):
    return [(e.data["night_mode"], e.data["reason"]) for e in events]


async def _room(hass, freezer, **overrides):
    entry = await setup_room(hass, **overrides)
    await tick(hass, freezer, 3)
    return entry


async def _music(hass, home, freezer, state="playing"):
    home.music(state)
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)


async def test_switched_while_the_room_is_on(hass, home, freezer):
    await _room(hass, freezer)
    await _music(hass, home, freezer)
    events = async_capture_events(hass, EVENT_NIGHT_MODE)
    await set_night(hass, True)
    await set_night(hass, False)
    assert _data(events) == [(True, "switched"), (False, "switched")]
    e = events[0].data
    assert (e["entity_id"], e["room"]) == (ROOM, "Living Room")


async def test_switched_while_the_room_is_off_waits_for_the_room(hass, home, freezer):
    await _room(hass, freezer)
    events = async_capture_events(hass, EVENT_NIGHT_MODE)
    await set_night(hass, True)
    assert events == []
    order: list[str] = []
    hass.bus.async_listen(EVENT_SOURCE_CHANGED, callback(lambda e: order.append("source")))
    hass.bus.async_listen(EVENT_NIGHT_MODE, callback(lambda e: order.append(f"night:{home.on(SPEAKERS)}")))
    await _music(hass, home, freezer)
    assert _data(events) == [(True, "room_on")]
    assert order == ["source", "night:True"], "after the source event, with the speakers on"


async def test_every_power_on_at_night(hass, home, freezer):
    await _room(hass, freezer)
    await set_night(hass, True)
    events = async_capture_events(hass, EVENT_NIGHT_MODE)
    for _ in range(2):
        await _music(hass, home, freezer)
        await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
        await tick(hass, freezer, 300, step=10)
    assert _data(events) == [(True, "room_on"), (True, "room_on")]


async def test_off_while_the_room_was_off_is_told_once(hass, home, freezer):
    await _room(hass, freezer)
    await _music(hass, home, freezer)
    await set_night(hass, True)
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await tick(hass, freezer, 300, step=10)
    events = async_capture_events(hass, EVENT_NIGHT_MODE)
    await set_night(hass, False)
    assert events == []
    await _music(hass, home, freezer)
    assert _data(events) == [(False, "room_on")]
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await tick(hass, freezer, 300, step=10)
    await _music(hass, home, freezer)
    assert _data(events) == [(False, "room_on")], "by day nothing more"


async def test_switched_during_the_start_comes_after_it(hass, home, freezer):
    await _room(hass, freezer, display_on_wait=5)
    events = async_capture_events(hass, EVENT_NIGHT_MODE)
    home.kodi_boots = True
    hass.async_create_task(
        hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    )
    await tick(hass, freezer, 2)
    await set_night(hass, True)
    assert events == []
    await tick(hass, freezer, 10)
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
    await tick(hass, freezer, 3)
    assert _data(events) == [(True, "switched")]


async def test_a_restart_with_the_room_on_tells_nothing(hass, home, freezer):
    entry = await _room(hass, freezer)
    await _music(hass, home, freezer)
    await set_night(hass, True)
    events = async_capture_events(hass, EVENT_NIGHT_MODE)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
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
    await set_night(hass, True)
    await set_night(hass, False)
    await hass.async_block_till_done()
    assert hits == ["night_mode_on", "night_mode_off"]


# --------------------------------------------------------------- event: timing


async def test_switched_during_a_source_switch_comes_when_it_is_done(hass, home, freezer):
    await _room(hass, freezer, display_on_wait=8)
    await _music(hass, home, freezer)
    events = async_capture_events(hass, EVENT_NIGHT_MODE)
    home.kodi_boots = True
    hass.async_create_task(
        hass.services.async_call("media_player", "select_source", {"entity_id": ROOM, "source": "Kodi"}, blocking=True)
    )
    await tick(hass, freezer, 2)
    await set_night(hass, True)
    assert events == [], "the TV is not up yet"
    await tick(hass, freezer, 15)
    assert _data(events) == [(True, "source")]
    assert hass.states.get("media_player.kodi").state != "off"


async def test_the_tv_after_music_is_told_at_night(hass, home, freezer):
    await _room(hass, freezer)
    await _music(hass, home, freezer)
    await set_night(hass, True)
    events = async_capture_events(hass, EVENT_NIGHT_MODE)
    home.kodi_boots = True
    await hass.services.async_call("media_player", "select_source", {"entity_id": ROOM, "source": "Kodi"}, blocking=True)
    await tick(hass, freezer, 10)
    assert _data(events) == [(True, "source")]


async def test_an_update_with_the_room_on_at_night_tells_nothing(hass, home, freezer):
    entry = await _room(hass, freezer)
    await _music(hass, home, freezer)
    await set_night(hass, True)
    entry.runtime_data.data.pop("night_announced")  # (stored before this version)
    events = async_capture_events(hass, EVENT_NIGHT_MODE)
    await tick(hass, freezer, 20)
    assert events == []
