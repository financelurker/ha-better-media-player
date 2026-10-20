"""Follow me: started in a room, follows the people, stopped back into one room."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
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
from custom_components.better_media_player.hub import get_hub

from .conftest import (
    BASS,
    FOLLOW,
    GROUP,
    KITCHEN,
    KITCHEN_OCC,
    KITCHEN_SPK,
    LIVING_OCC,
    MUSIC,
    SPEAKERS,
    TV,
    setup_hub,
    setup_room,
)

LIVING = "media_player.wohnzimmer"
KROOM = "media_player.kitchen"
START = "button.wohnzimmer_follow_me"
STOP = "button.wohnzimmer_stop_follow_me"
KSTART = "button.kitchen_follow_me"
KSTOP = "button.kitchen_stop_follow_me"


async def _tick(hass: HomeAssistant, freezer, seconds: float) -> None:
    left = seconds
    while left > 0:
        freezer.tick(timedelta(seconds=min(10, left)))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        left -= 10


async def follow_from(hass, house, player=MUSIC, room=LIVING, volume=0.25):
    """Music plays in a room on its own player; follow me is started there."""
    if player == MUSIC:
        house.music("playing", volume_level=volume)
    else:
        house.kitchen("playing", volume_level=volume)
    await hass.async_block_till_done()
    await hass.services.async_call(DOMAIN, "start_follow", {"entity_id": room}, blocking=True)
    await hass.async_block_till_done()


async def stop_follow(hass, room=LIVING):
    await hass.services.async_call(DOMAIN, "stop_follow", {"entity_id": room}, blocking=True)
    await hass.async_block_till_done()


def _calls(home, service):
    return [c[2] for c in home.calls if c[1] == service]


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
    assert hass.states.get("sensor.wohnzimmer_follow").state == "following"
    assert hass.states.get("sensor.better_media_player_follow_rooms").state == "1"


async def test_start_needs_music_on_the_room_player(hass, house):
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "start_follow", {"entity_id": LIVING}, blocking=True)


async def test_start_not_with_video(hass, house):
    house.music("playing")
    house.kodi("playing")
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "start_follow", {"entity_id": LIVING}, blocking=True)


async def test_start_not_in_a_quiet_room(hass, house):
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.wohnzimmer_quiet"}, blocking=True)
    house.music("playing")
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "start_follow", {"entity_id": LIVING}, blocking=True)


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
    await hass.services.async_call(DOMAIN, "start_follow", {"entity_id": LIVING}, blocking=True)
    await hass.async_block_till_done()
    assert house.transferred == []  # already there
    assert get_hub(hass).active


# --------------------------------------------------------------- buttons


async def test_buttons_shown_only_when_usable(hass, house, freezer):
    await _tick(hass, freezer, 5)  # hidden after the start and the hide delay
    reg = er.async_get(hass)
    hidden = er.RegistryEntryHider.INTEGRATION
    assert reg.async_get(START).hidden_by == hidden  # nothing plays
    assert reg.async_get(STOP).hidden_by == hidden

    house.music("playing")
    await hass.async_block_till_done()
    assert reg.async_get(START).hidden_by is None
    assert reg.async_get(KSTART).hidden_by == hidden  # nothing plays in the kitchen

    await hass.services.async_call("button", "press", {"entity_id": START}, blocking=True)
    await hass.async_block_till_done()
    assert get_hub(hass).active
    await _tick(hass, freezer, 4)  # hidden after the hide delay (3 s)
    assert reg.async_get(START).hidden_by == hidden  # follow me runs: no start anywhere
    assert reg.async_get(STOP).hidden_by is None
    assert reg.async_get(KSTOP).hidden_by is None

    await hass.services.async_call("button", "press", {"entity_id": STOP}, blocking=True)
    await hass.async_block_till_done()
    assert not get_hub(hass).active
    await _tick(hass, freezer, 4)
    assert reg.async_get(STOP).hidden_by == hidden


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

    await _tick(hass, freezer, 60)
    assert MUSIC in house.members(FOLLOW), "presence run-down (2 min) not over"
    await _tick(hass, freezer, 70)
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
    await _tick(hass, freezer, 130)
    assert house.members(FOLLOW) == []
    assert hass.states.get(FOLLOW).state == "paused"
    assert not get_hub(hass).active  # follow me is over
    await _tick(hass, freezer, 130)
    assert not house.on(SPEAKERS) and not house.on(KITCHEN_SPK)
    assert hass.states.get("sensor.kitchen_follow").state == "inactive"


async def test_follow_player_stopped_ends_follow_me(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house)
    await _tick(hass, freezer, 40)
    house.group("idle", house.members(FOLLOW), FOLLOW)  # stopped in the app
    await _tick(hass, freezer, 10)
    assert not get_hub(hass).active


async def test_presence_unknown_changes_nothing(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    await follow_from(hass, house)
    hass.states.async_set(LIVING_OCC, "unavailable")
    await _tick(hass, freezer, 900)
    assert house.members(FOLLOW) == [MUSIC]
    assert hass.states.get("sensor.wohnzimmer_follow").state == "presence_unknown"


async def test_nobody_detected_keeps_the_start_room(hass, house, freezer):
    """Started without presence (e.g. motion sensor misses the listener): kept as override."""
    await follow_from(hass, house)
    await _tick(hass, freezer, 600)
    assert house.members(FOLLOW) == [MUSIC]
    assert hass.states.get("sensor.wohnzimmer_follow").state == "override"


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
    await _tick(hass, freezer, 140)
    assert house.members(FOLLOW) == [KITCHEN]
    await stop_follow(hass, LIVING)  # the living room plays no longer along
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
    await _tick(hass, freezer, 30)
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
    assert hass.states.get("sensor.wohnzimmer_follow").state == "excluded_video"
    assert house.on(TV)

    house.kodi("off")
    await hass.async_block_till_done()
    assert MUSIC in house.members(FOLLOW)


async def test_quiet_switch_leaves_and_cuts_speakers(hass, house):
    hass.states.async_set(LIVING_OCC, "on")
    hass.states.async_set(KITCHEN_OCC, "on")
    await follow_from(hass, house)
    assert KITCHEN in house.members(FOLLOW) and house.on(KITCHEN_SPK)

    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.kitchen_quiet"}, blocking=True)
    await hass.async_block_till_done()
    assert KITCHEN not in house.members(FOLLOW)
    assert not house.on(KITCHEN_SPK), "speakers off right away"
    assert hass.states.get("sensor.kitchen_follow").state == "excluded_quiet"

    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.kitchen_quiet"}, blocking=True)
    await hass.async_block_till_done()
    assert KITCHEN in house.members(FOLLOW)


async def test_room_off_stays_out_for_the_session(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    hass.states.async_set(KITCHEN_OCC, "on")
    await follow_from(hass, house)
    await hass.services.async_call("media_player", "turn_off", {"entity_id": LIVING}, blocking=True)
    await hass.async_block_till_done()
    assert house.members(FOLLOW) == [KITCHEN]
    await _tick(hass, freezer, 60)
    assert MUSIC not in house.members(FOLLOW), "still in the room, but turned off"
    assert not house.on(SPEAKERS), "room off: no prewarming either"
    assert hass.states.get("sensor.wohnzimmer_follow").state == "excluded_off"


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
    await _tick(hass, freezer, 10)
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
    await _tick(hass, freezer, 300)
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
    await _tick(hass, freezer, 300)
    assert get_hub(hass).active
    assert house.members(FOLLOW) == [MUSIC]
    hass.states.async_set(LIVING_OCC, "off")  # sensors are back: normal rules again
    await _tick(hass, freezer, 130)
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
         "standby": {}, "follow": {CONF_OCCUPANCY: [LIVING_OCC]}},
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "save"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_OCCUPANCY] == [LIVING_OCC]
    assert hass.states.get("sensor.wohnzimmer_follow") is not None
