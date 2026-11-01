"""The setups documented under "Supported setups" (README, docs/devices.md), end to end.

All with simulated devices; on real devices so far only scenario 0 (Kodi for video,
a Sendspin device of Music Assistant for music)."""

from __future__ import annotations

from custom_components.better_media_player.const import CONF_CONNECTIONS, CONF_DISPLAY, CONF_SPEAKERS

from .conftest import AVR, BASS, KODI, LG, SPEAKERS, TV, setup_room
from .helpers import ROOM, call_order, tick


def _selects(home, entity_id: str) -> list[str]:
    return [d["source"] for s, e, d in home.played if s == "select_source" and e == entity_id]


async def test_scenario_0_simple_devices_on_plugs(hass, home, freezer):
    """TV, speakers and subwoofer on plugs; Kodi for video, a music player."""
    await setup_room(hass)  # display, speakers, subwoofer: plugs
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    home.kodi("idle")
    await hass.async_block_till_done()
    assert call_order(home, [TV, KODI, SPEAKERS, BASS], "turn_on") == [TV, KODI, SPEAKERS, BASS]
    home.kodi("off")
    await hass.async_block_till_done()
    await tick(hass, freezer, 11 * 60, step=30)  # past the run-downs
    assert not home.on(TV) and not home.on(SPEAKERS) and not home.on(BASS)

    home.played.clear()
    home.music("playing")  # music: speakers and subwoofer, the TV stays off
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert home.on(SPEAKERS) and home.on(BASS) and not home.on(TV)
    assert not [p for p in home.played if p[0] == "select_source"]  # nothing to choose


async def test_scenario_a_webos_tv_cec_receiver_kodi(hass, home, freezer):
    """Kodi wakes the TV over CEC and selects its input; the TV wakes the receiver
    (ARC); music goes to the receiver's network input, chosen by BMP."""
    hass.states.async_set(LG, "off", {"source": "HDMI 1", "source_list": ["HDMI 1", "HDMI 2"]})
    home.avr("off", source_list=["TV/ARC", "NET"])
    await setup_room(
        hass,
        **{
            CONF_DISPLAY: [LG],
            CONF_SPEAKERS: [SPEAKERS],
            "display_inputs": LG,
            "display_power_on": "cec",
            "display_cec_fallback": True,
            "speakers_inputs": AVR,
            CONF_CONNECTIONS: [
                {"from": "video_player", "to": "display", "signals": ["picture", "sound"], "input": "HDMI 2", "mode": "cec"},
                {"from": "display", "to": "speakers", "signals": ["sound"], "input": "TV/ARC", "mode": "cec"},
                {"from": "audio_player", "to": "speakers", "signals": ["sound"], "input": "NET"},
            ],
        },
    )
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert ("media_player", "turn_on", LG) not in home.calls  # CEC does it
    home.kodi("idle")
    hass.states.async_set(LG, "on", {"source": "HDMI 2", "source_list": ["HDMI 1", "HDMI 2"]})  # over CEC
    home.avr("on", source="TV/ARC")
    await hass.async_block_till_done()
    await tick(hass, freezer, 60)
    assert ("media_player", "turn_on", LG) not in home.calls
    assert _selects(home, LG) == [] and _selects(home, AVR) == []

    home.kodi("off")  # film over, music: the receiver goes to its network input
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    assert hass.states.get(AVR).attributes["source"] == "NET"
    assert _selects(home, AVR) == ["NET"]
