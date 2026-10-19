"""Wake up: regressions from the review."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.better_media_player.const import (
    DOMAIN,
    EVENT_WAKE_FAILED,
    EVENT_WAKE_FINISHED,
    EVENT_WAKE_STARTED,
)

from .conftest import MUSIC
from .test_wake import ROOM, WAKE, _count, _room, _sent, _settle, _tick, _vol, _wake, _wake_playing


async def test_unexpected_error_of_the_player_is_a_failed_wake(hass, home, freezer):
    failed = async_capture_events(hass, EVENT_WAKE_FAILED)
    await _room(hass, home)
    orig = home.handle

    def handle(domain, service, entity_id, data, context):
        if service == "play_media" and entity_id == MUSIC:
            raise ValueError("bad media id")
        return orig(domain, service, entity_id, data, context)

    home.handle = handle
    await _wake(hass)
    await _settle(hass)
    assert [e.data["reason"] for e in failed] == ["start_failed"]
    assert hass.states.get(WAKE).state == "idle"


async def test_replacing_playback_still_checks_the_new_one(hass, home, freezer):
    await _room(hass, home)
    home.music("playing")
    await hass.async_block_till_done()
    started = async_capture_events(hass, EVENT_WAKE_STARTED)
    failed = async_capture_events(hass, EVENT_WAKE_FAILED)
    await _wake(hass, start_volume=0.05, target_volume=0.4, fade_minutes=2)
    assert started == []  # the old playback is no proof
    home.music("idle")  # the new stream does not play
    await hass.async_block_till_done()
    await _tick(hass, freezer, 65)
    assert [e.data["reason"] for e in failed] == ["not_playing"]


async def test_snoozed_player_that_dropped_to_idle_is_started_again(hass, home, freezer):
    await _room(hass, home)
    await _wake_playing(hass, home, freezer)
    await hass.services.async_call(DOMAIN, "snooze", {"entity_id": ROOM, "minutes": 2}, blocking=True)
    await hass.async_block_till_done()
    n_play = _count(home, MUSIC, "play_media")
    home.music("idle")  # e.g. Music Assistant ended the paused queue
    await hass.async_block_till_done()
    await _tick(hass, freezer, 125)
    assert _count(home, MUSIC, "media_play") == 0
    assert _count(home, MUSIC, "play_media") == n_play + 1


async def test_night_mode_off_sets_no_room_volume_on_the_wake_player(hass, home, freezer):
    await _room(hass, home, volume=0.5)
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.wohnzimmer_night_mode"}, blocking=True)
    await hass.async_block_till_done()
    before = len(_sent(home, MUSIC))
    await _wake(hass, start_volume=0.05, target_volume=0.4, fade_minutes=2, night_mode=False)
    assert _sent(home, MUSIC)[before:] == [0.05]


async def test_snooze_while_starting(hass, home, freezer):
    await _room(hass, home)
    await _wake(hass)
    assert hass.states.get(WAKE).state == "starting"
    await hass.services.async_call(DOMAIN, "snooze", {"entity_id": ROOM, "minutes": 2}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(WAKE).state == "snoozed"
    n_play = _count(home, MUSIC, "play_media")
    await _tick(hass, freezer, 125)
    assert _count(home, MUSIC, "play_media") == n_play + 1  # started from the beginning


async def test_idle_between_two_items_is_no_stop(hass, home, freezer):
    finished = async_capture_events(hass, EVENT_WAKE_FINISHED)
    await _room(hass, home)
    await _wake_playing(hass, home, freezer)
    await _tick(hass, freezer, 10)
    home.music("idle")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 2, step=1)
    home.music("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 130)
    assert finished == []
    assert _vol(hass) == 0.4  # the fade went on, no room volume


async def test_new_wake_during_a_slow_start(hass, home, freezer):
    failed = async_capture_events(hass, EVENT_WAKE_FAILED)
    finished = async_capture_events(hass, EVENT_WAKE_FINISHED)
    await _room(hass, home)
    await _wake(hass, source="video")  # Kodi does not boot: the start waits
    assert hass.states.get(WAKE).state == "starting"
    n = _count(home, MUSIC, "play_media")
    await _wake(hass, source="audio")
    await _tick(hass, freezer, 5)
    assert _count(home, MUSIC, "play_media") == n + 1
    home.music("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 200, step=10)
    assert failed == []
    assert [e.data["reason"] for e in finished] == ["replaced"]
    assert hass.states.get(WAKE).state in ("fading", "playing")


async def test_pending_sleep_restore_does_not_hit_the_wake(hass, home, freezer):
    entry = await _room(hass, home)
    ctrl = entry.runtime_data
    home.kodi("off", volume_level=0.02)
    await hass.async_block_till_done()
    ctrl.data["volume_restore"] = {"media_player.kodi": {"volume": 0.5, "left": 0.02}}
    home.kodi_boots = True
    await _wake(hass, source="video", start_volume=0.1, media_content_type="movie")
    assert _sent(home, "media_player.kodi") == [0.1]


async def test_restart_after_play_request_does_not_restart_the_stream(hass, home, freezer):
    from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
    from homeassistant.core import CoreState

    entry = await _room(hass, home)
    await _wake(hass)
    n = _count(home, MUSIC, "play_media")
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    home.music("playing")  # it got going while HA restarted
    hass.set_state(CoreState.starting)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await _settle(hass)
    assert _count(home, MUSIC, "play_media") == n
    assert hass.states.get(WAKE).state == "fading"


async def test_night_mode_off_during_the_wake_raises_the_target(hass, home, freezer):
    await _room(hass, home)
    await _wake_playing(hass, home, freezer, night_mode=True)
    await _tick(hass, freezer, 130)
    assert _vol(hass) == 0.3  # limited
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.wohnzimmer_night_mode"}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 130)
    assert _vol(hass) == 0.4


async def test_controls_shown_by_the_user_stay_shown(hass, home, freezer):
    from homeassistant.helpers import entity_registry as er

    from .test_wake import SNOOZE

    entry = await _room(hass, home)
    reg = er.async_get(hass)
    assert reg.async_get(SNOOZE).hidden_by == er.RegistryEntryHider.INTEGRATION
    reg.async_update_entity(SNOOZE, hidden_by=None)  # the user shows it again
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await _wake_playing(hass, home, freezer)
    await hass.services.async_call(DOMAIN, "stop_wake", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert reg.async_get(SNOOZE).hidden_by is None
