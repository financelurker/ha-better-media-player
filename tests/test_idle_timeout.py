"""One rule for every player: nothing plays (paused, stopped, its menu) for the idle
timeout, then the room goes off: the sound first, then the player, then the
picture, the player's plug last (after its off_wait). A player turned off: at once."""

from __future__ import annotations

import asyncio

import pytest

from .conftest import AVR, BASS, KODI, LG, MUSIC, SPEAKERS, TV, device, setup_room, setup_room_devices
from .helpers import ROOM, call_order, calls_to, set_night, set_setting, settle_held, speak_later, tick, tick_held

HOST = "switch.kodi_box_plug"


def turned_off(home) -> list[str]:
    return [c[2] for c in home.calls if c[1] == "turn_off"]


async def _video_paused(hass, home, freezer, state="paused"):
    home.kodi("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    home.kodi(state)
    await hass.async_block_till_done()


async def test_a_paused_video_holds_then_sound_player_picture(hass, home, freezer):
    await setup_room(hass)
    await _video_paused(hass, home, freezer)
    await tick(hass, freezer, 29 * 60, step=60)
    assert home.on(TV) and home.on(SPEAKERS)
    assert not calls_to(home, KODI, "turn_off")
    await tick(hass, freezer, 90, step=5)
    assert not home.on(TV) and not home.on(SPEAKERS)
    assert hass.states.get(KODI).state == "off"
    off = turned_off(home)
    assert off.index(SPEAKERS) < off.index(KODI) < off.index(TV), off


async def test_kodi_s_menu_is_the_same_as_paused(hass, home, freezer):
    await setup_room(hass)
    await _video_paused(hass, home, freezer, "idle")
    await tick(hass, freezer, 29 * 60, step=60)
    assert home.on(TV)
    await tick(hass, freezer, 90, step=5)
    assert not home.on(TV) and hass.states.get(KODI).state == "off"


async def test_the_picture_waits_for_the_player_to_go_off(hass, home, freezer):
    """A slow Kodi: the TV stays until Kodi is off (at most a minute)."""
    home.kodi_suspends = False
    await setup_room(hass)
    await _video_paused(hass, home, freezer)
    await tick(hass, freezer, 30 * 60 + 30, step=10)
    assert calls_to(home, KODI, "turn_off") and not home.on(SPEAKERS)
    assert home.on(TV), "waits for Kodi"
    home.kodi("off")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert not home.on(TV)


async def test_the_picture_goes_after_a_minute_if_the_player_does_not(hass, home, freezer):
    home.kodi_suspends = False
    await setup_room(hass)
    await _video_paused(hass, home, freezer)
    await tick(hass, freezer, 30 * 60 + 30, step=10)
    assert home.on(TV)
    await tick(hass, freezer, 70, step=5)
    assert not home.on(TV)


async def test_playing_again_in_time_holds_anew(hass, home, freezer):
    await setup_room(hass)
    await _video_paused(hass, home, freezer)
    await tick(hass, freezer, 20 * 60, step=60)
    home.kodi("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 60)
    home.kodi("paused")
    await hass.async_block_till_done()
    await tick(hass, freezer, 25 * 60, step=60)
    assert home.on(TV), "the idle timeout counts from the second pause"


async def test_a_dropout_of_the_player_does_not_turn_the_room_off(hass, home, freezer):
    await setup_room(hass)
    home.kodi("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    home.kodi("unavailable")  # reconnecting
    await hass.async_block_till_done()
    await tick(hass, freezer, 60)
    assert home.on(TV) and home.on(SPEAKERS)
    home.kodi("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 40 * 60, step=60)
    assert home.on(TV)


async def test_the_night_idle_timeout(hass, home, freezer):
    await setup_room(hass)
    await set_night(hass)
    await _video_paused(hass, home, freezer)
    await tick(hass, freezer, 4 * 60, step=30)
    assert home.on(TV)
    await tick(hass, freezer, 90, step=5)
    assert not home.on(TV), "5 min at night"


async def test_music_after_a_video_the_tv_goes_at_once(hass, home, freezer):
    """A source chosen: what the new one does not need goes off right away."""
    home.kodi_boots = True
    await setup_room(hass)
    home.kodi("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    await hass.services.async_call("media_player", "select_source", {"entity_id": ROOM, "source": "Office Player"}, blocking=True)
    await hass.async_block_till_done()
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert not home.on(TV)
    assert home.on(SPEAKERS)


async def test_music_paused_while_a_video_plays_keeps_what_the_video_needs(hass, home, freezer):
    await setup_room(hass)
    home.music("playing")
    home.kodi("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    home.music("paused")
    await hass.async_block_till_done()
    await tick(hass, freezer, 40 * 60, step=60)
    assert home.on(SPEAKERS) and home.on(TV), "the video still needs them"


async def test_a_player_the_room_cannot_turn_off_stays_on(hass, home, freezer):
    """Kodi set to "None" (the room only wakes it by playing): its devices go off, Kodi stays."""
    entry = await setup_room(hass)
    sources = [dict(s, onoff="none") if s["id"] == "video" else s for s in entry.options["sources"]]
    hass.config_entries.async_update_entry(entry, options={**entry.options, "sources": sources})
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await _video_paused(hass, home, freezer)
    await tick(hass, freezer, 31 * 60, step=60)
    assert not home.on(TV) and not home.on(SPEAKERS)
    assert not calls_to(home, KODI, "turn_off")
    assert hass.states.get(KODI).state == "paused"
    assert hass.states.get("sensor.living_room_status").state == "off", "done with it"


async def test_the_player_s_plug_waits_for_it_to_shut_down(hass, home, freezer):
    hass.states.async_set(HOST, "on")
    await setup_room(hass, video_host=HOST)
    await set_setting(hass, "off_wait", 3, device="video")
    await _video_paused(hass, home, freezer)
    await tick(hass, freezer, 31 * 60, step=30)
    assert hass.states.get(KODI).state == "off" and not home.on(TV)
    assert home.on(HOST), "off_wait after Kodi is off"
    await tick(hass, freezer, 3 * 60 + 30, step=10)
    assert not home.on(HOST)


async def test_turned_off_the_player_s_plug_still_waits(hass, home, freezer):
    """Off means off for the devices; the player's hardware gets its time to shut down."""
    hass.states.async_set(HOST, "on")
    await setup_room(hass, video_host=HOST)
    home.kodi("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    home.kodi("off")  # turned off with its remote
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert not home.on(TV) and not home.on(SPEAKERS)
    assert home.on(HOST)
    await tick(hass, freezer, 2 * 60 + 30, step=10)
    assert not home.on(HOST)


async def test_a_tv_that_kodi_turns_off_goes_with_kodi(hass, home, freezer):
    """A TV switched off by HDMI-CEC: Kodi goes off before it, and takes it along."""
    hass.states.async_set(LG, "off")
    await setup_room_devices(
        hass,
        [device("display", "display", devices=[LG], control={"on": "bmp", "off": "cec", "fallback": True}),
         device("speakers", "speakers", SPEAKERS)],
        [{"from": "video", "to": "display", "signals": ["picture"]}, {"from": "video", "to": "speakers", "signals": ["sound"]}],
    )
    await _video_paused(hass, home, freezer)
    await tick(hass, freezer, 30 * 60 + 30, step=10)
    assert not home.on(SPEAKERS)
    assert hass.states.get(KODI).state == "off"
    assert call_order(home, [SPEAKERS, KODI], "turn_off") == [SPEAKERS, KODI]
    await tick(hass, freezer, 30, step=5)  # (CEC: its fallback after 15 s)
    assert hass.states.get(LG).state == "off"


async def test_an_idle_timeout_of_zero_never_ends(hass, home, freezer):
    await setup_room(hass)
    await set_setting(hass, "idle_timeout", 0)
    await _video_paused(hass, home, freezer)
    await tick(hass, freezer, 4 * 60 * 60, step=600)
    assert home.on(TV)


async def test_a_music_player_without_on_off_its_speakers_go(hass, home, freezer):
    await setup_room(hass, video_player=None, display=None)
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    home.music("idle")  # end of the list
    await hass.async_block_till_done()
    await tick(hass, freezer, 29 * 60, step=60)
    assert home.on(SPEAKERS)
    await tick(hass, freezer, 90, step=5)
    assert not home.on(SPEAKERS) and not home.on(BASS)
    assert not calls_to(home, MUSIC, "turn_off"), "nothing to turn it off with"


async def test_a_stopped_music_player_after_a_restart_does_not_switch_anything_on(hass, home, freezer):
    """Idle for days without on/off (Music Assistant without power control): no hold."""
    home.music("idle")
    await setup_room(hass)
    await tick(hass, freezer, 60)
    assert not home.on(SPEAKERS)


async def test_the_status_while_it_holds(hass, home, freezer):
    await setup_room(hass)
    await _video_paused(hass, home, freezer)
    await tick(hass, freezer, 5)
    assert hass.states.get("sensor.living_room_status").state == "on"


async def test_all_off_the_player_s_plug_waits_for_the_player(hass, home, freezer):
    """All off: its plug is not cut while the player still shuts down (then its off_wait)."""
    home.kodi_suspends = False
    hass.states.async_set(HOST, "on")
    await setup_room(hass, video_host=HOST)
    home.kodi("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    await hass.services.async_call("button", "press", {"entity_id": "button.living_room_all_off"}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert not home.on(TV) and not home.on(SPEAKERS)
    assert home.on(HOST), "Kodi is still on"
    home.kodi("off")
    await hass.async_block_till_done()
    await tick(hass, freezer, 2 * 60 + 30, step=10)
    assert not home.on(HOST)


# ------------------------------------------------------------- found in review


@pytest.mark.parametrize("off", ["cec", "never"])
async def test_a_sound_device_the_room_does_not_switch_off_does_not_hold_the_player(hass, home, freezer, off):
    """A receiver that goes by CEC (no fallback) or never: Kodi and the TV go anyway."""
    home.avr("off", volume_level=0.3)
    await setup_room_devices(
        hass,
        [device("display", "display", TV),
         device("receiver", "receiver", devices=[AVR], control={"on": "bmp", "off": off, "fallback": False})],
        [{"from": "video", "to": "display", "signals": ["picture"]}, {"from": "video", "to": "receiver", "signals": ["sound"]}],
    )
    home.kodi("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    home.avr("on")
    await hass.async_block_till_done()
    home.kodi("paused")
    await hass.async_block_till_done()
    await tick(hass, freezer, 30 * 60 + 20, step=10)
    assert calls_to(home, KODI, "turn_off"), "nothing to wait for: at once"
    await tick(hass, freezer, 60, step=10)
    assert not home.on(TV)


async def test_a_restart_before_kodi_reported_keeps_the_room(hass, home, freezer):
    for plug in (TV, SPEAKERS):
        hass.states.async_set(plug, "on")
    home.kodi("unavailable")
    await setup_room(hass)
    await tick(hass, freezer, 10)
    assert home.on(TV), "nothing switched before Kodi reported"
    home.kodi("paused")
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    assert home.on(TV) and not calls_to(home, TV, "turn_off")


async def test_a_restart_before_the_music_player_reported_holds(hass, home, freezer):
    for plug in (SPEAKERS, BASS):
        hass.states.async_set(plug, "on")
    home.music("unavailable")
    await setup_room(hass)
    await tick(hass, freezer, 3)
    home.music("paused")
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    assert home.on(SPEAKERS), "paused with its speakers on: it holds"


async def test_started_by_the_room_a_player_that_reports_off_holds(hass, home, freezer):
    """Switched on by actions, it reports off until it plays: the start keeps its speakers."""
    entry = await setup_room(hass)
    sources = [
        dict(s, onoff="actions", on_action="script.music_on", off_action="script.music_off") if s["id"] == "audio" else s
        for s in entry.options["sources"]
    ]
    hass.config_entries.async_update_entry(entry, options={**entry.options, "sources": sources})
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    home.music("off")
    await hass.async_block_till_done()
    await hass.services.async_call("media_player", "select_source", {"entity_id": ROOM, "source": "Office Player"}, blocking=False)
    await hass.async_block_till_done()
    await tick(hass, freezer, 1)
    home.music("idle")  # its action brought it up
    await hass.async_block_till_done()
    await tick(hass, freezer, 20)
    assert home.on(SPEAKERS), "started by the room: it holds until it plays"


async def test_a_music_player_with_only_its_hardware_plug_goes_off(hass, home, freezer):
    hass.states.async_set(HOST, "on")
    home.ma_power = True
    home.music("idle")
    await setup_room(hass, video_player=None, display=None, speakers=None, bass=None, audio_host=HOST)
    await tick(hass, freezer, 29 * 60, step=60)
    assert not calls_to(home, MUSIC, "turn_off"), "after a restart it holds"
    await tick(hass, freezer, 5 * 60, step=30)
    assert calls_to(home, MUSIC, "turn_off")
    assert not home.on(HOST)


async def test_paused_music_ends_when_a_video_is_chosen(hass, home, freezer):
    """Off means off: after the video nothing waits for the music paused before it."""
    home.kodi_boots = True
    await setup_room(hass)
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    home.music("paused")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    await hass.services.async_call("media_player", "select_source", {"entity_id": ROOM, "source": "Kodi"}, blocking=True)
    await hass.async_block_till_done()
    home.kodi("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 60)
    home.kodi("off")  # turned off with its remote
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    assert not home.on(SPEAKERS) and not home.on(TV)


async def test_a_moment_between_two_items_is_no_run_down(hass, home, freezer):
    """Kodi idle for a moment between two episodes: no "started" / "cancelled"."""
    await setup_room(hass)
    seen = []
    hass.bus.async_listen("better_media_player_run_down", lambda e: seen.append(e.data["type"]))
    home.kodi("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    home.kodi("idle")
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    home.kodi("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 30)
    assert seen == []


async def test_a_new_time_is_told_again(hass, home, freezer):
    """Night mode during a pause: the shorter idle timeout, "started" with the new time."""
    await setup_room(hass)
    seen = []
    hass.bus.async_listen("better_media_player_run_down", lambda e: seen.append(dict(e.data)))
    await _video_paused(hass, home, freezer)
    await tick(hass, freezer, 20)
    first = seen[-1]["devices"]["display"]
    n = len(seen)
    await set_night(hass)
    await tick(hass, freezer, 10)
    again = [e for e in seen[n:] if e["type"] == "started" and "display" in e["devices"]]
    assert again and again[-1]["devices"]["display"] < first


async def test_the_player_waits_for_its_sound_devices(hass, home, freezer):
    """Speakers that wait for their standby: Kodi goes after them (at most a minute)."""
    hass.states.async_set("sensor.speaker_power", "20")
    await setup_room(hass, audio_power=["sensor.speaker_power"], audio_standby=True)
    await _video_paused(hass, home, freezer)
    await tick(hass, freezer, 30 * 60 + 20, step=10)
    assert home.on(SPEAKERS), "waits for its standby"
    assert not calls_to(home, KODI, "turn_off"), "the sound first"
    await tick(hass, freezer, 60, step=10)
    assert calls_to(home, KODI, "turn_off"), "at most a minute"


async def test_an_announcement_in_a_room_that_is_off_is_no_use(hass, home, freezer):
    """The music player is stopped (idle); an announcement plays on it: nothing holds after."""
    home.ma_announce = True
    home.music("idle")
    await setup_room(hass)
    await tick(hass, freezer, 5)
    home.announce_hold = asyncio.Event()
    task = speak_later(hass)
    await settle_held(hass)
    await tick_held(hass, freezer, 3)
    assert home.on(SPEAKERS), "up for it"
    home.announce_hold.set()
    await task
    await tick(hass, freezer, 20)
    assert not home.on(SPEAKERS), "off right after it, no idle timeout"
