"""Helpers the test modules share: the room's entities, time, calls, and actions.

Test modules import from here (and conftest), never from each other.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.util import dt as dt_util

from custom_components.better_media_player.const import DOMAIN

from .conftest import BASS, FOLLOW, KITCHEN, KITCHEN_OCC, KITCHEN_SPK, LIVING_OCC, MUSIC, SPEAKERS, setup_room

# the room of setup_room ("Living Room") and the kitchen of setup_kitchen
ROOM = "media_player.living_room"
STATUS = "sensor.living_room_status"
NIGHT = "switch.living_room_night_mode"
QUIET = "switch.living_room_quiet"
PRESET = "select.living_room_volume_preset"
WAKE = "sensor.living_room_wake"
SNOOZE = "button.living_room_snooze"
SLEEP = "number.living_room_sleep_timer"
SLEEP_PLUS = "button.living_room_sleep_timer_15_min"
KROOM = "media_player.kitchen"


# ------------------------------------------------------------------- time


async def tick(hass, freezer, seconds: float = 3, step: float = 1, between=None) -> None:
    """Let time pass in steps, so periodic checks and timeouts fire on their way."""
    left = seconds
    while left > 0:
        freezer.tick(timedelta(seconds=min(step, left)))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        if between:
            between()
            await hass.async_block_till_done()
        left -= step


async def settle(hass) -> None:
    """Let background work run that async_block_till_done does not wait for (e.g. a wake's start)."""
    for _ in range(20):
        await asyncio.sleep(0)
        await hass.async_block_till_done()


async def settle_held(hass, ticks: int = 50) -> None:
    """Settle while an announcement is held: no async_block_till_done (it would wait for it)."""
    for _ in range(ticks):
        await asyncio.sleep(0)


async def tick_held(hass, freezer, seconds: int) -> None:
    """Let time pass while an announcement is held."""
    for _ in range(seconds):
        freezer.tick(timedelta(seconds=1))
        async_fire_time_changed(hass, dt_util.utcnow())
        await settle_held(hass)


# ---------------------------------------------------------- what happened


def vol(hass, entity_id: str = MUSIC):
    return hass.states.get(entity_id).attributes.get("volume_level")


def room_volume(hass):
    return hass.states.get(ROOM).attributes["room_volume"]


def calls_to(home, entity_id: str, service: str) -> list:
    return [c for c in home.calls if c[2] == entity_id and c[1] == service]


def count_calls(home, entity_id: str, service: str) -> int:
    return sum(1 for c in home.calls if c[2] == entity_id and c[1] == service)


def call_order(home, entities: list[str], service: str) -> list[str]:
    return [c[2] for c in home.calls if c[2] in entities and c[1] == service]


def volume_sets(home, entity_id: str) -> list[float]:
    return [d["volume_level"] for s, e, d in home.played if s == "volume_set" and e == entity_id]


# ---------------------------------------------------------------- actions


async def set_night(hass, on: bool = True) -> None:
    await hass.services.async_call("switch", "turn_on" if on else "turn_off", {"entity_id": NIGHT}, blocking=True)
    await hass.async_block_till_done()


async def start_wake(hass, **data) -> None:
    payload = {"entity_id": ROOM, "media_content_id": "library://radio/1", "media_content_type": "music", **data}
    await hass.services.async_call(DOMAIN, "wake", payload, blocking=True)
    await settle(hass)


async def wake_playing(hass, home, freezer, **data) -> None:
    """A wake whose radio has started."""
    await start_wake(hass, start_volume=0.05, target_volume=0.4, fade_minutes=2, **data)
    assert hass.states.get(WAKE).state == "starting"
    home.music("playing")  # the radio starts
    await hass.async_block_till_done()


async def follow_from(hass, house, player=MUSIC, room=ROOM, volume=0.25) -> None:
    """Music plays in a room on its own player; follow me is started there."""
    if player == MUSIC:
        house.music("playing", volume_level=volume)
    else:
        house.kitchen("playing", volume_level=volume)
    await hass.async_block_till_done()
    await hass.services.async_call(DOMAIN, "start_follow", {"entity_id": room}, blocking=True)
    await hass.async_block_till_done()


async def stop_follow(hass, room=ROOM) -> None:
    await hass.services.async_call(DOMAIN, "stop_follow", {"entity_id": room}, blocking=True)
    await hass.async_block_till_done()


async def quiet_leaves_and_cuts_speakers(hass, house) -> None:
    """Quiet in the kitchen during follow me: it leaves and cuts its speakers at once; off: it joins again."""
    hass.states.async_set(LIVING_OCC, "on")
    hass.states.async_set(KITCHEN_OCC, "on")
    await follow_from(hass, house)
    assert KITCHEN in house.members(FOLLOW) and house.on(KITCHEN_SPK)

    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.kitchen_quiet"}, blocking=True)
    await hass.async_block_till_done()
    assert KITCHEN not in house.members(FOLLOW)
    assert not house.on(KITCHEN_SPK), "speakers off right away"
    assert hass.states.get("sensor.kitchen_follow").state == "excluded_quiet"

    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.kitchen_quiet"}, blocking=True)
    await hass.async_block_till_done()
    assert KITCHEN in house.members(FOLLOW)


# ---------------------------------------------------------- announcements


async def speak(hass, **data) -> None:
    """tts.speak's call on the room player."""
    await hass.services.async_call(
        "media_player",
        "play_media",
        {"entity_id": ROOM, "media_content_id": "media-source://tts/x", "media_content_type": "music",
         "announce": True, **data},
        blocking=True,
    )
    await hass.async_block_till_done()


def speak_later(hass):
    """tts.speak's call, running on while the test looks at the room (untracked:
    async_block_till_done does not wait for it)."""
    return asyncio.ensure_future(
        hass.services.async_call(
            "media_player",
            "play_media",
            {"entity_id": ROOM, "media_content_id": "media-source://tts/x", "media_content_type": "music",
             "announce": True},
            blocking=True,
        )
    )


async def announce(hass, entity_id=ROOM, **data) -> None:
    await hass.services.async_call(
        DOMAIN, "announce", {"entity_id": entity_id, "media_content_id": "media-source://tts/x", **data},
        blocking=True,
    )
    await hass.async_block_till_done()


# ----------------------------------------------------------------- volume


async def music_at(hass, home, freezer, volume=0.4, **overrides):
    """The room (setup_room with overrides) plays music at a volume."""
    entry = await setup_room(hass, **overrides)
    home.music("playing", volume_level=volume)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert home.on(SPEAKERS) and home.on(BASS)
    return entry


async def select_preset(hass, option: str) -> None:
    await hass.services.async_call("select", "select_option", {"entity_id": PRESET, "option": option}, blocking=True)
    await hass.async_block_till_done()
