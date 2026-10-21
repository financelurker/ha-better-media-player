"""Preset fade: the volume fades to a preset instead of jumping there."""

from __future__ import annotations

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event

from custom_components.better_media_player.const import CONF_VOLUME_CONTROL, CONF_VOLUME_ENTITY, VOLUME_DEVICE
from custom_components.better_media_player.ramp import ease_in_out

from .conftest import AVR, GROUP, KODI, MUSIC, setup_room
from .test_volume_presets import NIGHT, ROOM, SELECT, _music_at, _room_volume, _select, _tick, _vol, _wake

FADE = "number.wohnzimmer_preset_fade"


def _track(hass: HomeAssistant, entity_id: str = MUSIC) -> list[float]:
    """The volumes entity_id reports from now on."""
    seen: list[float] = []

    @callback
    def _changed(event) -> None:
        new, old = event.data["new_state"], event.data["old_state"]
        level = new.attributes.get("volume_level") if new else None
        if level is not None and (old is None or old.attributes.get("volume_level") != level):
            seen.append(level)

    async_track_state_change_event(hass, [entity_id], _changed)
    return seen


async def _number(hass: HomeAssistant, entity_id: str, value: float) -> None:
    await hass.services.async_call("number", "set_value", {"entity_id": entity_id, "value": value}, blocking=True)
    await hass.async_block_till_done()


async def _preset(hass: HomeAssistant, preset: str, **data) -> None:
    await hass.services.async_call(
        "better_media_player", "set_volume_preset", {"entity_id": ROOM, "preset": preset, **data}, blocking=True
    )
    await hass.async_block_till_done()


def test_the_curve_is_slow_at_both_ends():
    assert ease_in_out(0) == 0 and abs(ease_in_out(1) - 1) < 1e-9
    assert abs(ease_in_out(0.5) - 0.5) < 1e-9
    assert ease_in_out(0.1) < 0.1 and ease_in_out(0.9) > 0.9


async def test_a_preset_fades_there(hass, home, freezer):
    await _music_at(hass, home, freezer, volume=0.2)
    assert float(hass.states.get(FADE).state) == 3
    steps = _track(hass)
    await _select(hass, "loud")
    assert hass.states.get(SELECT).state == "loud", "the select shows the preset at once"
    assert _room_volume(hass) == 0.5
    assert _vol(hass) == 0.2, "not a jump"
    await _tick(hass, freezer, 1.5, 0.5)
    middle = _vol(hass)
    assert 0.3 < middle < 0.4, "halfway in time: halfway in volume"
    await _tick(hass, freezer, 2, 0.5)
    assert _vol(hass) == 0.5
    assert len(steps) >= 5, "fine steps"
    assert steps == sorted(steps)
    assert steps[1] - steps[0] > steps[0] - 0.2, "slow at the start"
    assert hass.states.get(SELECT).state == "loud", "the own steps are not learned"


async def test_down_as_well(hass, home, freezer):
    await _music_at(hass, home, freezer, volume=0.6)
    await _select(hass, "quiet")
    await _tick(hass, freezer, 4, 0.5)
    assert _vol(hass) == 0.15
    assert hass.states.get(SELECT).state == "quiet"


async def test_zero_jumps(hass, home, freezer):
    await _music_at(hass, home, freezer, volume=0.2)
    await _number(hass, FADE, 0)
    await _select(hass, "loud")
    assert _vol(hass) == 0.5


async def test_the_action_sets_its_own_fade(hass, home, freezer):
    await _music_at(hass, home, freezer, volume=0.2)
    await _preset(hass, "loud", fade_seconds=0)
    assert _vol(hass) == 0.5, "0: at once"
    await _preset(hass, "quiet", fade_seconds=10)
    await _tick(hass, freezer, 4, 0.5)
    assert 0.15 < _vol(hass) < 0.5, "still on its way after 4 s"
    await _tick(hass, freezer, 7, 0.5)
    assert _vol(hass) == 0.15


async def test_a_change_during_the_fade_stops_it(hass, home, freezer):
    await _music_at(hass, home, freezer, volume=0.2)
    await _select(hass, "party")
    await _tick(hass, freezer, 1, 0.5)
    home.music("playing", volume_level=0.1)  # turned down by hand
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5, 0.5)
    assert _vol(hass) == 0.1, "the fade stops there"
    assert _room_volume(hass) == 0.1, "and it is the room volume"
    assert hass.states.get(SELECT).state == "manual"


async def test_another_preset_during_the_fade_goes_on_from_there(hass, home, freezer):
    await _music_at(hass, home, freezer, volume=0.2)
    await _select(hass, "party")
    await _tick(hass, freezer, 1.5, 0.5)
    middle = _vol(hass)
    steps = _track(hass)
    await _select(hass, "quiet")
    await _tick(hass, freezer, 0.5, 0.5)
    first = steps[0]
    assert 0.15 < first <= middle, "from where it is, no jump"
    await _tick(hass, freezer, 3, 0.5)
    assert _vol(hass) == 0.15
    assert hass.states.get(SELECT).state == "quiet"


async def test_late_reports_are_no_change(hass, home, freezer):
    await _music_at(hass, home, freezer, volume=0.2)
    home.volume_lag = 1.0  # the player reports each set a second later
    await _select(hass, "party")
    await _tick(hass, freezer, 5, 0.25)
    assert _vol(hass) == 0.7, "the fade goes on"
    assert hass.states.get(SELECT).state == "party"


async def test_another_preset_with_late_reports(hass, home, freezer):
    await _music_at(hass, home, freezer, volume=0.2)
    home.volume_lag = 1.0
    await _select(hass, "party")
    await _tick(hass, freezer, 1.25, 0.25)
    await _select(hass, "quiet")
    await _tick(hass, freezer, 5, 0.25)
    assert _vol(hass) == 0.15, "not stuck on the way"
    assert hass.states.get(SELECT).state == "quiet"


async def test_turned_back_to_a_passed_volume(hass, home, freezer):
    await _music_at(hass, home, freezer, volume=0.2)
    steps = _track(hass)
    await _select(hass, "party")
    await _tick(hass, freezer, 2, 0.5)
    back = steps[1]  # where the fade was a second ago
    home.music("playing", volume_level=back)  # turned back down by hand
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5, 0.5)
    assert _vol(hass) == back, "the fade stops there"
    assert hass.states.get(SELECT).state == "manual"


async def test_the_night_limit_holds(hass, home, freezer):
    await _music_at(hass, home, freezer, volume=0.2)
    await hass.services.async_call("switch", "turn_on", {"entity_id": NIGHT}, blocking=True)
    await hass.async_block_till_done()
    steps = _track(hass)
    await _select(hass, "party")
    await _tick(hass, freezer, 4, 0.5)
    assert steps and max(steps) <= 0.3
    assert _vol(hass) == 0.3
    assert hass.states.get(SELECT).state == "party", "comes back after the night"


async def test_the_night_mode_takes_over_a_fade(hass, home, freezer):
    await _music_at(hass, home, freezer, volume=0.2)
    await _select(hass, "party")
    await _tick(hass, freezer, 2, 0.5)
    await hass.services.async_call("switch", "turn_on", {"entity_id": NIGHT}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3, 0.5)
    assert _vol(hass) == 0.3, "the limit, no fade beyond it"


async def test_night_on_during_a_fade_down_goes_on_below_the_limit(hass, home, freezer):
    await _music_at(hass, home, freezer, volume=0.7)
    await _select(hass, "quiet")
    await _tick(hass, freezer, 0.5, 0.5)
    await hass.services.async_call("switch", "turn_on", {"entity_id": NIGHT}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 4, 0.5)
    assert _vol(hass) == 0.15, "not stuck at the limit"


async def test_night_off_during_a_fade_fades_back(hass, home, freezer):
    await _music_at(hass, home, freezer, volume=0.2)
    await hass.services.async_call("switch", "turn_on", {"entity_id": NIGHT}, blocking=True)
    await hass.async_block_till_done()
    await _select(hass, "party")
    await _tick(hass, freezer, 1, 0.5)
    await hass.services.async_call("switch", "turn_off", {"entity_id": NIGHT}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 150, 5)  # the night's fade back: 2 min
    assert _vol(hass) == 0.7
    assert hass.states.get(SELECT).state == "party"


async def test_a_start_during_the_fade_does_not_jump(hass, home, freezer):
    await _music_at(hass, home, freezer, volume=0.2)
    await _preset(hass, "party", fade_seconds=10)
    await _tick(hass, freezer, 0.5, 0.5)
    home.music("paused", volume_level=_vol(hass))
    await hass.async_block_till_done()
    home.music("playing", volume_level=_vol(hass))  # the next track starts
    await hass.async_block_till_done()
    steps = _track(hass)
    await _tick(hass, freezer, 2.5, 0.5)
    assert steps and steps[-1] < 0.4 and steps == sorted(steps), steps


async def test_a_start_during_a_device_fade_does_not_jump(hass, home, freezer):
    home.avr("on", volume_level=0.2)
    entry = await setup_room(hass, **{CONF_VOLUME_CONTROL: VOLUME_DEVICE, CONF_VOLUME_ENTITY: AVR})
    home.music("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    home.avr("on", volume_level=0.2)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    await _preset(hass, "loud", fade_seconds=10)
    await _tick(hass, freezer, 0.5, 0.5)
    home.music("paused")
    await hass.async_block_till_done()
    home.music("playing")
    await hass.async_block_till_done()
    steps = _track(hass, AVR)
    await _tick(hass, freezer, 2.5, 0.5)
    assert steps and steps[-1] < 0.35 and steps == sorted(steps), steps
    assert entry.runtime_data.volume.fading(AVR)


async def test_a_preset_during_the_nights_fade_back(hass, home, freezer):
    await _music_at(hass, home, freezer, volume=0.6)
    await hass.services.async_call("switch", "turn_on", {"entity_id": NIGHT}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    await hass.services.async_call("switch", "turn_off", {"entity_id": NIGHT}, blocking=True)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 30, 5)
    await _select(hass, "loud")
    await _tick(hass, freezer, 150, 1)
    assert _vol(hass) == 0.5, "the preset, not the night's fade back to 0.6"
    assert hass.states.get(SELECT).state == "loud"


async def test_the_sleep_timer_takes_over(hass, home, freezer):
    entry = await _music_at(hass, home, freezer, volume=0.2)
    await _select(hass, "party")
    await _tick(hass, freezer, 1, 0.5)
    await hass.services.async_call(
        "better_media_player", "set_sleep_timer", {"entity_id": ROOM, "minutes": 1, "fade_minutes": 1}, blocking=True
    )
    await hass.async_block_till_done()
    await _tick(hass, freezer, 20, 1)
    sleep = entry.runtime_data.sleep
    assert sleep.fading_player(MUSIC), "the sleep fade goes on"
    assert not entry.runtime_data.volume.fading(MUSIC)


async def test_a_wake_takes_over(hass, home, freezer):
    entry = await _music_at(hass, home, freezer, volume=0.2)
    await _select(hass, "party")
    await _tick(hass, freezer, 1, 0.5)
    await _wake(hass, start_volume=0.05, target_volume=0.3, fade_minutes=1)
    assert not entry.runtime_data.volume.fading(MUSIC)
    await _tick(hass, freezer, 10, 0.5)
    assert _vol(hass) <= 0.3, "the wake's volume, not the preset's"


async def test_a_wake_that_turns_the_room_on_starts_at_its_volume(hass, home, freezer):
    await setup_room(hass, power_on_preset="normal")
    steps = _track(hass)
    await _wake(hass, start_volume=0.05, fade_minutes=0)
    await _tick(hass, freezer, 4, 0.5)
    assert steps and steps[0] in (0.05, 0.3), steps
    assert not any(0.05 < v < 0.3 for v in steps), "no preset fade from the start volume"


async def test_in_a_group_only_remembered(hass, home, freezer):
    await _music_at(hass, home, freezer, volume=0.4)
    home.group("playing", [MUSIC], GROUP)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    steps = _track(hass)
    await _select(hass, "party")
    await _tick(hass, freezer, 4, 0.5)
    assert steps == []
    assert _room_volume(hass) == 0.7


async def test_a_volume_device_fades(hass, home, freezer):
    home.avr("on", volume_level=0.2)
    await setup_room(hass, **{CONF_VOLUME_CONTROL: VOLUME_DEVICE, CONF_VOLUME_ENTITY: AVR})
    home.music("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    home.avr("on", volume_level=0.2)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    steps = _track(hass, AVR)
    await _select(hass, "loud")
    assert _vol(hass, AVR) == 0.2
    await _tick(hass, freezer, 4, 0.5)
    assert _vol(hass, AVR) == 0.5
    assert len(steps) >= 5
    assert hass.states.get(SELECT).state == "loud"


async def test_the_power_on_preset_fades_from_the_start_volume(hass, home, freezer):
    entry = await setup_room(hass, power_on_preset="quiet")
    entry.runtime_data.data["room_volume"] = 0.7  # last night's party
    home.music("playing", volume_level=0.7)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 2.5, 0.5)  # the room settles
    await _tick(hass, freezer, 1, 0.5)
    assert 0.15 < _vol(hass) < 0.7, "fading"
    await _tick(hass, freezer, 3, 0.5)
    assert _vol(hass) == 0.15
    assert hass.states.get(SELECT).state == "quiet"


async def test_the_power_on_fade_is_once(hass, home, freezer):
    entry = await setup_room(hass, power_on_preset="quiet")
    entry.runtime_data.data["room_volume"] = 0.7
    home.music("playing", volume_level=0.7)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 8, 0.5)
    assert _vol(hass) == 0.15
    # the video player starts later: it gets the room volume at once
    steps = _track(hass, KODI)
    home.kodi("idle", volume_level=0.6)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 2.5, 0.5)
    assert steps[-1] == 0.15 and len(steps) <= 2, steps



async def test_turned_off_during_the_fade(hass, home, freezer):
    entry = await _music_at(hass, home, freezer, volume=0.2)
    await _select(hass, "party")
    await _tick(hass, freezer, 1, 0.5)
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    assert not entry.runtime_data.volume.fading(MUSIC)
    home.music("idle", volume_level=_vol(hass))  # the player keeps its volume
    await hass.async_block_till_done()
    steps = _track(hass)
    await _tick(hass, freezer, 5, 0.5)
    assert steps == []


async def test_the_sleep_timer_takes_over_with_late_reports(hass, home, freezer):
    entry = await _music_at(hass, home, freezer, volume=0.2)
    home.volume_lag = 1.0
    await _select(hass, "party")
    await _tick(hass, freezer, 1.5, 0.25)
    await hass.services.async_call(
        "better_media_player", "set_sleep_timer", {"entity_id": ROOM, "minutes": 1, "fade_minutes": 1}, blocking=True
    )
    await hass.async_block_till_done()
    await _tick(hass, freezer, 20, 0.5)
    assert entry.runtime_data.sleep.fading_player(MUSIC), "a late preset step does not stop the sleep fade"


async def test_a_wake_leaves_no_power_on_fade_behind(hass, home, freezer):
    await setup_room(hass, power_on_preset="normal")
    await _wake(hass, start_volume=0.05, fade_minutes=0)
    await _tick(hass, freezer, 10, 1)
    steps = _track(hass, KODI)
    home.kodi("idle", volume_level=0.9)  # the room is on: the video player comes up
    await hass.async_block_till_done()
    await _tick(hass, freezer, 4, 0.5)
    assert steps and not any(v not in (0.9, steps[-1]) for v in steps), f"at once, no fade: {steps}"


async def test_a_power_on_in_a_group_leaves_no_fade_behind(hass, home, freezer):
    entry = await setup_room(hass, power_on_preset="normal")
    entry.runtime_data.data["last_source"] = "audio"
    home.group("playing", [MUSIC], GROUP)
    home.music("playing", volume_level=0.4)  # the room comes on as a group member
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5, 0.5)
    assert _vol(hass) == 0.4, "the group decides"
    home.music("playing", volume_level=0.6)  # the group's volume
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3, 0.5)
    home.music("idle")
    home.group("idle", [], GROUP)  # later: on its own
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3, 0.5)
    steps = _track(hass)
    home.music("playing")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 4, 0.5)
    assert steps == [0.3], f"at once: {steps}"
