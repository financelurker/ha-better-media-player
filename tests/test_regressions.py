"""Regression tests for issues found in the independent code review."""
from __future__ import annotations
from .test_controller import _tick, ROOM
from .conftest import KODI, MUSIC, SPEAKERS, TV, TV_POWER, setup_room
from custom_components.better_media_player.const import CONF_DISPLAY_POWER, CONF_DISPLAY_STANDBY

HOST = "switch.kodi_box_plug"

async def test_off_order_host_before_display_with_standby(hass, home, freezer):
    hass.states.async_set(TV_POWER, "80")
    hass.states.async_set(HOST, "on")
    await setup_room(hass, **{CONF_DISPLAY_POWER: [TV_POWER], CONF_DISPLAY_STANDBY: True, "video_host": HOST})
    home.set(KODI, "idle", supported_features=0)
    await hass.async_block_till_done()
    assert home.on(TV)
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert home.on(TV)
    assert home.on(HOST), "host cut before display (order violation)"

async def test_turn_off_during_inflight_slow_on(hass, home, freezer):
    await setup_room(hass)
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.wohnzimmer_automatic_power"}, blocking=True)
    original = home.handle
    def late(domain, service, entity_id, data, context):
        if entity_id == SPEAKERS and service == "turn_on":
            home.calls.append((domain, service, entity_id)); return True
        return original(domain, service, entity_id, data, context)
    home.handle = late
    home.music("idle")
    await hass.services.async_call("media_player", "select_source", {"entity_id": ROOM, "source": "Office Player"}, blocking=True)
    await hass.async_block_till_done()
    assert ("switch", "turn_on", SPEAKERS) in home.calls
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    hass.states.async_set(SPEAKERS, "on")  # late report
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3600)
    assert not home.on(SPEAKERS), "speakers left on forever after room off"

async def test_lost_off_command_force_off_cleared(hass, home, freezer):
    await setup_room(hass)
    home.music("playing"); await hass.async_block_till_done()
    assert home.on(SPEAKERS)
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.wohnzimmer_automatic_power"}, blocking=True)
    original = home.handle
    lost = {"n": 0}
    def drop(domain, service, entity_id, data, context):
        if entity_id == SPEAKERS and service == "turn_off" and lost["n"] == 0:
            lost["n"] += 1; home.calls.append((domain, service, entity_id)); return True
        return original(domain, service, entity_id, data, context)
    home.handle = drop
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3600)
    assert not home.on(SPEAKERS), "lost off command never retried"

async def test_kodi_blip_after_room_off_turns_room_back_on(hass, home, freezer):
    home.kodi_suspends = False
    await setup_room(hass)
    home.kodi("idle"); await hass.async_block_till_done()
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert not home.on(TV)
    home.kodi("unavailable"); await hass.async_block_till_done()
    home.kodi("idle"); await hass.async_block_till_done()   # still suspending / reconnect
    assert not home.on(TV), "room switched back on without fresh playback"

async def test_unjoin_static_group_when_idle(hass, home):
    await setup_room(hass)
    home.group("off", [MUSIC])  # configured syncgroup, not playing
    await hass.async_block_till_done()
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert ("media_player", "unjoin", MUSIC) not in home.calls, "unjoined from idle static group"

async def test_reload_during_sequence_leaves_zombie(hass, home, freezer):
    entry = await setup_room(hass)
    old = entry.runtime_data
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert old.sequence_running
    seen = []
    orig0 = old.async_update
    async def spy0(reason=""):
        seen.append(reason); await orig0(reason)
    old.async_update = spy0
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert "sequence_done" not in seen, "old controller ran async_update after unload"
    await hass.async_block_till_done()
    calls = []
    orig = old.async_update
    async def spy(reason=""):
        calls.append(reason); await orig(reason)
    old.async_update = spy
    await hass.async_block_till_done()
    assert not old._started

async def test_manual_at_boot(hass, home):
    # plug restored as off then integration reports on before HA started
    from homeassistant.core import CoreState
    hass.set_state(CoreState.starting)
    entry = await setup_room(hass)
    hass.states.async_set(SPEAKERS, "on")
    await hass.async_block_till_done()
    assert not entry.runtime_data.manual, "boot-time state report flagged as manual"
