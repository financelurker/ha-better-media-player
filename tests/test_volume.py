"""Room volume: one volume per room, taken along between the players."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.components.media_player import MediaPlayerEntityFeature as F
from homeassistant.data_entry_flow import FlowResultType

from custom_components.better_media_player.const import DOMAIN

from .conftest import FOLLOW, GROUP, KODI, LIVING_OCC, MA_FEATURES, MUSIC, start_follow
from .helpers import ROOM, music_at, room_volume, tick, vol, volume_sets


async def test_first_start_learns_room_volume(hass, home, freezer):
    await music_at(hass, home, freezer, 0.3)
    assert room_volume(hass) == 0.3
    assert volume_sets(home, MUSIC) == []  # nothing to take along yet


async def test_change_on_player_is_learned(hass, home, freezer):
    await music_at(hass, home, freezer, 0.3)
    home.music("playing", volume_level=0.22)  # e.g. Music Assistant app
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)  # learned after a short moment (group check)
    assert room_volume(hass) == 0.22


async def test_change_on_room_player_is_learned(hass, home, freezer):
    await music_at(hass, home, freezer, 0.3)
    await hass.services.async_call(
        "media_player", "volume_set", {"entity_id": ROOM, "volume_level": 0.4}, blocking=True
    )
    await hass.async_block_till_done()
    assert vol(hass) == 0.4
    assert room_volume(hass) == 0.4


async def test_switch_to_video_takes_room_volume(hass, home, freezer):
    await music_at(hass, home, freezer, 0.25)
    home.kodi("off", volume_level=0.6)  # Kodi still has yesterday's volume
    home.kodi_boots = True
    await hass.services.async_call(
        "media_player", "select_source", {"entity_id": ROOM, "source": "Kodi"}, blocking=True
    )
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert vol(hass, KODI) == 0.25
    assert room_volume(hass) == 0.25  # our own set is not a new wish


async def test_cast_to_kodi_takes_room_volume(hass, home, freezer):
    await music_at(hass, home, freezer, 0.25)
    home.music("idle")
    home.kodi("playing", volume_level=0.6)  # started on Kodi itself
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert vol(hass, KODI) == 0.25
    assert volume_sets(home, KODI) == [0.25]  # once


async def test_own_play_now_order(hass, home, freezer):
    """From the room browser: volume first, then play."""
    entry = await music_at(hass, home, freezer, 0.25)
    home.music("idle", volume_level=0.5)
    await hass.async_block_till_done()
    entry.runtime_data.data["room_volume"] = 0.2
    await entry.runtime_data.async_play_on("audio", {"media_content_type": "track", "media_content_id": "x"})
    await hass.async_block_till_done()
    order = [s for s, e, _ in home.played if e == MUSIC and s in ("volume_set", "play_media")]
    assert order[-2:] == ["volume_set", "play_media"]
    assert vol(hass) == 0.2


async def test_sleep_fade_is_not_learned(hass, home, freezer):
    await music_at(hass, home, freezer, 0.5)
    await hass.services.async_call(DOMAIN, "set_sleep_timer", {"entity_id": ROOM, "minutes": 1, "fade_minutes": 1}, blocking=True)
    await tick(hass, freezer, 40)
    assert vol(hass) < 0.5
    assert room_volume(hass) == 0.5


async def test_group_playback_neither_learned_nor_applied(hass, home, freezer):
    await music_at(hass, home, freezer, 0.3)
    home.music("idle")
    await hass.async_block_till_done()
    home.group("playing", [MUSIC], GROUP)  # started from somewhere else
    home.music("playing", volume_level=0.8)  # the group sets the member's volume
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert vol(hass) == 0.8
    assert room_volume(hass) == 0.3
    assert volume_sets(home, MUSIC) == []

    # leaves the group, plays on its own again: back to the room's volume
    home.group("idle", [], GROUP)
    home.music("idle")
    await hass.async_block_till_done()
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert vol(hass) == 0.3


async def test_follow_join_volume_is_not_learned(hass, house, freezer):
    house.music("playing", volume_level=0.3)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    house.music("idle")
    await hass.async_block_till_done()
    assert room_volume(hass) == 0.3
    hass.states.async_set(LIVING_OCC, "on")
    await hass.async_block_till_done()
    await start_follow(hass, house)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert MUSIC in house.members(FOLLOW)
    assert vol(hass) == 0.5  # 60 % - 10 offset
    assert room_volume(hass) == 0.3


async def test_room_volume_survives_reload(hass, home, freezer):
    entry = await music_at(hass, home, freezer, 0.33)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert room_volume(hass) == 0.33


async def test_player_without_volume_gets_a_note(hass, home):
    home.set("media_player.radio", "idle", supported_features=int(F.PLAY | F.PAUSE), friendly_name="Radio")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "room"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"name": "Bathroom"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"audio_player": "media_player.radio", "special": {}}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"actions": {}, "standby": {}, "follow": {}})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "volume_note"
    assert result["description_placeholders"] == {"players": "Radio"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_player_with_volume_gets_no_note(hass, home):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "room"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"name": "Good"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"audio_player": MUSIC, "special": {}})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"actions": {}, "standby": {}, "follow": {}})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_member_reports_before_group_lists_it(hass, home, freezer):
    """MA: member playing + group volume before the group lists it -> must not be learned."""
    await music_at(hass, home, freezer, 0.3)
    home.music("idle")
    await hass.async_block_till_done()
    home.music("playing", volume_level=0.8)  # member first
    await hass.async_block_till_done()
    home.group("playing", [MUSIC], GROUP)  # group lists it a moment later
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert room_volume(hass) == 0.3


async def test_idle_group_member_volume(hass, home, freezer):
    """Member still joined to an idle group (e.g. joined, group not started yet)."""
    await music_at(hass, home, freezer, 0.3)
    home.music("idle")
    await hass.async_block_till_done()
    home.group("idle", [MUSIC], GROUP)
    await hass.async_block_till_done()
    home.music("idle", volume_level=0.8)  # group volume changed in the MA app / group start
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert room_volume(hass) == 0.3


async def test_user_back_to_applied_value_within_30s(hass, home, freezer):
    """Applied 0.25 on Kodi; user presses down (0.20) then up (0.25) on the remote."""
    await music_at(hass, home, freezer, 0.25)
    home.music("idle")
    home.kodi("playing", volume_level=0.6)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert vol(hass, KODI) == 0.25
    home.kodi("playing", volume_level=0.2)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert room_volume(hass) == 0.2
    home.kodi("playing", volume_level=0.25)  # user goes back up, < 30 s after our set
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert room_volume(hass) == 0.25, "user's change ignored as 'own'"


async def test_kodi_wake_restore_and_room_volume_double_set(hass, home, freezer):
    """Sleep ends on Kodi (suspends), room volume changes later, Kodi wakes: one set expected."""
    await setup_room_kodi_playing(hass, home, freezer, 0.5)
    await hass.services.async_call(
        DOMAIN, "set_sleep_timer", {"entity_id": ROOM, "minutes": 1, "fade_minutes": 1}, blocking=True
    )
    await tick(hass, freezer, 75)
    assert hass.states.get(KODI).state == "off"
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert KODI in entry.runtime_data.data.get("volume_restore", {})
    # music later, user changes it -> room volume 0.35
    home.music("playing", volume_level=0.5)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    home.music("playing", volume_level=0.35)
    await hass.async_block_till_done()
    await tick(hass, freezer, 40)
    assert room_volume(hass) == 0.35
    home.music("idle")
    await hass.async_block_till_done()
    left = entry.runtime_data.data["volume_restore"][KODI]["left"]
    home.kodi("off", volume_level=None)  # real HA: no volume attribute while off
    await hass.async_block_till_done()
    home.played.clear()
    home.kodi("idle", volume_level=left)  # Kodi wakes (CEC / remote), at what the fade left
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert vol(hass, KODI) == 0.35
    assert volume_sets(home, KODI) == [0.35], volume_sets(home, KODI)


async def setup_room_kodi_playing(hass, home, freezer, volume):
    from .conftest import setup_room

    home.kodi_suspends = True
    entry = await setup_room(hass)
    home.kodi("playing", volume_level=volume)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert room_volume(hass) == volume
    return entry


async def test_player_without_volume_set_is_learned_from(hass, home, freezer):
    """A player without VOLUME_SET takes no part, but its reported volume is learned."""
    from .conftest import setup_room

    await setup_room(hass)
    # set via the conftest helper would reset features: write the state directly
    feats = int(MA_FEATURES & ~F.VOLUME_SET)
    hass.states.async_set(MUSIC, "playing", {"supported_features": feats, "volume_level": 0.3})
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    hass.states.async_set(MUSIC, "playing", {"supported_features": feats, "volume_level": 0.9})
    await hass.async_block_till_done()
    assert room_volume(hass) is None, f"learned {room_volume(hass)} from a player that cannot set volume"


async def test_play_on_after_unjoin_with_stale_group(hass, home, freezer):
    """MA reports the unjoin after the service returns: async_apply sees the group and skips."""
    entry = await music_at(hass, home, freezer, 0.3)
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
    await music_at(hass, home, freezer, 0.3)
    home.group("playing", [MUSIC], GROUP)
    await hass.async_block_till_done()
    await hass.services.async_call(
        "media_player", "volume_set", {"entity_id": ROOM, "volume_level": 0.7}, blocking=True
    )
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert room_volume(hass) == 0.3
