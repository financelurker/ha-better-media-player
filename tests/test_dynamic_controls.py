"""Dynamic controls: only what makes sense right now is shown."""

from __future__ import annotations

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState
from homeassistant.helpers import entity_registry as er

from custom_components.better_media_player.const import CONF_ENTRY_TYPE, DOMAIN, ENTRY_TYPE_HUB

from .conftest import KITCHEN_OCC, LIVING_OCC, SPEAKERS, setup_hub, setup_room
from .helpers import (
    MUSIC_ONLY,
    NIGHT,
    PRESET,
    QUIET,
    ROOM,
    SLEEP,
    SLEEP_END,
    SLEEP_PLUS,
    WAKE,
    follow_from,
    tick,
)

ALL_OFF = "button.living_room_all_off"
FOLLOW_ROOMS = "sensor.better_media_player_follow_rooms"
ROOM_CONTROLS = [ALL_OFF, SLEEP, SLEEP_PLUS, SLEEP_END, NIGHT, QUIET, WAKE, PRESET]
ALWAYS = [ROOM, "switch.living_room_automatic_power", "number.living_room_night_maximum_volume",
          "number.living_room_preset_normal", "sensor.living_room_status"]


def _hidden(hass, entity_id) -> bool:
    return er.async_get(hass).async_get(entity_id).hidden_by == er.RegistryEntryHider.INTEGRATION


def _shown(hass) -> set[str]:
    return {e for e in ROOM_CONTROLS if not _hidden(hass, e)}


async def _music(hass, home, freezer, state="playing"):
    home.music(state)
    await hass.async_block_till_done()
    await tick(hass, freezer, 1)


async def test_nothing_plays_only_the_room_player_and_night_mode(hass, home, freezer):
    await setup_room(hass)
    await tick(hass, freezer, 5)
    assert _shown(hass) == {NIGHT}  # night mode is set ahead: always shown
    assert not any(_hidden(hass, e) for e in ALWAYS)


async def test_playback_shows_its_controls_at_once(hass, home, freezer):
    await setup_room(hass)
    await tick(hass, freezer, 5)
    await _music(hass, home, freezer)
    assert _shown(hass) == {ALL_OFF, SLEEP, SLEEP_PLUS, NIGHT, PRESET}


async def test_controls_go_after_the_hide_delay(hass, home, freezer):
    await setup_room(hass)
    await _music(hass, home, freezer)
    await tick(hass, freezer, 5)
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 1)
    assert PRESET in _shown(hass), "not before the delay"
    await tick(hass, freezer, 6)
    assert _shown(hass) == {NIGHT}


async def test_all_off_stays_while_something_has_power(hass, home, freezer):
    await setup_room(hass)
    await _music(hass, home, freezer)
    await tick(hass, freezer, 5)
    await _music(hass, home, freezer, "idle")  # music stopped: speakers run down (2 min)
    await tick(hass, freezer, 10)
    assert home.on(SPEAKERS)
    assert _shown(hass) == {ALL_OFF, NIGHT}
    await tick(hass, freezer, 130, step=5)
    assert not home.on(SPEAKERS)
    assert _shown(hass) == {NIGHT}


async def test_a_short_stop_between_two_items_hides_nothing(hass, home, freezer):
    await setup_room(hass)
    await _music(hass, home, freezer)
    await tick(hass, freezer, 5)
    home.music("idle")
    await hass.async_block_till_done()
    await tick(hass, freezer, 1)
    await _music(hass, home, freezer)
    await tick(hass, freezer, 10)
    assert PRESET in _shown(hass)


async def test_sleep_timer_shows_its_end_and_stays(hass, home, freezer):
    await setup_room(hass)
    await _music(hass, home, freezer)
    await hass.services.async_call(DOMAIN, "set_sleep_timer", {"entity_id": ROOM, "minutes": 30}, blocking=True)
    await hass.async_block_till_done()
    assert SLEEP_END in _shown(hass)


async def test_night_mode_stays_while_on(hass, home, freezer):
    await setup_room(hass)
    await tick(hass, freezer, 5)
    await hass.services.async_call("switch", "turn_on", {"entity_id": NIGHT}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert _shown(hass) == {NIGHT}


async def test_wake_shows_its_sensor(hass, home, freezer):
    import asyncio

    await setup_room(hass)
    await tick(hass, freezer, 5)
    await hass.services.async_call(
        DOMAIN, "wake", {"entity_id": ROOM, "media_content_id": "x", "media_content_type": "music"}, blocking=True
    )
    for _ in range(10):
        await asyncio.sleep(0)
        await hass.async_block_till_done()
    assert WAKE in _shown(hass)


# ---------------------------------------------------------------- follow me


async def test_quiet_and_night_are_set_ahead_in_follow_rooms(hass, house, freezer):
    await tick(hass, freezer, 5)
    assert not _hidden(hass, QUIET), "set before follow me starts"
    assert not _hidden(hass, NIGHT)
    assert _hidden(hass, FOLLOW_ROOMS)
    hass.states.async_set("binary_sensor.kitchen_occupancy", "on")
    await follow_from(hass, house, player="media_player.kitchen_player", room="media_player.kitchen")
    await hass.async_block_till_done()
    assert not _hidden(hass, FOLLOW_ROOMS)


async def test_quiet_without_follow_me_only_while_on(hass, home, freezer):
    await setup_room(hass)  # no occupancy sensors, no central settings
    await tick(hass, freezer, 5)
    assert _hidden(hass, QUIET)
    await hass.services.async_call("switch", "turn_on", {"entity_id": QUIET}, blocking=True)
    await hass.async_block_till_done()
    assert not _hidden(hass, QUIET)


async def test_quiet_stays_while_on(hass, house, freezer):
    await tick(hass, freezer, 5)
    await hass.services.async_call("switch", "turn_on", {"entity_id": QUIET}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert not _hidden(hass, QUIET)


# ---------------------------------------------------------- central options


async def test_dynamic_controls_off_shows_everything(hass, home, freezer):
    await setup_room(hass)
    await tick(hass, freezer, 5)
    assert _shown(hass) == {NIGHT}
    await setup_hub(hass, dynamic_controls=False)
    await tick(hass, freezer, 5)
    assert _shown(hass) == set(ROOM_CONTROLS)
    assert not _hidden(hass, FOLLOW_ROOMS)


async def test_hide_delay_from_the_central_settings(hass, home, freezer):
    await setup_hub(hass, hide_delay=10)
    await setup_room(hass)
    await _music(hass, home, freezer)
    await tick(hass, freezer, 5)
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 6)
    assert PRESET in _shown(hass)
    await tick(hass, freezer, 8)
    assert PRESET not in _shown(hass)


async def test_hub_without_follow_player(hass, home, freezer):
    from homeassistant import config_entries

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "hub"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"].value == "create_entry"
    assert result["options"] == {}
    await hass.async_block_till_done()
    # the central settings are on its device
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.better_media_player_dynamic_controls"}, blocking=True
    )
    await hass.async_block_till_done()
    await setup_room(hass)
    await tick(hass, freezer, 5)
    assert _shown(hass) == set(ROOM_CONTROLS)


async def test_hide_delay_on_the_central_device(hass, home, freezer):
    await setup_hub(hass)
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.better_media_player_hide_delay", "value": 10}, blocking=True
    )
    await setup_room(hass)
    await _music(hass, home, freezer)
    await tick(hass, freezer, 5)
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 6)
    assert PRESET in _shown(hass)
    await tick(hass, freezer, 8)
    assert PRESET not in _shown(hass)


async def test_user_shown_control_stays_shown(hass, home, freezer):
    await setup_room(hass)
    await tick(hass, freezer, 5)
    er.async_get(hass).async_update_entity(PRESET, hidden_by=None)  # the user shows it
    await _music(hass, home, freezer)
    await tick(hass, freezer, 5)
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    assert PRESET in _shown(hass)


async def test_night_mode_hidden_by_an_older_version_comes_back(hass, home, freezer):
    entry = await setup_room(hass)
    await tick(hass, freezer, 5)
    er.async_get(hass).async_update_entity(NIGHT, hidden_by=er.RegistryEntryHider.INTEGRATION)
    entry.runtime_data.data["controls"][er.async_get(hass).async_get(NIGHT).unique_id] = "hidden"
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert not _hidden(hass, NIGHT)


async def test_quiet_shows_when_follow_me_is_set_up(hass, home, freezer):
    await setup_room(hass, occupancy=[LIVING_OCC])
    await tick(hass, freezer, 5)
    assert _hidden(hass, QUIET)
    await setup_hub(hass)
    await hass.async_block_till_done()
    assert not _hidden(hass, QUIET)


async def test_quiet_hidden_with_central_settings_but_no_follow_player(hass, home, freezer):
    await setup_hub(hass, follow_player=None)
    await setup_room(hass, occupancy=[LIVING_OCC])
    await tick(hass, freezer, 5)
    assert _hidden(hass, QUIET), "without a follow-me player there is no follow me"


async def test_quiet_follows_the_central_settings(hass, home, freezer):
    hub = await setup_hub(hass)
    await setup_room(hass, occupancy=[LIVING_OCC])
    await tick(hass, freezer, 5)
    assert not _hidden(hass, QUIET)
    assert await hass.config_entries.async_reload(hub.entry_id)  # (an options change)
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert not _hidden(hass, QUIET), "a reload keeps it"
    hass.config_entries.async_update_entry(hub, options={**hub.options, "follow_player": None})
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert _hidden(hass, QUIET), "no follow-me player: no follow me"
    await hass.config_entries.async_remove(hub.entry_id)
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert _hidden(hass, QUIET)


def _registry_log(hass):
    out = []

    def _cb(event):
        if event.data.get("action") == "update" and "hidden_by" in event.data.get("changes", {}):
            out.append(event.data["entity_id"])

    hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, _cb)
    return out


async def test_restart_while_playing_hides_nothing(hass, home, freezer):
    hass.set_state(CoreState.not_running)
    home.music("unknown")
    hass.states.async_set(SPEAKERS, "on")
    log = _registry_log(hass)
    await setup_room(hass)
    home.music("playing")  # the player reports while Home Assistant starts
    await hass.async_block_till_done()
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    await tick(hass, freezer, 6)
    assert PRESET in _shown(hass)
    assert PRESET not in log, "hidden and shown again at the start"


async def test_restart_without_hide_delay_hides_nothing_before_the_players_report(hass, home, freezer):
    hass.set_state(CoreState.not_running)
    home.music("unknown")
    hass.states.async_set(SPEAKERS, "on")
    await setup_hub(hass, hide_delay=0)
    log = _registry_log(hass)
    await setup_room(hass)
    home.music("playing")
    await hass.async_block_till_done()
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert PRESET not in log


async def test_rooms_before_the_central_entry_with_dynamic_controls_off(hass, home, freezer):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.better_media_player.const import (
        CONF_ENTRY_TYPE,
        ENTRY_TYPE_HUB,
    )

    # the central entry exists (but is not set up yet) when the room loads
    hub = MockConfigEntry(
        domain=DOMAIN, title="Better Media Player", data={CONF_ENTRY_TYPE: ENTRY_TYPE_HUB},
        options={"dynamic_controls": False}, version=2,  # (moved to the device by the migration)
    )
    hub.add_to_hass(hass)
    log = _registry_log(hass)
    await setup_room(hass)  # (sets up the domain: the central entry comes along, in any order)
    await tick(hass, freezer, 6)
    assert log == []


async def test_hidden_by_the_user_then_shown_stays_shown(hass, home, freezer):
    reg = er.async_get(hass)
    await setup_room(hass)
    await _music(hass, home, freezer)
    await tick(hass, freezer, 5)
    reg.async_update_entity(PRESET, hidden_by=er.RegistryEntryHider.USER)  # the user hides it
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 6)
    reg.async_update_entity(PRESET, hidden_by=None)  # ... and shows it again
    await tick(hass, freezer, 15)
    assert not _hidden(hass, PRESET)


async def test_paused_music_after_a_reload_stays_shown(hass, home, freezer):
    entry = await setup_room(hass)
    await _music(hass, home, freezer)
    await tick(hass, freezer, 5)
    await _music(hass, home, freezer, "paused")
    await tick(hass, freezer, 5)
    assert PRESET in _shown(hass)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await tick(hass, freezer, 6)
    assert PRESET in _shown(hass)


async def test_a_new_hide_delay_also_for_a_pending_hide(hass, home, freezer):
    await setup_hub(hass, hide_delay=60)
    await setup_room(hass)
    await _music(hass, home, freezer)
    await tick(hass, freezer, 5)
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)  # the hide waits 60 s
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.better_media_player_hide_delay", "value": 2}, blocking=True
    )
    await hass.async_block_till_done()
    await tick(hass, freezer, 4)
    assert PRESET not in _shown(hass)


# --------------------------- without plugs, while turning on, central settings


async def test_controls_show_at_once_without_plugs(hass, home, freezer):
    await setup_room(hass, **MUSIC_ONLY)
    await tick(hass, freezer, 6)
    home.music("playing")
    await hass.async_block_till_done()
    assert PRESET in _shown(hass), "shown before the source is announced"


async def test_controls_go_promptly_without_plugs(hass, home, freezer):
    await setup_room(hass, **MUSIC_ONLY)
    await _music(hass, home, freezer)
    await tick(hass, freezer, 5)
    await _music(hass, home, freezer, "idle")
    await tick(hass, freezer, 6)
    assert PRESET not in _shown(hass)


async def test_controls_show_during_the_turn_on(hass, home, freezer):
    await setup_room(hass, display_on_wait=8)
    await tick(hass, freezer, 6)
    home.kodi_boots = True
    hass.async_create_task(
        hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    )
    await tick(hass, freezer, 1)
    assert PRESET in _shown(hass)


async def test_sleep_controls_stay_while_the_timer_runs(hass, home, freezer):
    await setup_room(hass)
    await _music(hass, home, freezer)
    await hass.services.async_call(DOMAIN, "set_sleep_timer", {"entity_id": ROOM, "minutes": 30}, blocking=True)
    await hass.async_block_till_done()
    await _music(hass, home, freezer, "idle")  # stopped, the timer still runs
    await tick(hass, freezer, 10)
    assert {SLEEP, SLEEP_PLUS} <= _shown(hass)
    assert PRESET not in _shown(hass)


async def test_night_mode_stays_while_follow_me_runs(hass, house, freezer):
    await tick(hass, freezer, 5)
    hass.states.async_set(KITCHEN_OCC, "on")
    await follow_from(hass, house, player="media_player.kitchen_player", room="media_player.kitchen")
    await tick(hass, freezer, 10)
    assert not _hidden(hass, NIGHT)


async def test_disabled_central_settings_use_the_defaults(hass, home, freezer):
    from homeassistant.config_entries import ConfigEntryDisabler

    hub = await setup_hub(hass, dynamic_controls=False)
    await setup_room(hass)
    await tick(hass, freezer, 5)
    assert PRESET in _shown(hass), "dynamic controls off: everything shown"
    await hass.config_entries.async_set_disabled_by(hub.entry_id, ConfigEntryDisabler.USER)
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert _hidden(hass, PRESET), "without central settings: dynamic controls on (default)"
    await _music(hass, home, freezer)
    await tick(hass, freezer, 2)
    assert PRESET in _shown(hass), "and they keep working"


async def test_failed_central_settings_use_the_defaults(hass, home, freezer):
    from unittest.mock import patch

    from homeassistant.config_entries import ConfigEntryState
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    hub = MockConfigEntry(
        domain=DOMAIN, title="Better Media Player", data={CONF_ENTRY_TYPE: ENTRY_TYPE_HUB}, options={}, version=3
    )
    hub.add_to_hass(hass)
    with patch("custom_components.better_media_player.hub.FollowManager.async_start", side_effect=RuntimeError):
        await hass.config_entries.async_setup(hub.entry_id)
        await hass.async_block_till_done()
    assert hub.state is ConfigEntryState.SETUP_ERROR
    await setup_room(hass)
    await tick(hass, freezer, 5)
    assert _hidden(hass, PRESET)
    await _music(hass, home, freezer)
    await tick(hass, freezer, 2)
    assert PRESET in _shown(hass)
