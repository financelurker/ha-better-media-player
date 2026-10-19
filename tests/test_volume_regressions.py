"""Room volume: regressions from the review."""

from __future__ import annotations

from homeassistant.components.media_player import MediaPlayerEntityFeature as F

from custom_components.better_media_player.const import DOMAIN

from .conftest import GROUP, KODI, MUSIC, MA_FEATURES
from .test_volume import ROOM, _music_at, _room_volume, _sets, _tick, _vol


async def test_r1_member_reports_before_group_lists_it(hass, home, freezer):
    """MA: member playing + group volume before the group lists it -> must not be learned."""
    await _music_at(hass, home, freezer, 0.3)
    home.music("idle")
    await hass.async_block_till_done()
    home.music("playing", volume_level=0.8)  # member first
    await hass.async_block_till_done()
    home.group("playing", [MUSIC], GROUP)  # group lists it a moment later
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert _room_volume(hass) == 0.3


async def test_r2_idle_group_member_volume(hass, home, freezer):
    """Member still joined to an idle group (e.g. joined, group not started yet)."""
    await _music_at(hass, home, freezer, 0.3)
    home.music("idle")
    await hass.async_block_till_done()
    home.group("idle", [MUSIC], GROUP)
    await hass.async_block_till_done()
    home.music("idle", volume_level=0.8)  # group volume changed in the MA app / group start
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert _room_volume(hass) == 0.3


async def test_r3_user_back_to_applied_value_within_30s(hass, home, freezer):
    """Applied 0.25 on Kodi; user presses down (0.20) then up (0.25) on the remote."""
    await _music_at(hass, home, freezer, 0.25)
    home.music("idle")
    home.kodi("playing", volume_level=0.6)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert _vol(hass, KODI) == 0.25
    home.kodi("playing", volume_level=0.2)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert _room_volume(hass) == 0.2
    home.kodi("playing", volume_level=0.25)  # user goes back up, < 30 s after our set
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert _room_volume(hass) == 0.25, "user's change ignored as 'own'"


async def test_r4_kodi_wake_restore_and_room_volume_double_set(hass, home, freezer):
    """Sleep ends on Kodi (suspends), room volume changes later, Kodi wakes: one set expected."""
    await setup_room_kodi_playing(hass, home, freezer, 0.5)
    await hass.services.async_call(
        DOMAIN, "set_sleep_timer", {"entity_id": ROOM, "minutes": 1, "fade_minutes": 1}, blocking=True
    )
    await _tick(hass, freezer, 75)
    assert hass.states.get(KODI).state == "off"
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert KODI in entry.runtime_data.data.get("volume_restore", {})
    # music later, user changes it -> room volume 0.35
    home.music("playing", volume_level=0.5)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    home.music("playing", volume_level=0.35)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 40)
    assert _room_volume(hass) == 0.35
    home.music("idle")
    await hass.async_block_till_done()
    left = entry.runtime_data.data["volume_restore"][KODI]["left"]
    home.kodi("off", volume_level=None)  # real HA: no volume attribute while off
    await hass.async_block_till_done()
    home.played.clear()
    home.kodi("idle", volume_level=left)  # Kodi wakes (CEC / remote), at what the fade left
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert _vol(hass, KODI) == 0.35
    assert _sets(home, KODI) == [0.35], _sets(home, KODI)


async def setup_room_kodi_playing(hass, home, freezer, volume):
    from .conftest import setup_room

    home.kodi_suspends = True
    entry = await setup_room(hass)
    home.kodi("playing", volume_level=volume)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert _room_volume(hass) == volume
    return entry


async def test_r5_player_without_volume_set_is_learned_from(hass, home, freezer):
    """A player without VOLUME_SET takes no part, but its reported volume is learned."""
    from .conftest import setup_room

    await setup_room(hass)
    # set via the conftest helper would reset features: write the state directly
    feats = int(MA_FEATURES & ~F.VOLUME_SET)
    hass.states.async_set(MUSIC, "playing", {"supported_features": feats, "volume_level": 0.3})
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    hass.states.async_set(MUSIC, "playing", {"supported_features": feats, "volume_level": 0.9})
    await hass.async_block_till_done()
    assert _room_volume(hass) is None, f"learned {_room_volume(hass)} from a player that cannot set volume"


async def test_r6_play_on_after_unjoin_with_stale_group(hass, home, freezer):
    """MA reports the unjoin after the service returns: async_apply sees the group and skips."""
    entry = await _music_at(hass, home, freezer, 0.3)
    home.group("playing", [MUSIC], GROUP)
    home.music("playing", volume_level=0.8)
    await hass.async_block_till_done()
    orig = home.handle

    def lazy(domain, service, entity_id, data, context):
        if service == "unjoin":
            home.played.append((service, entity_id, dict(data)))
            return True  # state update comes later
        return orig(domain, service, entity_id, data, context)

    home.handle = lazy
    home.played.clear()
    await entry.runtime_data.async_play_on("audio", {"media_content_type": "track", "media_content_id": "x"})
    await hass.async_block_till_done()
    order = [s for s, e, _ in home.played if e == MUSIC and s in ("volume_set", "play_media")]
    assert order[:2] == ["volume_set", "play_media"], order


async def test_room_player_volume_in_group_is_not_learned(hass, home, freezer):
    await _music_at(hass, home, freezer, 0.3)
    home.group("playing", [MUSIC], GROUP)
    await hass.async_block_till_done()
    await hass.services.async_call(
        "media_player", "volume_set", {"entity_id": ROOM, "volume_level": 0.7}, blocking=True
    )
    await hass.async_block_till_done()
    await _tick(hass, freezer, 3)
    assert _room_volume(hass) == 0.3
