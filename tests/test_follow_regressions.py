"""Regression tests for issues found in the independent review of v0.2 (follow me)."""
from __future__ import annotations

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.better_media_player.const import CONF_ALWAYS_GROUPS, CONF_FOLLOW_GROUPS

from .conftest import FOLLOW, GROUP, KITCHEN, KITCHEN_OCC, KITCHEN_SPK, KODI, LIVING_OCC, MUSIC, SPEAKERS
from .conftest import setup_hub
from .test_follow import LIVING, _start_follow, _tick


def _n(home, service, eid=None):
    return len([c for c in home.calls if c[1] == service and (eid is None or c[2] == eid)])


async def test_join_storm_when_ma_does_not_update(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    await _start_follow(hass)
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
    joins, vols = _n(house, "join"), _n(house, "volume_set", KITCHEN)
    assert joins <= 3, f"join storm: {joins} joins / {vols} volume_set in 60 s"


async def test_all_off_on_prewarmed_waiting_room(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    await _start_follow(hass)
    assert house.on(KITCHEN_SPK)  # kitchen waiting, prewarmed
    await hass.services.async_call("button", "press", {"entity_id": "button.kitchen_all_off"}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 200)
    assert not house.on(KITCHEN_SPK), "room off while waiting: prewarm switched speakers back on"
    hass.states.async_set(KITCHEN_OCC, "on")
    await hass.async_block_till_done()
    assert KITCHEN not in house.members(FOLLOW), "room off during session, but joined on presence"


async def test_start_follow_volume_ignored_when_group_has_volume(hass, house):
    hass.states.async_set(LIVING_OCC, "on")
    await _start_follow(hass)
    house.set(FOLLOW, "playing", volume_level=0.6)
    await hass.async_block_till_done()
    hass.states.async_set(KITCHEN_OCC, "on")
    # give the explicit volume with the call; kitchen not yet joined (eval may already have joined it)
    house.calls.clear()
    await _start_follow(hass, volume=0.2)
    vol = hass.states.get(KITCHEN).attributes.get("volume_level")
    assert abs(vol - 0.2) < 1e-6, f"explicit volume 0.2 ignored, kitchen got {vol}"


async def test_kodi_in_only_member_room_music_never_returns(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    await _start_follow(hass)
    assert house.members(FOLLOW) == [MUSIC]
    house.kodi("playing")
    await hass.async_block_till_done()
    assert hass.states.get(FOLLOW).state == "paused"
    house.kodi("off")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 30)
    assert MUSIC in house.members(FOLLOW), "after Kodi ends with presence the room should join again"


async def test_manual_add_during_override_is_kicked(hass, house, freezer):
    await _start_follow(hass, fallback_rooms=[LIVING])
    await _tick(hass, freezer, 300)  # kitchen empty for a long time
    # user adds the kitchen in the MA UI (nobody detected anywhere)
    await hass.services.async_call("media_player", "join", {"entity_id": FOLLOW, "group_members": [KITCHEN]},
                                   blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 20)
    assert KITCHEN in house.members(FOLLOW), "manually added room kicked although nobody is present anywhere"


async def test_manual_add_with_presence_elsewhere_kicked_without_run_down(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    await _start_follow(hass)
    await _tick(hass, freezer, 300)
    await hass.services.async_call("media_player", "join", {"entity_id": FOLLOW, "group_members": [KITCHEN]},
                                   blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 20)
    assert KITCHEN in house.members(FOLLOW), "manual add left before its 2 min presence run-down"


async def test_ma_unavailable_blip_resets_session(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    hass.states.async_set(KITCHEN_OCC, "on")
    await _start_follow(hass)
    await hass.services.async_call("media_player", "turn_off", {"entity_id": LIVING}, blocking=True)
    await hass.async_block_till_done()
    assert house.members(FOLLOW) == [KITCHEN]
    # MA reconnects: group briefly unavailable, then playing again
    st = hass.states.get(FOLLOW)
    hass.states.async_set(FOLLOW, "unavailable", st.attributes)
    await hass.async_block_till_done()
    hass.states.async_set(FOLLOW, "playing", st.attributes)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 20)
    assert MUSIC not in house.members(FOLLOW), "room turned off this session rejoined after MA blip"


async def test_older_follow_group_keeps_playing_in_empty_room(hass, home, freezer):
    hass.states.async_set(LIVING_OCC, "off")
    hass.states.async_set(KITCHEN_OCC, "off")
    hass.states.async_set(KITCHEN_SPK, "off")
    home.kitchen("idle")
    home.group("idle", [], FOLLOW)
    home.group("idle", [], GROUP)
    from .conftest import setup_room
    from .conftest import setup_kitchen
    await setup_room(hass, occupancy=[LIVING_OCC])
    await setup_kitchen(hass)
    await setup_hub(hass, **{CONF_FOLLOW_GROUPS: [GROUP, FOLLOW], CONF_ALWAYS_GROUPS: []})
    hass.states.async_set(KITCHEN_OCC, "on")
    await _start_follow(hass, group=GROUP)
    assert home.members(GROUP) == [KITCHEN]
    hass.states.async_set(KITCHEN_OCC, "off")
    hass.states.async_set(LIVING_OCC, "on")
    await hass.async_block_till_done()
    await _start_follow(hass, group=FOLLOW)
    assert home.members(FOLLOW) == [MUSIC] or MUSIC in home.members(FOLLOW)
    await _tick(hass, freezer, 600)
    assert not (hass.states.get(GROUP).state == "playing" and home.members(GROUP)), \
        "older follow group still plays in the empty kitchen 10 min later"


async def test_resume_paused_group_in_empty_house_stops_again(hass, house, freezer):
    hass.states.async_set(KITCHEN_OCC, "on")
    await _start_follow(hass)
    await hass.services.async_call("media_player", "media_pause", {"entity_id": FOLLOW}, blocking=True)
    house.group("paused", [KITCHEN], FOLLOW)
    await hass.async_block_till_done()
    hass.states.async_set(KITCHEN_OCC, "off")
    await _tick(hass, freezer, 600)
    assert house.members(FOLLOW) == [KITCHEN]
    # nobody detected; press Start follow (= resume)
    await hass.services.async_call("button", "press", {"entity_id": "button.better_media_player_start_follow"},
                                   blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 20)
    assert house.members(FOLLOW) == [KITCHEN], "resumed member without presence is not kept as override"


async def test_hub_reload_loses_override(hass, house, freezer):
    await _start_follow(hass, fallback_rooms=[LIVING])
    entry = next(e for e in hass.config_entries.async_entries("better_media_player") if e.title == "Better Media Player")
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 300)
    assert house.members(FOLLOW) == [MUSIC], "override lost after hub reload (options change)"


async def test_start_follow_during_session_clears_opt_out(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    hass.states.async_set(KITCHEN_OCC, "on")
    await _start_follow(hass)
    await hass.services.async_call("media_player", "turn_off", {"entity_id": LIVING}, blocking=True)
    await hass.async_block_till_done()
    assert house.members(FOLLOW) == [KITCHEN]
    await _start_follow(hass)  # someone presses the Start follow button again
    assert MUSIC not in house.members(FOLLOW), "turned-off room rejoined because start_follow cleared opted_out"


async def test_quiet_unjoin_storm_while_ma_slow(hass, house, freezer):
    hass.states.async_set(KITCHEN_OCC, "on")
    hass.states.async_set(LIVING_OCC, "on")
    await _start_follow(hass)
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
    n = _n(house, "unjoin", KITCHEN)
    assert n <= 3, f"unjoin storm: {n} in 60 s"


async def test_restart_ma_late_treated_as_new_session(hass, home, freezer):
    """HA restart: MA group shows up only after HA started -> must count as restored (no overrides)."""
    from .conftest import setup_room
    from .conftest import setup_kitchen
    hass.states.async_set(LIVING_OCC, "off")
    hass.states.async_set(KITCHEN_OCC, "off")
    hass.states.async_set(KITCHEN_SPK, "off")
    home.kitchen("idle")
    hass.states.async_set(FOLLOW, "unavailable", {})
    await setup_room(hass, occupancy=[LIVING_OCC])
    await setup_kitchen(hass)
    await setup_hub(hass)
    await _tick(hass, freezer, 10)
    home.group("playing", [MUSIC], FOLLOW)  # MA connected
    await hass.async_block_till_done()
    await _tick(hass, freezer, 300)
    assert home.members(FOLLOW) == [], "late MA after restart -> member became an override and never leaves"


async def test_older_follow_group_with_time(hass, home, freezer):
    from .conftest import setup_room
    from .conftest import setup_kitchen
    hass.states.async_set(LIVING_OCC, "off")
    hass.states.async_set(KITCHEN_OCC, "off")
    hass.states.async_set(KITCHEN_SPK, "off")
    home.kitchen("idle")
    home.group("idle", [], FOLLOW)
    home.group("idle", [], GROUP)
    await setup_room(hass, occupancy=[LIVING_OCC])
    await setup_kitchen(hass)
    await setup_hub(hass, **{CONF_FOLLOW_GROUPS: [GROUP, FOLLOW], CONF_ALWAYS_GROUPS: []})
    hass.states.async_set(KITCHEN_OCC, "on")
    await _start_follow(hass, group=GROUP)
    await _tick(hass, freezer, 10)
    hass.states.async_set(LIVING_OCC, "on")
    await _start_follow(hass, group=FOLLOW)
    hass.states.async_set(KITCHEN_OCC, "off")
    await _tick(hass, freezer, 600)
    assert not (hass.states.get(GROUP).state == "playing" and home.members(GROUP)), \
        "older follow group still plays in the empty kitchen 10 min later"


async def test_suppressed_idle_kodi_blocks_follow_forever(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    hass.states.async_set(KITCHEN_OCC, "on")
    await _start_follow(hass)
    assert MUSIC in house.members(FOLLOW)
    # Kodi without turn_off support, sits idle after a movie; user turns the room off
    house.set(KODI, "idle", supported_features=0)
    await hass.async_block_till_done()
    assert MUSIC not in house.members(FOLLOW)
    await _tick(hass, freezer, 31 * 60)  # video idle timeout 30 min -> suppressed, cannot be turned off
    assert MUSIC in house.members(FOLLOW), "suppressed idle Kodi keeps the room out of follow forever"


async def test_prewarm_does_not_power_audio_host(hass, home, freezer):
    from .conftest import setup_room
    HOST = "switch.kodi_box_plug"
    hass.states.async_set(HOST, "off")
    hass.states.async_set(LIVING_OCC, "off")
    hass.states.async_set(KITCHEN_OCC, "on")
    hass.states.async_set(KITCHEN_SPK, "off")
    home.kitchen("idle")
    home.group("idle", [], FOLLOW)
    home.group("idle", [], GROUP)
    await setup_room(hass, occupancy=[LIVING_OCC], audio_host=HOST)
    from .conftest import setup_kitchen
    await setup_kitchen(hass)
    await setup_hub(hass)
    await _start_follow(hass)
    assert home.on(SPEAKERS)
    assert home.on(HOST), "prewarm switches speakers but not the music player's own power (audio_host)"


async def test_resume_paused_members_nobody_present(hass, house, freezer):
    hass.states.async_set(KITCHEN_OCC, "on")
    await _start_follow(hass)
    house.group("paused", [KITCHEN], FOLLOW)
    await hass.async_block_till_done()
    hass.states.async_set(KITCHEN_OCC, "off")
    await _tick(hass, freezer, 600)
    await hass.services.async_call("button", "press", {"entity_id": "button.better_media_player_start_follow"},
                                   blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 200)
    assert house.members(FOLLOW) == [KITCHEN], "resumed member without presence is not kept as override"


async def test_manual_group_then_start_follow_nobody_present(hass, house, freezer):
    # user builds the group in the MA UI (idle group with a member), nobody detected
    house.group("idle", [KITCHEN], FOLLOW)
    await hass.async_block_till_done()
    await _start_follow(hass)
    assert hass.states.get(FOLLOW).state == "playing"
    await _tick(hass, freezer, 200)
    assert house.members(FOLLOW) == [KITCHEN], "manual member kicked (should be override while nobody present)"


async def test_volume_drifts_with_negative_offset(hass, house, freezer):
    """MA group volume = average of member volumes."""
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
    seen = []
    for _ in range(3):
        hass.states.async_set(LIVING_OCC, "on")
        await hass.async_block_till_done()
        await _start_follow(hass)
        seen.append(round(hass.states.get(MUSIC).attributes["volume_level"], 3))
        hass.states.async_set(LIVING_OCC, "off")
        await _tick(hass, freezer, 130)
        assert house.members(FOLLOW) == []
    assert seen[0] == seen[-1], f"volume drifts by the offset every session: {seen}"


async def test_older_follow_group_left_playing(hass, home, freezer):
    from .conftest import setup_room
    from .conftest import setup_kitchen
    hass.states.async_set(LIVING_OCC, "off")
    hass.states.async_set(KITCHEN_OCC, "off")
    hass.states.async_set(KITCHEN_SPK, "off")
    home.kitchen("idle")
    home.group("idle", [], FOLLOW)
    home.group("idle", [], GROUP)
    await setup_room(hass, occupancy=[LIVING_OCC])
    await setup_kitchen(hass)
    await setup_hub(hass, **{CONF_FOLLOW_GROUPS: [GROUP, FOLLOW], CONF_ALWAYS_GROUPS: []})
    hass.states.async_set(KITCHEN_OCC, "on")
    await _start_follow(hass, group=GROUP)
    await _tick(hass, freezer, 10)
    hass.states.async_set(KITCHEN_OCC, "off")  # kitchen left just before B starts
    hass.states.async_set(LIVING_OCC, "on")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 10)
    await _start_follow(hass, group=FOLLOW)
    await _tick(hass, freezer, 900)
    assert not (hass.states.get(GROUP).state == "playing" and home.members(GROUP)), \
        "older follow group keeps playing in the empty kitchen forever"


async def test_hub_reload_powers_ignored_group(hass, home, freezer):
    from .conftest import setup_room
    other = "media_player.all_players"
    home.group("idle", [], other)
    hass.states.async_set(LIVING_OCC, "off")
    await setup_room(hass, occupancy=[LIVING_OCC])
    entry = await setup_hub(hass, **{CONF_ALWAYS_GROUPS: []})
    home.group("playing", [MUSIC], other)
    await hass.async_block_till_done()
    assert not home.on(SPEAKERS)
    # follow group plays elsewhere -> living prewarmed? no: start a follow session so prewarm toggles on reload
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert not home.on(SPEAKERS), "hub reload window: ignored group powered the speakers (v0.1 fallback)"


async def test_fallback_room_without_occupancy_is_rejected(hass, home, freezer):
    """A fallback room without occupancy sensors could never leave again: refuse it clearly."""
    from .conftest import setup_room
    from .conftest import setup_kitchen
    hass.states.async_set(KITCHEN_OCC, "off")
    hass.states.async_set(KITCHEN_SPK, "off")
    home.kitchen("idle")
    home.group("idle", [], FOLLOW)
    home.group("idle", [], GROUP)
    await setup_room(hass)  # living room WITHOUT occupancy sensors
    await setup_kitchen(hass)
    await setup_hub(hass)
    with pytest.raises(HomeAssistantError):
        await _start_follow(hass, fallback_rooms=[MUSIC])
    assert home.members(FOLLOW) == []


async def test_override_room_leaves_without_run_down(hass, house, freezer):
    await _start_follow(hass, fallback_rooms=[LIVING])
    await _tick(hass, freezer, 600)
    hass.states.async_set(KITCHEN_OCC, "on")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 10)
    assert MUSIC in house.members(FOLLOW), "override room dropped instantly (no run-down) when presence appears"


async def test_presence_in_quiet_room_kills_fallback_override(hass, house, freezer):
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.kitchen_quiet"}, blocking=True)
    await hass.async_block_till_done()
    await _start_follow(hass, fallback_rooms=[LIVING])
    assert house.members(FOLLOW) == [MUSIC]
    await _tick(hass, freezer, 300)
    hass.states.async_set(KITCHEN_OCC, "on")  # someone sleeps in the quiet room
    await hass.async_block_till_done()
    await _tick(hass, freezer, 20)
    assert house.members(FOLLOW) == [MUSIC], "presence in an excluded (quiet) room ended the fallback session"


async def test_stale_empty_since_kicks_member_of_new_session(hass, house, freezer):
    # session 1 in the kitchen; living room empty the whole time -> _empty_since[living] set
    hass.states.async_set(KITCHEN_OCC, "on")
    await _start_follow(hass)
    await _tick(hass, freezer, 20)
    await hass.services.async_call("media_player", "media_pause", {"entity_id": FOLLOW}, blocking=True)
    house.set(FOLLOW, "idle", group_members=[])
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3600)
    # later: user builds a group in MA with living + kitchen (kitchen present), presses play
    hass.states.async_set(LIVING_OCC, "on")
    await hass.async_block_till_done()
    hass.states.async_set(LIVING_OCC, "off")  # walked through 1 s ago
    await hass.async_block_till_done()
    house.group("playing", [KITCHEN, MUSIC], FOLLOW)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 10)
    assert MUSIC in house.members(FOLLOW), "left immediately: run-down measured from a stale timestamp"


async def test_fallback_override_lost_when_ma_reports_late(hass, house, freezer):
    """Real MA: service returns, state update arrives later (members first, playing later or vice versa)."""
    original = house.handle
    deferred = []

    def late(domain, service, entity_id, data, context):
        if service in ("join", "media_play", "play_media"):
            house.calls.append((domain, service, entity_id))
            deferred.append((domain, service, entity_id, data, context))
            return True
        return original(domain, service, entity_id, data, context)

    house.handle = late
    await _start_follow(hass, fallback_rooms=[LIVING])   # hub eval runs while MA still shows idle/empty
    house.handle = original
    # MA now reports: playing first (MA reports group state), then membership
    house.set(FOLLOW, "playing")
    await hass.async_block_till_done()
    for d in deferred:
        if d[1] == "join":
            original(*d)
    await hass.async_block_till_done()
    assert house.members(FOLLOW) == [MUSIC]
    await _tick(hass, freezer, 600)
    assert house.members(FOLLOW) == [MUSIC], "fallback override lost because MA reported late"


async def test_hub_reload_window_ignored_group_powers(hass, house, freezer):
    other = "media_player.ignored_group"
    # kitchen plays in an ignored group; a follow session in the living room prewarms the kitchen
    house.set(other, "playing", group_members=[KITCHEN], mass_player_type="group")
    house.kitchen("playing")
    hass.states.async_set(LIVING_OCC, "on")
    await _start_follow(hass)
    await _tick(hass, freezer, 200)
    hub = next(e for e in hass.config_entries.async_entries("better_media_player") if e.title == "Better Media Player")
    hass.config_entries.async_update_entry(hub, options={**hub.options, "default_volume": 30})
    await hass.async_block_till_done()
    await _tick(hass, freezer, 10)
