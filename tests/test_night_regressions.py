"""Night mode: regressions from the review."""

from __future__ import annotations

import asyncio
import statistics

import pytest

from custom_components.better_media_player.const import DOMAIN
from custom_components.better_media_player.hub import get_hub

from .conftest import BASS, FOLLOW, GROUP, GROUPS, KITCHEN, KITCHEN_OCC, LIVING_OCC, MUSIC, setup_room, start_follow
from .test_night import NIGHT, ROOM, _music_at, _night, _sent, _tick, _vol

KNIGHT = "switch.kitchen_night_mode"


async def _sw(hass, entity, on):
    await hass.services.async_call("switch", "turn_on" if on else "turn_off", {"entity_id": entity}, blocking=True)
    await hass.async_block_till_done()


async def _follow_at_night(hass, house, freezer, kitchen_night=True):
    hass.states.async_set(LIVING_OCC, "on")
    hass.states.async_set(KITCHEN_OCC, "on")
    await hass.async_block_till_done()
    await start_follow(hass, house)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    await _sw(hass, NIGHT, True)
    if kitchen_night:
        await _sw(hass, KNIGHT, True)
    await _tick(hass, freezer, 3)


# the room leaves the night mode: its member gets follow me's volume back
async def test_single_room_member_fades_back(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    await hass.async_block_till_done()
    await start_follow(hass, house)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    await _sw(hass, NIGHT, True)
    assert house.members(FOLLOW) == [MUSIC]
    assert _vol(hass) == 0.3
    n = len(_sent(house, MUSIC))
    living = next(e for e in hass.config_entries.async_entries(DOMAIN) if e.title == "Wohnzimmer")
    living.runtime_data.data["room_volume"] = 0.4  # the room's own volume plays no part in the group
    await _sw(hass, NIGHT, False)
    await _tick(hass, freezer, 60, step=5)
    assert 0.3 < _vol(hass) < 0.5  # fading
    await _tick(hass, freezer, 90, step=5)
    assert get_hub(hass).data["volumes"][FOLLOW] == 60  # never remembered limited
    assert _vol(hass) == 0.5  # 60 % - 10 offset
    steps = _sent(house, MUSIC)[n:]
    assert steps == sorted(steps)  # one ramp only: the hub's (not also the room's)
    

async def test_both_rooms_leave_together(hass, house, freezer):
    await _follow_at_night(hass, house, freezer)
    await _sw(hass, NIGHT, False)
    await _sw(hass, KNIGHT, False)
    await _tick(hass, freezer, 150, step=5)
    assert get_hub(hass).data["volumes"][FOLLOW] == 60
    assert _vol(hass, MUSIC) == 0.5
    assert _vol(hass, KITCHEN) == 0.6


async def test_paused_group_member_back_at_once(hass, house, freezer):
    await _follow_at_night(hass, house, freezer)
    house.group("paused", house.members(FOLLOW), FOLLOW)
    await hass.async_block_till_done()
    await _sw(hass, KNIGHT, False)
    await _tick(hass, freezer, 6, step=2)
    assert _vol(hass, KITCHEN) == 0.6
    assert _vol(hass, MUSIC) == 0.3  # still in night mode


async def test_other_group_member_back(hass, house, freezer):
    house.group("playing", [MUSIC, KITCHEN], GROUP, volume_level=0.6)
    house.music("playing", volume_level=0.6)
    house.kitchen("playing", volume_level=0.6)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 12, step=4)  # the group's volume while nobody limits it
    await _sw(hass, NIGHT, True)
    await _sw(hass, KNIGHT, True)
    await _tick(hass, freezer, 12, step=4)
    assert _vol(hass, GROUP) == 0.3
    await _sw(hass, KNIGHT, False)
    await _tick(hass, freezer, 150, step=5)
    assert _vol(hass, KITCHEN) == 0.6


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
    await _tick(hass, freezer, 3)
    await hass.services.async_call("switch", "turn_on", {"entity_id": NIGHT}, blocking=True)
    await hass.services.async_call("switch", "turn_on", {"entity_id": KNIGHT}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    ms = house.members(FOLLOW)
    vols = [_vol(hass, m) for m in ms]
    house.set(FOLLOW, "playing", volume_level=round(statistics.fmean(vols), 3))
    await hass.async_block_till_done()
    await _tick(hass, freezer, 12, step=4)
    n = len(_sent(house, MUSIC))
    await hass.services.async_call("switch", "turn_off", {"entity_id": KNIGHT}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 150, step=5)
    assert _vol(hass, KITCHEN) == pytest.approx(0.6, abs=0.01)
    assert _vol(hass, MUSIC) == 0.3
    assert len(_sent(house, MUSIC)) - n <= 1  # no fight with the living room's limit
    assert _sent(house, FOLLOW) == [] or max(_sent(house, FOLLOW)) <= 0.3


async def test_night_on_during_start_keeps_bass_off(hass, home, freezer):
    await setup_room(hass, audio_on_wait=5)
    home.music("playing", volume_level=0.2)
    await hass.async_block_till_done()
    await asyncio.sleep(0)
    ctrl = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    assert ctrl.sequence_running
    await _night(hass)
    await _tick(hass, freezer, 8)
    on_count = sum(1 for c in home.calls if c[2] == BASS and c[1] == "turn_on")
    await _tick(hass, freezer, 10)
    assert not home.on(BASS)


# 6: night mode switched on during the sleep fade: the fade must go on
async def test_night_on_during_sleep_fade_keeps_fading(hass, home, freezer):
    await _music_at(hass, home, freezer, 0.5)
    await hass.services.async_call(
        DOMAIN, "set_sleep_timer", {"entity_id": ROOM, "minutes": 4, "fade_minutes": 3}, blocking=True
    )
    await hass.async_block_till_done()
    await _tick(hass, freezer, 90, step=10)  # fade running, ~0.42
    v = _vol(hass)
    assert 0.3 < v < 0.5
    await _night(hass)
    assert _vol(hass) == 0.3
    await _tick(hass, freezer, 60, step=10)
    ctrl = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    assert _vol(hass) < 0.25  # still fading down


# 7: restart (reload) in the middle of the fade back
async def test_restart_mid_fade_back_goes_on(hass, home, freezer):
    entry = await _music_at(hass, home, freezer, 0.5)
    await _night(hass)
    await _night(hass, False)
    await _tick(hass, freezer, 40, step=5)
    mid = _vol(hass)
    assert 0.3 < mid < 0.5
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 200, step=5)
    assert _vol(hass) == 0.5


# 8: quick on/off: subwoofer back
async def test_quick_on_off_bass_back(hass, home, freezer):
    await _music_at(hass, home, freezer)
    await _night(hass)
    await _night(hass, False)
    await _tick(hass, freezer, 40)
    assert home.on(BASS)


# 9: duplicate commands on night on
async def test_one_volume_command_on_switch(hass, home, freezer):
    await _music_at(hass, home, freezer, 0.5)
    n = len(_sent(home, MUSIC))
    await _night(hass)
    await _tick(hass, freezer, 10)
    assert _sent(home, MUSIC)[n:] == [0.3]


async def test_lower_limit_holds_at_once(hass, home, freezer):
    await _music_at(hass, home, freezer, 0.5)
    await _night(hass)
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.wohnzimmer_night_maximum_volume", "value": 15}, blocking=True
    )
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    assert _vol(hass) == 0.15


async def test_raised_while_down_is_limited(hass, home, freezer):
    entry = await _music_at(hass, home, freezer, 0.5)
    await _night(hass)
    await hass.config_entries.async_unload(entry.entry_id)
    home.music("playing", volume_level=0.8)
    await hass.async_block_till_done()
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    assert _vol(hass) == 0.3
