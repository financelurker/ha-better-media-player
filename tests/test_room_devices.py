"""A room set up with devices and cables beyond the three roles: any kinds, passive
devices, names, the volume on a device (what the panel will edit)."""

from __future__ import annotations

from .conftest import AVR, BASS, KODI, SPEAKERS, TV, device, setup_room_devices
from .helpers import ROOM, call_order, get_settings, room_volume, set_setting, tick, vol

# Kodi's picture to the TV, its sound and the music to a receiver; the receiver
# feeds passive speakers and, over its sub out, the subwoofer
DEVICES = [
    device("tv", "display", TV, name="TV"),
    device("avr", "receiver", SPEAKERS, devices=[AVR], name="Receiver"),
    device("boxes", "speakers", name="Speakers"),  # passive: no power of their own
    device("sub", "subwoofer", BASS),
]
CABLES = [
    {"from": "video", "to": "tv", "signals": ["picture"], "input": None, "mode": "bmp"},
    {"from": "video", "to": "avr", "signals": ["sound"], "input": None, "mode": "bmp"},
    {"from": "audio", "to": "avr", "signals": ["sound"], "input": None, "mode": "bmp"},
    {"from": "avr", "to": "boxes", "signals": ["sound"], "input": None, "mode": "bmp"},
    {"from": "avr", "to": "sub", "signals": ["sound"], "input": None, "mode": "bmp"},
]


async def test_the_order_follows_the_cables(hass, home, freezer):
    home.avr("off")
    entry = await setup_room_devices(hass, DEVICES, CABLES)
    power = entry.runtime_data.power
    assert power.on_order == ["tv", "avr", "sub"], "picture first, then the sound from the source on"
    assert power.off_order == ["sub", "avr", "tv"]
    assert "boxes" not in power.nodes, "passive: only on the routes"
    assert set(power.routes["audio"].nodes) == {"avr", "boxes", "sub"}
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    home.kodi("idle")
    await hass.async_block_till_done()
    assert call_order(home, [TV, KODI, SPEAKERS, BASS], "turn_on") == [TV, KODI, SPEAKERS, BASS]


async def test_music_leaves_the_tv_off(hass, home, freezer):
    home.avr("off")
    await setup_room_devices(hass, DEVICES, CABLES)
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert home.on(SPEAKERS) and home.on(BASS) and not home.on(TV)


async def test_after_the_idle_timeout_off_in_order(hass, home, freezer):
    """What a device feeds goes off before it: the subwoofer before the receiver."""
    home.avr("off")
    await setup_room_devices(hass, DEVICES, CABLES)
    await set_setting(hass, "on_wait", 5, device="Receiver")  # (by its name)
    assert (await get_settings(hass))["devices"]["avr"]["on_wait"] == 5
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    home.music("paused")
    await hass.async_block_till_done()
    await tick(hass, freezer, 29 * 60, step=60)
    assert home.on(BASS) and ("media_player", "turn_off", AVR) not in home.calls
    await tick(hass, freezer, 90, step=5)
    assert not home.on(BASS)
    assert ("media_player", "turn_off", AVR) in home.calls
    off = [c[2] for c in home.calls if c[1] == "turn_off"]
    assert off.index(BASS) < off.index(AVR)


async def test_the_room_volume_on_a_device(hass, home, freezer):
    home.avr("off")
    home.music("idle", volume_level=0.8)
    devices = [dict(d) for d in DEVICES]
    devices[1] = {**devices[1], "volume": AVR}
    await setup_room_devices(hass, devices, CABLES, volume={"where": "device", "device": "avr"})
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    await hass.services.async_call("media_player", "volume_set", {"entity_id": ROOM, "volume_level": 0.3}, blocking=True)
    await hass.async_block_till_done()
    assert vol(hass, AVR) == 0.3 and room_volume(hass) == 0.3
    assert vol(hass) == 0.8, "the player keeps its level"
