"""Wake up with follow me: the music follows you out of the wake room."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import async_capture_events

from homeassistant.exceptions import ServiceValidationError

from custom_components.better_media_player.room.browser import KIND_ITEM, make_id
from custom_components.better_media_player.const import DOMAIN, EVENT_WAKE_FINISHED
from custom_components.better_media_player.follow import get_hub
from custom_components.better_media_player.follow.manager import FollowSession

from .conftest import FOLLOW, KITCHEN, KITCHEN_OCC, LIVING_OCC, MUSIC, SPEAKERS, setup_hub, setup_room
from .helpers import KROOM, ROOM, WAKE, set_setting, settle, settle_held, tick, tick_held, vol

LSTATE = "sensor.living_room_follow"
KSTATE = "sensor.kitchen_follow"


def _played(house, entity_id, service):
    return [d for s, e, d in house.played if s == service and e == entity_id]


async def _wake(hass, entity_id=ROOM, **data):
    payload = {
        "entity_id": entity_id,
        "media_content_id": "library://radio/1",
        "media_content_type": "music",
        "follow": True,
        "start_volume": 0.05,
        "target_volume": 0.4,
        "fade_minutes": 2,
        **data,
    }
    await hass.services.async_call(DOMAIN, "wake", payload, blocking=True)
    await settle(hass)


async def test_wake_room_joins_and_fades(hass, house, freezer):
    await _wake(hass)
    assert MUSIC in house.members(FOLLOW)
    assert _played(house, FOLLOW, "play_media")[-1]["media_content_id"] == "library://radio/1"
    assert hass.states.get(LSTATE).state == "waking"
    assert hass.states.get(WAKE).state == "fading"
    assert vol(hass) == 0.05
    assert [d["volume_level"] for d in _played(house, MUSIC, "volume_set")][0] == 0.05  # quiet from the join
    await tick(hass, freezer, 130)
    assert vol(hass) == 0.4
    assert house.on(SPEAKERS)
    assert get_hub(hass).data["volumes"].get(FOLLOW, 25) == 25  # the fade is not follow me's volume


async def test_occupied_rooms_are_held_out(hass, house, freezer):
    hass.states.async_set(KITCHEN_OCC, "on")  # the partner is already in the kitchen
    await hass.async_block_till_done()
    await _wake(hass)
    await tick(hass, freezer, 10)
    assert KITCHEN not in house.members(FOLLOW)
    assert hass.states.get(KSTATE).state == "held_out"
    hass.states.async_set(KITCHEN_OCC, "off")
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    hass.states.async_set(KITCHEN_OCC, "on")  # comes in again
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    assert KITCHEN in house.members(FOLLOW)
    assert vol(hass, KITCHEN) == 0.25  # follow me's volume, not the wake's


async def test_rooms_joining_later_get_follow_volume(hass, house, freezer):
    await _wake(hass)
    await tick(hass, freezer, 20)
    hass.states.async_set(KITCHEN_OCC, "on")
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    assert KITCHEN in house.members(FOLLOW)
    assert vol(hass, KITCHEN) == 0.25
    assert vol(hass) < 0.25  # the wake room still fades


async def test_leaving_the_wake_room_ends_the_wake(hass, house, freezer):
    finished = async_capture_events(hass, EVENT_WAKE_FINISHED)
    await _wake(hass)
    hass.states.async_set(LIVING_OCC, "on")  # getting up
    await hass.async_block_till_done()
    await tick(hass, freezer, 20)
    hass.states.async_set(KITCHEN_OCC, "on")
    hass.states.async_set(LIVING_OCC, "off")  # ... and off to the kitchen
    await hass.async_block_till_done()
    await tick(hass, freezer, 60)
    assert MUSIC in house.members(FOLLOW)  # presence run-down (2 min)
    await tick(hass, freezer, 80)
    assert [e.data["reason"] for e in finished] == ["left"]
    assert MUSIC not in house.members(FOLLOW)
    assert KITCHEN in house.members(FOLLOW)  # the music followed


async def test_sleeper_not_seen_stays(hass, house, freezer):
    """Motion sensors miss sleepers: presence elsewhere does not end the wake room."""
    finished = async_capture_events(hass, EVENT_WAKE_FINISHED)
    await _wake(hass)
    hass.states.async_set(KITCHEN_OCC, "on")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5 * 60, step=10)
    assert MUSIC in house.members(FOLLOW)
    assert finished == []
    assert hass.states.get(LSTATE).state == "waking"


async def test_snooze_only_this_room(hass, house, freezer):
    await _wake(hass)
    hass.states.async_set(KITCHEN_OCC, "on")
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    assert KITCHEN in house.members(FOLLOW)
    await hass.services.async_call(DOMAIN, "snooze", {"entity_id": ROOM, "minutes": 2}, blocking=True)
    await settle(hass)
    await tick(hass, freezer, 20)
    assert MUSIC not in house.members(FOLLOW)
    assert KITCHEN in house.members(FOLLOW)
    assert hass.states.get(FOLLOW).state == "playing"
    assert hass.states.get(LSTATE).state == "snoozed"
    hass.states.async_set(LIVING_OCC, "on")  # presence does not bring it back while snoozed
    await hass.async_block_till_done()
    await tick(hass, freezer, 20)
    assert MUSIC not in house.members(FOLLOW)
    assert house.on(SPEAKERS)  # the room stays ready
    await tick(hass, freezer, 85)
    assert MUSIC in house.members(FOLLOW)
    assert vol(hass) <= 0.07  # back at the start volume, fading in again
    assert hass.states.get(WAKE).state == "fading"


async def test_snooze_as_only_member_resumes_the_group(hass, house, freezer):
    await _wake(hass)
    await hass.services.async_call(DOMAIN, "snooze", {"entity_id": ROOM, "minutes": 2}, blocking=True)
    await settle(hass)
    assert house.members(FOLLOW) == []
    assert hass.states.get(FOLLOW).state == "paused"
    await tick(hass, freezer, 130)
    assert MUSIC in house.members(FOLLOW)
    assert _played(house, FOLLOW, "media_play")
    assert hass.states.get(WAKE).state in ("fading", "playing")


async def test_second_wake_room_joins_without_media_change(hass, house, freezer):
    await _wake(hass)
    n = len(_played(house, FOLLOW, "play_media"))
    await tick(hass, freezer, 60)
    await _wake(hass, entity_id=KROOM, media_content_id="library://radio/2", start_volume=0.1)
    assert KITCHEN in house.members(FOLLOW)
    assert len(_played(house, FOLLOW, "play_media")) == n  # the group's media goes on
    assert vol(hass, KITCHEN) == 0.1
    assert hass.states.get("sensor.kitchen_wake").state == "fading"


async def test_quiet_wake_room_still_wakes(hass, house, freezer):
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.living_room_quiet"}, blocking=True)
    await hass.async_block_till_done()
    await _wake(hass)
    await tick(hass, freezer, 10)
    assert MUSIC in house.members(FOLLOW)
    assert house.on(SPEAKERS)


async def test_stop_wake_makes_it_a_normal_follow_room(hass, house, freezer):
    await _wake(hass)
    hass.states.async_set(LIVING_OCC, "on")
    await hass.async_block_till_done()
    await hass.services.async_call(DOMAIN, "stop_wake", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    assert MUSIC in house.members(FOLLOW)
    assert hass.states.get(LSTATE).state == "following"


async def test_max_duration_only_this_room_goes(hass, house, freezer):
    await _wake(hass, max_duration_minutes=3)
    hass.states.async_set(KITCHEN_OCC, "on")
    await hass.async_block_till_done()
    await tick(hass, freezer, 3 * 60 + 5, step=10)
    assert hass.states.get("number.living_room_sleep_timer").state != "0.0"
    await tick(hass, freezer, 6 * 60, step=10)
    assert MUSIC not in house.members(FOLLOW)
    assert KITCHEN in house.members(FOLLOW)


async def test_restart_mid_fade_keeps_waking(hass, house, freezer):
    await _wake(hass)
    await tick(hass, freezer, 60)
    entry = next(e for e in hass.config_entries.async_entries(DOMAIN) if e.title == "Living Room")
    assert await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)
    await tick(hass, freezer, 70)
    assert vol(hass) == 0.4
    assert hass.states.get(LSTATE).state == "waking"


async def test_needs_follow_capable_room(hass, home):
    await setup_room(hass)  # no occupancy sensors
    await setup_hub(hass)
    with pytest.raises(ServiceValidationError):
        await _wake(hass)


async def test_video_media_cannot_follow(hass, house):
    with pytest.raises(ServiceValidationError):
        await _wake(hass, media_content_id=make_id(KIND_ITEM, "video", "movie", "1"), media_content_type="movie")


# ------------------------------------------------- left vs sensor gap (mmWave)


async def test_sensor_gap_in_bed_is_no_left(hass, house, freezer):
    finished = async_capture_events(hass, EVENT_WAKE_FINISHED)
    hass.states.async_set(LIVING_OCC, "on")  # mmWave sees the sleeper
    await hass.async_block_till_done()
    await _wake(hass)
    await tick(hass, freezer, 30)
    hass.states.async_set(LIVING_OCC, "off")  # loses the very still sleeper for a while
    await hass.async_block_till_done()
    await tick(hass, freezer, 5 * 60, step=10)
    assert finished == []
    assert MUSIC in house.members(FOLLOW)


async def test_arrival_long_before_does_not_count(hass, house, freezer):
    finished = async_capture_events(hass, EVENT_WAKE_FINISHED)
    hass.states.async_set(LIVING_OCC, "on")
    await hass.async_block_till_done()
    await _wake(hass)
    await tick(hass, freezer, 30)
    hass.states.async_set(KITCHEN_OCC, "on")  # the partner gets up first
    await hass.async_block_till_done()
    await tick(hass, freezer, 10 * 60, step=10)
    hass.states.async_set(LIVING_OCC, "off")  # sensor gap much later
    await hass.async_block_till_done()
    await tick(hass, freezer, 5 * 60, step=10)
    assert finished == []


async def test_other_sensor_first_then_bedroom_empty_is_left(hass, house, freezer):
    finished = async_capture_events(hass, EVENT_WAKE_FINISHED)
    hass.states.async_set(LIVING_OCC, "on")
    await hass.async_block_till_done()
    await _wake(hass)
    await tick(hass, freezer, 5 * 60, step=10)
    hass.states.async_set(KITCHEN_OCC, "on")  # the bathroom sensor is faster ...
    await hass.async_block_till_done()
    await tick(hass, freezer, 60, step=10)
    hass.states.async_set(LIVING_OCC, "off")  # ... than the bedroom's going empty
    await hass.async_block_till_done()
    await tick(hass, freezer, 3 * 60, step=10)
    assert [e.data["reason"] for e in finished] == ["left"]


async def test_left_detection_can_be_switched_off(hass, house, freezer):
    await set_setting(hass, "wake_detect_left", False)
    finished = async_capture_events(hass, EVENT_WAKE_FINISHED)
    hass.states.async_set(LIVING_OCC, "on")
    await hass.async_block_till_done()
    await _wake(hass)
    await tick(hass, freezer, 30)
    hass.states.async_set(KITCHEN_OCC, "on")
    hass.states.async_set(LIVING_OCC, "off")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5 * 60, step=10)
    assert finished == []
    assert MUSIC in house.members(FOLLOW)


async def test_old_members_of_a_paused_group_leave(hass, house, freezer):
    # last night the group was paused with the kitchen in it; nobody in the kitchen now
    house.group("paused", [KITCHEN], FOLLOW)
    await hass.async_block_till_done()
    await _wake(hass)
    await tick(hass, freezer, 5 * 60, step=10)
    assert KITCHEN not in house.members(FOLLOW), "empty kitchen plays the alarm"


async def test_snooze_as_only_member_keeps_session_and_held_out(hass, house, freezer):
    hass.states.async_set(KITCHEN_OCC, "on")
    await hass.async_block_till_done()
    await _wake(hass)
    await tick(hass, freezer, 60)
    assert hass.states.get(KSTATE).state == "held_out"
    await hass.services.async_call(DOMAIN, "snooze", {"entity_id": "media_player.living_room", "minutes": 2}, blocking=True)
    await settle(hass)
    await tick(hass, freezer, 20)
    snooze_state = hass.states.get(LSTATE).state
    await tick(hass, freezer, 120)
    assert snooze_state == "snoozed", snooze_state
    assert KITCHEN not in house.members(FOLLOW), "held_out lost: kitchen joined though never left"


async def test_wake_volume_is_not_follow_me_volume(hass, house, freezer):
    await _wake(hass)
    await tick(hass, freezer, 200)
    assert vol(hass) == 0.4
    house.group("playing", house.members(FOLLOW), FOLLOW, volume_level=0.4)  # MA: members' average
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    hass.states.async_set(KITCHEN_OCC, "on")
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    assert vol(hass, KITCHEN) == 0.25


async def test_one_unjoin_on_snooze(hass, house, freezer):
    await _wake(hass)
    await tick(hass, freezer, 20)
    hass.states.async_set(KITCHEN_OCC, "on")
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    house.played.clear()
    # MA slow: the unjoin is reported later
    orig = house.handle
    def slow(domain, service, entity_id, data, ctx):
        if service == "unjoin":
            house.played.append((service, entity_id, dict(data)))
            return True
        return orig(domain, service, entity_id, data, ctx)
    house.handle = slow
    await hass.services.async_call(DOMAIN, "snooze", {"entity_id": "media_player.living_room", "minutes": 5}, blocking=True)
    await settle(hass)
    await tick(hass, freezer, 5)
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
    await hass.services.async_call(DOMAIN, "wake", {"entity_id": "media_player.living_room", **payload}, blocking=True)
    await hass.services.async_call(DOMAIN, "wake", {"entity_id": "media_player.kitchen", **payload,
                                                     "media_content_id": "library://radio/2"}, blocking=True)
    await settle(hass)
    pm = _played(house, FOLLOW, "play_media")
    assert len(pm) == 1


async def test_restart_with_stopped_group_joins_again(hass, house, freezer):
    await _wake(hass)
    await tick(hass, freezer, 60)
    # HA (and MA) restart: the group is idle and empty afterwards, session gone
    for e in hass.config_entries.async_entries(DOMAIN):
        await hass.config_entries.async_unload(e.entry_id)
    await hass.async_block_till_done()
    house.group("idle", [], FOLLOW)
    house.music("idle")
    hass.data[DOMAIN]["session"] = FollowSession()
    for e in hass.config_entries.async_entries(DOMAIN):
        await hass.config_entries.async_setup(e.entry_id)
    await settle(hass)
    await tick(hass, freezer, 120)
    assert MUSIC in house.members(FOLLOW)


async def test_dropped_wake_room_is_taken_back(hass, house, freezer):
    await _wake(hass)
    await tick(hass, freezer, 30)
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
    await tick(hass, freezer, 15)
    assert hass.states.get(WAKE).state != "idle"


async def test_group_stopped_by_hand_ends_the_wake(hass, house, freezer):
    finished = async_capture_events(hass, EVENT_WAKE_FINISHED)
    await _wake(hass)
    await tick(hass, freezer, 30)
    house.group("idle", house.members(FOLLOW), FOLLOW)  # stopped in the app: still a member
    house.music("idle")
    await hass.async_block_till_done()
    await tick(hass, freezer, 15)
    assert [e.data["reason"] for e in finished] == ["stopped"]


async def test_the_group_refusing_at_first_is_tried_again(hass, house, freezer):
    """Music Assistant refuses to play the follow-me player right after the join
    (as in real life at 5:20): tried again, the wake goes on."""
    from .conftest import FOLLOW as GROUP_PLAYER

    hass.loop.set_debug(True)  # (a timer that wakes the start from another thread would fail here)
    house.group_play_fails = 2
    await hass.services.async_call(DOMAIN, "wake", {
        "entity_id": ROOM, "media_content_id": "library://album/1", "media_content_type": "music",
        "follow": True, "start_volume": 0.05, "target_volume": 0.4, "fade_minutes": 2}, blocking=True)
    await settle_held(hass)
    await tick_held(hass, freezer, 40)  # (each try runs a while)
    await settle(hass)
    assert len(_played(house, GROUP_PLAYER, "play_media")) == 3
    assert hass.states.get(WAKE).state in ("fading", "playing")
    assert MUSIC in house.members(FOLLOW)


async def test_the_group_refusing_for_good_fails_the_wake_and_tells_why(hass, house, freezer):
    from custom_components.better_media_player.const import EVENT_WAKE_FAILED

    failed = async_capture_events(hass, EVENT_WAKE_FAILED)
    follow_states = []
    hass.bus.async_listen("state_changed", lambda e: e.data["entity_id"] == LSTATE and e.data["new_state"] and follow_states.append(e.data["new_state"].state))
    house.group_play_fails = 5
    await hass.services.async_call(DOMAIN, "wake", {
        "entity_id": ROOM, "media_content_id": "library://album/1", "media_content_type": "music",
        "follow": True, "start_volume": 0.05, "fade_minutes": 2}, blocking=True)
    await settle_held(hass)
    await tick_held(hass, freezer, 40)
    await settle(hass)
    assert len(_played(house, FOLLOW, "play_media")) == 3, "three tries"
    assert [e.data["reason"] for e in failed] == ["start_failed"]
    assert "The command to the player failed" in failed[0].data["error"]
    assert hass.states.get(WAKE).state == "idle"
    # the room went off again (it was off): tidying up, follow me does not keep it out
    assert "excluded_off" not in follow_states
    # and the logbook says why
    from custom_components.better_media_player import logbook as bmp_logbook

    described = {}
    bmp_logbook.async_describe_events(hass, lambda domain, event, fn: described.__setitem__(event, fn))
    out = described[EVENT_WAKE_FAILED](failed[0])
    assert out["entity_id"] == ROOM
    assert out["message"].startswith("wake failed: the start failed (")


async def test_the_join_reported_late_is_waited_for(hass, house, freezer):
    house.join_lag = 3.0  # Music Assistant reports the wake room in the group 3 s later
    await hass.services.async_call(DOMAIN, "wake", {
        "entity_id": ROOM, "media_content_id": "library://album/1", "media_content_type": "music",
        "follow": True, "start_volume": 0.05, "fade_minutes": 2}, blocking=True)
    await settle_held(hass)
    assert not _played(house, FOLLOW, "play_media"), "not before the join is reported"
    await tick_held(hass, freezer, 5)
    await settle(hass)
    assert len(_played(house, FOLLOW, "play_media")) == 1
    assert MUSIC in house.members(FOLLOW)


async def test_a_join_never_reported_is_played_anyway(hass, house, freezer):
    hass.loop.set_debug(True)
    house.join_lag = 60.0  # Music Assistant does not report the join in time
    await hass.services.async_call(DOMAIN, "wake", {
        "entity_id": ROOM, "media_content_id": "library://album/1", "media_content_type": "music",
        "follow": True, "start_volume": 0.05, "fade_minutes": 2}, blocking=True)
    await settle_held(hass)
    await tick_held(hass, freezer, 8)
    assert not _played(house, FOLLOW, "play_media")
    await tick_held(hass, freezer, 5)
    assert len(_played(house, FOLLOW, "play_media")) == 1, "after 10 s: played anyway"
    await tick_held(hass, freezer, 60)
    await settle(hass)


async def test_an_error_that_comes_after_it_plays_is_no_second_start(hass, house, freezer):
    """Music Assistant raises, but the group plays: not started again."""
    def playing_then_error(*_):
        house.group("playing", [MUSIC], FOLLOW)

    house.group_play_fails = 1
    house.on_group_play_fail = playing_then_error
    await hass.services.async_call(DOMAIN, "wake", {
        "entity_id": ROOM, "media_content_id": "library://album/1", "media_content_type": "music",
        "follow": True, "start_volume": 0.05, "fade_minutes": 2}, blocking=True)
    await settle_held(hass)
    await tick_held(hass, freezer, 20)
    await settle(hass)
    assert len(_played(house, FOLLOW, "play_media")) == 1


async def test_a_room_kept_out_before_stays_out_when_its_wake_fails(hass, house, freezer):
    """Turned off during follow me last night: a failed wake does not let it in."""
    from custom_components.better_media_player.room import get_rooms

    hass.states.async_set(KITCHEN_OCC, "on")
    await hass.async_block_till_done()
    house.group("playing", [KITCHEN], FOLLOW)  # follow me runs elsewhere
    hub = get_hub(hass)
    living = next(r for r in get_rooms(hass).values() if r.entry.title == "Living Room")
    hub._activate(next(r for r in get_rooms(hass).values() if r.entry.title == "Kitchen"))
    hub.session.opted_out.add(living.entry.entry_id)
    house.plays_media = False
    await hass.services.async_call(DOMAIN, "wake", {
        "entity_id": ROOM, "media_content_id": "library://album/1", "media_content_type": "music",
        "follow": True, "start_volume": 0.05, "fade_minutes": 2}, blocking=True)
    await settle(hass)
    living.wake._w["requested_at"] = None  # (let the start fail at the next look)
    await living.wake._fail(living.wake._w, "start_failed", "test")
    await hass.async_block_till_done()
    assert living.entry.entry_id in hub.session.opted_out
