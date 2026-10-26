"""A Kodi player that is off, idle (its menu) or plays a TV show."""

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature as F,
    MediaPlayerState,
    MediaType,
)

from homeassistant.util import dt as dt_util

from . import PLAYERS


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    players = [StandInKodi(p["name"], p["unique_id"]) for p in config["players"]]
    async_add_entities(players)


class StandInKodi(MediaPlayerEntity):
    _attr_supported_features = (
        F.TURN_ON | F.TURN_OFF | F.PLAY | F.PAUSE | F.STOP | F.SEEK | F.VOLUME_SET | F.VOLUME_STEP
        | F.VOLUME_MUTE | F.PLAY_MEDIA | F.NEXT_TRACK | F.PREVIOUS_TRACK
    )
    _attr_media_content_type = MediaType.TVSHOW
    _attr_should_poll = False

    def __init__(self, name: str, unique_id: str) -> None:
        self._attr_name = name
        self._attr_unique_id = unique_id
        self._attr_state = MediaPlayerState.OFF
        self._attr_volume_level = 0.4
        self._attr_is_volume_muted = False
        self._show(None)

    async def async_added_to_hass(self) -> None:
        PLAYERS[self.entity_id] = self

    def _show(self, title: str | None) -> None:
        self._attr_media_title = title
        self._attr_media_series_title = "The Long Evening" if title else None
        self._attr_media_season = "1" if title else None
        self._attr_media_episode = "3" if title else None
        self._attr_media_duration = 2640 if title else None
        self._attr_media_position = 1210 if title else None
        self._attr_media_position_updated_at = dt_util.utcnow() if title else None

    def take_over(self, other: "StandInKodi") -> None:
        self._attr_state = other.state
        self._show(other.media_title)
        self._attr_media_position_updated_at = other.media_position_updated_at
        self.async_write_ha_state()

    async def async_turn_on(self) -> None:
        self._attr_state = MediaPlayerState.IDLE
        self.async_write_ha_state()

    async def async_turn_off(self) -> None:
        self._attr_state = MediaPlayerState.OFF
        self._show(None)
        self.async_write_ha_state()

    async def async_play_media(self, media_type, media_id, **kwargs) -> None:
        self._attr_state = MediaPlayerState.PLAYING
        self._show("Lights Out")
        self.async_write_ha_state()

    async def async_media_play(self) -> None:
        if self.state != MediaPlayerState.OFF:
            if self.media_title is None:
                self._show("Lights Out")
            self._attr_state = MediaPlayerState.PLAYING
            self.async_write_ha_state()

    async def async_media_pause(self) -> None:
        if self.state == MediaPlayerState.PLAYING:
            self._attr_state = MediaPlayerState.PAUSED
            self.async_write_ha_state()

    async def async_media_stop(self) -> None:
        if self.state != MediaPlayerState.OFF:
            self._attr_state = MediaPlayerState.IDLE
            self._show(None)
            self.async_write_ha_state()

    async def async_set_volume_level(self, volume: float) -> None:
        self._attr_volume_level = volume
        self.async_write_ha_state()

    async def async_mute_volume(self, mute: bool) -> None:
        self._attr_is_volume_muted = mute
        self.async_write_ha_state()

    async def async_media_seek(self, position: float) -> None:
        self._attr_media_position = position
        self.async_write_ha_state()
