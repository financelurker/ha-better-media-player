"""A device that does not follow its commands: tried a few times, then left as it is
until the next start (and said so in the log and live)."""

from __future__ import annotations

import logging

from custom_components.better_media_player.devices.const import COMMAND_TRIES

from .conftest import LG, device, setup_room, setup_room_devices
from .helpers import ROOM, tick


def _calls(home, entity_id, service):
    return [c for c in home.calls if c[2] == entity_id and c[1] == service]


async def _turn_on(hass):
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()


async def _turn_off(hass):
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()


def _warnings(caplog, text):
    return [r for r in caplog.records if r.levelno == logging.WARNING and text in r.getMessage()]


async def test_a_tv_that_does_not_come_on_is_tried_three_times(hass, home, freezer, caplog):
    hass.states.async_set(LG, "off")
    entry = await setup_room(hass, display=[LG])
    home.kodi_boots = True
    home.lg_obeys = False
    await _turn_on(hass)
    await tick(hass, freezer, 10 * 60, step=5)
    assert len(_calls(home, LG, "turn_on")) == COMMAND_TRIES
    assert LG in entry.runtime_data.power.held
    assert len(_warnings(caplog, "was not switched on after")) == 1


async def test_the_next_start_tries_again(hass, home, freezer):
    hass.states.async_set(LG, "off")
    entry = await setup_room(hass, display=[LG])
    home.kodi_boots = True
    home.lg_obeys = False
    await _turn_on(hass)
    await tick(hass, freezer, 3 * 60, step=5)
    assert LG in entry.runtime_data.power.held
    home.lg_obeys = True
    await _turn_on(hass)  # the room's power button
    assert hass.states.get(LG).state == "on"
    assert LG not in entry.runtime_data.power.held


async def test_a_slow_tv_is_not_given_up(hass, home, freezer, caplog):
    """It reacts to the second command: counted anew next time."""
    hass.states.async_set(LG, "off")
    entry = await setup_room(hass, display=[LG])
    home.kodi_boots = True
    home.lg_obeys = False
    await _turn_on(hass)
    await tick(hass, freezer, 20)
    home.lg_obeys = True
    await tick(hass, freezer, 60, step=5)
    assert hass.states.get(LG).state == "on"
    assert len(_calls(home, LG, "turn_on")) == 2
    assert LG not in entry.runtime_data.power.held
    assert entry.runtime_data.power.devices._tries.get(LG) is None, "the count starts anew"
    assert not _warnings(caplog, "was not switched")


async def test_a_tv_that_does_not_go_off_is_left_on(hass, home, freezer, caplog):
    hass.states.async_set(LG, "off")
    entry = await setup_room(hass, display=[LG])
    home.kodi_boots = True
    await _turn_on(hass)
    assert hass.states.get(LG).state == "on"
    home.lg_obeys = False
    home.kodi("off")
    await hass.async_block_till_done()
    await tick(hass, freezer, 30 * 60, step=15)
    assert len(_calls(home, LG, "turn_off")) == COMMAND_TRIES
    assert len(_warnings(caplog, "was not switched off after")) == 1
    node = entry.runtime_data.power.nodes["display"]
    assert entry.runtime_data.power.devices.live(node, 0)["stuck"] == [LG]
    # a start wants it on (it is): the count is gone, the next off is tried again
    home.lg_obeys = True
    await _turn_on(hass)
    await _turn_off(hass)
    assert hass.states.get(LG).state == "off"


async def test_cec_with_fallback_gives_up_too(hass, home, freezer, caplog):
    hass.states.async_set(LG, "off")
    home.lg_obeys = False
    entry = await setup_room_devices(
        hass,
        [device("display", "display", devices=[LG], control={"on": "cec", "off": "bmp", "fallback": True, "timeout": 10})],
        [{"from": "video", "to": "display", "signals": ["picture"]}],
    )
    home.kodi("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 10 * 60, step=5)
    assert len(_calls(home, LG, "turn_on")) == COMMAND_TRIES
    assert LG in entry.runtime_data.power.held
    assert len(_warnings(caplog, "was not switched on after")) == 1


async def test_cec_off_without_fallback_is_told(hass, home, freezer, caplog):
    hass.states.async_set(LG, "off")
    entry = await setup_room_devices(
        hass,
        [device("display", "display", devices=[LG], control={"on": "bmp", "off": "cec", "fallback": False, "timeout": 10})],
        [{"from": "video", "to": "display", "signals": ["picture"]}],
    )
    home.kodi("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert hass.states.get(LG).state == "on"
    home.kodi("off")
    await hass.async_block_till_done()
    await tick(hass, freezer, 30 * 60, step=15)
    assert not _calls(home, LG, "turn_off")
    assert len(_warnings(caplog, "was not switched off over CEC")) == 1
    node = entry.runtime_data.power.nodes["display"]
    live = entry.runtime_data.power.devices.live(node, 0)
    assert live["stuck"] == [LG]
    assert live["cec"] == [], "no longer waits for CEC"
