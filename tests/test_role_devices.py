"""Roles with devices that turn themselves on/off, mixed with plugs, and own actions."""

from __future__ import annotations

from .conftest import BASS, HARMONY, IR_OFF, IR_ON, KODI, LG, MUSIC, SPEAKERS, TV, TV_POWER, setup_room
from .helpers import ROOM, call_order, tick


def _calls(home, entity_id, service=None):
    return [c[1] for c in home.calls if c[2] == entity_id and (service is None or c[1] == service)]


async def _turn_on(hass):
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()


async def _turn_off(hass):
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()


def _state(hass, entity_id):
    return hass.states.get(entity_id).state


# ------------------------------------------------------------------ devices


async def test_a_tv_as_display(hass, home, freezer):
    hass.states.async_set(LG, "off")
    await setup_room(hass, display=[LG])
    home.kodi_boots = True
    await _turn_on(hass)
    assert _state(hass, LG) == "on"
    assert call_order(home, [LG, KODI, SPEAKERS], "turn_on") == [LG, KODI, SPEAKERS]
    await _turn_off(hass)
    assert _state(hass, LG) == "off"
    assert ("media_player", "turn_off", LG) in home.calls


async def test_a_remote_as_speakers(hass, home, freezer):
    hass.states.async_set(HARMONY, "off")
    entry = await setup_room(hass, speakers=[HARMONY], bass=None)
    entry.runtime_data.data["last_source"] = "audio"
    await _turn_on(hass)
    assert _state(hass, HARMONY) == "on"
    assert ("remote", "turn_on", HARMONY) in home.calls
    await _turn_off(hass)
    assert ("remote", "turn_off", HARMONY) in home.calls


async def test_plug_then_device(hass, home, freezer):
    home.lg_on_plug = True
    hass.states.async_set(LG, "unavailable")
    await setup_room(hass, display=[TV, LG])
    home.kodi_boots = True
    await _turn_on(hass)
    assert home.on(TV) and _state(hass, LG) == "on"
    assert call_order(home, [TV, LG, KODI], "turn_on") == [TV, LG, KODI]


async def test_a_device_that_comes_up_late_is_waited_for(hass, home, freezer):
    hass.states.async_set(LG, "unavailable")
    await setup_room(hass, display=[TV, LG])
    home.kodi_boots = True
    hass.async_create_task(_turn_on(hass))
    await tick(hass, freezer, 10)
    assert home.on(TV)
    assert not _calls(home, KODI, "turn_on"), "the TV comes first"
    hass.states.async_set(LG, "off")  # booted
    await hass.async_block_till_done()
    assert _state(hass, LG) == "on"
    assert _calls(home, KODI, "turn_on")


async def test_a_device_that_never_comes_is_left_out(hass, home, freezer):
    hass.states.async_set(LG, "unavailable")
    entry = await setup_room(hass, display=[TV, LG])
    home.kodi_boots = True
    hass.async_create_task(_turn_on(hass))
    await tick(hass, freezer, 100)  # start timeout 90 s
    assert _calls(home, KODI, "turn_on"), "the start goes on without it"
    assert LG in entry.runtime_data.held
    assert _state(hass, "sensor.living_room_status") == "on"


async def test_off_device_first_then_its_plug(hass, home, freezer):
    home.lg_on_plug = True
    hass.states.async_set(LG, "unavailable")
    await setup_room(hass, display=[TV, LG])
    home.kodi_boots = True
    await _turn_on(hass)
    await _turn_off(hass)
    assert call_order(home, [LG, TV], "turn_off") == [LG, TV]
    assert not home.on(TV)


async def test_the_plug_waits_for_the_device_to_go_off(hass, home, freezer):
    hass.states.async_set(LG, "off")
    await setup_room(hass, display=[TV, LG])
    home.kodi_boots = True
    await _turn_on(hass)
    home.lg_obeys = False  # the TV does not react to turn_off
    await _turn_off(hass)
    assert ("media_player", "turn_off", LG) in home.calls
    assert home.on(TV), "the TV still reports on"
    await tick(hass, freezer, 70)
    assert not home.on(TV), "after 60 s the plug cuts it"


async def test_standby_wait_after_the_device_is_off(hass, home, freezer):
    hass.states.async_set(LG, "off")
    hass.states.async_set(TV_POWER, "80")
    entry = await setup_room(hass, display=[TV, LG], display_power=[TV_POWER])
    await entry.runtime_data.async_set_setting("display_standby", True)
    home.kodi_boots = True
    await _turn_on(hass)
    await _turn_off(hass)
    assert _state(hass, LG) == "off"
    await tick(hass, freezer, 30)
    assert home.on(TV), "panel care: 80 W"
    hass.states.async_set(TV_POWER, "1")
    await tick(hass, freezer, 40)
    assert not home.on(TV)


async def test_a_device_only_role_has_no_standby_wait(hass, home, freezer):
    hass.states.async_set(LG, "off")
    hass.states.async_set(TV_POWER, "80")
    entry = await setup_room(hass, display=[LG], display_power=[TV_POWER])
    await entry.runtime_data.async_set_setting("display_standby", True)
    home.kodi_boots = True
    await _turn_on(hass)
    await _turn_off(hass)
    assert _state(hass, LG) == "off"
    assert _state(hass, "sensor.living_room_status") != "stopping"


# ---------------------------------------------------------- outside changes


async def test_a_tv_turned_off_by_hand_stays_off_until_the_next_start(hass, home, freezer):
    hass.states.async_set(LG, "off")
    entry = await setup_room(hass, display=[LG])
    home.kodi_boots = True
    await _turn_on(hass)
    home.kodi("playing")
    await hass.async_block_till_done()
    hass.states.async_set(LG, "off")  # the TV's own remote
    await tick(hass, freezer, 60)
    assert _state(hass, LG) == "off", "not turned back on"
    assert not entry.runtime_data.manual, "everything else stays automatic"
    assert home.on(SPEAKERS)
    await _turn_off(hass)
    await tick(hass, freezer, 300)
    await _turn_on(hass)
    assert _state(hass, LG) == "on", "the next start switches it again"


async def test_a_device_turned_on_by_hand_is_manual(hass, home, freezer):
    hass.states.async_set(LG, "off")
    entry = await setup_room(hass, display=[LG])
    hass.states.async_set(LG, "on")  # live TV, without the room
    await tick(hass, freezer, 900)
    assert entry.runtime_data.manual
    assert _state(hass, LG) == "on", "not run down"


async def test_a_plug_switched_by_hand_is_still_manual(hass, home, freezer):
    entry = await setup_room(hass)
    hass.states.async_set(TV, "on")
    await hass.async_block_till_done()
    assert entry.runtime_data.manual


# ------------------------------------------------------------ own actions


async def test_own_actions(hass, home, freezer):
    hass.states.async_set(IR_ON, "off")
    hass.states.async_set(IR_OFF, "off")
    entry = await setup_room(hass, bass=None, bass_on_action=IR_ON, bass_off_action=IR_OFF)
    entry.runtime_data.data["last_source"] = "audio"
    await _turn_on(hass)
    assert ("script", "turn_on", IR_ON) in home.calls
    assert call_order(home, [SPEAKERS, IR_ON], "turn_on") == [SPEAKERS, IR_ON]
    calls = len(home.calls)
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert ("script", "turn_on", IR_ON) not in home.calls[calls:], "assumed on: not sent again"
    await _turn_off(hass)
    assert ("script", "turn_on", IR_OFF) in home.calls


async def test_the_assumed_state_survives_a_reload(hass, home, freezer):
    hass.states.async_set(IR_ON, "off")
    hass.states.async_set(IR_OFF, "off")
    entry = await setup_room(hass, bass=None, bass_on_action=IR_ON, bass_off_action=IR_OFF)
    entry.runtime_data.data["last_source"] = "audio"
    await _turn_on(hass)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await _turn_off(hass)
    assert ("script", "turn_on", IR_OFF) in home.calls


async def test_night_mode_switches_an_action_subwoofer_off(hass, home, freezer):
    hass.states.async_set(IR_ON, "off")
    hass.states.async_set(IR_OFF, "off")
    entry = await setup_room(hass, bass=None, bass_on_action=IR_ON, bass_off_action=IR_OFF)
    entry.runtime_data.data["last_source"] = "audio"
    await _turn_on(hass)
    home.music("playing")
    await hass.async_block_till_done()
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.living_room_night_mode"}, blocking=True)
    await tick(hass, freezer, 10)
    assert ("script", "turn_on", IR_OFF) in home.calls
    assert home.on(SPEAKERS)


# --------------------------------------------------------- one device, two roles


async def test_a_tv_with_its_own_speakers(hass, home, freezer):
    hass.states.async_set(LG, "off")
    entry = await setup_room(hass, display=[LG], speakers=[LG], bass=[BASS])
    entry.runtime_data.data["last_source"] = "audio"
    await _turn_on(hass)
    home.music("playing")
    await hass.async_block_till_done()
    assert _state(hass, LG) == "on", "music needs its speakers"
    await tick(hass, freezer, 60)
    assert _state(hass, LG) == "on", "the display's run-down does not cut what the speakers need"
    home.music("idle")
    await tick(hass, freezer, 200)
    assert _state(hass, LG) == "off"
    assert MUSIC  # (the music player stays the room's)


async def test_a_held_tv_turned_on_again_while_needed_stays_automatic(hass, home, freezer):
    hass.states.async_set(LG, "off")
    entry = await setup_room(hass, display=[LG])
    ctrl = entry.runtime_data
    home.kodi_boots = True
    await _turn_on(hass)
    home.kodi("playing")
    await hass.async_block_till_done()
    hass.states.async_set(LG, "off")  # own remote
    await tick(hass, freezer, 40)
    assert LG in ctrl.held
    hass.states.async_set(LG, "on")  # own remote again, Kodi still plays (room needs it)
    await tick(hass, freezer, 5)
    home.kodi("off")  # Kodi gone (its own remote)
    await tick(hass, freezer, 1200)
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
    await tick(hass, freezer, 40)
    assert LG in ctrl.held
    home.kodi("off")  # Kodi suspended by its own remote
    await tick(hass, freezer, 1500)  # display run-down 10 min
    assert not home.on(TV) and _state(hass, LG) == "unavailable"
    # next day: Kodi started by its own remote (automatic start, not the room's on)
    home.kodi("idle")
    await tick(hass, freezer, 30)
    assert _state(hass, LG) == "on", "next start must switch the TV again"


async def test_a_device_gone_behind_its_plug_counts_as_off(hass, home, freezer):
    home.lg_on_plug = True
    hass.states.async_set(LG, "unavailable")
    entry = await setup_room(hass, display=[TV, LG])
    ctrl = entry.runtime_data
    home.kodi_boots = True
    await _turn_on(hass)
    await _turn_off(hass)
    await tick(hass, freezer, 300)
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
    await tick(hass, freezer, 900)
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
    await tick(hass, freezer, 20)
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
    await tick(hass, freezer, 900)
    hass.states.async_set(LG, "on")  # live TV, by its own remote
    await tick(hass, freezer, 10)
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
    await tick(hass, freezer, 1500)
    assert not ctrl.sleep.active


async def test_a_network_tv_gone_in_deep_standby_counts_as_off(hass, home, freezer):
    hass.states.async_set(LG, "off")
    entry = await setup_room(hass, display=[LG])
    ctrl = entry.runtime_data
    home.kodi_boots = True
    await _turn_on(hass)
    await _turn_off(hass)
    hass.states.async_set(LG, "unavailable")  # network TV gone in deep standby
    await tick(hass, freezer, 300)
    assert ctrl._fully_off()


async def test_music_powers_a_shared_device_through_the_display_plug(hass, home, freezer):
    home.lg_on_plug = True
    hass.states.async_set(LG, "unavailable")
    await setup_room(hass, display=[TV, LG], speakers=[LG], bass=None)
    home.music("playing")  # music only: the display is not needed, its plug is
    await tick(hass, freezer, 10)
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


# -------------------------------- devices on their own, failed starts, actions


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
    await tick(hass, freezer, 95)  # Kodi never answers: rolled back
    assert ("media_player", "turn_off", LG) in home.calls
    assert home.on(TV), "the TV still reports on: its plug waits"
    await tick(hass, freezer, 70)
    assert not home.on(TV)


async def test_the_rooms_own_turn_on_switches_a_held_tv_again(hass, home, freezer):
    hass.states.async_set(LG, "off")
    entry = await setup_room(hass, display=[LG])
    home.kodi_boots = True
    await _turn_on(hass)
    home.kodi("playing")
    await hass.async_block_till_done()
    hass.states.async_set(LG, "off")
    await tick(hass, freezer, 10)
    assert LG in entry.runtime_data.held
    await _turn_on(hass)  # the room's power button
    assert _state(hass, LG) == "on"


async def test_no_standby_settings_for_a_role_without_a_plug(hass, home):
    from homeassistant.helpers import entity_registry as er

    hass.states.async_set(LG, "off")
    await setup_room(hass, display=[LG], display_power=["sensor.tv_power"])
    reg = er.async_get(hass)
    assert reg.async_get("switch.living_room_display_wait_for_standby") is None
    assert reg.async_get("number.living_room_display_standby_below") is None


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
    await tick(hass, freezer, 20)
    assert _state(hass, "sensor.living_room_status") == "playing"


async def test_display_actions_need_a_video_player(hass, home):
    from custom_components.better_media_player.config_flow import _validate

    from .conftest import room_options

    options = room_options(video_player=None, display=None, display_on_action="script.a", display_off_action="script.b")
    assert _validate(hass, options).get("display") == "display_needs_video"
