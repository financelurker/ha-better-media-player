"""Cabling: connections choose the inputs of display and speakers; CEC does it for BMP."""

from __future__ import annotations

from custom_components.better_media_player.const import (
    CONF_CONNECTIONS,
    CONF_DISPLAY,
    CONF_SPEAKERS,
)

from .conftest import AVR, KODI, LG, MUSIC, SPEAKERS, TV, setup_room
from .helpers import ROOM, roles, tick

VIDEO_IN = {"from": "video_player", "to": "display", "signals": ["picture", "sound"], "input": "HDMI 2"}
VIDEO_AVR = {"from": "video_player", "to": "speakers", "signals": ["sound"], "input": "BD"}
MUSIC_AVR = {"from": "audio_player", "to": "speakers", "signals": ["sound"], "input": "NET"}


def selects(home, entity_id: str) -> list[str]:
    return [d["source"] for s, e, d in home.played if s == "select_source" and e == entity_id]


async def _room(hass, home, connections=(VIDEO_IN, VIDEO_AVR, MUSIC_AVR), **opts):
    hass.states.async_set(LG, "off", {"source": "HDMI 1", "source_list": ["HDMI 1", "HDMI 2"]})
    home.avr("off", source_list=["BD", "NET", "TUNER"])
    return await setup_room(
        hass,
        **{
            CONF_DISPLAY: [LG],
            CONF_SPEAKERS: [SPEAKERS],
            "display_inputs": LG,
            "speakers_inputs": AVR,
            CONF_CONNECTIONS: list(connections),
            **opts,
        },
    )


async def _start_video(hass, home, freezer):
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    home.kodi("idle")
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)


# ------------------------------------------------------------------ default


async def test_without_connections_no_input_is_chosen(hass, home, freezer):
    await _room(hass, home, connections=())
    await _start_video(hass, home, freezer)
    assert home.on(SPEAKERS) and hass.states.get(LG).state == "on"
    assert selects(home, LG) == [] and selects(home, AVR) == []


# ------------------------------------------------------------------ inputs


async def test_a_film_puts_tv_and_receiver_on_its_inputs(hass, home, freezer):
    await _room(hass, home)
    await _start_video(hass, home, freezer)
    assert hass.states.get(LG).attributes["source"] == "HDMI 2"
    assert hass.states.get(AVR).attributes["source"] == "BD"
    assert selects(home, LG) == ["HDMI 2"] and selects(home, AVR) == ["BD"]


async def test_music_puts_the_receiver_on_its_input_and_leaves_the_tv_off(hass, home, freezer):
    await _room(hass, home)
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    assert hass.states.get(AVR).attributes["source"] == "NET"
    assert hass.states.get(LG).state == "off"
    assert selects(home, LG) == []


async def test_the_inputs_follow_the_source_in_use(hass, home, freezer):
    await _room(hass, home)
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    home.kodi("playing")  # a film: video takes over
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    assert hass.states.get(AVR).attributes["source"] == "BD"
    home.kodi("off")  # film over, the music still plays: its input again
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    assert hass.states.get(AVR).attributes["source"] == "NET"


async def test_an_input_changed_by_hand_is_left_until_the_source_changes(hass, home, freezer):
    await _room(hass, home)
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    home.avr("on", source="TUNER")  # the receiver's remote
    await hass.async_block_till_done()
    await tick(hass, freezer, 30)
    assert hass.states.get(AVR).attributes["source"] == "TUNER"
    assert selects(home, AVR) == ["NET"]
    assert entry_power(hass).devices.input_manual(entry_power(hass).nodes["speakers"])
    home.kodi("playing")  # another source: its input again
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    assert hass.states.get(AVR).attributes["source"] == "BD"


async def test_an_input_not_taken_is_chosen_once_more_then_given_up(hass, home, freezer):
    home.inputs_obey = False
    await _room(hass, home)
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 60)
    assert selects(home, AVR) == ["NET", "NET"]


async def test_inputs_by_actions_without_feedback(hass, home, freezer):
    hass.states.async_set("script.avr_net", "off")
    await _room(
        hass,
        home,
        connections=(MUSIC_AVR,),
        speakers_inputs=None,
        speakers_input_actions={"NET": "script.avr_net"},
    )
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 30)
    runs = [e for s, e, d in home.played if e == "script.avr_net"]
    assert runs == ["script.avr_net"]  # once: there is nothing to check


async def test_a_route_through_the_tv_switches_it_on_for_music(hass, home, freezer):
    """Music played by the TV (its app): the music's route goes through the display."""
    await _room(hass, home, connections=({"from": "audio_player", "to": "display", "signals": ["sound"]},))
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    assert hass.states.get(LG).state == "on"


# --------------------------------------------------------------------- CEC


async def test_cec_chooses_the_input_bmp_only_waits(hass, home, freezer):
    await _room(hass, home, connections=({**VIDEO_IN, "mode": "cec"},))
    await _start_video(hass, home, freezer)
    hass.states.async_set(LG, "on", {"source": "HDMI 2"})  # Kodi over CEC
    await hass.async_block_till_done()
    await tick(hass, freezer, 60)
    assert selects(home, LG) == []


async def test_cec_input_falls_back_to_bmp(hass, home, freezer):
    await _room(hass, home, connections=({**VIDEO_IN, "mode": "cec"},), display_cec_fallback=True, display_cec_timeout=30)
    await _start_video(hass, home, freezer)
    assert selects(home, LG) == []
    await tick(hass, freezer, 40)  # nothing came over CEC
    assert selects(home, LG) == ["HDMI 2"]


async def test_cec_switches_the_tv_on_bmp_does_not(hass, home, freezer):
    await _room(hass, home, connections=(), display_power_on="cec")
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert ("media_player", "turn_on", LG) not in home.calls
    assert ("media_player", "turn_on", KODI) in home.calls  # the start goes on: Kodi wakes the TV
    home.kodi("idle")
    hass.states.async_set(LG, "on")  # over CEC
    await hass.async_block_till_done()
    await tick(hass, freezer, 60)
    assert ("media_player", "turn_on", LG) not in home.calls
    assert entry_power(hass).held == set()


async def test_a_tv_cec_did_not_switch_on_is_left_off(hass, home, freezer):
    await _room(hass, home, connections=(), display_power_on="cec", display_cec_timeout=10)
    await _start_video(hass, home, freezer)
    await tick(hass, freezer, 30)
    assert ("media_player", "turn_on", LG) not in home.calls
    assert LG in entry_power(hass).held


async def test_cec_power_falls_back_to_bmp(hass, home, freezer):
    await _room(hass, home, connections=(), display_power_on="cec", display_cec_fallback=True, display_cec_timeout=10)
    await _start_video(hass, home, freezer)
    await tick(hass, freezer, 30)
    assert ("media_player", "turn_on", LG) in home.calls
    assert hass.states.get(LG).state == "on"


async def test_cec_switches_the_tv_off_bmp_does_not(hass, home, freezer):
    await _room(hass, home, connections=(), display_power_off="cec")
    await _start_video(hass, home, freezer)
    home.kodi("off")
    await hass.async_block_till_done()
    await tick(hass, freezer, 12 * 60, step=30)  # past the TV's run-down
    assert ("media_player", "turn_off", LG) not in home.calls
    assert hass.states.get(LG).state == "on"


async def test_never_leaves_the_tv_alone(hass, home, freezer):
    await _room(hass, home, connections=(), display_power_on="never", display_power_off="never")
    await _start_video(hass, home, freezer)
    home.kodi("off")
    await hass.async_block_till_done()
    await tick(hass, freezer, 12 * 60, step=30)
    assert not [c for c in home.calls if c[2] == LG and c[1] in ("turn_on", "turn_off")]


def entry_power(hass):
    entry = next(e for e in hass.config_entries.async_entries("better_media_player"))
    return entry.runtime_data.power


async def test_a_plug_is_always_switched_by_bmp(hass, home, freezer):
    """The modes are for devices: a plug in the role is switched as ever."""
    await _room(hass, home, connections=(), **{CONF_DISPLAY: [TV, LG]}, display_power_on="cec")
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert home.on(TV)
    assert ("media_player", "turn_on", LG) not in home.calls
    assert ("media_player", "turn_on", MUSIC) not in home.calls


# ------------------------------------------------------------------ dialog


async def _options(hass, entry):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    return await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "cabling"})


async def _go(hass, result, data):
    return await hass.config_entries.options.async_configure(result["flow_id"], data)


async def _save(hass, result):
    result = await _go(hass, result, {"next_step_id": "init"})
    result = await _go(hass, result, {"next_step_id": "save"})
    await hass.async_block_till_done()
    return result


async def test_the_cabling_menu_offers_what_the_room_has(hass, home):
    entry = await _room(hass, home, connections=())
    result = await _options(hass, entry)
    assert result["menu_options"] == ["connection_add", "control_display", "control_speakers", "init"]
    result = await _go(hass, result, {"next_step_id": "connection_add"})
    assert result["step_id"] == "connection"


async def test_adding_a_connection_in_the_dialog(hass, home, freezer):
    entry = await _room(hass, home, connections=())
    result = await _options(hass, entry)
    result = await _go(hass, result, {"next_step_id": "connection_add"})
    result = await _go(hass, result, {"from": "video_player", "to": "display", "signals": ["picture", "sound"]})
    assert result["step_id"] == "connection_input"
    schema = result["data_schema"].schema
    input_key = next(k for k in schema if k == "input")
    assert schema[input_key].config["options"] == ["HDMI 1", "HDMI 2"]  # the TV's source list
    result = await _go(hass, result, {"input": "HDMI 2", "mode": "bmp"})
    assert result["menu_options"][:3] == ["connection_add", "connection_edit", "connection_remove"]
    result = await _save(hass, result)
    assert roles(entry)["connections"] == [
        {"from": "video_player", "to": "display", "signals": ["picture", "sound"], "input": "HDMI 2", "mode": "bmp"}
    ]
    await _start_video(hass, home, freezer)  # the reloaded room uses it
    assert hass.states.get(LG).attributes["source"] == "HDMI 2"


async def test_an_input_the_device_does_not_offer_is_refused(hass, home):
    entry = await _room(hass, home, connections=())
    result = await _options(hass, entry)
    result = await _go(hass, result, {"next_step_id": "connection_add"})
    result = await _go(hass, result, {"from": "video_player", "to": "display", "signals": ["picture"]})
    result = await _go(hass, result, {"input": "SCART", "mode": "bmp"})
    assert result["errors"] == {"input": "unknown_input"}


async def test_a_connection_to_itself_or_without_signal_is_refused(hass, home):
    entry = await _room(hass, home, connections=())
    result = await _options(hass, entry)
    result = await _go(hass, result, {"next_step_id": "connection_add"})
    result = await _go(hass, result, {"from": "display", "to": "display", "signals": ["sound"]})
    assert result["errors"] == {"base": "connection_to_itself"}
    result = await _go(hass, result, {"from": "video_player", "to": "display", "signals": []})
    assert result["errors"] == {"base": "connection_no_signal"}


async def test_a_circle_and_two_inputs_for_one_source_are_refused(hass, home):
    entry = await _room(hass, home, connections=({"from": "display", "to": "speakers", "signals": ["sound"], "input": "TV"},))
    result = await _options(hass, entry)
    result = await _go(hass, result, {"next_step_id": "connection_add"})
    result = await _go(hass, result, {"from": "speakers", "to": "display", "signals": ["sound"]})
    assert result["errors"] == {"base": "connection_cycle"}

    entry2 = await _room(hass, home, connections=(VIDEO_AVR, {"from": "video_player", "to": "display", "signals": ["sound"], "input": "HDMI 2"}))
    result = await _options(hass, entry2)
    result = await _go(hass, result, {"next_step_id": "connection_add"})
    result = await _go(hass, result, {"from": "display", "to": "speakers", "signals": ["sound"]})
    result = await _go(hass, result, {"input": "NET", "mode": "bmp"})  # the film would need BD and NET
    assert result["errors"] == {"base": "input_conflict"}


async def test_editing_and_removing_connections(hass, home):
    entry = await _room(hass, home)
    result = await _options(hass, entry)
    result = await _go(hass, result, {"next_step_id": "connection_edit"})
    result = await _go(hass, result, {"connection": "2"})  # music → speakers
    result = await _go(hass, result, {"from": "audio_player", "to": "speakers", "signals": ["sound"]})
    result = await _go(hass, result, {"input": "TUNER", "mode": "cec"})
    result = await _go(hass, result, {"next_step_id": "connection_remove"})
    result = await _go(hass, result, {"connections": ["1"]})  # film → speakers
    await _save(hass, result)
    assert [(c["from"], c["to"], c["input"], c.get("mode", "bmp")) for c in roles(entry)["connections"]] == [
        ("video_player", "display", "HDMI 2", "bmp"),
        ("audio_player", "speakers", "TUNER", "cec"),
    ]


async def test_the_control_of_a_device_in_the_dialog(hass, home):
    entry = await _room(hass, home, connections=())
    result = await _options(hass, entry)
    result = await _go(hass, result, {"next_step_id": "control_display"})
    result = await _go(
        hass,
        result,
        {"inputs": LG, "input_actions": {}, "input_pause": 0, "power_on": "cec", "power_off": "bmp", "cec_fallback": True, "cec_timeout": 20},
    )
    await _save(hass, result)
    power = entry_power(hass)
    control = power.nodes["display"].control
    assert (control.on, control.off, control.fallback, control.timeout) == ("cec", "bmp", True, 20.0)
    assert "display_input_actions" not in entry.options  # empty: not stored


async def test_removing_a_role_drops_its_connections(hass, home):
    entry = await _room(hass, home)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await _go(hass, result, {"next_step_id": "devices"})
    result = await _go(hass, result, {CONF_DISPLAY: [LG], "actions": {}, "standby": {}, "follow": {}})  # no speakers
    result = await _go(hass, result, {"next_step_id": "save"})
    await hass.async_block_till_done()
    assert [(c["from"], c["to"]) for c in roles(entry)["connections"]] == [("video_player", "display")]
    assert "speakers_inputs" not in entry.options


# ------------------------------------------------- from the review: timing


async def test_a_second_start_waits_for_cec_again(hass, home, freezer):
    await _room(hass, home, connections=(), display_power_on="cec")
    await _start_video(hass, home, freezer)
    hass.states.async_set(LG, "on")  # over CEC
    await hass.async_block_till_done()
    home.kodi("off")
    await hass.async_block_till_done()
    await tick(hass, freezer, 12 * 60, step=30)  # the TV runs down, BMP switches it off
    assert hass.states.get(LG).state == "off"
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert LG not in entry_power(hass).held  # waits for CEC again, not given up at once
    assert ("media_player", "turn_on", LG) not in home.calls


async def test_no_input_while_the_tv_is_starting(hass, home, freezer):
    await _room(hass, home, connections=(VIDEO_IN,), display_on_wait=8)
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)  # the TV's wait after switching on
    assert selects(home, LG) == []
    await tick(hass, freezer, 5)
    assert selects(home, LG) == ["HDMI 2"]


async def test_no_input_action_while_the_device_is_off(hass, home, freezer):
    hass.states.async_set("script.avr_net", "off")
    await _room(
        hass, home, connections=({**VIDEO_IN, "input": "NET", "to": "display"},),
        display_inputs=None, display_input_actions={"NET": "script.avr_net"}, display_power_on="cec",
    )
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    home.kodi("idle")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)  # the TV is still off (CEC expected)
    assert not [e for s, e, d in home.played if e == "script.avr_net"]
    hass.states.async_set(LG, "on")
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert [e for s, e, d in home.played if e == "script.avr_net"] == ["script.avr_net"]


async def test_cec_is_awaited_from_when_the_player_is_up(hass, home, freezer):
    """A slow Kodi: the TV's CEC timeout counts from Kodi's start, not the TV's."""
    await _room(hass, home, connections=(), display_power_on="cec", display_cec_timeout=15)
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 30)  # Kodi takes 30 s
    home.kodi("idle")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert LG not in entry_power(hass).held
    await tick(hass, freezer, 15)  # now nothing came over CEC
    assert LG in entry_power(hass).held


async def test_a_tv_switched_by_hand_puts_the_room_into_no_manual_mode(hass, home, freezer):
    await _room(hass, home, connections=(), display_power_on="never", display_power_off="never")
    hass.states.async_set(LG, "on")  # by hand
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert not entry_power(hass).ctrl.manual
    await _start_video(hass, home, freezer)
    home.kodi("off")
    await hass.async_block_till_done()
    await tick(hass, freezer, 12 * 60, step=30)
    assert hass.states.get(LG).state == "on"  # never switched off by the room
    assert ("media_player", "turn_off", LG) not in home.calls


async def test_back_on_its_default_input_after_a_power_cycle(hass, home, freezer):
    await _room(hass, home)
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    home.avr("off", source="TUNER")  # eco standby, back on its default input
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    home.avr("on")
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    assert hass.states.get(AVR).attributes["source"] == "NET"  # chosen again, no "by hand"


async def test_a_change_by_hand_is_forgotten_with_the_next_source(hass, home, freezer):
    same = {**VIDEO_AVR, "input": "NET"}  # film and music on the same input
    await _room(hass, home, connections=(same, MUSIC_AVR))
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    home.avr("on", source="TUNER")
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    home.kodi("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    assert hass.states.get(AVR).attributes["source"] == "NET"


async def test_manual_mode_leaves_the_inputs_alone(hass, home, freezer):
    await _room(hass, home)
    hass.states.async_set(SPEAKERS, "on")  # plug by hand: manual mode
    home.avr("on", source="TUNER")
    await hass.async_block_till_done()
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 30)
    assert entry_power(hass).ctrl.manual
    assert selects(home, AVR) == []


async def test_a_cec_input_that_does_not_come_is_given_up(hass, home, freezer):
    await _room(hass, home, connections=({**VIDEO_IN, "mode": "cec"},), display_cec_timeout=10)
    await _start_video(hass, home, freezer)
    await tick(hass, freezer, 60)
    assert selects(home, LG) == []
    assert hass.states.get(LG).attributes["source"] == "HDMI 1"


async def test_a_cec_off_falls_back_to_bmp(hass, home, freezer):
    await _room(hass, home, connections=(), display_power_off="cec", display_cec_fallback=True, display_cec_timeout=10)
    await _start_video(hass, home, freezer)
    home.kodi("off")
    await hass.async_block_till_done()
    await tick(hass, freezer, 11 * 60, step=10)
    assert ("media_player", "turn_off", LG) in home.calls


async def test_a_picture_only_connection_is_not_on_the_music_route(hass, home, freezer):
    await _room(hass, home, connections=({"from": "audio_player", "to": "display", "signals": ["picture"]},))
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    assert hass.states.get(LG).state == "off"


# ------------------------------------------------- from the review: dialog


async def test_two_alike_connections_are_told_apart(hass, home):
    twin = {**MUSIC_AVR, "mode": "cec"}
    entry = await _room(hass, home, connections=(MUSIC_AVR, twin))
    result = await _options(hass, entry)
    result = await _go(hass, result, {"next_step_id": "connection_remove"})
    result = await _go(hass, result, {"connections": ["1"]})
    await _save(hass, result)
    assert [c.get("mode", "bmp") for c in roles(entry)["connections"]] == ["bmp"]


async def test_a_circle_is_refused_in_the_first_form(hass, home):
    entry = await _room(hass, home, connections=({"from": "display", "to": "speakers", "signals": ["sound"]},))
    result = await _options(hass, entry)
    result = await _go(hass, result, {"next_step_id": "connection_add"})
    result = await _go(hass, result, {"from": "speakers", "to": "display", "signals": ["sound"]})
    assert result["step_id"] == "connection" and result["errors"] == {"base": "connection_cycle"}


async def test_an_input_on_a_device_without_input_control_is_refused(hass, home):
    entry = await _room(hass, home, connections=(), **{"bass": ["switch.bass_plug"]})
    result = await _options(hass, entry)
    result = await _go(hass, result, {"next_step_id": "connection_add"})
    result = await _go(hass, result, {"from": "video_player", "to": "bass", "signals": ["sound"]})
    result = await _go(hass, result, {"input": "LFE", "mode": "bmp"})
    assert result["errors"] == {"input": "no_input_control"}


async def test_input_actions_must_map_inputs_to_actions(hass, home):
    entry = await _room(hass, home, connections=())
    result = await _options(hass, entry)
    result = await _go(hass, result, {"next_step_id": "control_display"})
    result = await _go(hass, result, {"inputs": LG, "input_actions": {"HDMI 1": "light.hallway"}, "power_on": "bmp", "power_off": "bmp"})
    assert result["errors"] == {"input_actions": "invalid_input_actions"}
    result = await _go(hass, result, {"inputs": LG, "input_actions": {"HDMI 1": "script.tv_hdmi1"}, "power_on": "bmp", "power_off": "bmp"})
    assert result["type"].value == "menu"
