"""Follow me: started in a room, follows the people, stopped back into one room."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.better_media_player.const import (
    CONF_DEFAULT_VOLUME,
    CONF_ENTRY_TYPE,
    CONF_FOLLOW_PLAYER,
    CONF_OCCUPANCY,
    DOMAIN,
    ENTRY_TYPE_HUB,
)
from custom_components.better_media_player.follow import get_hub

from .conftest import (
    BASS,
    FOLLOW,
    GROUP,
    KITCHEN,
    KITCHEN_OCC,
    KITCHEN_SPK,
    KODI,
    LIVING_OCC,
    MUSIC,
    SPEAKERS,
    TV,
    setup_hub,
    setup_room,
)
from .helpers import KROOM, ROOM, follow_from, quiet_leaves_and_cuts_speakers, tick

START = "button.living_room_follow_me"
FOLLOW_STOP = "button.living_room_stop_follow_me"
KSTART = "button.kitchen_follow_me"
KITCHEN_STOP = "button.kitchen_stop_follow_me"
TRACK = "library://track/42"


def _calls(home, service):
    return [c[2] for c in home.calls if c[1] == service]


async def stop_follow(hass, room=ROOM) -> None:
    await hass.services.async_call(DOMAIN, "stop_follow", {"entity_id": room}, blocking=True)
    await hass.async_block_till_done()


# ------------------------------------------------------------------ start


async def test_start_moves_the_music_and_prewarms(hass, house):
    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house, volume=0.25)
    assert house.transferred == [(MUSIC, FOLLOW)]
    assert house.members(FOLLOW) == [MUSIC]
    assert hass.states.get(FOLLOW).state == "playing"
    assert get_hub(hass).data["volumes"][FOLLOW] == 35  # 25 % in a room with offset -10
    # kitchen is empty but prewarmed: speakers already on
    assert house.on(SPEAKERS) and house.on(BASS) and house.on(KITCHEN_SPK)
    assert KITCHEN not in house.members(FOLLOW)
    assert hass.states.get("sensor.kitchen_follow").state == "waiting"
    assert hass.states.get("sensor.living_room_follow").state == "following"
    assert hass.states.get("sensor.better_media_player_follow_rooms").state == "1"


async def test_start_needs_music_on_the_room_player(hass, house):
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "start_follow", {"entity_id": ROOM}, blocking=True)


async def test_start_not_with_video(hass, house):
    house.music("playing")
    house.kodi("playing")
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "start_follow", {"entity_id": ROOM}, blocking=True)


async def test_start_not_in_a_quiet_room(hass, house):
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.living_room_quiet"}, blocking=True)
    house.music("playing")
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "start_follow", {"entity_id": ROOM}, blocking=True)


async def test_only_one_follow_me(hass, house):
    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house)
    house.kitchen("playing")
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "start_follow", {"entity_id": KROOM}, blocking=True)


async def test_follow_player_playing_without_follow_me_can_be_started(hass, house):
    house.group("playing", [MUSIC], FOLLOW)  # started in the Music Assistant app
    await hass.async_block_till_done()
    await hass.services.async_call(DOMAIN, "start_follow", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert house.transferred == []  # already there
    assert get_hub(hass).active


# --------------------------------------------------------------- buttons


async def test_buttons_shown_only_when_usable(hass, house, freezer):
    await tick(hass, freezer, 5)  # hidden after the start and the hide delay
    reg = er.async_get(hass)
    hidden = er.RegistryEntryHider.INTEGRATION
    assert reg.async_get(START).hidden_by == hidden  # nothing plays
    assert reg.async_get(FOLLOW_STOP).hidden_by == hidden

    house.music("playing")
    await hass.async_block_till_done()
    assert reg.async_get(START).hidden_by is None
    assert reg.async_get(KSTART).hidden_by == hidden  # nothing plays in the kitchen

    await hass.services.async_call("button", "press", {"entity_id": START}, blocking=True)
    await hass.async_block_till_done()
    assert get_hub(hass).active
    await tick(hass, freezer, 4)  # hidden after the hide delay (3 s)
    assert reg.async_get(START).hidden_by == hidden  # follow me runs: no start anywhere
    assert reg.async_get(FOLLOW_STOP).hidden_by is None
    assert reg.async_get(KITCHEN_STOP).hidden_by is None

    await hass.services.async_call("button", "press", {"entity_id": FOLLOW_STOP}, blocking=True)
    await hass.async_block_till_done()
    assert not get_hub(hass).active
    await tick(hass, freezer, 4)
    assert reg.async_get(FOLLOW_STOP).hidden_by == hidden


async def test_pressing_a_hidden_start_fails(hass, house):
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call("button", "press", {"entity_id": START}, blocking=True)


# --------------------------------------------------------------- walking


async def test_music_follows_through_the_house(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house)

    hass.states.async_set(KITCHEN_OCC, "on")
    hass.states.async_set(LIVING_OCC, "off")
    await hass.async_block_till_done()
    assert set(house.members(FOLLOW)) == {MUSIC, KITCHEN}
    assert hass.states.get(KITCHEN).attributes["volume_level"] == pytest.approx(0.35)  # base 35 + 0

    await tick(hass, freezer, 60)
    assert MUSIC in house.members(FOLLOW), "presence run-down (2 min) not over"
    await tick(hass, freezer, 70)
    assert house.members(FOLLOW) == [KITCHEN]
    assert house.on(SPEAKERS), "living room stays prewarmed while follow plays"

    # back in the living room: joins again, volume first
    house.calls.clear()
    hass.states.async_set(LIVING_OCC, "on")
    await hass.async_block_till_done()
    assert MUSIC in house.members(FOLLOW)
    assert _calls(house, "volume_set")[0] == MUSIC


async def test_house_empty_ends_follow_me(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house)
    hass.states.async_set(LIVING_OCC, "off")
    await tick(hass, freezer, 130)
    assert house.members(FOLLOW) == []
    assert hass.states.get(FOLLOW).state == "paused"
    assert not get_hub(hass).active  # follow me is over
    await tick(hass, freezer, 130)
    assert not house.on(SPEAKERS) and not house.on(KITCHEN_SPK)
    assert hass.states.get("sensor.kitchen_follow").state == "inactive"


async def test_follow_player_stopped_ends_follow_me(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house)
    await tick(hass, freezer, 40)
    house.group("idle", house.members(FOLLOW), FOLLOW)  # stopped in the app
    await tick(hass, freezer, 10)
    assert not get_hub(hass).active


async def test_presence_unknown_changes_nothing(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house)
    hass.states.async_set(LIVING_OCC, "unavailable")
    await tick(hass, freezer, 900)
    assert house.members(FOLLOW) == [MUSIC]
    assert hass.states.get("sensor.living_room_follow").state == "presence_unknown"


async def test_nobody_detected_keeps_the_start_room(hass, house, freezer):
    """Started without presence (e.g. motion sensor misses the listener): kept as override."""
    await follow_from(hass, house)
    await tick(hass, freezer, 600)
    assert house.members(FOLLOW) == [MUSIC]
    assert hass.states.get("sensor.living_room_follow").state == "override"


# ------------------------------------------------------------------- stop


async def test_stop_in_a_room_that_plays_along_keeps_it_there(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house)
    hass.states.async_set(KITCHEN_OCC, "on")
    await hass.async_block_till_done()
    assert set(house.members(FOLLOW)) == {MUSIC, KITCHEN}
    await stop_follow(hass, KROOM)
    assert house.members(FOLLOW) == []
    assert house.transferred[-1] == (FOLLOW, KITCHEN)
    assert hass.states.get(KITCHEN).state == "playing"
    assert hass.states.get(MUSIC).state != "playing"
    assert not get_hub(hass).active


async def test_stop_elsewhere_goes_back_to_the_start_room(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house)
    hass.states.async_set(KITCHEN_OCC, "on")
    hass.states.async_set(LIVING_OCC, "off")
    await tick(hass, freezer, 140)
    assert house.members(FOLLOW) == [KITCHEN]
    await stop_follow(hass, ROOM)  # the living room plays no longer along
    assert house.transferred[-1] == (FOLLOW, MUSIC)
    assert hass.states.get(MUSIC).state == "playing"


async def test_stop_without_follow_me_is_an_error(hass, house):
    with pytest.raises(ServiceValidationError):
        await stop_follow(hass)
    assert house.transferred == []


# ------------------------------------------------------------- exclusions


async def test_never_steals_from_another_group(hass, house, freezer):
    house.group("playing", [KITCHEN], GROUP)  # kitchen plays "All Players"
    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house)
    hass.states.async_set(KITCHEN_OCC, "on")
    await tick(hass, freezer, 30)
    assert KITCHEN not in house.members(FOLLOW)
    assert house.members(GROUP) == [KITCHEN]
    assert hass.states.get("sensor.kitchen_follow").state == "excluded_busy"


async def test_kodi_takes_the_room_out_and_back(hass, house):
    hass.states.async_set(LIVING_OCC, "on")
    hass.states.async_set(KITCHEN_OCC, "on")
    await follow_from(hass, house)
    assert MUSIC in house.members(FOLLOW)

    house.kodi("playing")
    await hass.async_block_till_done()
    assert MUSIC not in house.members(FOLLOW)
    assert hass.states.get("sensor.living_room_follow").state == "excluded_video"
    assert house.on(TV)

    house.kodi("off")
    await hass.async_block_till_done()
    assert MUSIC in house.members(FOLLOW)


async def test_quiet_switch_leaves_and_cuts_speakers(hass, house):
    await quiet_leaves_and_cuts_speakers(hass, house)


async def test_room_off_stays_out_for_the_session(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    hass.states.async_set(KITCHEN_OCC, "on")
    await follow_from(hass, house)
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert house.members(FOLLOW) == [KITCHEN]
    await tick(hass, freezer, 60)
    assert MUSIC not in house.members(FOLLOW), "still in the room, but turned off"
    assert not house.on(SPEAKERS), "room off: no prewarming either"
    assert hass.states.get("sensor.living_room_follow").state == "excluded_off"


async def test_quiet_room_is_not_prewarmed(hass, house):
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.kitchen_quiet"}, blocking=True)
    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house)
    assert house.on(SPEAKERS)
    assert not house.on(KITCHEN_SPK)


async def test_quiet_cuts_speakers_even_if_music_assistant_is_slow(hass, house):
    """Real MA reports the unjoin a bit later: the speakers must still go off."""
    hass.states.async_set(KITCHEN_OCC, "on")
    await follow_from(hass, house, player=KITCHEN, room=KROOM)
    original = house.handle

    def slow(domain, service, entity_id, data, context):
        if service == "unjoin":
            house.calls.append((domain, service, entity_id))
            return True  # MA has not updated the states yet
        return original(domain, service, entity_id, data, context)

    house.handle = slow
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.kitchen_quiet"}, blocking=True)
    await hass.async_block_till_done()
    assert ("media_player", "unjoin", KITCHEN) in house.calls
    assert hass.states.get(KITCHEN).state == "playing"
    assert not house.on(KITCHEN_SPK)


# ----------------------------------------------------------------- volume


async def test_volume_remembered_for_the_next_join(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house)
    house.set(FOLLOW, "playing", volume_level=0.32)  # someone turns the group up
    await tick(hass, freezer, 10)
    assert get_hub(hass).data["volumes"][FOLLOW] == 42  # 32 % with average offset -10
    hass.states.async_set(KITCHEN_OCC, "on")
    await hass.async_block_till_done()
    assert hass.states.get(KITCHEN).attributes["volume_level"] == pytest.approx(0.42)


# ------------------------------------------------------------ other groups


async def test_other_groups_power_the_speakers(hass, home):
    home.group("idle", [], GROUP)
    home.group("idle", [], FOLLOW)
    await setup_room(hass)
    await setup_hub(hass)
    home.group("playing", [MUSIC], GROUP)
    await hass.async_block_till_done()
    assert home.on(SPEAKERS)


async def test_follow_player_without_follow_me_is_a_normal_group(hass, house, freezer):
    """A party on the follow-me player: plays, but nobody leaves an empty room."""
    house.group("playing", [MUSIC, KITCHEN], FOLLOW)
    await tick(hass, freezer, 300)
    assert set(house.members(FOLLOW)) == {MUSIC, KITCHEN}
    assert house.on(SPEAKERS) and house.on(KITCHEN_SPK)
    assert hass.states.get("sensor.kitchen_follow").state == "inactive"


async def test_without_hub_every_group_counts(hass, home):
    await setup_room(hass)
    home.group("playing", [MUSIC], "media_player.all_players")
    await hass.async_block_till_done()
    assert home.on(SPEAKERS)


# ----------------------------------------------------------------- restart


async def test_reload_while_follow_me_plays_keeps_members(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house)
    hub_entry = next(e for e in hass.config_entries.async_entries(DOMAIN) if e.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_HUB)
    hass.states.async_set(LIVING_OCC, "unavailable")
    assert await hass.config_entries.async_reload(hub_entry.entry_id)
    await hass.async_block_till_done()
    await tick(hass, freezer, 300)
    assert get_hub(hass).active
    assert house.members(FOLLOW) == [MUSIC]
    hass.states.async_set(LIVING_OCC, "off")  # sensors are back: normal rules again
    await tick(hass, freezer, 130)
    assert house.members(FOLLOW) == []


# --------------------------------------------------------------- config flow


async def test_hub_flow_and_single_instance(hass, home):
    home.group("idle", [], FOLLOW)
    hass.states.async_set("media_player.radio", "idle")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "hub"})
    assert result["step_id"] == "hub"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_FOLLOW_PLAYER: "media_player.radio"})
    assert result["errors"] == {CONF_FOLLOW_PLAYER: "not_a_group"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_FOLLOW_PLAYER: FOLLOW})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == {CONF_FOLLOW_PLAYER: FOLLOW}
    await hass.async_block_till_done()

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "hub"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "hub_exists"


async def test_old_central_entry_is_migrated(hass, home):
    home.group("idle", [], FOLLOW)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Better Media Player",
        data={CONF_ENTRY_TYPE: ENTRY_TYPE_HUB},
        options={"follow_groups": [FOLLOW, GROUP], "always_groups": [GROUP], CONF_DEFAULT_VOLUME: 30},
        version=1,
    )
    entry.add_to_hass(hass)
    reg = er.async_get(hass)
    reg.async_get_or_create("button", DOMAIN, f"{entry.entry_id}_start_follow", config_entry=entry)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.version == 3
    assert dict(entry.options) == {CONF_FOLLOW_PLAYER: FOLLOW}
    assert float(hass.states.get("number.better_media_player_follow_me_default_volume").state) == 30
    assert reg.async_get_entity_id("button", DOMAIN, f"{entry.entry_id}_start_follow") is None


async def test_room_follow_options(hass, home):
    entry = await setup_room(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "devices"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"display": entry.options["display"], "speakers": entry.options["speakers"], "bass": entry.options["bass"],
         "actions": {}, "standby": {}, "follow": {CONF_OCCUPANCY: [LIVING_OCC]}},
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "save"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_OCCUPANCY] == [LIVING_OCC]
    assert hass.states.get("sensor.living_room_follow") is not None


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
    await tick(hass, freezer, 60)
    assert _n(house, "join") <= 3


async def test_all_off_on_prewarmed_waiting_room(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house)
    assert house.on(KITCHEN_SPK)  # kitchen waiting, prewarmed
    await hass.services.async_call("button", "press", {"entity_id": "button.kitchen_all_off"}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 200)
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
    await tick(hass, freezer, 30)
    assert MUSIC in house.members(FOLLOW), "after Kodi ends with presence the room should join again"


async def test_manual_add_with_presence_elsewhere_has_its_run_down(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house)
    await tick(hass, freezer, 300)
    await hass.services.async_call(
        "media_player", "join", {"entity_id": FOLLOW, "group_members": [KITCHEN]}, blocking=True
    )
    await hass.async_block_till_done()
    await tick(hass, freezer, 20)
    assert KITCHEN in house.members(FOLLOW), "manual add left before its 2 min presence run-down"


async def test_ma_unavailable_blip_keeps_room_off(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    hass.states.async_set(KITCHEN_OCC, "on")
    await follow_from(hass, house)
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert house.members(FOLLOW) == [KITCHEN]
    st = hass.states.get(FOLLOW)
    hass.states.async_set(FOLLOW, "unavailable", st.attributes)  # MA reconnects
    await hass.async_block_till_done()
    hass.states.async_set(FOLLOW, "playing", st.attributes)
    await hass.async_block_till_done()
    await tick(hass, freezer, 20)
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
    await tick(hass, freezer, 60)
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
    await tick(hass, freezer, 31 * 60)  # video idle timeout 30 min -> suppressed, cannot be turned off
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
        await tick(hass, freezer, 10)
        seen.append(round(hass.states.get(MUSIC).attributes["volume_level"], 3))
        hass.states.async_set(LIVING_OCC, "off")
        await tick(hass, freezer, 130)
        assert MUSIC not in house.members(FOLLOW)
    assert seen == [0.3, 0.3, 0.3], f"volume drifts by the offset every join: {seen}"


async def test_room_without_occupancy_cannot_start(hass, home):
    home.group("idle", [], FOLLOW)
    await setup_room(hass)  # no occupancy sensors: it could never leave again
    await setup_hub(hass)
    home.music("playing")
    await hass.async_block_till_done()
    assert hass.states.get("button.living_room_follow_me") is None
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "start_follow", {"entity_id": ROOM}, blocking=True)


async def test_override_room_has_its_run_down_when_presence_appears(hass, house, freezer):
    await follow_from(hass, house)  # nobody detected: the start room is an override
    await tick(hass, freezer, 600)
    hass.states.async_set(KITCHEN_OCC, "on")
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    assert MUSIC in house.members(FOLLOW), "override room dropped instantly (no run-down) when presence appears"


async def test_presence_in_quiet_room_keeps_the_override(hass, house, freezer):
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.kitchen_quiet"}, blocking=True)
    await hass.async_block_till_done()
    await follow_from(hass, house)
    await tick(hass, freezer, 300)
    hass.states.async_set(KITCHEN_OCC, "on")  # someone sleeps in the quiet room
    await hass.async_block_till_done()
    await tick(hass, freezer, 200)
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
    await tick(hass, freezer, 600)
    assert house.members(FOLLOW) == [MUSIC], "override lost because MA reported late"


async def test_reload_keeps_the_override(hass, house, freezer):
    await follow_from(hass, house)
    hub = next(e for e in hass.config_entries.async_entries(DOMAIN) if e.title == "Better Media Player")
    await hass.config_entries.async_reload(hub.entry_id)
    await hass.async_block_till_done()
    await tick(hass, freezer, 300)
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
    await tick(hass, freezer, 900)
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
    await tick(hass, freezer, 140)
    assert hass.states.get("sensor.living_room_follow").state != "override"


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
    await tick(hass, freezer, 60)
    assert hass.states.get(FOLLOW).state == "playing"


async def test_resume_after_video_with_presence_unknown_for_a_moment(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house)
    house.kodi("playing")
    await hass.async_block_till_done()
    hass.states.async_set(LIVING_OCC, "unavailable")
    await hass.async_block_till_done()
    house.kodi("off")
    await tick(hass, freezer, 20)
    hass.states.async_set(LIVING_OCC, "on")
    await tick(hass, freezer, 20)
    assert MUSIC in house.members(FOLLOW)


async def test_base_volume_with_unmanaged_member(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house, volume=0.15)  # base 25
    other = "media_player.unmanaged"
    hass.states.async_set(other, "playing", {"volume_level": 0.25})
    # MA group: living 15 % + unmanaged player 25 % -> average 20 %
    house.set(FOLLOW, "playing", group_members=[MUSIC, other], volume_level=0.20)
    assert abs(get_hub(hass).volume.base_volume(FOLLOW) - 25) < 0.01


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
    await tick(hass, freezer, 130)  # kitchen empties -> group paused, follow me over
    assert hass.states.get(FOLLOW).state == "paused"
    assert not get_hub(hass).active
    await tick(hass, freezer, 3600)
    house.kodi("off")  # movie over an hour later
    await tick(hass, freezer, 10)
    assert hass.states.get(FOLLOW).state != "playing"


async def test_short_pause_keeps_room_off(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    hass.states.async_set(KITCHEN_OCC, "on")
    await follow_from(hass, house)
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert house.members(FOLLOW) == [KITCHEN]
    await tick(hass, freezer, 60)
    house.set(FOLLOW, "paused")  # phone call: pause in the MA app
    await tick(hass, freezer, 30)
    house.group("playing", [KITCHEN], FOLLOW)  # resume in the MA app
    await tick(hass, freezer, 20)
    assert MUSIC not in house.members(FOLLOW), "short pause ended the session: turned-off room rejoined"


# ---------------------------------------------------- failures, stops, reloads


def _hub_entry(hass):
    return next(e for e in hass.config_entries.async_entries(DOMAIN) if e.title == "Better Media Player")


async def test_stop_while_paused_stays_paused(hass, house):
    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house)
    house.group("paused", [MUSIC], FOLLOW)  # paused by the user: follow me goes on
    await hass.async_block_till_done()
    assert get_hub(hass).active
    await hass.services.async_call(DOMAIN, "stop_follow", {"entity_id": ROOM}, blocking=True)
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
    await tick(hass, freezer, 40)
    assert not get_hub(hass).active
    house.transferred.clear()
    await follow_from(hass, house)  # music again directly on the room player
    assert house.transferred == [(MUSIC, FOLLOW)]
    assert hass.states.get(FOLLOW).state == "playing"
    await tick(hass, freezer, 60)
    assert get_hub(hass).active


async def test_two_stops_at_once_run_once(hass, house, caplog):
    import asyncio

    from custom_components.better_media_player.room import get_rooms

    hass.states.async_set(LIVING_OCC, "on")
    hass.states.async_set(KITCHEN_OCC, "on")
    await follow_from(hass, house)
    assert KITCHEN in house.members(FOLLOW)
    house.transferred.clear()
    hub = get_hub(hass)
    rooms = {r.entry.title: r for r in get_rooms(hass).values()}
    results = await asyncio.gather(
        hub.async_follow_stop(rooms["Living Room"]), hub.async_follow_stop(rooms["Kitchen"]), return_exceptions=True
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
        await hass.services.async_call(DOMAIN, "stop_follow", {"entity_id": ROOM}, blocking=True)
    assert not get_hub(hass).active


# ------------------------------------------- why follow me cannot start here


async def _start_error(hass, room=ROOM):
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "start_follow", {"entity_id": room}, blocking=True)
    return err.value


async def test_start_in_a_group_says_so(hass, house):
    from .conftest import GROUP

    house.group("playing", [MUSIC], GROUP)  # started in the group "All Players"
    await hass.async_block_till_done()
    err = await _start_error(hass)
    assert err.translation_key == "start_in_group"
    assert err.translation_placeholders == {"room": "Living Room", "player": "Office Player", "group": "All Players"}
    assert hass.states.get("button.living_room_follow_me").attributes["blocked_by"] == "start_in_group"
    assert "All Players" in str(err)


async def test_start_without_music_says_so(hass, house):
    err = await _start_error(hass)
    assert err.translation_key == "start_not_playing"
    assert err.translation_placeholders == {"room": "Living Room", "player": "Office Player"}


async def test_start_with_video_says_so(hass, house):
    house.music("playing")
    house.kodi("idle")  # Kodi in its menu
    await hass.async_block_till_done()
    err = await _start_error(hass)
    assert err.translation_key == "start_video"
    assert err.translation_placeholders["player"] == "Kodi"


async def test_start_in_quiet_mode_says_so(hass, house):
    house.music("playing")
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.living_room_quiet"}, blocking=True)
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
    assert hass.states.get("button.living_room_follow_me").attributes["blocked_by"] is None


async def test_start_while_left_in_the_paused_follow_player_says_so(hass, house):
    house.group("paused", [MUSIC], FOLLOW)
    await hass.async_block_till_done()
    err = await _start_error(hass)
    assert err.translation_key == "start_follow_paused"
    assert err.translation_placeholders["group"] == "All Players (follow)"


def _seeks(house, entity_id):
    return [d["seek_position"] for s, e, d in house.played if s == "media_seek" and e == entity_id]


async def _start(hass, house, freezer, position=90.0, duration=240):
    hass.states.async_set(LIVING_OCC, "on")
    house.music(
        "playing", volume_level=0.25, media_content_id=TRACK, media_duration=duration,
        media_position=position - 5, media_position_updated_at=house.now(),
    )
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)  # it played on for 5 s since its last report
    await hass.services.async_call(DOMAIN, "start_follow", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()


async def test_start_goes_on_where_the_track_was(hass, house, freezer):
    house.transfer_restarts = True
    await _start(hass, house, freezer)
    assert _seeks(house, FOLLOW) == [90.0]
    assert hass.states.get(FOLLOW).attributes["media_position"] == 90.0


async def test_no_seek_when_the_move_kept_the_position(hass, house, freezer):
    await _start(hass, house, freezer)
    house.set(FOLLOW, "playing", media_position=120.0, media_position_updated_at=dt_util.utcnow())
    await hass.async_block_till_done()
    await stop_follow(hass)  # the move back keeps the position
    assert hass.states.get(MUSIC).attributes["media_position"] == 120.0
    assert _seeks(house, MUSIC) == []


async def test_stop_goes_on_where_the_track_was(hass, house, freezer):
    await _start(hass, house, freezer)
    house.transfer_restarts = True
    house.set(FOLLOW, "playing", media_position=120.0, media_position_updated_at=dt_util.utcnow())
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    await stop_follow(hass)
    assert house.transferred[-1] == (FOLLOW, MUSIC)
    assert _seeks(house, MUSIC) == [123.0]


async def test_stop_in_another_room(hass, house, freezer):
    await _start(hass, house, freezer)
    hass.states.async_set(KITCHEN_OCC, "on")
    await hass.async_block_till_done()
    house.transfer_restarts = True
    await stop_follow(hass, KROOM)
    assert house.transferred[-1] == (FOLLOW, KITCHEN)
    assert len(_seeks(house, KITCHEN)) == 1


async def test_a_paused_stop_is_not_started(hass, house, freezer):
    await _start(hass, house, freezer)
    house.transfer_restarts = True
    house.group("paused", house.members(FOLLOW), FOLLOW)
    await hass.async_block_till_done()
    await stop_follow(hass)
    assert hass.states.get(MUSIC).state == "paused"
    assert _seeks(house, MUSIC) == []


async def test_near_the_end_of_the_track_nothing_is_set(hass, house, freezer):
    house.transfer_restarts = True
    await _start(hass, house, freezer, position=237)
    assert _seeks(house, FOLLOW) == []


async def test_another_track_by_then_is_left_alone(hass, house, freezer):
    house.transfer_restarts = True
    house.transfer_track = "library://track/43"
    await _start(hass, house, freezer)
    assert _seeks(house, FOLLOW) == []


async def test_without_a_position_report_nothing_is_set(hass, house, freezer):
    house.transfer_restarts = True
    hass.states.async_set(LIVING_OCC, "on")
    house.music("playing", volume_level=0.25, media_content_id=TRACK)
    await hass.async_block_till_done()
    await hass.services.async_call(DOMAIN, "start_follow", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(FOLLOW).state == "playing"
    assert _seeks(house, FOLLOW) == []


async def test_a_late_report_is_waited_for(hass, house, freezer):
    house.transfer_reports = False
    await _start(hass, house, freezer)
    assert _seeks(house, FOLLOW) == []
    await tick(hass, freezer, 2)
    house.set(FOLLOW, "playing", media_position=2.0, media_position_updated_at=dt_util.utcnow())
    await hass.async_block_till_done()
    assert _seeks(house, FOLLOW) == [90.0]


async def test_gives_up_without_a_report(hass, house, freezer):
    house.transfer_reports = False
    await _start(hass, house, freezer)
    await tick(hass, freezer, 30)
    house.set(FOLLOW, "playing", media_position=2.0, media_position_updated_at=dt_util.utcnow())
    await hass.async_block_till_done()
    assert _seeks(house, FOLLOW) == []


async def test_an_old_report_of_the_same_track_is_not_taken(hass, house, freezer):
    house.transfer_reports = False
    house.set(FOLLOW, "idle", media_content_id=TRACK, media_position=0.0, media_position_updated_at=dt_util.utcnow())
    await _start(hass, house, freezer)  # the old report is 5 s old by then
    assert _seeks(house, FOLLOW) == []


async def test_unloading_stops_the_watch(hass, house, freezer):
    house.transfer_reports = False
    await _start(hass, house, freezer)
    hub = next(e for e in hass.config_entries.async_entries(DOMAIN) if e.data.get("entry_type") == "hub")
    assert await hass.config_entries.async_unload(hub.entry_id)
    await hass.async_block_till_done()
    house.set(FOLLOW, "playing", media_position=2.0, media_position_updated_at=dt_util.utcnow())
    await hass.async_block_till_done()
    assert _seeks(house, FOLLOW) == []


async def test_a_stream_without_duration_is_not_set(hass, house, freezer):
    house.transfer_restarts = True
    await _start(hass, house, freezer, duration=None)  # radio
    assert _seeks(house, FOLLOW) == []


async def test_music_assistant_on_a_clock_behind(hass, house, freezer):
    house.transfer_restarts = True
    house.clock_skew = -10  # its reports look 10 s old to HA
    await _start(hass, house, freezer)
    assert len(_seeks(house, FOLLOW)) == 1


async def test_music_assistant_on_a_clock_ahead(hass, house, freezer):
    house.transfer_reports = False
    house.clock_skew = 10
    house.set(FOLLOW, "idle", media_content_id=TRACK, media_position=0.0, media_position_updated_at=house.now())
    await _start(hass, house, freezer)  # its old report looks fresh to HA: not taken
    assert _seeks(house, FOLLOW) == []
