"""Follow me (v0.2): central settings, group policies, join/leave by presence."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from custom_components.better_media_player.const import (
    CONF_ALWAYS_GROUPS,
    CONF_AUDIO_PLAYER,
    CONF_DEFAULT_VOLUME,
    CONF_ENTRY_TYPE,
    CONF_FOLLOW_GROUPS,
    CONF_OCCUPANCY,
    CONF_SPEAKERS,
    CONF_VOLUME_OFFSET,
    DEFAULTS,
    DOMAIN,
    ENTRY_TYPE_HUB,
    ENTRY_TYPE_ROOM,
    HUB_DEFAULTS,
)

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
    setup_room,
)

LIVING = "media_player.wohnzimmer"


async def _tick(hass: HomeAssistant, freezer, seconds: float) -> None:
    left = seconds
    while left > 0:
        freezer.tick(timedelta(seconds=min(10, left)))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        left -= 10


async def setup_hub(hass: HomeAssistant, **overrides) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Better Media Player",
        data={CONF_ENTRY_TYPE: ENTRY_TYPE_HUB},
        options={**HUB_DEFAULTS, CONF_FOLLOW_GROUPS: [FOLLOW], CONF_ALWAYS_GROUPS: [GROUP], **overrides},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def setup_kitchen(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Kitchen",
        data={CONF_ENTRY_TYPE: ENTRY_TYPE_ROOM},
        options={**DEFAULTS, CONF_AUDIO_PLAYER: KITCHEN, CONF_SPEAKERS: [KITCHEN_SPK], CONF_OCCUPANCY: [KITCHEN_OCC]},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.fixture
async def house(hass, home):
    """Living room (Kodi + music, offset -10) and kitchen (music only), both with occupancy."""
    hass.states.async_set(LIVING_OCC, "off")
    hass.states.async_set(KITCHEN_OCC, "off")
    hass.states.async_set(KITCHEN_SPK, "off")
    home.kitchen("idle")
    home.group("idle", [], FOLLOW)
    home.group("idle", [], GROUP)
    await setup_room(hass, occupancy=[LIVING_OCC], volume_offset=-10)
    await setup_kitchen(hass)
    await setup_hub(hass)
    return home


async def _start_follow(hass, **data):
    await hass.services.async_call(DOMAIN, "start_follow", data, blocking=True)
    await hass.async_block_till_done()


def _calls(home, service):
    return [c[2] for c in home.calls if c[1] == service]


# ------------------------------------------------------------------ start


async def test_start_follow_joins_present_rooms_and_prewarms(hass, house):
    hass.states.async_set(LIVING_OCC, "on")
    await _start_follow(hass)

    assert house.members(FOLLOW) == [MUSIC]
    assert hass.states.get(FOLLOW).state == "playing"
    # volume first (default 25 % - 10 offset), then join
    order = [c for c in house.calls if c[2] in (MUSIC, FOLLOW) and c[1] in ("volume_set", "join")]
    assert order[0] == ("media_player", "volume_set", MUSIC)
    assert hass.states.get(MUSIC).attributes["volume_level"] == pytest.approx(0.15)
    # kitchen is empty but prewarmed: speakers already on
    assert house.on(SPEAKERS) and house.on(BASS) and house.on(KITCHEN_SPK)
    assert KITCHEN not in house.members(FOLLOW)
    assert hass.states.get("sensor.kitchen_follow").state == "waiting"
    assert hass.states.get("sensor.wohnzimmer_follow").state == "following"
    assert hass.states.get("sensor.better_media_player_follow_rooms").state == "1"


async def test_start_without_anyone_uses_fallback_as_override(hass, house, freezer):
    await _start_follow(hass, fallback_rooms=[LIVING])
    assert house.members(FOLLOW) == [MUSIC]
    await _tick(hass, freezer, 600)
    assert house.members(FOLLOW) == [MUSIC], "nobody anywhere: the fallback room stays"
    assert hass.states.get("sensor.wohnzimmer_follow").state == "override"

    hass.states.async_set(KITCHEN_OCC, "on")  # someone shows up in the kitchen
    await hass.async_block_till_done()
    assert KITCHEN in house.members(FOLLOW)
    await _tick(hass, freezer, 130)
    assert house.members(FOLLOW) == [KITCHEN], "override ends once presence is seen"


async def test_start_without_anyone_and_without_fallback_fails(hass, house):
    with pytest.raises(HomeAssistantError):
        await _start_follow(hass)


async def test_button_resumes_in_present_rooms(hass, house):
    hass.states.async_set(KITCHEN_OCC, "on")
    await hass.async_block_till_done()
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.better_media_player_start_follow"}, blocking=True
    )
    await hass.async_block_till_done()
    assert house.members(FOLLOW) == [KITCHEN]
    assert ("media_player", "media_play", FOLLOW) in house.calls


async def test_start_follow_with_media_and_volume(hass, house):
    hass.states.async_set(KITCHEN_OCC, "on")
    await _start_follow(hass, media_content_id="library://radio/42", volume=0.4)
    assert ("media_player", "play_media", FOLLOW) in house.calls
    assert hass.states.get(KITCHEN).attributes["volume_level"] == pytest.approx(0.4)


# --------------------------------------------------------------- walking


async def test_music_follows_through_the_house(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    await _start_follow(hass)

    hass.states.async_set(KITCHEN_OCC, "on")
    hass.states.async_set(LIVING_OCC, "off")
    await hass.async_block_till_done()
    assert set(house.members(FOLLOW)) == {MUSIC, KITCHEN}

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


async def test_house_empty_pauses_and_powers_down(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    await _start_follow(hass)
    hass.states.async_set(LIVING_OCC, "off")
    await _tick(hass, freezer, 130)
    assert house.members(FOLLOW) == []
    assert hass.states.get(FOLLOW).state == "paused"
    await _tick(hass, freezer, 130)
    assert not house.on(SPEAKERS) and not house.on(KITCHEN_SPK)
    assert hass.states.get("sensor.kitchen_follow").state == "inactive"


async def test_presence_unknown_changes_nothing(hass, house, freezer):
    hass.states.async_set(LIVING_OCC, "on")
    await _start_follow(hass)
    hass.states.async_set(LIVING_OCC, "unavailable")
    await _tick(hass, freezer, 900)
    assert house.members(FOLLOW) == [MUSIC]
    assert hass.states.get("sensor.wohnzimmer_follow").state == "presence_unknown"


# ------------------------------------------------------------- exclusions


async def test_never_steals_from_another_group(hass, house, freezer):
    house.group("playing", [KITCHEN], GROUP)  # kitchen plays "All Players"
    hass.states.async_set(LIVING_OCC, "on")
    await _start_follow(hass)
    hass.states.async_set(KITCHEN_OCC, "on")
    await _tick(hass, freezer, 30)
    assert KITCHEN not in house.members(FOLLOW)
    assert house.members(GROUP) == [KITCHEN]
    assert hass.states.get("sensor.kitchen_follow").state == "excluded_busy"


async def test_kodi_takes_the_room_out_and_back(hass, house):
    hass.states.async_set(LIVING_OCC, "on")
    hass.states.async_set(KITCHEN_OCC, "on")
    await _start_follow(hass)
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
    await _start_follow(hass)
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
    await _start_follow(hass)
    await hass.services.async_call("media_player", "turn_off", {"entity_id": LIVING}, blocking=True)
    await hass.async_block_till_done()
    assert house.members(FOLLOW) == [KITCHEN]
    await _tick(hass, freezer, 60)
    assert MUSIC not in house.members(FOLLOW), "still in the room, but turned off"
    assert not house.on(SPEAKERS), "room off: no prewarming either"
    assert hass.states.get("sensor.wohnzimmer_follow").state == "excluded_off"

    # session ends (everyone leaves) and a new one starts: the room is back
    hass.states.async_set(KITCHEN_OCC, "off")
    hass.states.async_set(LIVING_OCC, "off")
    await _tick(hass, freezer, 130)
    assert house.members(FOLLOW) == []
    hass.states.async_set(LIVING_OCC, "on")
    await _start_follow(hass)
    assert MUSIC in house.members(FOLLOW)


# ----------------------------------------------------------------- volume


async def test_volume_remembered_when_group_was_empty(hass, house, freezer):
    hass.states.async_set(KITCHEN_OCC, "on")
    await _start_follow(hass)
    house.set(FOLLOW, "playing", volume_level=0.32)
    await _tick(hass, freezer, 10)
    hass.states.async_set(KITCHEN_OCC, "off")
    await _tick(hass, freezer, 130)  # last member leaves -> paused, volume 0
    assert hass.states.get(FOLLOW).attributes["volume_level"] == 0

    hass.states.async_set(LIVING_OCC, "on")
    await _start_follow(hass)
    # remembered 32 % - 10 offset
    assert hass.states.get(MUSIC).attributes["volume_level"] == pytest.approx(0.22)


# ----------------------------------------------------------------- policies


async def test_ignored_group_does_not_power_speakers(hass, home):
    other = "media_player.all_players"  # configured as nothing
    home.group("idle", [], other)
    await setup_room(hass)
    await setup_hub(hass, **{CONF_ALWAYS_GROUPS: []})
    home.group("playing", [MUSIC], other)
    await hass.async_block_till_done()
    assert not home.on(SPEAKERS)


async def test_always_group_powers_speakers(hass, home):
    home.group("idle", [], GROUP)
    await setup_room(hass)
    await setup_hub(hass)
    home.group("playing", [MUSIC], GROUP)
    await hass.async_block_till_done()
    assert home.on(SPEAKERS)


async def test_without_hub_every_group_counts(hass, home):
    await setup_room(hass)
    home.group("playing", [MUSIC], "media_player.all_players")
    await hass.async_block_till_done()
    assert home.on(SPEAKERS)


# ----------------------------------------------------------------- restart


async def test_restart_while_follow_plays_keeps_members(hass, home, freezer):
    hass.states.async_set(LIVING_OCC, "unavailable")
    home.group("playing", [MUSIC], FOLLOW)
    await setup_room(hass, occupancy=[LIVING_OCC])
    await setup_hub(hass)
    await _tick(hass, freezer, 300)
    assert home.members(FOLLOW) == [MUSIC]
    hass.states.async_set(LIVING_OCC, "off")  # sensors are back: normal rules again
    await _tick(hass, freezer, 130)
    assert home.members(FOLLOW) == []


# --------------------------------------------------------------- config flow


async def test_hub_flow_and_single_instance(hass, home):
    home.group("idle", [], FOLLOW)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "hub"})
    assert result["step_id"] == "hub"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_FOLLOW_GROUPS: [FOLLOW], CONF_ALWAYS_GROUPS: [FOLLOW]}
    )
    assert result["errors"] == {CONF_ALWAYS_GROUPS: "group_twice"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_FOLLOW_GROUPS: [FOLLOW], CONF_DEFAULT_VOLUME: 30}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"][CONF_DEFAULT_VOLUME] == 30
    await hass.async_block_till_done()

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "hub"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "hub_exists"


async def test_room_follow_options(hass, home):
    entry = await setup_room(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "follow"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_OCCUPANCY: [LIVING_OCC], CONF_VOLUME_OFFSET: -10}
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "save"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_OCCUPANCY] == [LIVING_OCC]
    assert hass.states.get("sensor.wohnzimmer_follow") is not None


async def test_quiet_room_is_not_prewarmed(hass, house):
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.kitchen_quiet"}, blocking=True)
    hass.states.async_set(LIVING_OCC, "on")
    await _start_follow(hass)
    assert house.on(SPEAKERS)
    assert not house.on(KITCHEN_SPK)


async def test_quiet_cuts_speakers_even_if_music_assistant_is_slow(hass, house):
    """Real MA reports the unjoin a bit later: the speakers must still go off."""
    hass.states.async_set(KITCHEN_OCC, "on")
    await _start_follow(hass)
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
