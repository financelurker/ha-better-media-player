"""Announcements: a message or a sound in a room, over what plays there.

Which player announces (the first that fits; a player announces when it
supports Home Assistant's announcements, e.g. Music Assistant's):

1. The player of the active source, if it announces: the announcement takes
   the same way as what plays (same speakers, same input of an amplifier),
   and the player pauses and resumes it itself.
2. Else the other player of the room, if it announces; a film that plays
   meanwhile is paused, overlaid or turned down (setting on the device).
3. The room is off: the music player, else the video player. The room's
   devices (speakers, volume device) come up for it and go off right after.

The announcing player's system does the rest (Music Assistant: chime, its
announcement volume, pausing and resuming the music) and its call returns
once all of that is over. Until then the room takes nothing it sees on its
players for a wish: no volume learned, no fade or night fade back stopped,
no snooze from a pause, no source change.

At night the announcement is at most the night mode's limit. A quiet room is
left out (nothing from outside, see the quiet switch) unless ignore_quiet.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any

from homeassistant.components.media_player import (
    ATTR_MEDIA_ANNOUNCE,
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    ATTR_MEDIA_EXTRA,
    MediaPlayerEntityFeature,
)
from homeassistant.components.media_player import DOMAIN as MP_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.service import async_extract_entity_ids

import voluptuous as vol

from .const import (
    ANNOUNCE_LOWER,
    ANNOUNCE_PAUSE,
    ATTR_IGNORE_QUIET,
    CONF_ANNOUNCE_VIDEO,
    CONF_ANNOUNCE_VIDEO_LOWER,
    CONF_START_TIMEOUT,
    DOMAIN,
    PRESETS,
    SERVICE_ANNOUNCE,
    SOURCE_AUDIO,
    SOURCE_VIDEO,
)

if TYPE_CHECKING:
    from .controller import RoomController, Source

_LOGGER = logging.getLogger(__name__)

ANNOUNCE_VOLUME = "announce_volume"  # in extra: 0..100 (Music Assistant's)
SETTLE_SECONDS = 2  # the room holds still this long after the announcement
_NOT_READY = {STATE_OFF, "standby", "unavailable", "unknown"}


def announce_volume(volume: float | None, cap: float | None) -> int | None:
    """The announcement's volume (0..100) for the player: asked for, at most
    the night mode's limit (also when none was asked for)."""
    if cap is not None:
        volume = cap if volume is None else min(volume, cap)
    return None if volume is None else round(max(0.0, min(1.0, volume)) * 100)


class RoomAnnouncer:
    """Announcements in one room."""

    def __init__(self, ctrl: RoomController) -> None:
        self.ctrl = ctrl
        self.hass = ctrl.hass
        # players an announcement acts on (a count: announcements may overlap)
        self._protected: dict[str, int] = {}
        self._held: set[str] = set()  # roles kept on for it

    # -------------------------------------------------------------- queries

    @property
    def active(self) -> bool:
        return bool(self._protected)

    def protects(self, entity_id: str | None) -> bool:
        """An announcement acts on this player or output right now: what it
        reports is no wish."""
        return entity_id is not None and self._protected.get(entity_id, 0) > 0

    def holds(self, role) -> bool:
        return role.key in self._held

    def can(self, src: Source) -> bool:
        """The player announces itself (Home Assistant's announcements)."""
        return self.ctrl.player_supports(src, MediaPlayerEntityFeature.MEDIA_ANNOUNCE)

    @property
    def available(self) -> bool:
        return any(self.can(s) for s in self.ctrl.sources.values())

    def choose(self) -> Source | None:
        """The player that announces (see the module's docstring)."""
        ctrl = self.ctrl
        active = [s for s in ctrl.sources.values() if ctrl.source_active(s)]
        if active:
            current = ctrl.active_source if ctrl.active_source in active else active[0]
            if self.can(current):
                return current
            other = next((s for s in ctrl.sources.values() if s is not current and self.can(s)), None)
            return other
        for key in (SOURCE_AUDIO, SOURCE_VIDEO):
            src = ctrl.sources.get(key)
            if src is not None and self.can(src):
                return src
        return None

    def skipped(self, ignore_quiet: bool) -> bool:
        """A quiet room: nothing from outside (unless asked to ignore it)."""
        return self.ctrl.quiet and not ignore_quiet

    def resolve(self, volume: float | str | None) -> float | None:
        """A volume as asked for: 0..1, or a preset of this room."""
        return self.ctrl.volume.preset_value(volume) if isinstance(volume, str) else volume

    def cap(self) -> float | None:
        """The night mode's limit for the player (with a volume device the limit
        is the device's)."""
        return self.ctrl.night.cap if self.ctrl.volume_device is None else None

    def volume(self, volume: float | str | None) -> int | None:
        """The announcement's volume for this room's player (night: at most the limit)."""
        return announce_volume(self.resolve(volume), self.cap())

    # ---------------------------------------------------------- announcing

    async def async_announce(
        self,
        media_id: str,
        media_type: str,
        volume: float | str | None = None,
        extra: dict[str, Any] | None = None,
        ignore_quiet: bool = False,
    ) -> None:
        """Announce in this room (returns once it is over); volume: 0..1 or a
        preset of the room."""
        ctrl = self.ctrl
        if self.skipped(ignore_quiet):
            _LOGGER.info("%s: quiet, announcement left out", ctrl.entry.title)
            return
        src = self.choose()
        if src is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="announce_not_supported",
                translation_placeholders={"room": ctrl.entry.title},
            )
        was_on = ctrl.room_on
        players = [src.player]
        video = ctrl.sources.get(SOURCE_VIDEO)
        film = (
            video is not None
            and video is not src
            and ctrl.source_playing(video)
        )
        if film:
            players.append(video.player)
        async with self.protect(players):
            await self._power_up(src)
            undo_film = await self._film_before(video) if film else None
            try:
                data = {**(extra or {})}
                if (level := self.volume(volume)) is not None:
                    data[ANNOUNCE_VOLUME] = level
                await self._play(src.player, media_id, media_type, data)
            finally:
                if undo_film is not None:
                    await undo_film()
                self._release(was_on)

    async def _play(self, player: str, media_id: str, media_type: str, extra: dict[str, Any]) -> None:
        data: dict[str, Any] = {
            ATTR_ENTITY_ID: player,
            ATTR_MEDIA_CONTENT_ID: media_id,
            ATTR_MEDIA_CONTENT_TYPE: media_type,
            ATTR_MEDIA_ANNOUNCE: True,
        }
        if extra:
            data[ATTR_MEDIA_EXTRA] = extra
        _LOGGER.info("%s: announcement on %s", self.ctrl.entry.title, player)
        # returns once the player's system is through (announcement and restore)
        await self.hass.services.async_call(MP_DOMAIN, "play_media", data, blocking=True)

    async def _power_up(self, src: Source) -> None:
        """The devices the player needs are kept on (and come up if off); a
        player that is off is woken (unless the room is in manual mode)."""
        ctrl = self.ctrl
        roles = [r for r in ctrl.roles.values() if src.key in r.sources and ctrl.night.role_enabled(r.key)]
        if not roles or not ctrl.auto_power or ctrl.manual:
            return
        self._held = {r.key for r in roles}
        await ctrl.async_update("announce")
        timeout = float(ctrl.setting(CONF_START_TIMEOUT))
        if not await ctrl._wait_until(lambda: all(ctrl.role_fully_on(r) for r in roles), timeout):
            _LOGGER.warning("%s: devices not up for the announcement, announcing anyway", ctrl.entry.title)
        if ctrl.player_state(src) in _NOT_READY and ctrl.player_supports(src, MediaPlayerEntityFeature.TURN_ON):
            await ctrl._source_on(src)
            await ctrl._wait_until(lambda: ctrl.player_state(src) not in _NOT_READY, timeout)

    @callback
    def _release(self, was_on: bool) -> None:
        """Over: the devices kept on for it go off right away if nothing needs
        them (the room was off before)."""
        ctrl = self.ctrl
        held, self._held = self._held, set()
        if not was_on:
            for key in held:
                role = ctrl.roles.get(key)
                if role is not None and not ctrl.role_demand(role):
                    role.force_off = True  # no run-down: it only came on for the announcement

    async def _film_before(self, video: Source) -> Callable[[], Awaitable[None]] | None:
        """The film meanwhile: paused, overlaid or turned down; returns what
        undoes it afterwards."""
        ctrl = self.ctrl
        mode = ctrl.setting(CONF_ANNOUNCE_VIDEO)
        if mode == ANNOUNCE_PAUSE and ctrl.player_supports(video, MediaPlayerEntityFeature.PAUSE):
            await ctrl._player(video, "media_pause")

            async def _resume() -> None:
                if ctrl.player_state(video) == "paused":
                    await ctrl._player(video, "media_play")

            return _resume
        if mode == ANNOUNCE_LOWER:
            out, _src = ctrl.player_output(video.player)
            level = out.level() if out is not None else None
            if out is None or level is None or not out.can_set:
                return None
            lower = max(0.0, min(100.0, float(ctrl.setting(CONF_ANNOUNCE_VIDEO_LOWER)))) / 100
            down = round(level * (1 - lower), 3)
            await ctrl._player(video, "volume_set", {"volume_level": down})

            async def _back() -> None:
                current = out.level()
                if current is not None and abs(current - down) <= out.tolerance:
                    await ctrl._player(video, "volume_set", {"volume_level": level})

            return _back
        return None  # overlaid

    def protect(self, players: Iterable[str]) -> _Protection:
        return _Protection(self, list(players))


class _Protection:
    """While an announcement acts on the room's players: the room's own
    reactions to them hold still (and catch up afterwards)."""

    def __init__(self, announcer: RoomAnnouncer, players: list[str]) -> None:
        self.announcer = announcer
        self.players = players
        self._fades = 0.0
        self._night = False

    async def __aenter__(self) -> None:
        a = self.announcer
        ctrl = a.ctrl
        # (not the volume device: it comes up for the announcement and gets the
        # room's volume as usual; the announcement's volume is the player's)
        for player in self.players:
            a._protected[player] = a._protected.get(player, 0) + 1
        self._fades = ctrl.volume.async_pause_fades()
        self._night = ctrl.night.async_pause_ramps()
        ctrl.wake.async_announce_begin()

    async def __aexit__(self, *exc) -> None:
        # the players' last reports (the restore) may come a moment after the call
        # returned: the room holds still a little longer
        async_call_later(self.announcer.hass, SETTLE_SECONDS, self._end)

    @callback
    def _end(self, _now=None) -> None:
        a = self.announcer
        for player in self.players:
            if (count := a._protected.get(player, 0)) > 1:
                a._protected[player] = count - 1
            else:
                a._protected.pop(player, None)
        if not a.ctrl._stopped:
            a.ctrl._spawn(self._catch_up())

    async def _catch_up(self) -> None:
        ctrl = self.announcer.ctrl
        ctrl.sleep.async_announce_done()
        ctrl.wake.async_announce_done()
        await ctrl.volume.async_resume_fades(self._fades)
        if self._night:
            await ctrl.night.async_resume_ramps()
        await ctrl.async_update("announced")
        if (hub := ctrl.hub) is not None:
            hub.async_schedule("announced")


async def async_announce_rooms(
    hass: HomeAssistant,
    rooms: list[RoomController],
    media_id: str,
    media_type: str,
    volume: float | str | None = None,
    extra: dict[str, Any] | None = None,
    ignore_quiet: bool = False,
) -> None:
    """Announce in several rooms at once.

    Rooms whose music players together are all the members of a group that
    plays get it once, through the group (in step, and the group goes on
    afterwards); the others each on their own player.
    """
    rooms = [r for r in rooms if not r.announce.skipped(ignore_quiet)]
    by_group: dict[str, list[RoomController]] = {}
    alone: list[RoomController] = []
    for room in rooms:
        src = room.announce.choose()
        group = _playing_group(room, src)
        if group is not None:
            by_group.setdefault(group, []).append(room)
        else:
            alone.append(room)
    tasks = []
    for group, members in by_group.items():
        players = {r.sources[SOURCE_AUDIO].player for r in members}
        listed = set(hass.states.get(group).attributes.get("group_members") or [])
        if listed and listed <= players:
            tasks.append(_announce_group(group, members, media_id, media_type, volume, extra))
        else:
            alone.extend(members)  # only some of its rooms: each on its own player
    tasks += [
        r.announce.async_announce(media_id, media_type, volume, extra, ignore_quiet=True) for r in alone
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    errors = [e for e in results if isinstance(e, Exception)]
    if errors:
        if len(errors) == len(results) or len(results) == 1:
            raise errors[0]
        for err in errors:
            _LOGGER.warning("Announcement: %s", err)


def _playing_group(room: RoomController, src: Source | None) -> str | None:
    if src is None or src.key != SOURCE_AUDIO:
        return None
    groups = src.backend.groups_of(src.player)
    return groups[0] if groups else None


async def _announce_group(
    group: str,
    rooms: list[RoomController],
    media_id: str,
    media_type: str,
    volume: float | str | None,
    extra: dict[str, Any] | None,
) -> None:
    """Once through the group: its rooms hold still meanwhile (the quietest
    room's volume and limit hold for all)."""
    levels = [v for r in rooms if (v := r.announce.volume(volume)) is not None]
    level = min(levels) if levels else None
    data = {**(extra or {})}
    if level is not None:
        data[ANNOUNCE_VOLUME] = level
    async with AsyncExitStack() as stack:
        for room in rooms:
            await stack.enter_async_context(room.announce.protect([room.sources[SOURCE_AUDIO].player]))
        await rooms[0].announce._play(group, media_id, media_type, data)



# ------------------------------------------------------------------ action

ANNOUNCE_SCHEMA = vol.Schema(
    {
        vol.Exclusive("message", "what"): cv.string,
        vol.Exclusive(ATTR_MEDIA_CONTENT_ID, "what"): cv.string,
        vol.Optional(ATTR_MEDIA_CONTENT_TYPE, default="music"): cv.string,
        vol.Optional("tts_entity"): cv.entity_id,
        vol.Optional("language"): cv.string,
        vol.Optional("volume"): vol.Any(vol.All(vol.Coerce(float), vol.Range(min=0, max=1)), vol.In(PRESETS)),
        vol.Optional(ATTR_IGNORE_QUIET, default=False): cv.boolean,
        vol.Optional("pre_announce"): cv.boolean,
    },
    extra=vol.ALLOW_EXTRA,  # the target (entity_id, area_id, device_id)
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """better_media_player.announce: one or more rooms, coordinated."""
    from .hub import room_of  # noqa: PLC0415 (hub imports the controller, which imports this)

    async def _announce(call: ServiceCall) -> None:
        data = call.data
        if "message" not in data and ATTR_MEDIA_CONTENT_ID not in data:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="announce_nothing")
        rooms = [
            room
            for entity_id in sorted(await async_extract_entity_ids(call))
            if (room := room_of(hass, entity_id)) is not None
        ]
        if not rooms:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="announce_no_room")
        if "message" in data:
            from homeassistant.components.tts import generate_media_source_id  # noqa: PLC0415

            media_id = generate_media_source_id(
                hass, data["message"], engine=data.get("tts_entity"), language=data.get("language")
            )
            media_type = "music"
        else:
            media_id, media_type = data[ATTR_MEDIA_CONTENT_ID], data[ATTR_MEDIA_CONTENT_TYPE]
        extra = {"use_pre_announce": data["pre_announce"]} if "pre_announce" in data else None
        await async_announce_rooms(
            hass, rooms, media_id, media_type, data.get("volume"), extra, ignore_quiet=data[ATTR_IGNORE_QUIET]
        )

    hass.services.async_register(DOMAIN, SERVICE_ANNOUNCE, _announce, ANNOUNCE_SCHEMA)
