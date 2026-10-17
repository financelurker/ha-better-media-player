"""The room media player entity."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from homeassistant.components.media_player import (
    ATTR_MEDIA_ENQUEUE,
    ATTR_MEDIA_EXTRA,
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    RepeatMode,
)
from homeassistant.const import ATTR_ENTITY_PICTURE, ATTR_FRIENDLY_NAME, ATTR_SUPPORTED_FEATURES
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import BetterMediaPlayerConfigEntry
from .const import SOURCE_AUDIO, SOURCE_VIDEO
from .entity import BetterMediaPlayerEntity

BASE_FEATURES = MediaPlayerEntityFeature.TURN_ON | MediaPlayerEntityFeature.TURN_OFF

# Features of the active source that are passed through
PASSTHROUGH = (
    MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.SEEK
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.PLAY_MEDIA
    | MediaPlayerEntityFeature.VOLUME_STEP
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.SHUFFLE_SET
    | MediaPlayerEntityFeature.REPEAT_SET
    | MediaPlayerEntityFeature.MEDIA_ENQUEUE
)

STATE_MAP = {
    MediaPlayerState.PLAYING: MediaPlayerState.PLAYING,
    MediaPlayerState.PAUSED: MediaPlayerState.PAUSED,
    MediaPlayerState.BUFFERING: MediaPlayerState.BUFFERING,
    MediaPlayerState.IDLE: MediaPlayerState.IDLE,
}

DEFAULT_SOURCE_NAMES = {SOURCE_VIDEO: "Video", SOURCE_AUDIO: "Music"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterMediaPlayerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([RoomMediaPlayer(entry, entry.runtime_data)])


class RoomMediaPlayer(BetterMediaPlayerEntity, MediaPlayerEntity):
    """Room player: power sequence + controls of the active source."""

    _attr_name = None
    _attr_device_class = MediaPlayerDeviceClass.RECEIVER

    def __init__(self, entry, controller) -> None:
        super().__init__(entry, controller, None)

    # ----------------------------------------------------------------- helpers

    def _child(self) -> State | None:
        src = self.ctrl.active_source
        if src is None:
            return None
        return self.hass.states.get(src.player)

    def _child_live(self) -> State | None:
        """Active child, only while it is on (for media info)."""
        child = self._child()
        if child is None or child.state not in (*STATE_MAP, MediaPlayerState.ON):
            return None
        return child

    def _attr(self, name: str) -> Any:
        child = self._child_live()
        return child.attributes.get(name) if child is not None else None

    def _source_name(self, key: str) -> str:
        src = self.ctrl.sources[key]
        state = self.hass.states.get(src.player)
        if state is not None and state.attributes.get(ATTR_FRIENDLY_NAME):
            return str(state.attributes[ATTR_FRIENDLY_NAME])
        return DEFAULT_SOURCE_NAMES[key]

    # ------------------------------------------------------------------- state

    @property
    def state(self) -> MediaPlayerState:
        if not self.ctrl.room_on:
            return MediaPlayerState.OFF
        child = self._child()
        if child is not None and child.state in STATE_MAP:
            src = self.ctrl.active_source
            if src is not None and not src.suppressed:
                return STATE_MAP[child.state]
        return MediaPlayerState.ON

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        features = BASE_FEATURES
        if len(self.ctrl.sources) > 1:
            features |= MediaPlayerEntityFeature.SELECT_SOURCE
        child = self._child_live()
        if child is not None:
            child_features = int(child.attributes.get(ATTR_SUPPORTED_FEATURES, 0) or 0)
            features |= MediaPlayerEntityFeature(child_features) & PASSTHROUGH
        return features

    @property
    def source_list(self) -> list[str] | None:
        if len(self.ctrl.sources) < 2:
            return None
        return [self._source_name(k) for k in self.ctrl.sources]

    @property
    def source(self) -> str | None:
        src = self.ctrl.active_source
        return self._source_name(src.key) if src is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        src = self.ctrl.active_source
        return {
            "active_player": src.player if src else None,
            "room_status": self.ctrl.status,
        }

    # -------------------------------------------------------------- media info

    @property
    def volume_level(self) -> float | None:
        value = self._attr("volume_level")
        return float(value) if value is not None else None

    @property
    def is_volume_muted(self) -> bool | None:
        return self._attr("is_volume_muted")

    @property
    def media_content_id(self) -> str | None:
        return self._attr("media_content_id")

    @property
    def media_content_type(self) -> MediaType | str | None:
        return self._attr("media_content_type")

    @property
    def media_duration(self) -> int | None:
        return self._attr("media_duration")

    @property
    def media_position(self) -> int | None:
        return self._attr("media_position")

    @property
    def media_position_updated_at(self):
        value = self._attr("media_position_updated_at")
        if isinstance(value, str):
            return dt_util.parse_datetime(value)
        return value

    @property
    def media_title(self) -> str | None:
        return self._attr("media_title")

    @property
    def media_artist(self) -> str | None:
        return self._attr("media_artist")

    @property
    def media_album_name(self) -> str | None:
        return self._attr("media_album_name")

    @property
    def media_album_artist(self) -> str | None:
        return self._attr("media_album_artist")

    @property
    def media_series_title(self) -> str | None:
        return self._attr("media_series_title")

    @property
    def media_season(self) -> str | None:
        return self._attr("media_season")

    @property
    def media_episode(self) -> str | None:
        return self._attr("media_episode")

    @property
    def app_name(self) -> str | None:
        return self._attr("app_name")

    @property
    def shuffle(self) -> bool | None:
        return self._attr("shuffle")

    @property
    def repeat(self) -> RepeatMode | str | None:
        return self._attr("repeat")

    @property
    def media_image_url(self) -> str | None:
        return self._attr(ATTR_ENTITY_PICTURE)

    @property
    def entity_picture(self) -> str | None:
        url = self.media_image_url
        if not url or self.state == MediaPlayerState.OFF:
            return None
        if urlparse(url).hostname is None:
            # already served by Home Assistant (e.g. Kodi's /api/media_player_proxy/...)
            return url
        # absolute URL (e.g. Music Assistant's own image server, often plain http):
        # serve it through our own HA image proxy so it also works via https / remotely
        return self.media_image_local

    # ---------------------------------------------------------------- commands

    async def async_turn_on(self) -> None:
        await self.ctrl.async_turn_on()

    async def async_turn_off(self) -> None:
        await self.ctrl.async_turn_off()

    async def async_select_source(self, source: str) -> None:
        for key in self.ctrl.sources:
            if self._source_name(key) == source or key == source:
                await self.ctrl.async_select_source(key)
                return

    async def async_media_play(self) -> None:
        await self.ctrl.async_media_call("media_play")

    async def async_media_pause(self) -> None:
        await self.ctrl.async_media_call("media_pause")

    async def async_media_stop(self) -> None:
        await self.ctrl.async_media_call("media_stop")

    async def async_media_next_track(self) -> None:
        await self.ctrl.async_media_call("media_next_track")

    async def async_media_previous_track(self) -> None:
        await self.ctrl.async_media_call("media_previous_track")

    async def async_media_seek(self, position: float) -> None:
        await self.ctrl.async_media_call("media_seek", {"seek_position": position})

    async def async_set_volume_level(self, volume: float) -> None:
        await self.ctrl.async_media_call("volume_set", {"volume_level": volume})

    async def async_volume_up(self) -> None:
        await self.ctrl.async_media_call("volume_up")

    async def async_volume_down(self) -> None:
        await self.ctrl.async_media_call("volume_down")

    async def async_mute_volume(self, mute: bool) -> None:
        await self.ctrl.async_media_call("volume_mute", {"is_volume_muted": mute})

    async def async_set_shuffle(self, shuffle: bool) -> None:
        await self.ctrl.async_media_call("shuffle_set", {"shuffle": shuffle})

    async def async_set_repeat(self, repeat: RepeatMode) -> None:
        await self.ctrl.async_media_call("repeat_set", {"repeat": repeat})

    async def async_play_media(self, media_type: MediaType | str, media_id: str, **kwargs: Any) -> None:
        data: dict[str, Any] = {"media_content_type": media_type, "media_content_id": media_id}
        if kwargs.get(ATTR_MEDIA_ENQUEUE) is not None:
            data["enqueue"] = kwargs[ATTR_MEDIA_ENQUEUE]
        if kwargs.get(ATTR_MEDIA_EXTRA):
            data["extra"] = kwargs[ATTR_MEDIA_EXTRA]
        await self.ctrl.async_media_call("play_media", data)
