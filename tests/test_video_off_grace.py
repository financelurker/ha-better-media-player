"""A video player that reports off by itself gets a moment (video_off_grace): Home
Assistant's Kodi reports off when a ping fails and its state again once it has
reconnected. Its devices stay on meanwhile, instead of going off and on again."""

from __future__ import annotations

from .conftest import KODI, SPEAKERS, TV, setup_room
from .helpers import ROOM, set_setting, tick


def _switched(home, since: int) -> list:
    return [c for c in home.calls[since:] if c[1] in ("turn_on", "turn_off") and c[2] != KODI]


async def _playing(hass, home, freezer):
    entry = await setup_room(hass)
    home.kodi("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert home.on(TV) and home.on(SPEAKERS)
    return entry


async def _gone(hass, home, freezer, seconds: float, back: str = "playing") -> None:
    home.kodi("off")
    await hass.async_block_till_done()
    await tick(hass, freezer, seconds)
    home.kodi(back)
    await hass.async_block_till_done()


async def test_a_lost_connection_switches_nothing(hass, home, freezer):
    await _playing(hass, home, freezer)
    since = len(home.calls)
    for _ in range(3):
        await _gone(hass, home, freezer, 10)
        await tick(hass, freezer, 30)
    assert _switched(home, since) == [], "no off and on again"
    assert hass.states.get(ROOM).state == "playing"


async def test_paused_it_keeps_its_idle_timeout(hass, home, freezer):
    entry = await _playing(hass, home, freezer)
    home.kodi("paused")
    await hass.async_block_till_done()
    await tick(hass, freezer, 60)
    held = entry.runtime_data.sources["video"].hold_since
    await _gone(hass, home, freezer, 10, back="paused")
    await tick(hass, freezer, 5)
    assert home.on(TV) and home.on(SPEAKERS)
    assert entry.runtime_data.sources["video"].hold_since == held, "the idle timeout counts on from the pause"


async def test_off_for_good_goes_off_after_the_moment(hass, home, freezer):
    await _playing(hass, home, freezer)
    home.kodi("off")
    await hass.async_block_till_done()
    await tick(hass, freezer, 25)
    assert home.on(TV) and home.on(SPEAKERS)
    await tick(hass, freezer, 10)
    assert not home.on(TV) and not home.on(SPEAKERS)


async def test_the_moment_is_the_setting(hass, home, freezer):
    await _playing(hass, home, freezer)
    await set_setting(hass, "video_off_grace", 16)
    home.kodi("off")
    await hass.async_block_till_done()
    await tick(hass, freezer, 15, step=0.5)
    assert home.on(TV) and home.on(SPEAKERS)
    await tick(hass, freezer, 1.5, step=0.5)
    assert not home.on(TV) and not home.on(SPEAKERS), "on time (not only with the next periodic look)"


async def test_zero_is_off_at_once(hass, home, freezer):
    await _playing(hass, home, freezer)
    await set_setting(hass, "video_off_grace", 0)
    home.kodi("off")
    await hass.async_block_till_done()
    await tick(hass, freezer, 2)
    assert not home.on(TV) and not home.on(SPEAKERS)


async def test_turned_off_by_the_room_goes_at_once(hass, home, freezer):
    await _playing(hass, home, freezer)
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert ("media_player", "turn_off", KODI) in home.calls
    assert not home.on(TV) and not home.on(SPEAKERS)


async def test_the_idle_timeout_turns_it_off_without_a_moment(hass, home, freezer):
    await setup_room(hass)
    home.kodi("idle")
    await hass.async_block_till_done()
    await tick(hass, freezer, 30 * 60 + 70, step=10)
    assert ("media_player", "turn_off", KODI) in home.calls
    assert not home.on(TV) and not home.on(SPEAKERS)


async def test_after_keep_on_a_lost_connection_switches_nothing(hass, home, freezer):
    await _playing(hass, home, freezer)
    keep = "switch.living_room_keep_on"
    await hass.services.async_call("switch", "turn_on", {"entity_id": keep}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 30)
    await hass.services.async_call("switch", "turn_off", {"entity_id": keep}, blocking=True)
    await hass.async_block_till_done()
    since = len(home.calls)
    await _gone(hass, home, freezer, 10, back="paused")
    await tick(hass, freezer, 60)
    assert _switched(home, since) == []


async def test_off_then_gone_is_off(hass, home, freezer):
    """Shut down: off, then unavailable (its network is gone): no dropout, off after the moment."""
    await _playing(hass, home, freezer)
    home.kodi("off")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    home.kodi("unavailable")
    await hass.async_block_till_done()
    await tick(hass, freezer, 15)
    assert home.on(TV) and home.on(SPEAKERS), "still its moment"
    await tick(hass, freezer, 15)
    assert not home.on(TV) and not home.on(SPEAKERS)


async def test_off_then_gone_and_back_switches_nothing(hass, home, freezer):
    await _playing(hass, home, freezer)
    since = len(home.calls)
    home.kodi("off")
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    home.kodi("unavailable")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    home.kodi("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 30)
    assert _switched(home, since) == []


async def test_standby_counts_as_off(hass, home, freezer):
    await _playing(hass, home, freezer)
    since = len(home.calls)
    await _gone(hass, home, freezer, 10)
    home.kodi("standby")
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    home.kodi("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 30)
    assert _switched(home, since) == []


async def test_a_shorter_setting_counts_at_once(hass, home, freezer):
    await _playing(hass, home, freezer)
    await set_setting(hass, "video_off_grace", 300)
    home.kodi("off")
    await hass.async_block_till_done()
    await tick(hass, freezer, 10, step=0.5)
    await set_setting(hass, "video_off_grace", 16)
    await tick(hass, freezer, 5, step=0.5)
    assert home.on(TV)
    await tick(hass, freezer, 1.5, step=0.5)
    assert not home.on(TV) and not home.on(SPEAKERS)


async def test_back_during_an_announcement_starts_anew(hass, home, freezer):
    """What the player reports while an announcement protects it still counts for the moment."""
    entry = await _playing(hass, home, freezer)
    ctrl = entry.runtime_data
    src = ctrl.sources["video"]
    home.kodi("off")
    await hass.async_block_till_done()
    protects = ctrl.announce.protects
    ctrl.announce.protects = lambda entity_id: entity_id == KODI
    home.kodi("playing")
    await hass.async_block_till_done()
    ctrl.announce.protects = protects
    assert src.off_since is None, "back: no old time for the next drop"


async def test_all_off_while_it_is_gone_goes_at_once(hass, home, freezer):
    await _playing(hass, home, freezer)
    home.kodi("off")
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert home.on(TV)
    await hass.services.async_call("media_player", "turn_off", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert not home.on(TV) and not home.on(SPEAKERS)
