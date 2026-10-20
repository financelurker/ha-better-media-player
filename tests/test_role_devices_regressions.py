"""Roles with devices: regressions found in review."""

from __future__ import annotations

from .conftest import HARMONY, LG, SPEAKERS, TV, setup_room
from .test_role_devices import ROOM, _state, _tick, _turn_off, _turn_on


async def test_a_held_tv_turned_on_again_while_needed_stays_automatic(hass, home, freezer):
    hass.states.async_set(LG, "off")
    entry = await setup_room(hass, display=[LG])
    ctrl = entry.runtime_data
    home.kodi_boots = True
    await _turn_on(hass)
    home.kodi("playing")
    await hass.async_block_till_done()
    hass.states.async_set(LG, "off")  # own remote
    await _tick(hass, freezer, 40)
    assert LG in ctrl.held
    hass.states.async_set(LG, "on")  # own remote again, Kodi still plays (room needs it)
    await _tick(hass, freezer, 5)
    home.kodi("off")  # Kodi gone (its own remote)
    await _tick(hass, freezer, 1200)
    assert not ctrl.manual
    assert not home.on(SPEAKERS)


async def test_a_held_tv_is_switched_again_at_the_next_automatic_start(hass, home, freezer):
    home.lg_on_plug = True
    hass.states.async_set(LG, "unavailable")
    entry = await setup_room(hass, display=[TV, LG])
    ctrl = entry.runtime_data
    home.kodi_boots = True
    await _turn_on(hass)
    home.kodi("playing")
    await hass.async_block_till_done()
    hass.states.async_set(LG, "off")  # own remote while needed -> held
    await _tick(hass, freezer, 40)
    assert LG in ctrl.held
    home.kodi("off")  # Kodi suspended by its own remote
    await _tick(hass, freezer, 1500)  # display run-down 10 min
    assert not home.on(TV) and _state(hass, LG) == "unavailable"
    # next day: Kodi started by its own remote (automatic start, not the room's on)
    home.kodi("idle")
    await _tick(hass, freezer, 30)
    assert _state(hass, LG) == "on", "next start must switch the TV again"


async def test_a_device_gone_behind_its_plug_counts_as_off(hass, home, freezer):
    home.lg_on_plug = True
    hass.states.async_set(LG, "unavailable")
    entry = await setup_room(hass, display=[TV, LG])
    ctrl = entry.runtime_data
    home.kodi_boots = True
    await _turn_on(hass)
    await _turn_off(hass)
    await _tick(hass, freezer, 300)
    assert ctrl._fully_off()


async def test_the_plug_of_a_shared_device_stays_while_another_role_needs_it(hass, home, freezer):
    home.lg_on_plug = True
    hass.states.async_set(LG, "unavailable")
    await setup_room(hass, display=[TV, LG], speakers=[LG], bass=None)
    home.kodi_boots = True
    await _turn_on(hass)  # video
    home.kodi("playing")
    await hass.async_block_till_done()
    home.music("playing")
    await hass.async_block_till_done()
    home.kodi("off")
    await _tick(hass, freezer, 900)
    assert _state(hass, LG) == "on", "music still needs the TV's speakers"


async def test_a_device_woken_over_cec_during_the_start_is_no_manual_mode(hass, home, freezer):
    hass.states.async_set(LG, "off")
    hass.states.async_set(HARMONY, "off")
    entry = await setup_room(hass, display=[LG], speakers=[HARMONY], bass=None)
    ctrl = entry.runtime_data
    await ctrl.async_set_setting("display_on_wait", 5)
    orig = home.handle

    def handle(domain, service, entity_id, data, context):
        r = orig(domain, service, entity_id, data, context)
        if entity_id == LG and service == "turn_on":
            hass.loop.call_later(1, lambda: hass.states.async_set(HARMONY, "on"))  # ARC/CEC wakes the AVR
        return r

    home.handle = handle
    home.kodi_boots = True
    hass.async_create_task(_turn_on(hass))
    await _tick(hass, freezer, 20, step=1)
    assert not ctrl.manual


async def test_a_room_player_is_no_role_device(hass, home):
    from custom_components.better_media_player.config_flow import _validate
    from .conftest import room_options

    await setup_room(hass)
    assert _validate(hass, room_options(display=[ROOM])) == {"display": "device_is_room"}


async def test_a_tv_turned_on_by_hand_from_unavailable_is_manual(hass, home, freezer):
    hass.states.async_set(LG, "unavailable")  # e.g. a TV that drops off the network when off
    entry = await setup_room(hass, display=[LG])
    ctrl = entry.runtime_data
    await _tick(hass, freezer, 900)
    hass.states.async_set(LG, "on")  # live TV, by its own remote
    await _tick(hass, freezer, 10)
    assert ctrl.manual
    assert _state(hass, LG) == "on"


async def test_the_sleep_timer_ends_when_a_room_with_a_device_goes_off(hass, home, freezer):
    home.lg_on_plug = True
    hass.states.async_set(LG, "unavailable")
    entry = await setup_room(hass, display=[TV, LG])
    ctrl = entry.runtime_data
    home.kodi_boots = True
    await _turn_on(hass)
    home.kodi("playing")
    await hass.async_block_till_done()
    await ctrl.async_set_sleep_timer(120)
    home.kodi("off")
    await _tick(hass, freezer, 1500)
    assert not ctrl.sleep.active


async def test_a_network_tv_gone_in_deep_standby_counts_as_off(hass, home, freezer):
    hass.states.async_set(LG, "off")
    entry = await setup_room(hass, display=[LG])
    ctrl = entry.runtime_data
    home.kodi_boots = True
    await _turn_on(hass)
    await _turn_off(hass)
    hass.states.async_set(LG, "unavailable")  # network TV gone in deep standby
    await _tick(hass, freezer, 300)
    assert ctrl._fully_off()


async def test_music_powers_a_shared_device_through_the_display_plug(hass, home, freezer):
    home.lg_on_plug = True
    hass.states.async_set(LG, "unavailable")
    await setup_room(hass, display=[TV, LG], speakers=[LG], bass=None)
    home.music("playing")  # music only: the display is not needed, its plug is
    await _tick(hass, freezer, 10)
    assert home.on(TV)
    assert _state(hass, LG) == "on"


async def test_a_host_plug_used_by_a_role_is_shown_in_the_devices_step(hass, home):
    from homeassistant.data_entry_flow import FlowResultType

    entry = await setup_room(hass, video_host="switch.kodi_box_plug")
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "devices"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"display": [TV, "switch.kodi_box_plug"], "speakers": [SPEAKERS], "actions": {}, "standby": {}, "follow": {}},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"display": "duplicate_device"}


# ------------------------------------------------------- from bug injection


async def test_a_tv_on_its_own_app_counts_as_on(hass, home, freezer):
    hass.states.async_set(LG, "off")
    await setup_room(hass, display=[LG])
    home.kodi_boots = True
    await _turn_on(hass)
    hass.states.async_set(LG, "playing", context=None)  # (its own app; reported as playing)
    await hass.async_block_till_done()
    await _turn_off(hass)
    assert ("media_player", "turn_off", LG) in home.calls


async def test_a_failed_start_waits_for_the_device_before_its_plug(hass, home, freezer):
    hass.states.async_set(LG, "off")
    home.lg_obeys = False
    await setup_room(hass, display=[TV, LG])
    hass.states.async_set(LG, "on")  # (the TV comes on by itself with the plug)
    hass.async_create_task(_turn_on(hass))
    await _tick(hass, freezer, 95)  # Kodi never answers: rolled back
    assert ("media_player", "turn_off", LG) in home.calls
    assert home.on(TV), "the TV still reports on: its plug waits"
    await _tick(hass, freezer, 70)
    assert not home.on(TV)


async def test_the_rooms_own_turn_on_switches_a_held_tv_again(hass, home, freezer):
    hass.states.async_set(LG, "off")
    entry = await setup_room(hass, display=[LG])
    home.kodi_boots = True
    await _turn_on(hass)
    home.kodi("playing")
    await hass.async_block_till_done()
    hass.states.async_set(LG, "off")
    await _tick(hass, freezer, 10)
    assert LG in entry.runtime_data.held
    await _turn_on(hass)  # the room's power button
    assert _state(hass, LG) == "on"


async def test_no_standby_settings_for_a_role_without_a_plug(hass, home):
    from homeassistant.helpers import entity_registry as er

    hass.states.async_set(LG, "off")
    await setup_room(hass, display=[LG], display_power=["sensor.tv_power"])
    reg = er.async_get(hass)
    assert reg.async_get("switch.wohnzimmer_display_wait_for_standby") is None
    assert reg.async_get("number.wohnzimmer_display_standby_below") is None


async def test_a_device_without_a_plug_is_not_waited_for(hass, home, freezer):
    hass.states.async_set(LG, "unavailable")  # a network TV, gone while off
    await setup_room(hass, display=[LG])
    home.kodi_boots = True
    await _turn_on(hass)
    assert home.calls and ("media_player", "turn_on", "media_player.kodi") in home.calls


async def test_an_unavailable_plug_does_not_restart_the_start_forever(hass, home, freezer):
    hass.states.async_set(TV, "unavailable")
    await setup_room(hass)
    home.kodi("playing")
    await _tick(hass, freezer, 20)
    assert _state(hass, "sensor.wohnzimmer_status") == "playing"


async def test_display_actions_need_a_video_player(hass, home):
    from custom_components.better_media_player.config_flow import _validate

    from .conftest import room_options

    options = room_options(video_player=None, display=None, display_on_action="script.a", display_off_action="script.b")
    assert _validate(hass, options).get("display") == "display_needs_video"
