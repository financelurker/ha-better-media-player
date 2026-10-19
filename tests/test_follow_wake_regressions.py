"""Wake up with follow me: regressions from the review."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.better_media_player.const import DOMAIN, EVENT_WAKE_FINISHED
from custom_components.better_media_player.hub import FollowSession

from .conftest import FOLLOW, KITCHEN, KITCHEN_OCC, LIVING_OCC, MUSIC
from .test_follow_wake import KSTATE, LSTATE, WAKE, _played, _settle, _tick, _vol, _wake


async def test_old_members_of_a_paused_group_leave(hass, house, freezer):
    # last night the group was paused with the kitchen in it; nobody in the kitchen now
    house.group("paused", [KITCHEN], FOLLOW)
    await hass.async_block_till_done()
    await _wake(hass)
    await _tick(hass, freezer, 5 * 60, step=10)
    assert KITCHEN not in house.members(FOLLOW), "empty kitchen plays the alarm"


async def test_snooze_as_only_member_keeps_session_and_held_out(hass, house, freezer):
    hass.states.async_set(KITCHEN_OCC, "on")
    await hass.async_block_till_done()
    await _wake(hass)
    await _tick(hass, freezer, 60)
    assert hass.states.get(KSTATE).state == "held_out"
    await hass.services.async_call(DOMAIN, "snooze", {"entity_id": "media_player.wohnzimmer", "minutes": 2}, blocking=True)
    await _settle(hass)
    await _tick(hass, freezer, 20)
    snooze_state = hass.states.get(LSTATE).state
    await _tick(hass, freezer, 120)
    assert snooze_state == "snoozed", snooze_state
    assert KITCHEN not in house.members(FOLLOW), "held_out lost: kitchen joined though never left"


async def test_wake_volume_is_not_follow_me_volume(hass, house, freezer):
    await _wake(hass)
    await _tick(hass, freezer, 200)
    assert _vol(hass) == 0.4
    house.group("playing", house.members(FOLLOW), FOLLOW, volume_level=0.4)  # MA: members' average
    await hass.async_block_till_done()
    await _tick(hass, freezer, 10)
    hass.states.async_set(KITCHEN_OCC, "on")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 10)
    assert _vol(hass, KITCHEN) == 0.25


async def test_one_unjoin_on_snooze(hass, house, freezer):
    await _wake(hass)
    await _tick(hass, freezer, 20)
    hass.states.async_set(KITCHEN_OCC, "on")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 10)
    house.played.clear()
    # MA slow: the unjoin is reported later
    orig = house.handle
    def slow(domain, service, entity_id, data, ctx):
        if service == "unjoin":
            house.played.append((service, entity_id, dict(data)))
            return True
        return orig(domain, service, entity_id, data, ctx)
    house.handle = slow
    await hass.services.async_call(DOMAIN, "snooze", {"entity_id": "media_player.wohnzimmer", "minutes": 5}, blocking=True)
    await _settle(hass)
    await _tick(hass, freezer, 5)
    n = len(_played(house, MUSIC, "unjoin"))
    assert n == 1


async def test_two_wakes_at_once_with_slow_music_assistant(hass, house, freezer):
    orig = house.handle
    def slow(domain, service, entity_id, data, ctx):
        if service in ("play_media", "media_play") and entity_id == FOLLOW:
            house.played.append((service, entity_id, dict(data)))
            return True  # MA reports "playing" later
        return orig(domain, service, entity_id, data, ctx)
    house.handle = slow
    hass.states.async_set(LIVING_OCC, "on")
    await hass.async_block_till_done()
    payload = dict(media_content_id="library://radio/1", media_content_type="music", follow=True,
                   start_volume=0.05, target_volume=0.4, fade_minutes=2)
    await hass.services.async_call(DOMAIN, "wake", {"entity_id": "media_player.wohnzimmer", **payload}, blocking=True)
    await hass.services.async_call(DOMAIN, "wake", {"entity_id": "media_player.kitchen", **payload,
                                                     "media_content_id": "library://radio/2"}, blocking=True)
    await _settle(hass)
    pm = _played(house, FOLLOW, "play_media")
    assert len(pm) == 1


async def test_restart_with_stopped_group_joins_again(hass, house, freezer):
    await _wake(hass)
    await _tick(hass, freezer, 60)
    # HA (and MA) restart: the group is idle and empty afterwards, session gone
    for e in hass.config_entries.async_entries(DOMAIN):
        await hass.config_entries.async_unload(e.entry_id)
    await hass.async_block_till_done()
    house.group("idle", [], FOLLOW)
    house.music("idle")
    hass.data[DOMAIN]["session"] = FollowSession()
    for e in hass.config_entries.async_entries(DOMAIN):
        await hass.config_entries.async_setup(e.entry_id)
    await _settle(hass)
    await _tick(hass, freezer, 120)
    assert MUSIC in house.members(FOLLOW)


async def test_dropped_wake_room_is_taken_back(hass, house, freezer):
    await _wake(hass)
    await _tick(hass, freezer, 30)
    # MA drops the room from the group (e.g. player reconnect) and reports the join late
    orig = house.handle
    def slow(domain, service, entity_id, data, ctx):
        if service == "join":
            house.played.append((service, entity_id, dict(data)))
            return True
        return orig(domain, service, entity_id, data, ctx)
    house.handle = slow
    house._leave_groups(MUSIC)
    house.music("idle")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 15)
    assert hass.states.get(WAKE).state != "idle"


async def test_group_stopped_by_hand_ends_the_wake(hass, house, freezer):
    finished = async_capture_events(hass, EVENT_WAKE_FINISHED)
    await _wake(hass)
    await _tick(hass, freezer, 30)
    house.group("idle", house.members(FOLLOW), FOLLOW)  # stopped in the app: still a member
    house.music("idle")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 15)
    assert [e.data["reason"] for e in finished] == ["stopped"]
