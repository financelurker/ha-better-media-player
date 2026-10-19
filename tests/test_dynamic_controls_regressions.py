"""Dynamic controls: regressions from the review and bug injection."""

from __future__ import annotations

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState
from homeassistant.helpers import entity_registry as er

from custom_components.better_media_player.const import DOMAIN

from .conftest import KITCHEN_OCC, MUSIC, SPEAKERS, setup_hub, setup_room
from .test_dynamic_controls import NIGHT, PRESET, ROOM, SLEEP, SLEEP_PLUS, _hidden, _music, _shown, _tick

MUSIC_ONLY = {"video_player": None, "display": None, "speakers": None, "bass": None}


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
    await _tick(hass, freezer, 6)
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
    await _tick(hass, freezer, 3)
    assert PRESET not in log


async def test_rooms_before_the_central_entry_with_dynamic_controls_off(hass, home, freezer):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.better_media_player.const import CONF_ENTRY_TYPE, ENTRY_TYPE_HUB, HUB_DEFAULTS

    # the central entry exists (but is not set up yet) when the room loads
    hub = MockConfigEntry(
        domain=DOMAIN, title="Better Media Player", data={CONF_ENTRY_TYPE: ENTRY_TYPE_HUB},
        options={**HUB_DEFAULTS, "dynamic_controls": False}, version=2,
    )
    hub.add_to_hass(hass)
    log = _registry_log(hass)
    await setup_room(hass)  # (sets up the domain: the central entry comes along, in any order)
    await _tick(hass, freezer, 6)
    assert log == []


async def test_hidden_by_the_user_then_shown_stays_shown(hass, home, freezer):
    reg = er.async_get(hass)
    await setup_room(hass)
    await _music(hass, home, freezer)
    await _tick(hass, freezer, 5)
    reg.async_update_entity(PRESET, hidden_by=er.RegistryEntryHider.USER)  # the user hides it
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 6)
    reg.async_update_entity(PRESET, hidden_by=None)  # ... and shows it again
    await _tick(hass, freezer, 15)
    assert not _hidden(hass, PRESET)


async def test_paused_music_after_a_reload_stays_shown(hass, home, freezer):
    entry = await setup_room(hass)
    await _music(hass, home, freezer)
    await _tick(hass, freezer, 5)
    await _music(hass, home, freezer, "paused")
    await _tick(hass, freezer, 5)
    assert PRESET in _shown(hass)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 6)
    assert PRESET in _shown(hass)


async def test_a_new_hide_delay_also_for_a_pending_hide(hass, home, freezer):
    hub = await setup_hub(hass, hide_delay=60)
    await setup_room(hass)
    await _music(hass, home, freezer)
    await _tick(hass, freezer, 5)
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)  # the hide waits 60 s
    hass.config_entries.async_update_entry(hub, options={**hub.options, "hide_delay": 2})
    await hass.async_block_till_done()
    await _tick(hass, freezer, 4)
    assert PRESET not in _shown(hass)


# ------------------------------------------------------- from bug injection


async def test_controls_show_at_once_without_plugs(hass, home, freezer):
    await setup_room(hass, **MUSIC_ONLY)
    await _tick(hass, freezer, 6)
    home.music("playing")
    await hass.async_block_till_done()
    assert PRESET in _shown(hass), "shown before the source is announced"


async def test_controls_go_promptly_without_plugs(hass, home, freezer):
    await setup_room(hass, **MUSIC_ONLY)
    await _music(hass, home, freezer)
    await _tick(hass, freezer, 5)
    await _music(hass, home, freezer, "idle")
    await _tick(hass, freezer, 6)
    assert PRESET not in _shown(hass)


async def test_controls_show_during_the_turn_on(hass, home, freezer):
    await setup_room(hass, display_on_wait=8)
    await _tick(hass, freezer, 6)
    home.kodi_boots = True
    hass.async_create_task(
        hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    )
    await _tick(hass, freezer, 1)
    assert PRESET in _shown(hass)


async def test_sleep_controls_stay_while_the_timer_runs(hass, home, freezer):
    await setup_room(hass)
    await _music(hass, home, freezer)
    await hass.services.async_call(DOMAIN, "set_sleep_timer", {"entity_id": ROOM, "minutes": 30}, blocking=True)
    await hass.async_block_till_done()
    await _music(hass, home, freezer, "idle")  # stopped, the timer still runs
    await _tick(hass, freezer, 10)
    assert {SLEEP, SLEEP_PLUS} <= _shown(hass)
    assert PRESET not in _shown(hass)


async def test_night_mode_stays_while_follow_me_runs(hass, house, freezer):
    from .test_follow import follow_from

    await _tick(hass, freezer, 5)
    hass.states.async_set(KITCHEN_OCC, "on")
    await follow_from(hass, house, player="media_player.kitchen_player", room="media_player.kitchen")
    await _tick(hass, freezer, 10)
    assert not _hidden(hass, NIGHT)
