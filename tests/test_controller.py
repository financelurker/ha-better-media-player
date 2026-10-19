"""Behaviour of the room controller."""

from __future__ import annotations

from datetime import timedelta

from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.better_media_player.const import (
    CONF_BASS,
    CONF_DISPLAY,
    CONF_DISPLAY_POWER,
    CONF_DISPLAY_STANDBY,
    CONF_VIDEO_PLAYER,
)

from .conftest import BASS, GROUP, KODI, MUSIC, SPEAKERS, TV, TV_POWER, setup_room

ROOM = "media_player.wohnzimmer"


async def _tick(hass: HomeAssistant, freezer, seconds: float) -> None:
    """Advance time in steps so periodic checks and timeouts fire."""
    step = 10.0
    left = seconds
    while left > 0:
        freezer.tick(timedelta(seconds=min(step, left)))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        left -= step


def _order(home, entities: list[str], service: str) -> list[str]:
    return [c[2] for c in home.calls if c[2] in entities and c[1] == service]


# ------------------------------------------------------------------- start


async def test_turn_on_starts_kodi_in_order(hass, home, freezer):
    await setup_room(hass)
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()

    # TV first, then Kodi is woken; speakers wait for Kodi
    assert home.on(TV)
    assert ("media_player", "turn_on", KODI) in home.calls
    assert not home.on(SPEAKERS)
    assert hass.states.get("sensor.wohnzimmer_status").state == "starting"

    home.kodi("idle")  # Kodi is up
    await hass.async_block_till_done()
    assert home.on(SPEAKERS) and home.on(BASS)
    assert _order(home, [TV, KODI, SPEAKERS, BASS], "turn_on") == [TV, KODI, SPEAKERS, BASS]
    assert hass.states.get(ROOM).state == "idle"
    assert hass.states.get("sensor.wohnzimmer_status").state == "on"


async def test_start_timeout_rolls_back(hass, home, freezer):
    await setup_room(hass)
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert home.on(TV)

    await _tick(hass, freezer, 100)  # Kodi never answers (timeout 90 s)
    assert not home.on(TV)
    assert not home.on(SPEAKERS)
    assert hass.states.get("binary_sensor.wohnzimmer_problem").state == "on"
    assert hass.states.get("sensor.wohnzimmer_status").state == "error"


async def test_turn_on_music_only_switches_speakers(hass, home):
    entry = await setup_room(hass)
    entry.runtime_data.data["last_source"] = "audio"
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert home.on(SPEAKERS) and home.on(BASS)
    assert not home.on(TV)
    assert ("media_player", "turn_on", KODI) not in home.calls


# ---------------------------------------------------------- auto on / off


async def test_music_auto_on_and_run_down(hass, home, freezer):
    await setup_room(hass)
    home.music("playing")
    await hass.async_block_till_done()
    assert home.on(SPEAKERS) and home.on(BASS) and not home.on(TV)
    assert _order(home, [SPEAKERS, BASS], "turn_on") == [SPEAKERS, BASS]

    home.music("paused")
    await _tick(hass, freezer, 60)
    assert home.on(SPEAKERS), "run-down (2 min) not over yet"
    await _tick(hass, freezer, 70)
    assert not home.on(SPEAKERS) and not home.on(BASS)
    assert _order(home, [SPEAKERS, BASS], "turn_off") == [BASS, SPEAKERS]


async def test_group_playback_counts_as_demand(hass, home):
    await setup_room(hass)
    home.group("playing", [MUSIC])
    home.music("playing", active_queue="syncgroup_x")
    await hass.async_block_till_done()
    assert home.on(SPEAKERS)


async def test_shared_speakers_stay_on_for_kodi(hass, home, freezer):
    await setup_room(hass)
    home.music("playing")
    home.kodi("playing")
    await hass.async_block_till_done()
    assert home.on(TV) and home.on(SPEAKERS)

    home.music("idle")  # music stops, Kodi still plays
    await _tick(hass, freezer, 300)
    assert home.on(SPEAKERS) and home.on(TV)


async def test_kodi_off_display_run_down_longer_than_speakers(hass, home, freezer):
    await setup_room(hass)
    home.kodi("playing")
    await hass.async_block_till_done()
    home.kodi("off")
    await _tick(hass, freezer, 130)
    assert not home.on(SPEAKERS)
    assert home.on(TV), "display run-down is 10 min"
    await _tick(hass, freezer, 480)
    assert not home.on(TV)


async def test_video_idle_timeout_turns_kodi_off(hass, home, freezer):
    await setup_room(hass)
    home.kodi("idle")
    await hass.async_block_till_done()
    assert home.on(TV)
    await _tick(hass, freezer, 29 * 60)
    assert ("media_player", "turn_off", KODI) not in home.calls
    await _tick(hass, freezer, 70)
    assert ("media_player", "turn_off", KODI) in home.calls


async def test_auto_power_off_does_nothing_automatically(hass, home):
    await setup_room(hass)
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.wohnzimmer_automatic_power"}, blocking=True)
    home.music("playing")
    await hass.async_block_till_done()
    assert not home.on(SPEAKERS)
    assert hass.states.get("sensor.wohnzimmer_status").state == "disabled"


# --------------------------------------------------------------- room off


async def test_room_off_leaves_group_and_stays_off(hass, home, freezer):
    await setup_room(hass)
    home.group("playing", [MUSIC])
    home.music("playing")
    await hass.async_block_till_done()
    assert home.on(SPEAKERS)

    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert ("media_player", "unjoin", MUSIC) in home.calls
    assert ("media_player", "media_stop", MUSIC) not in home.calls
    assert not home.on(SPEAKERS) and not home.on(BASS)
    assert hass.states.get(GROUP).attributes["group_members"] == []

    await _tick(hass, freezer, 60)
    assert not home.on(SPEAKERS)
    assert hass.states.get(ROOM).state == "off"


async def test_room_off_with_kodi_that_suspends_slowly(hass, home, freezer):
    home.kodi_suspends = False
    await setup_room(hass)
    home.kodi("idle")
    await hass.async_block_till_done()
    assert home.on(TV)

    await hass.services.async_call("button", "press", {"entity_id": "button.wohnzimmer_all_off"}, blocking=True)
    await hass.async_block_till_done()
    assert ("media_player", "turn_off", KODI) in home.calls
    assert not home.on(TV) and not home.on(SPEAKERS)

    # Kodi is still "idle" for a while: must not switch the room back on
    await _tick(hass, freezer, 30)
    assert not home.on(TV)
    home.kodi("off")
    await hass.async_block_till_done()
    # later someone starts a movie: that is a fresh wish
    home.kodi("playing")
    await hass.async_block_till_done()
    assert home.on(TV)


async def test_direct_playback_is_stopped_on_room_off(hass, home):
    await setup_room(hass)
    home.music("playing")
    await hass.async_block_till_done()
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert ("media_player", "media_stop", MUSIC) in home.calls
    assert not home.on(SPEAKERS)


# ------------------------------------------------------------- manual mode


async def test_manual_switching_is_respected(hass, home, freezer):
    await setup_room(hass)
    hass.states.async_set(SPEAKERS, "on")  # someone switches the plug by hand
    await hass.async_block_till_done()
    assert hass.states.get("sensor.wohnzimmer_status").state == "manual"

    await _tick(hass, freezer, 600)
    assert home.on(SPEAKERS), "manual mode: not switched off after run-down"

    home.music("playing")
    hass.states.async_set(SPEAKERS, "off")  # all off by hand -> manual mode ends
    await hass.async_block_till_done()
    assert hass.states.get("sensor.wohnzimmer_status").state != "manual"
    assert not home.on(SPEAKERS), "still playing music must not switch it straight back on"

    home.music("idle")
    await hass.async_block_till_done()
    home.music("playing")  # new playback: back to automatic
    await hass.async_block_till_done()
    assert home.on(SPEAKERS)


# ----------------------------------------------------------------- sources


async def test_select_source_switches_and_remembers(hass, home):
    entry = await setup_room(hass)
    home.kodi_boots = True
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert home.on(TV)

    state = hass.states.get(ROOM)
    assert state.attributes["source_list"] == ["Kodi", "Office Player"]
    await hass.services.async_call(
        "media_player", "select_source", {"entity_id": ROOM, "source": "Office Player"}, blocking=True
    )
    await hass.async_block_till_done()
    assert ("media_player", "turn_off", KODI) in home.calls
    assert entry.runtime_data.last_source == "audio"

    # survives a reload (= HA restart)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.last_source == "audio"


async def test_volume_goes_to_active_source(hass, home):
    await setup_room(hass)
    home.music("playing", volume_level=0.38, media_title="All Summer Long")
    await hass.async_block_till_done()
    state = hass.states.get(ROOM)
    assert state.state == "playing"
    assert state.attributes["media_title"] == "All Summer Long"
    assert state.attributes["volume_level"] == 0.38

    await hass.services.async_call(
        "media_player", "volume_set", {"entity_id": ROOM, "volume_level": 0.5}, blocking=True
    )
    assert ("media_player", "volume_set", MUSIC) in home.calls
    assert ("media_player", "volume_set", KODI) not in home.calls


# ----------------------------------------------------------------- standby


async def test_display_waits_for_standby(hass, home, freezer):
    hass.states.async_set(TV_POWER, "80")
    await setup_room(hass, **{CONF_DISPLAY_POWER: [TV_POWER], CONF_DISPLAY_STANDBY: True})
    home.kodi("playing")
    await hass.async_block_till_done()
    home.kodi("off")
    await _tick(hass, freezer, 11 * 60)
    assert home.on(TV), "TV still draws power"
    assert hass.states.get("sensor.wohnzimmer_status").state == "stopping"

    hass.states.async_set(TV_POWER, "0.5")
    await _tick(hass, freezer, 40)
    assert not home.on(TV)


async def test_display_standby_max_wait(hass, home, freezer):
    hass.states.async_set(TV_POWER, "80")
    await setup_room(hass, **{CONF_DISPLAY_POWER: [TV_POWER], CONF_DISPLAY_STANDBY: True})
    home.kodi("playing")
    await hass.async_block_till_done()
    home.kodi("off")
    await _tick(hass, freezer, 10 * 60 + 21 * 60)
    assert not home.on(TV)


# ---------------------------------------------------------------- scenarios


async def test_scenario_2_music_only(hass, home):
    await setup_room(hass, **{CONF_VIDEO_PLAYER: None, CONF_DISPLAY: None, CONF_BASS: None})
    state = hass.states.get(ROOM)
    assert state.state == "off"
    assert "source_list" not in state.attributes
    home.music("playing")
    await hass.async_block_till_done()
    assert home.on(SPEAKERS) and not home.on(TV)


# ------------------------------------------------------------ robustness


async def test_late_device_report_is_not_manual(hass, home, freezer):
    """Zigbee & co. report the new state later with their own context."""
    await setup_room(hass)
    original = home.handle

    def late(domain, service, entity_id, data, context):
        if entity_id == SPEAKERS:
            home.calls.append((domain, service, entity_id))
            return True  # command sent, state arrives later
        return original(domain, service, entity_id, data, context)

    home.handle = late
    home.music("playing")
    await hass.async_block_till_done()
    assert ("switch", "turn_on", SPEAKERS) in home.calls
    hass.states.async_set(SPEAKERS, "on")  # device reports, new context
    await hass.async_block_till_done()
    assert hass.states.get("sensor.wohnzimmer_status").state == "playing"


async def test_restart_with_plugs_on_runs_down(hass, home, freezer):
    hass.states.async_set(SPEAKERS, "on")
    hass.states.async_set(TV, "on")
    await setup_room(hass)
    assert hass.states.get("sensor.wohnzimmer_status").state == "run_down"
    await _tick(hass, freezer, 130)
    assert not home.on(SPEAKERS)
    await _tick(hass, freezer, 500)
    assert not home.on(TV)


async def test_player_behind_host_plug(hass, home, freezer):
    """Music player on its own hardware (e.g. a Pi) that hangs on a plug."""
    hass.states.async_set("switch.kodi_box_plug", "off")
    home.music("unavailable")
    entry = await setup_room(hass, video_player=None, display=None, audio_host="switch.kodi_box_plug")
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert home.on("switch.kodi_box_plug")
    assert not home.on(SPEAKERS), "speakers wait for the player"

    home.music("idle")  # player booted
    await hass.async_block_till_done()
    assert home.on(SPEAKERS)
    assert entry.runtime_data.status == "run_down"  # on, but nothing plays yet

    await _tick(hass, freezer, 130)  # nothing played: speakers + hardware run down
    assert not home.on(SPEAKERS)
    assert not home.on("switch.kodi_box_plug")


async def test_entity_ids_match_readme(hass, home):
    hass.states.async_set("switch.kodi_box_plug", "off")
    entry = await setup_room(hass, audio_host="switch.kodi_box_plug")
    ids = {e.entity_id for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)}
    assert ids == {
        "media_player.wohnzimmer",
        "sensor.wohnzimmer_status",
        "binary_sensor.wohnzimmer_problem",
        "binary_sensor.wohnzimmer_display_needed",
        "binary_sensor.wohnzimmer_speakers_needed",
        "switch.wohnzimmer_automatic_power",
        "switch.wohnzimmer_quiet",
        "number.wohnzimmer_display_run_down",
        "number.wohnzimmer_speaker_run_down",
        "number.wohnzimmer_player_hardware_run_down",
        "number.wohnzimmer_video_idle_timeout",
        "button.wohnzimmer_all_off",
        "number.wohnzimmer_sleep_timer",
        "button.wohnzimmer_sleep_timer_15_min",
        "sensor.wohnzimmer_sleep_timer_end",
        "switch.wohnzimmer_night_mode",
        "number.wohnzimmer_night_video_idle_timeout",
        "number.wohnzimmer_night_display_run_down",
        "number.wohnzimmer_night_maximum_volume",
    }


# ------------------------------------------------------------------ cover art


MA_COVER = "http://192.168.10.21:8095/imageproxy/abc?size=512&fmt=jpg"


async def test_music_cover_goes_through_ha_proxy(hass, home, hass_client, aioclient_mock):
    """Music Assistant gives an absolute http URL: serve it via the HA image proxy."""
    aioclient_mock.get(MA_COVER, content=b"JPEGDATA", headers={"Content-Type": "image/jpeg"})
    await setup_room(hass)
    home.music("playing", entity_picture=MA_COVER, media_title="All Summer Long")
    await hass.async_block_till_done()

    picture = hass.states.get(ROOM).attributes["entity_picture"]
    assert picture.startswith(f"/api/media_player_proxy/{ROOM}?token=")
    assert "192.168.10.21" not in picture

    client = await hass_client()
    resp = await client.get(picture)
    assert resp.status == 200
    assert await resp.read() == b"JPEGDATA"


async def test_relative_cover_is_passed_through(hass, home):
    """Kodi's picture is already an HA proxy path: keep it."""
    await setup_room(hass)
    kodi_pic = "/api/media_player_proxy/media_player.kodi?token=x&cache=y"
    home.kodi("playing", entity_picture=kodi_pic)
    await hass.async_block_till_done()
    assert hass.states.get(ROOM).attributes["entity_picture"] == kodi_pic


async def test_device_shows_manifest_version(hass, home):
    """The version shown on the device page comes from manifest.json (set from the release tag)."""
    import json
    from pathlib import Path

    from homeassistant.helpers import device_registry as dr

    manifest = Path(__file__).parents[1] / "custom_components" / "better_media_player" / "manifest.json"
    version = json.loads(manifest.read_text())["version"]
    entry = await setup_room(hass)
    device = next(iter(dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)))
    assert device.sw_version == version
