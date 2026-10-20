"""Follow me: regressions found in the reviews (ported to follow me started in a room)."""

from __future__ import annotations

import pytest

from homeassistant.exceptions import ServiceValidationError

from custom_components.better_media_player.const import DOMAIN
from custom_components.better_media_player.hub import get_hub

from .conftest import FOLLOW, KITCHEN, KITCHEN_OCC, KITCHEN_SPK, KODI, LIVING_OCC, MUSIC, SPEAKERS, setup_hub, setup_room
from .test_follow import LIVING, _tick, follow_from


def _n(home, service, eid=None):
    return len([c for c in home.calls if c[1] == service and (eid is None or c[2] == eid)])


async def test_join_storm_when_ma_does_not_update(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house)
    original = house.handle

    def lazy(domain, service, entity_id, data, context):
        if service == "join":
            house.calls.append((domain, service, entity_id))
            return True  # MA rejects / is slow: membership never changes
        return original(domain, service, entity_id, data, context)

    house.handle = lazy
    house.calls.clear()
    hass.states.async_set(KITCHEN_OCC, "on")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 60)
    assert _n(house, "join") <= 3


async def test_all_off_on_prewarmed_waiting_room(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house)
    assert house.on(KITCHEN_SPK)  # kitchen waiting, prewarmed
    await hass.services.async_call("button", "press", {"entity_id": "button.kitchen_all_off"}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 200)
    assert not house.on(KITCHEN_SPK), "room off while waiting: prewarm switched speakers back on"
    hass.states.async_set(KITCHEN_OCC, "on")
    await hass.async_block_till_done()
    assert KITCHEN not in house.members(FOLLOW), "room off during session, but joined on presence"


async def test_kodi_in_only_member_room_music_returns(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house)
    assert house.members(FOLLOW) == [MUSIC]
    house.kodi("playing")
    await hass.async_block_till_done()
    assert hass.states.get(FOLLOW).state == "paused"
    assert get_hub(hass).active  # left for video: follow me waits
    house.kodi("off")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 30)
    assert MUSIC in house.members(FOLLOW), "after Kodi ends with presence the room should join again"


async def test_manual_add_with_presence_elsewhere_has_its_run_down(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house)
    await _tick(hass, freezer, 300)
    await hass.services.async_call(
        "media_player", "join", {"entity_id": FOLLOW, "group_members": [KITCHEN]}, blocking=True
    )
    await hass.async_block_till_done()
    await _tick(hass, freezer, 20)
    assert KITCHEN in house.members(FOLLOW), "manual add left before its 2 min presence run-down"


async def test_ma_unavailable_blip_keeps_room_off(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    hass.states.async_set(KITCHEN_OCC, "on")
    await follow_from(hass, house)
    await hass.services.async_call("media_player", "turn_off", {"entity_id": LIVING}, blocking=True)
    await hass.async_block_till_done()
    assert house.members(FOLLOW) == [KITCHEN]
    st = hass.states.get(FOLLOW)
    hass.states.async_set(FOLLOW, "unavailable", st.attributes)  # MA reconnects
    await hass.async_block_till_done()
    hass.states.async_set(FOLLOW, "playing", st.attributes)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 20)
    assert MUSIC not in house.members(FOLLOW), "room turned off this session rejoined after MA blip"
    assert get_hub(hass).active


async def test_quiet_unjoin_storm_while_ma_slow(hass, house, freezer):
    hass.states.async_set(KITCHEN_OCC, "on")
    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house)
    original = house.handle

    def slow(domain, service, entity_id, data, context):
        if service == "unjoin":
            house.calls.append((domain, service, entity_id))
            return True
        return original(domain, service, entity_id, data, context)

    house.handle = slow
    house.calls.clear()
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.kitchen_quiet"}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 60)
    assert _n(house, "unjoin", KITCHEN) <= 3


async def test_suppressed_idle_kodi_does_not_block_follow_forever(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    hass.states.async_set(KITCHEN_OCC, "on")
    await follow_from(hass, house)
    assert MUSIC in house.members(FOLLOW)
    # Kodi without turn_off support, sits idle after a movie
    house.set(KODI, "idle", supported_features=0)
    await hass.async_block_till_done()
    assert MUSIC not in house.members(FOLLOW)
    await _tick(hass, freezer, 31 * 60)  # video idle timeout 30 min -> suppressed, cannot be turned off
    assert MUSIC in house.members(FOLLOW), "suppressed idle Kodi keeps the room out of follow forever"


async def test_prewarm_powers_the_audio_host(hass, home, freezer):
    from .conftest import setup_kitchen

    host = "switch.kodi_box_plug"
    hass.states.async_set(host, "off")
    hass.states.async_set(LIVING_OCC, "off")
    hass.states.async_set(KITCHEN_OCC, "on")
    hass.states.async_set(KITCHEN_SPK, "off")
    home.kitchen("idle")
    home.group("idle", [], FOLLOW)
    await setup_room(hass, occupancy=[LIVING_OCC], audio_host=host)
    await setup_kitchen(hass)
    await setup_hub(hass)
    await follow_from(hass, home, player=KITCHEN, room="media_player.kitchen")
    assert home.on(SPEAKERS)  # the living room is prewarmed
    assert home.on(host), "prewarm must also power the music player itself (audio host)"


async def test_member_volume_does_not_drift_with_negative_offset(hass, house, freezer):
    """MA reports the group volume as the members' average (with their offsets)."""
    original = house.handle

    def avg(domain, service, entity_id, data, context):
        r = original(domain, service, entity_id, data, context)
        g = hass.states.get(FOLLOW)
        mem = list(g.attributes.get("group_members", []))
        if mem:
            vols = [hass.states.get(m).attributes.get("volume_level", 0) for m in mem]
            house.set(FOLLOW, g.state, volume_level=sum(vols) / len(vols))
        return r

    house.handle = avg
    hass.states.async_set(KITCHEN_OCC, "on")
    await follow_from(hass, house, player=KITCHEN, room="media_player.kitchen", volume=0.4)
    seen = []
    for _ in range(3):
        hass.states.async_set(LIVING_OCC, "on")
        await hass.async_block_till_done()
        await _tick(hass, freezer, 10)
        seen.append(round(hass.states.get(MUSIC).attributes["volume_level"], 3))
        hass.states.async_set(LIVING_OCC, "off")
        await _tick(hass, freezer, 130)
        assert MUSIC not in house.members(FOLLOW)
    assert seen == [0.3, 0.3, 0.3], f"volume drifts by the offset every join: {seen}"


async def test_room_without_occupancy_cannot_start(hass, home):
    home.group("idle", [], FOLLOW)
    await setup_room(hass)  # no occupancy sensors: it could never leave again
    await setup_hub(hass)
    home.music("playing")
    await hass.async_block_till_done()
    assert hass.states.get("button.wohnzimmer_follow_me") is None
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "start_follow", {"entity_id": LIVING}, blocking=True)


async def test_override_room_has_its_run_down_when_presence_appears(hass, house, freezer):
    await follow_from(hass, house)  # nobody detected: the start room is an override
    await _tick(hass, freezer, 600)
    hass.states.async_set(KITCHEN_OCC, "on")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 10)
    assert MUSIC in house.members(FOLLOW), "override room dropped instantly (no run-down) when presence appears"


async def test_presence_in_quiet_room_keeps_the_override(hass, house, freezer):
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.kitchen_quiet"}, blocking=True)
    await hass.async_block_till_done()
    await follow_from(hass, house)
    await _tick(hass, freezer, 300)
    hass.states.async_set(KITCHEN_OCC, "on")  # someone sleeps in the quiet room
    await hass.async_block_till_done()
    await _tick(hass, freezer, 200)
    assert house.members(FOLLOW) == [MUSIC], "presence in an excluded (quiet) room ended the override"


async def test_override_kept_when_ma_reports_late(hass, house, freezer):
    original = house.handle
    deferred = []

    def late(domain, service, entity_id, data, context):
        if service in ("join", "transfer_queue"):
            house.calls.append((domain, service, entity_id))
            deferred.append((domain, service, entity_id, data, context))
            return True
        return original(domain, service, entity_id, data, context)

    house.handle = late
    await follow_from(hass, house)  # the hub evaluates while MA still shows the group empty
    house.handle = original
    for d in deferred:
        original(*d)
    await hass.async_block_till_done()
    assert house.members(FOLLOW) == [MUSIC]
    await _tick(hass, freezer, 600)
    assert house.members(FOLLOW) == [MUSIC], "override lost because MA reported late"


async def test_reload_keeps_the_override(hass, house, freezer):
    await follow_from(hass, house)
    hub = next(e for e in hass.config_entries.async_entries(DOMAIN) if e.title == "Better Media Player")
    await hass.config_entries.async_reload(hub.entry_id)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 300)
    assert house.members(FOLLOW) == [MUSIC], "override lost after hub reload (options change)"


async def test_last_room_unjoin_fails_once_still_leaves(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house)
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
    assert house.members(FOLLOW) == []


async def test_slow_unjoin_is_no_override(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house)
    original = house.handle

    def slow(domain, service, entity_id, data, context):
        if service == "unjoin":
            house.calls.append((domain, service, entity_id))
            return True
        return original(domain, service, entity_id, data, context)

    house.handle = slow
    hass.states.async_set(LIVING_OCC, "off")
    await _tick(hass, freezer, 140)
    assert hass.states.get("sensor.wohnzimmer_follow").state != "override"


async def test_resume_after_video_when_ma_reports_the_join_late(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house)
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
    assert hass.states.get(FOLLOW).state == "playing"


async def test_resume_after_video_with_presence_unknown_for_a_moment(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house)
    house.kodi("playing")
    await hass.async_block_till_done()
    hass.states.async_set(LIVING_OCC, "unavailable")
    await hass.async_block_till_done()
    house.kodi("off")
    await _tick(hass, freezer, 20)
    hass.states.async_set(LIVING_OCC, "on")
    await _tick(hass, freezer, 20)
    assert MUSIC in house.members(FOLLOW)


async def test_base_volume_with_unmanaged_member(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house, volume=0.15)  # base 25
    other = "media_player.unmanaged"
    hass.states.async_set(other, "playing", {"volume_level": 0.25})
    # MA group: living 15 % + unmanaged player 25 % -> average 20 %
    house.set(FOLLOW, "playing", group_members=[MUSIC, other], volume_level=0.20)
    assert abs(get_hub(hass).base_volume(FOLLOW) - 25) < 0.01


async def test_hub_removal_clears_the_session(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    hass.states.async_set(KITCHEN_OCC, "on")
    await follow_from(hass, house)
    await hass.services.async_call("media_player", "turn_off", {"entity_id": "media_player.kitchen"}, blocking=True)
    await hass.async_block_till_done()
    hub = next(e for e in hass.config_entries.async_entries(DOMAIN) if e.title == "Better Media Player")
    await hass.config_entries.async_remove(hub.entry_id)
    await hass.async_block_till_done()
    sess = hass.data[DOMAIN].get("session")
    assert sess is None or sess.group is None


async def test_no_resume_long_after_everybody_left(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    hass.states.async_set(KITCHEN_OCC, "on")
    await follow_from(hass, house)
    house.kodi("playing")  # living leaves for the movie, music goes on in the kitchen
    await hass.async_block_till_done()
    assert house.members(FOLLOW) == [KITCHEN]
    hass.states.async_set(KITCHEN_OCC, "off")
    await _tick(hass, freezer, 130)  # kitchen empties -> group paused, follow me over
    assert hass.states.get(FOLLOW).state == "paused"
    assert not get_hub(hass).active
    await _tick(hass, freezer, 3600)
    house.kodi("off")  # movie over an hour later
    await _tick(hass, freezer, 10)
    assert hass.states.get(FOLLOW).state != "playing"


async def test_short_pause_keeps_room_off(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    hass.states.async_set(KITCHEN_OCC, "on")
    await follow_from(hass, house)
    await hass.services.async_call("media_player", "turn_off", {"entity_id": LIVING}, blocking=True)
    await hass.async_block_till_done()
    assert house.members(FOLLOW) == [KITCHEN]
    await _tick(hass, freezer, 60)
    house.set(FOLLOW, "paused")  # phone call: pause in the MA app
    await _tick(hass, freezer, 30)
    house.group("playing", [KITCHEN], FOLLOW)  # resume in the MA app
    await _tick(hass, freezer, 20)
    assert MUSIC not in house.members(FOLLOW), "short pause ended the session: turned-off room rejoined"


# ------------------------------------------------- review of follow me 2


def _hub_entry(hass):
    return next(e for e in hass.config_entries.async_entries(DOMAIN) if e.title == "Better Media Player")


async def test_stop_while_paused_stays_paused(hass, house):
    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house)
    house.group("paused", [MUSIC], FOLLOW)  # paused by the user: follow me goes on
    await hass.async_block_till_done()
    assert get_hub(hass).active
    await hass.services.async_call(DOMAIN, "stop_follow", {"entity_id": LIVING}, blocking=True)
    await hass.async_block_till_done()
    assert house.transferred[-1] == (FOLLOW, MUSIC)
    assert hass.states.get(MUSIC).state == "paused", "stopping a paused follow me started the music"


async def test_failed_queue_move_leaves_the_room_out(hass, house):
    from homeassistant.exceptions import HomeAssistantError

    original = house.handle

    def handle(domain, service, entity_id, data, context):
        if domain == "music_assistant" and service == "transfer_queue":
            raise HomeAssistantError("MA down")
        return original(domain, service, entity_id, data, context)

    house.handle = handle
    hass.states.async_set(LIVING_OCC, "on")
    with pytest.raises(HomeAssistantError):
        await follow_from(hass, house)
    await hass.async_block_till_done()
    assert not get_hub(hass).active
    assert MUSIC not in house.members(FOLLOW), "failed start left the room in the follow-me player"


async def test_failed_join_does_not_move_the_queue(hass, house):
    from homeassistant.exceptions import HomeAssistantError

    original = house.handle

    def handle(domain, service, entity_id, data, context):
        if service == "join":
            raise HomeAssistantError("MA down")
        return original(domain, service, entity_id, data, context)

    house.handle = handle
    hass.states.async_set(LIVING_OCC, "on")
    with pytest.raises(HomeAssistantError):
        await follow_from(hass, house)
    assert house.transferred == [], "queue moved into an empty follow-me player"
    assert not get_hub(hass).active


async def test_start_with_a_member_left_in_the_idle_follow_player(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house)
    house.group("idle", [MUSIC], FOLLOW)  # stopped in the MA app: the member stays listed
    await _tick(hass, freezer, 40)
    assert not get_hub(hass).active
    house.transferred.clear()
    await follow_from(hass, house)  # music again directly on the room player
    assert house.transferred == [(MUSIC, FOLLOW)]
    assert hass.states.get(FOLLOW).state == "playing"
    await _tick(hass, freezer, 60)
    assert get_hub(hass).active


async def test_two_stops_at_once_run_once(hass, house, caplog):
    import asyncio

    from custom_components.better_media_player.hub import get_rooms

    hass.states.async_set(LIVING_OCC, "on")
    hass.states.async_set(KITCHEN_OCC, "on")
    await follow_from(hass, house)
    assert KITCHEN in house.members(FOLLOW)
    house.transferred.clear()
    hub = get_hub(hass)
    rooms = {r.entry.title: r for r in get_rooms(hass).values()}
    results = await asyncio.gather(
        hub.async_follow_stop(rooms["Wohnzimmer"]), hub.async_follow_stop(rooms["Kitchen"]), return_exceptions=True
    )
    await hass.async_block_till_done()
    assert caplog.text.count("Follow me ended") == 1
    assert len(house.transferred) == 1
    assert sum(isinstance(r, ServiceValidationError) for r in results) == 1


async def test_other_follow_player_in_the_options_ends_follow_me(hass, house):
    from custom_components.better_media_player.const import CONF_FOLLOW_PLAYER

    from .conftest import GROUP

    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house)
    house.group("playing", [KITCHEN], GROUP)  # another group plays in the kitchen
    await hass.async_block_till_done()
    entry = _hub_entry(hass)
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_FOLLOW_PLAYER: GROUP})
    await hass.async_block_till_done()
    hub = get_hub(hass)
    assert hub.follow_player == GROUP
    assert not hub.active, "a group that just plays was taken over as follow me"
    assert house.members(GROUP) == [KITCHEN]


async def test_reload_with_the_same_player_keeps_follow_me(hass, house):
    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house)
    entry = _hub_entry(hass)
    hass.config_entries.async_update_entry(entry, options={**entry.options, "default_volume": 20})
    await hass.async_block_till_done()
    assert get_hub(hass).active


async def test_stop_reports_a_failed_queue_move(hass, house):
    from homeassistant.exceptions import HomeAssistantError

    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house)
    original = house.handle

    def handle(domain, service, entity_id, data, context):
        if domain == "music_assistant" and service == "transfer_queue":
            raise HomeAssistantError("MA down")
        return original(domain, service, entity_id, data, context)

    house.handle = handle
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(DOMAIN, "stop_follow", {"entity_id": LIVING}, blocking=True)
    assert not get_hub(hass).active



# ------------------------------------------- why follow me cannot start here


async def _start_error(hass, room=LIVING):
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "start_follow", {"entity_id": room}, blocking=True)
    return err.value


async def test_start_in_a_group_says_so(hass, house):
    from .conftest import GROUP

    house.group("playing", [MUSIC], GROUP)  # started in the group "All Players"
    await hass.async_block_till_done()
    err = await _start_error(hass)
    assert err.translation_key == "start_in_group"
    assert err.translation_placeholders == {"room": "Wohnzimmer", "player": "Office Player", "group": "All Players"}
    assert hass.states.get("button.wohnzimmer_follow_me").attributes["blocked_by"] == "start_in_group"
    assert "All Players" in str(err)


async def test_start_without_music_says_so(hass, house):
    err = await _start_error(hass)
    assert err.translation_key == "start_not_playing"
    assert err.translation_placeholders["state"] == "idle"


async def test_start_with_video_says_so(hass, house):
    house.music("playing")
    house.kodi("idle")  # Kodi in its menu
    await hass.async_block_till_done()
    err = await _start_error(hass)
    assert err.translation_key == "start_video"
    assert err.translation_placeholders["player"] == "Kodi"


async def test_start_in_quiet_mode_says_so(hass, house):
    house.music("playing")
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.wohnzimmer_quiet"}, blocking=True)
    await hass.async_block_till_done()
    assert (await _start_error(hass)).translation_key == "start_quiet"


async def test_start_while_running_says_so(hass, house):
    await follow_from(hass, house)
    assert (await _start_error(hass, "media_player.kitchen")).translation_key == "follow_running"


async def test_start_in_a_room_without_occupancy_says_so(hass, home):
    home.group("idle", [], FOLLOW)
    await setup_room(hass)
    await setup_hub(hass)
    home.music("playing")
    await hass.async_block_till_done()
    assert (await _start_error(hass)).translation_key == "start_not_follow_room"


async def test_start_without_follow_player_says_so(hass, home):
    await setup_room(hass, occupancy=[LIVING_OCC])
    await setup_hub(hass, follow_player=None)
    home.music("playing")
    await hass.async_block_till_done()
    assert (await _start_error(hass)).translation_key == "no_follow_player"


async def test_can_start_shows_no_blocker(hass, house):
    house.music("playing")
    await hass.async_block_till_done()
    assert hass.states.get("button.wohnzimmer_follow_me").attributes["blocked_by"] is None
