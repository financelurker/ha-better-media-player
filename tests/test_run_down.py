"""The run-down made visible ("off at" sensors, triggers) and held ("Keep on")."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .conftest import BASS, KODI, LG, MUSIC, SPEAKERS, TV, device, setup_room, setup_room_devices
from .helpers import ROOM, change_room, set_night, settle_held, speak, speak_later, tick

KEEP_ON = "switch.living_room_keep_on"
STATUS = "sensor.living_room_status"
EVENT = "better_media_player_run_down"


def off_at(hass, device: str):
    state = hass.states.get(f"sensor.living_room_{device}_off_at").state
    return None if state == "unknown" else dt_util.parse_datetime(state)


def events(hass):
    seen = []
    hass.bus.async_listen(EVENT, lambda e: seen.append({k: v for k, v in e.data.items() if k not in ("entity_id", "entry_id", "room")}))
    return seen


async def music_then_stop(hass, home, freezer):
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert home.on(SPEAKERS)
    home.music("paused")
    await hass.async_block_till_done()
    await tick(hass, freezer, 1)
    return dt_util.utcnow()


async def keep_on(hass, on: bool = True):
    await hass.services.async_call("switch", "turn_on" if on else "turn_off", {"entity_id": KEEP_ON}, blocking=True)
    await hass.async_block_till_done()


# ------------------------------------------------------------------ off at


async def test_off_at_is_the_end_of_the_run_down(hass, home, freezer):
    await setup_room(hass)
    seen = events(hass)
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert off_at(hass, "speakers") is None, "needed: none planned"
    home.music("paused")
    await hass.async_block_till_done()
    stopped = dt_util.utcnow()
    await tick(hass, freezer, 1)
    for dev in ("speakers", "subwoofer"):
        assert abs(off_at(hass, dev) - (stopped + timedelta(minutes=2))) <= timedelta(seconds=10)
    assert off_at(hass, "display") is None, "off: none planned"
    assert seen == [{"type": "started", "devices": {k: off_at(hass, k).isoformat() for k in ("speakers", "subwoofer")}}]

    await tick(hass, freezer, 130)
    assert not home.on(SPEAKERS) and not home.on(BASS)
    assert off_at(hass, "speakers") is None
    assert sorted((e["device"], e["name"]) for e in seen if e["type"] == "off") == [("speakers", "Speakers"), ("subwoofer", "Subwoofer")]


async def test_needed_again_cancels_it(hass, home, freezer):
    await setup_room(hass)
    seen = events(hass)
    await music_then_stop(hass, home, freezer)
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 1)
    assert off_at(hass, "speakers") is None
    assert seen[-1] == {"type": "cancelled", "devices": ["speakers", "subwoofer"], "reason": "needed"}


async def test_at_night_the_night_run_down(hass, home, freezer):
    await setup_room(hass)
    home.kodi("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    await set_night(hass)
    home.kodi("off")
    await hass.async_block_till_done()
    stopped = dt_util.utcnow()
    await tick(hass, freezer, 1)
    assert abs(off_at(hass, "display") - (stopped + timedelta(minutes=1))) <= timedelta(seconds=10)


async def test_all_off_is_no_run_down(hass, home, freezer):
    await setup_room(hass)
    seen = events(hass)
    await music_then_stop(hass, home, freezer)
    await hass.services.async_call("button", "press", {"entity_id": "button.living_room_all_off"}, blocking=True)
    await tick(hass, freezer, 5)
    assert not home.on(SPEAKERS)
    assert [e["type"] for e in seen] == ["started", "cancelled"]
    assert seen[-1]["reason"] == "all_off"


async def test_a_player_s_hardware_plug_has_one(hass, home, freezer):
    hass.states.async_set("switch.kodi_box_plug", "off")
    await setup_room(hass, audio_host="switch.kodi_box_plug")
    state = hass.states.get("sensor.living_room_audio_host_off_at")
    assert state.attributes["friendly_name"] == "Living Room Music player hardware off at"


async def test_a_removed_device_takes_its_sensor_along(hass, home):
    entry = await setup_room(hass)
    reg = er.async_get(hass)
    assert reg.async_get("sensor.living_room_subwoofer_off_at") is not None
    await change_room(hass, entry, bass=None)
    assert reg.async_get("sensor.living_room_subwoofer_off_at") is None
    assert reg.async_get("sensor.living_room_speakers_off_at") is not None


# ------------------------------------------------------------------ keep on


async def test_keep_on_holds_the_run_down(hass, home, freezer):
    entry = await setup_room(hass)
    seen = events(hass)
    await music_then_stop(hass, home, freezer)
    await keep_on(hass)
    assert seen[-1] == {"type": "cancelled", "devices": ["speakers", "subwoofer"], "reason": "kept_on"}
    assert off_at(hass, "speakers") is None
    assert hass.states.get(STATUS).state == "kept_on"
    await tick(hass, freezer, 30 * 60, step=60)
    assert home.on(SPEAKERS) and home.on(BASS)

    # a start still switches on; the end of it runs down nothing
    home.kodi("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert home.on(TV)
    home.kodi("off")
    await hass.async_block_till_done()
    await tick(hass, freezer, 20 * 60, step=60)
    assert home.on(TV)
    assert entry.runtime_data.keep_on, "stays on until turned off"

    # turned off: runs down from now (not from its last need)
    await keep_on(hass, False)
    await tick(hass, freezer, 60)
    assert home.on(SPEAKERS)
    await tick(hass, freezer, 80)
    assert not home.on(SPEAKERS)
    assert home.on(TV)
    await tick(hass, freezer, 9 * 60, step=30)
    assert not home.on(TV)


async def test_keep_on_set_ahead_and_over_a_reload(hass, home, freezer):
    entry = await setup_room(hass)
    await keep_on(hass)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(KEEP_ON).state == "on"
    await music_then_stop(hass, home, freezer)
    await tick(hass, freezer, 5 * 60, step=30)
    assert home.on(SPEAKERS)
    assert hass.states.get(STATUS).state == "kept_on"


async def test_all_off_ends_keep_on(hass, home, freezer):
    await setup_room(hass)
    await keep_on(hass)
    await music_then_stop(hass, home, freezer)
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await tick(hass, freezer, 5)
    assert not home.on(SPEAKERS)
    assert hass.states.get(KEEP_ON).state == "off"


async def test_keep_on_holds_every_automatic_off(hass, home, freezer):
    """Night (the subwoofer off at night), the idle video player, the sleep timer."""
    await setup_room(hass)
    await keep_on(hass)
    home.kodi("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    await set_night(hass)
    await tick(hass, freezer, 5)
    assert home.on(BASS), "kept on: not off at night"
    home.kodi("idle")
    await hass.async_block_till_done()
    await tick(hass, freezer, 40 * 60, step=60)
    assert ("media_player", "turn_off", KODI) not in home.calls, "kept on: the idle player is not turned off"
    assert home.on(TV) and home.on(SPEAKERS)

    # the sleep timer stops the room's sources; kept on, its devices stay on
    await hass.services.async_call("better_media_player", "set_sleep_timer", {"entity_id": ROOM, "minutes": 1, "fade_minutes": 0}, blocking=True)
    await tick(hass, freezer, 90, step=5)
    assert home.on(TV) and home.on(SPEAKERS)

    # off again: the night's subwoofer goes at once
    await keep_on(hass, False)
    await tick(hass, freezer, 5)
    assert not home.on(BASS)


async def test_switched_off_by_hand_is_no_run_down(hass, home, freezer):
    await setup_room(hass)
    seen = events(hass)
    await music_then_stop(hass, home, freezer)
    await hass.services.async_call("switch", "turn_off", {"entity_id": SPEAKERS}, blocking=True)
    await tick(hass, freezer, 5)
    assert not [e for e in seen if e["type"] == "off"]
    assert seen[-1]["type"] == "cancelled" and seen[-1]["reason"] in ("manual", "switched_off")


async def test_the_sensor_keeps_its_id_when_the_device_is_renamed(hass, home):
    entry = await setup_room(hass)
    options = {**entry.options, "devices": [dict(d, name="LG TV") if d["id"] == "display" else d for d in entry.options["devices"]]}
    hass.config_entries.async_update_entry(entry, options=options)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.living_room_display_off_at").attributes["friendly_name"] == "Living Room LG TV off at"


async def test_a_device_turned_off_before_its_time_is_no_run_down(hass, home, freezer):
    """A TV turned off with its remote while it runs down: not "switched off after its run-down"."""
    hass.states.async_set(LG, "off")
    await setup_room_devices(hass, [device("display", "display", devices=[LG])], [{"from": "video", "to": "display", "signals": ["picture"]}])
    seen = events(hass)
    home.kodi("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert hass.states.get(LG).state == "on"
    home.kodi("off")
    await hass.async_block_till_done()
    await tick(hass, freezer, 60, step=5)
    assert seen and seen[-1]["type"] == "started"
    hass.states.async_set(LG, "off")  # its remote
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert not [e for e in seen if e["type"] == "off"], seen
    assert seen[-1] == {"type": "cancelled", "devices": ["display"], "reason": "switched_off"}


async def test_kept_on_what_an_announcement_brought_up_stays(hass, home, freezer):
    home.ma_announce = True
    home.music("off")
    await setup_room(hass)
    await keep_on(hass)
    await speak(hass)
    await tick(hass, freezer, 10)
    assert home.on(SPEAKERS), "kept on: not off after the announcement"


async def test_kept_on_a_music_player_turned_on_stays_on(hass, home, freezer):
    """Turned on (in Music Assistant) but nothing played: kept on, neither it nor its speakers go off."""
    home.ma_power = True
    home.music("off")
    entry = await setup_room(hass, video_player=None, display=None)
    entry.runtime_data.data["last_source"] = "audio"
    await keep_on(hass)
    home.music("idle")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5 * 60, step=15)
    assert home.on(SPEAKERS) and home.on(BASS)
    assert not [c for c in home.calls if c[1] == "turn_off"]


# ------------------------------------------------------------------ not a run-down


async def test_a_start_is_no_run_down(hass, home, freezer):
    """The display is on but not yet needed while the player starts: nothing runs down."""
    await setup_room(hass)
    seen = events(hass)
    home.kodi("off")
    await hass.async_block_till_done()
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    for _ in range(5):
        await tick(hass, freezer, 1)
        assert off_at(hass, "display") is None
    home.kodi("idle")
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert home.on(TV)
    assert seen == [], seen


async def test_a_failed_start_is_no_run_down(hass, home, freezer):
    await setup_room(hass)
    seen = events(hass)
    home.kodi("off")
    await hass.async_block_till_done()
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await tick(hass, freezer, 120, step=2)
    assert not home.on(TV), "taken down again"
    assert seen == [], seen


async def test_a_failed_start_s_device_slow_to_go_off_is_no_run_down(hass, home, freezer):
    """Taken down, but the TV stays on a while: still not its run-down."""
    hass.states.async_set(LG, "off")
    await setup_room_devices(hass, [device("display", "display", devices=[LG])], [{"from": "video", "to": "display", "signals": ["picture"]}])
    seen = events(hass)
    home.kodi("off")
    await hass.async_block_till_done()
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await tick(hass, freezer, 5)
    assert hass.states.get(LG).state == "on"
    home.lg_obeys = False  # (it ignores the off for now)
    await tick(hass, freezer, 180, step=2)
    assert hass.states.get(LG).state == "on"
    assert seen == [], seen


async def test_a_restart_starts_no_second_run_down(hass, home, freezer):
    entry = await setup_room(hass)
    await music_then_stop(hass, home, freezer)
    seen = events(hass)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert off_at(hass, "speakers") is not None, "still runs down (anew)"
    assert seen == [], seen


async def test_a_room_off_is_room_off_not_all_off(hass, home, freezer):
    """The sleep timer's end (not the user's All off)."""
    await setup_room(hass)
    seen = events(hass)
    await music_then_stop(hass, home, freezer)
    await hass.services.async_call("better_media_player", "set_sleep_timer", {"entity_id": ROOM, "minutes": 1, "fade_minutes": 0}, blocking=True)
    await tick(hass, freezer, 90, step=5)
    assert not home.on(SPEAKERS)
    assert seen[-1] == {"type": "cancelled", "devices": ["speakers", "subwoofer"], "reason": "room_off"}


async def test_a_device_never_switched_off_has_no_time(hass, home, freezer):
    """Switched off by no one but its own remote: no "off at"."""
    hass.states.async_set(LG, "off")
    await setup_room_devices(
        hass, [device("display", "display", devices=[LG], control={"on": "bmp", "off": "never"})],
        [{"from": "video", "to": "display", "signals": ["picture"]}],
    )
    home.kodi("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    home.kodi("off")
    await hass.async_block_till_done()
    await tick(hass, freezer, 60)
    assert hass.states.get(LG).state == "on"
    assert off_at(hass, "display") is None


async def test_an_unnamed_device_s_sensor_is_named_by_its_kind(hass, home):
    """(Translated: "Lautsprecher aus um" in German.)"""
    await setup_room(hass)
    reg = er.async_get(hass)
    assert reg.async_get("sensor.living_room_speakers_off_at").translation_key == "speakers_off_at"


# ------------------------------------------------------------------ keep on, more


async def test_keep_on_off_does_not_turn_an_idle_player_off_at_once(hass, home, freezer):
    await setup_room(hass)
    await keep_on(hass)
    home.kodi("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    home.kodi("idle")
    await hass.async_block_till_done()
    await tick(hass, freezer, 40 * 60, step=60)
    await keep_on(hass, False)
    await tick(hass, freezer, 60)
    assert ("media_player", "turn_off", KODI) not in home.calls, "idle counts from now"


async def test_keep_on_stops_an_automatic_off_on_its_way(hass, home, freezer):
    entry = await setup_room(hass)
    await music_then_stop(hass, home, freezer)
    ctrl = entry.runtime_data
    for key in ("speakers", "subwoofer"):
        ctrl.power.nodes[key].force_off = True  # (e.g. a music player's turn-on window ended)
        ctrl.power.nodes[key].stop_since = 0.0
    await keep_on(hass)
    await tick(hass, freezer, 5 * 60, step=15)
    assert home.on(SPEAKERS) and home.on(BASS)
    assert hass.states.get(STATUS).state == "kept_on"


async def test_kept_on_a_room_off_during_an_announcement_still_stops_the_players(hass, home, freezer):
    home.ma_announce = True
    home.ma_power = True
    home.music("paused")
    entry = await setup_room(hass)
    entry.runtime_data.data["last_source"] = "audio"
    await tick(hass, freezer, 3)
    await keep_on(hass)
    home.announce_hold = asyncio.Event()
    task = speak_later(hass)
    await settle_held(hass)
    await entry.runtime_data.async_turn_off()  # (the sleep timer's end)
    n = len(home.calls)
    home.announce_hold.set()
    await task
    await tick(hass, freezer, 5)
    assert ("media_player", "turn_off", MUSIC) in home.calls[n:], "the players stop"
    assert home.on(SPEAKERS), "kept on: the devices stay"
