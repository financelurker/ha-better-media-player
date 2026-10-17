"""Regression tests for issues found in the second review of v0.2 (follow me)."""
from __future__ import annotations

from custom_components.better_media_player.const import CONF_ALWAYS_GROUPS, CONF_FOLLOW_GROUPS, DOMAIN

from .conftest import FOLLOW, GROUP, KITCHEN, KITCHEN_OCC, KITCHEN_SPK, LIVING_OCC, MUSIC, setup_hub
from .test_follow import _start_follow, _tick


def _n(home, service, eid=None):
    return len([c for c in home.calls if c[1] == service and (eid is None or c[2] == eid)])


async def test_last_room_unjoin_fails_once_never_leaves(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    await _start_follow(hass)
    original = house.handle
    failed = []

    def flaky(domain, service, entity_id, data, context):
        if service == "unjoin" and not failed:
            failed.append(1)
            raise RuntimeError("MA busy")
        return original(domain, service, entity_id, data, context)

    house.handle = flaky
    hass.states.async_set(LIVING_OCC, "off")
    await _tick(hass, freezer, 900)
    assert failed
    assert house.members(FOLLOW) == [], (
        "unjoin failed once -> room became an 'override' and never leaves; "
        f"state={hass.states.get('sensor.wohnzimmer_follow').state}"
    )


async def test_last_room_slow_unjoin_shows_override(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    await _start_follow(hass)
    original = house.handle
    pending = []

    def slow(domain, service, entity_id, data, context):
        if service == "unjoin":
            house.calls.append((domain, service, entity_id))
            pending.append((domain, service, entity_id, data, context))
            return True
        return original(domain, service, entity_id, data, context)

    house.handle = slow
    hass.states.async_set(LIVING_OCC, "off")
    await _tick(hass, freezer, 130)
    await _tick(hass, freezer, 10)
    st = hass.states.get("sensor.wohnzimmer_follow").state
    assert st != "override", "room that was told to leave is shown (and treated) as override while MA is slow"


async def _two_groups(hass, home):
    from .conftest import setup_kitchen, setup_room
    hass.states.async_set(LIVING_OCC, "off")
    hass.states.async_set(KITCHEN_OCC, "off")
    hass.states.async_set(KITCHEN_SPK, "off")
    home.kitchen("idle")
    home.group("idle", [], FOLLOW)
    home.group("idle", [], GROUP)
    await setup_room(hass, occupancy=[LIVING_OCC])
    await setup_kitchen(hass)
    await setup_hub(hass, **{CONF_FOLLOW_GROUPS: [GROUP, FOLLOW], CONF_ALWAYS_GROUPS: []})


async def test_new_group_ma_late_room_pulled_back_into_old(hass, home, freezer):
    await _two_groups(hass, home)
    hass.states.async_set(KITCHEN_OCC, "on")
    await _start_follow(hass, group=GROUP)
    hass.states.async_set(LIVING_OCC, "on")
    await hass.async_block_till_done()
    assert set(home.members(GROUP)) == {KITCHEN, MUSIC}
    hass.states.async_set(KITCHEN_OCC, "off")  # kitchen in run-down, A keeps playing
    await hass.async_block_till_done()
    original = home.handle
    deferred = []

    def late(domain, service, entity_id, data, context):
        if service in ("media_play", "play_media") and entity_id == FOLLOW:
            home.calls.append((domain, service, entity_id))
            deferred.append((domain, service, entity_id, data, context))
            return True
        return original(domain, service, entity_id, data, context)

    home.handle = late
    await _start_follow(hass, group=FOLLOW)  # living moves A -> B, B "playing" reported late
    await _tick(hass, freezer, 10)
    home.handle = original
    for d in deferred:
        original(*d)
    await hass.async_block_till_done()
    assert MUSIC in home.members(FOLLOW), (
        f"living pulled back into the older group while B's start was pending: A={home.members(GROUP)} "
        f"B={home.members(FOLLOW)}"
    )


async def test_resume_after_video_ma_reports_join_late(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    await _start_follow(hass)
    house.kodi("playing")
    await hass.async_block_till_done()
    assert hass.states.get(FOLLOW).state == "paused" and house.members(FOLLOW) == []
    original = house.handle
    deferred = []

    def late(domain, service, entity_id, data, context):
        if service == "join":
            house.calls.append((domain, service, entity_id))
            deferred.append((domain, service, entity_id, data, context))
            return True
        return original(domain, service, entity_id, data, context)

    house.handle = late
    house.kodi("off")
    await hass.async_block_till_done()
    house.handle = original
    for d in deferred:
        original(*d)  # MA applies the join a moment later
    await hass.async_block_till_done()
    await _tick(hass, freezer, 60)
    assert hass.states.get(FOLLOW).state == "playing", (
        f"music never resumes after video when MA reports the join late; media_play calls={_n(house, 'media_play')}"
    )


async def test_resume_video_lost_when_presence_unknown(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    await _start_follow(hass)
    house.kodi("playing")
    await hass.async_block_till_done()
    hass.states.async_set(LIVING_OCC, "unavailable")
    await hass.async_block_till_done()
    house.kodi("off")
    await _tick(hass, freezer, 20)
    hass.states.async_set(LIVING_OCC, "on")
    await _tick(hass, freezer, 20)
    assert MUSIC in house.members(FOLLOW), "presence unknown for a moment after video: resume lost"


async def test_base_volume_with_unmanaged_member(hass, house, freezer):
    from custom_components.better_media_player.hub import get_hub
    hass.states.async_set(LIVING_OCC, "on")
    await _start_follow(hass)  # living at 25 - 10 = 15
    other = "media_player.unmanaged"
    hass.states.async_set(other, "playing", {"volume_level": 0.25})
    # MA group: living 15 % + unmanaged player 25 % -> average 20 %
    house.set(FOLLOW, "playing", group_members=[MUSIC, other], volume_level=0.20)
    base = get_hub(hass).base_volume(FOLLOW)
    assert abs(base - 25) < 0.01, f"base volume {base}, expected 25 (unmanaged member counted as offset 0)"


async def test_unjoin_join_ping_pong_old_group(hass, home, freezer):
    await _two_groups(hass, home)
    hass.states.async_set(KITCHEN_OCC, "on")
    await _start_follow(hass, group=GROUP)
    original = home.handle

    def deaf_kitchen(domain, service, entity_id, data, context):
        if (service == "unjoin" and entity_id == KITCHEN) or (
            service == "join" and KITCHEN in data.get("group_members", [])
        ):
            home.calls.append((domain, service, entity_id))
            return True  # MA ignores / is slow for the kitchen player
        return original(domain, service, entity_id, data, context)

    home.handle = deaf_kitchen
    hass.states.async_set(LIVING_OCC, "on")
    await hass.async_block_till_done()
    await _start_follow(hass, group=FOLLOW)
    home.calls.clear()
    await _tick(hass, freezer, 60)
    j, u = _n(home, "join"), _n(home, "unjoin", KITCHEN)
    assert j + u <= 6, f"ping-pong: {j} joins / {u} unjoins of the kitchen in 60 s"


async def test_session_survives_hub_removal(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    hass.states.async_set(KITCHEN_OCC, "on")
    await _start_follow(hass)
    # kitchen turned off in the session
    await hass.services.async_call("media_player", "turn_off", {"entity_id": "media_player.kitchen"}, blocking=True)
    await hass.async_block_till_done()
    hub = next(e for e in hass.config_entries.async_entries(DOMAIN) if e.title == "Better Media Player")
    await hass.config_entries.async_remove(hub.entry_id)
    await hass.async_block_till_done()
    sess = hass.data[DOMAIN].get("session")
    assert sess is None or sess.group is None, f"stale session left in hass.data after hub removal: {sess}"


async def test_video_room_resumes_group_paused_by_others(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    hass.states.async_set(KITCHEN_OCC, "on")
    await _start_follow(hass)
    house.kodi("playing")  # living leaves for the movie, music goes on in the kitchen
    await hass.async_block_till_done()
    assert house.members(FOLLOW) == [KITCHEN]
    hass.states.async_set(KITCHEN_OCC, "off")
    await _tick(hass, freezer, 130)  # kitchen empties -> group paused by the kitchen leaving
    assert hass.states.get(FOLLOW).state == "paused"
    await _tick(hass, freezer, 3600)
    house.kodi("off")  # movie over an hour later
    await _tick(hass, freezer, 10)
    assert hass.states.get(FOLLOW).state != "playing", (
        "music auto-resumed in the TV room an hour after the group paused because the kitchen emptied"
    )


async def test_pause_resume_forgets_room_off(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    hass.states.async_set(KITCHEN_OCC, "on")
    await _start_follow(hass)
    await hass.services.async_call("media_player", "turn_off", {"entity_id": "media_player.wohnzimmer"}, blocking=True)
    await hass.async_block_till_done()
    assert house.members(FOLLOW) == [KITCHEN]
    await _tick(hass, freezer, 60)
    house.set(FOLLOW, "paused")  # phone call: pause in the MA app
    await _tick(hass, freezer, 30)
    house.group("playing", [KITCHEN], FOLLOW)  # resume in the MA app
    await _tick(hass, freezer, 20)
    assert MUSIC not in house.members(FOLLOW), "short pause ended the session: turned-off room rejoined"
