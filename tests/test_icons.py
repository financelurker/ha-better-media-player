"""Icons: icons.json matches the entities and actions; the room player shows its active source."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import yaml
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.util import dt as dt_util

from custom_components.better_media_player.const import FOLLOW_STATES, STATUSES

from .conftest import setup_room

COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "better_media_player"
ICONS = json.loads((COMPONENT / "icons.json").read_text(encoding="utf-8"))
STRINGS = json.loads((COMPONENT / "translations" / "en.json").read_text(encoding="utf-8"))
ROOM = "media_player.wohnzimmer"
# their device class brings the icons
DEVICE_CLASS_ICONS = {("binary_sensor", "problem")}


def test_every_entity_has_an_icon():
    translated = {(platform, key) for platform, keys in STRINGS["entity"].items() for key in keys}
    with_icon = {(platform, key) for platform, keys in ICONS["entity"].items() for key in keys}
    assert sorted(with_icon - translated) == [], "icons for entities that do not exist"
    assert sorted(translated - with_icon - DEVICE_CLASS_ICONS) == [], "entities without an icon"


def test_state_icons_match_the_states():
    for platform, keys in ICONS["entity"].items():
        for key, icon in keys.items():
            assert icon["default"].startswith("mdi:"), (platform, key)
            if "state" not in icon:
                continue
            if platform in ("switch", "binary_sensor"):
                states = {"on", "off"}
            else:
                states = set(STRINGS["entity"][platform][key].get("state", {}))
            assert set(icon["state"]) == states, (platform, key)


def test_enum_sensor_states_complete():
    assert set(ICONS["entity"]["sensor"]["status"]["state"]) == set(STATUSES)
    assert set(ICONS["entity"]["sensor"]["follow_state"]["state"]) == set(FOLLOW_STATES)


def test_every_action_has_an_icon():
    actions = yaml.safe_load((COMPONENT / "services.yaml").read_text(encoding="utf-8"))
    assert set(ICONS["services"]) == set(actions)


def test_no_icons_in_code():
    """An icon set in code wins over icons.json (only the room player sets one: its source)."""
    users = [
        p.name
        for p in COMPONENT.glob("*.py")
        if "_attr_icon" in p.read_text(encoding="utf-8") or ("def icon(" in p.read_text(encoding="utf-8") and p.name != "media_player.py")
    ]
    assert users == []


async def _tick(hass, freezer, seconds: float) -> None:
    for _ in range(int(seconds)):
        freezer.tick(timedelta(seconds=1))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()


def _icon(hass) -> str:
    return hass.states.get(ROOM).attributes["icon"]


async def test_room_player_icon_follows_the_source(hass, home, freezer):
    await setup_room(hass)
    await _tick(hass, freezer, 3)
    assert _icon(hass) == "mdi:television-speaker-off"
    home.music("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    assert _icon(hass) == "mdi:music"
    home.kodi("playing")  # a film cast to Kodi: the video takes over
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    assert _icon(hass) == "mdi:television-play"
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    assert _icon(hass) == "mdi:television-speaker-off"
