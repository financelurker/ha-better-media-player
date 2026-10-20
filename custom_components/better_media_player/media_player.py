"""The room media player entity."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import voluptuous as vol
from homeassistant.components.media_player import (
    ATTR_MEDIA_ENQUEUE,
    ATTR_MEDIA_EXTRA,
    BrowseMedia,
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    RepeatMode,
    SearchMedia,
    SearchMediaQuery,
)
from homeassistant.const import (
    ATTR_ENTITY_PICTURE,
    ATTR_FRIENDLY_NAME,
    ATTR_SUPPORTED_FEATURES,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import BetterMediaPlayerConfigEntry
from .browser import RoomBrowser, is_own_id
from .const import (
    ATTR_PRESET,
    PRESETS,
    SERVICE_HANDOVER,
    SERVICE_SET_VOLUME_PRESET,
    SERVICE_FOLLOW_START,
    SERVICE_FOLLOW_STOP,
    SERVICE_SNOOZE,
    SERVICE_STOP_WAKE,
    SERVICE_WAKE,
    ATTR_FADE_MINUTES,
    ATTR_NIGHT_MODE,
    DOMAIN,
    ATTR_MINUTES,
    SERVICE_CANCEL_SLEEP_TIMER,
    SERVICE_SET_SLEEP_TIMER,
    SOURCE_AUDIO,
    SOURCE_VIDEO,
)
from .entity import BetterMediaPlayerEntity
from .hub import get_hub, room_of
from .sleep import MAX_MINUTES

BASE_FEATURES = MediaPlayerEntityFeature.TURN_ON | MediaPlayerEntityFeature.TURN_OFF

DEVICE_VOLUME = (
    MediaPlayerEntityFeature.VOLUME_SET | MediaPlayerEntityFeature.VOLUME_MUTE | MediaPlayerEntityFeature.VOLUME_STEP
)

# Features of the active source that are passed through
PASSTHROUGH = (
    MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.SEEK
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.VOLUME_STEP
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.SHUFFLE_SET
    | MediaPlayerEntityFeature.REPEAT_SET
)

# Offered whenever any of the room's players supports them (browser, search,
# playing from them), not only while that player is active
ANY_SOURCE = (
    MediaPlayerEntityFeature.PLAY_MEDIA
    | MediaPlayerEntityFeature.MEDIA_ENQUEUE
    | MediaPlayerEntityFeature.BROWSE_MEDIA
    | MediaPlayerEntityFeature.SEARCH_MEDIA
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
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_SET_SLEEP_TIMER,
        {
            vol.Required(ATTR_MINUTES): vol.All(vol.Coerce(float), vol.Range(min=1, max=MAX_MINUTES)),
            vol.Optional(ATTR_FADE_MINUTES): vol.All(vol.Coerce(float), vol.Range(min=0, max=60)),
        },
        "async_set_sleep_timer",
    )
    platform.async_register_entity_service(SERVICE_CANCEL_SLEEP_TIMER, None, "async_cancel_sleep_timer")
    platform.async_register_entity_service(SERVICE_FOLLOW_START, None, "async_start_follow")
    platform.async_register_entity_service(SERVICE_FOLLOW_STOP, None, "async_stop_follow")
    # a volume 0..1, or a preset by name
    volume = vol.Any(vol.All(vol.Coerce(float), vol.Range(min=0, max=1)), vol.In(PRESETS))
    platform.async_register_entity_service(
        SERVICE_WAKE,
        {
            vol.Required("media_content_id"): cv.string,
            vol.Required("media_content_type"): cv.string,
            vol.Optional("start_volume"): volume,
            vol.Optional("target_volume"): volume,
            vol.Optional("fade_minutes"): vol.All(vol.Coerce(float), vol.Range(min=0, max=120)),
            vol.Optional("max_duration_minutes"): vol.All(vol.Coerce(float), vol.Range(min=1, max=720)),
            vol.Optional(ATTR_NIGHT_MODE): cv.boolean,
            vol.Optional("source"): vol.In([SOURCE_AUDIO, SOURCE_VIDEO]),
            vol.Optional("follow"): cv.boolean,
        },
        "async_wake",
    )
    platform.async_register_entity_service(
        SERVICE_SNOOZE,
        {vol.Optional("minutes"): vol.All(vol.Coerce(float), vol.Range(min=1, max=120))},
        "async_snooze",
    )
    platform.async_register_entity_service(SERVICE_STOP_WAKE, None, "async_stop_wake")
    platform.async_register_entity_service(
        SERVICE_SET_VOLUME_PRESET, {vol.Required(ATTR_PRESET): vol.In(PRESETS)}, "async_set_volume_preset"
    )
    platform.async_register_entity_service(
        SERVICE_HANDOVER,
        {
            vol.Required("destination"): cv.entity_id,
            vol.Optional("source"): vol.In([SOURCE_AUDIO, SOURCE_VIDEO]),
            vol.Optional("keep_source_playing", default=False): cv.boolean,
        },
        "async_handover",
    )


class RoomMediaPlayer(BetterMediaPlayerEntity, MediaPlayerEntity):
    """Room player: power sequence + controls of the active source."""

    _attr_name = None
    _attr_device_class = MediaPlayerDeviceClass.RECEIVER

    def __init__(self, entry, controller) -> None:
        super().__init__(entry, controller, None)
        self._browser = RoomBrowser(controller.hass, controller, self._source_name)

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
        for src in self.ctrl.sources.values():
            state = self.hass.states.get(src.player)
            if state is not None:
                features |= MediaPlayerEntityFeature(
                    int(state.attributes.get(ATTR_SUPPORTED_FEATURES, 0) or 0)
                ) & ANY_SOURCE
        if (device := self.ctrl.volume_device) is not None:
            # the room's volume is the device's, also while nothing plays
            features &= ~DEVICE_VOLUME
            features |= MediaPlayerEntityFeature.VOLUME_SET | MediaPlayerEntityFeature.VOLUME_STEP
            if device.can_mute:
                features |= MediaPlayerEntityFeature.VOLUME_MUTE
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
        ends = self.ctrl.sleep.ends_at
        return {
            "active_player": src.player if src else None,
            "room_status": self.ctrl.status,
            "sleep_timer_ends_at": ends.isoformat() if ends else None,
            "sleep_timer_fading": self.ctrl.sleep.fading,
            "room_volume": self.ctrl.volume.value,
            "volume_preset": self.ctrl.volume.preset,
            "night_mode": self.ctrl.night.active,
            "wake": self.ctrl.wake.phase,
        }

    # -------------------------------------------------------------- media info

    @property
    def volume_level(self) -> float | None:
        if (device := self.ctrl.volume_device) is not None:
            level = device.level()
            return level if level is not None else self.ctrl.volume.value
        value = self._attr("volume_level")
        return float(value) if value is not None else None

    @property
    def is_volume_muted(self) -> bool | None:
        if (device := self.ctrl.volume_device) is not None:
            return device.muted
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
        enqueue = kwargs.get(ATTR_MEDIA_ENQUEUE)
        extra = kwargs.get(ATTR_MEDIA_EXTRA)
        if isinstance(extra, dict) and ATTR_NIGHT_MODE in extra:
            # ours: switches the room's night mode, not passed on to the player
            extra = dict(extra)
            night = extra.pop(ATTR_NIGHT_MODE)
            if not isinstance(night, bool):
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="night_mode_bool"
                )
            kwargs[ATTR_MEDIA_EXTRA] = extra or None
            if is_own_id(media_id):
                self._browser.resolve_play(media_id)  # a bad id fails before anything changes
            await self.ctrl.async_set_night_mode(night)
        if is_own_id(media_id):
            # from the room browser or search: the id names the player
            key, inner_type, inner_id, mode = self._browser.resolve_play(media_id)
            data: dict[str, Any] = {
                "media_content_type": inner_type or media_type,
                "media_content_id": inner_id,
            }
            if mode is not None:
                data["enqueue"] = mode
            elif enqueue is not None:
                data["enqueue"] = enqueue
            if kwargs.get(ATTR_MEDIA_EXTRA):
                data["extra"] = kwargs[ATTR_MEDIA_EXTRA]
            # the player may carry the mode in its own id (e.g. Better Kodi's choices):
            # passed through unchanged, but the room decides by it
            await self.ctrl.async_play_on(
                key, data, room_mode=mode or self._browser.player_mode(key, inner_id)
            )
            return
        data = {"media_content_type": media_type, "media_content_id": media_id}
        if enqueue is not None:
            data["enqueue"] = enqueue
        if kwargs.get(ATTR_MEDIA_EXTRA):
            data["extra"] = kwargs[ATTR_MEDIA_EXTRA]
        await self.ctrl.async_media_call("play_media", data)

    async def async_set_sleep_timer(self, minutes: float, fade_minutes: float | None = None) -> None:
        await self.ctrl.async_set_sleep_timer(minutes, fade_minutes)

    async def async_cancel_sleep_timer(self) -> None:
        await self.ctrl.sleep.async_cancel()

    async def async_wake(
        self,
        media_content_id: str,
        media_content_type: str,
        start_volume: float | str | None = None,
        target_volume: float | str | None = None,
        fade_minutes: float | None = None,
        max_duration_minutes: float | None = None,
        night_mode: bool | None = None,
        source: str | None = None,
        follow: bool = False,
    ) -> None:
        """Wake up in this room; the player comes from the media id (room browser),
        from source, or is the music player (else the video player). Volumes may
        be given as a preset of the room."""
        room = self.ctrl.volume
        if isinstance(start_volume, str):
            start_volume = room.preset_value(start_volume)
        if isinstance(target_volume, str):
            target_volume = room.preset_value(target_volume)
        room_mode = None
        if is_own_id(media_content_id):
            key, inner_type, inner_id, _mode = self._browser.resolve_play(media_content_id)
            data = {"media_content_type": inner_type or media_content_type, "media_content_id": inner_id}
            room_mode = self._browser.player_mode(key, inner_id)
        else:
            key = source or (SOURCE_AUDIO if SOURCE_AUDIO in self.ctrl.sources else SOURCE_VIDEO)
            data = {"media_content_type": media_content_type, "media_content_id": media_content_id}
        if key not in self.ctrl.sources:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_source", translation_placeholders={"source": key}
            )
        await self.ctrl.wake.async_wake(
            key,
            data,
            room_mode,
            start_volume,
            target_volume,
            fade_minutes,
            max_duration_minutes,
            night_mode,
            follow=bool(follow),
        )

    async def async_start_follow(self) -> None:
        """Follow me from this room (its music moves to the follow-me player)."""
        hub = get_hub(self.hass)
        if hub is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_follow_player")
        await hub.async_follow_start(self.ctrl)

    async def async_stop_follow(self) -> None:
        """Follow me off: the music stays in this room if it plays along, else
        goes back where follow me was started."""
        hub = get_hub(self.hass)
        if hub is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="follow_not_running")
        await hub.async_follow_stop(self.ctrl)

    async def async_set_volume_preset(self, preset: str) -> None:
        await self.ctrl.async_set_volume_preset(preset)

    async def async_handover(
        self, destination: str, source: str | None = None, keep_source_playing: bool = False
    ) -> None:
        await self.ctrl.async_handover(room_of(self.hass, destination), source, keep_source_playing, destination)

    async def async_snooze(self, minutes: float | None = None) -> None:
        await self.ctrl.wake.async_snooze(minutes)

    async def async_stop_wake(self) -> None:
        await self.ctrl.wake.async_finish("stopped")

    async def async_browse_media(
        self, media_content_type: MediaType | str | None = None, media_content_id: str | None = None
    ) -> BrowseMedia:
        return await self._browser.async_browse(media_content_type, media_content_id)

    async def async_search_media(self, query: SearchMediaQuery) -> SearchMedia:
        return await self._browser.async_search(query)
