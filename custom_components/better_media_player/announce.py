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
4. No player of the room announces (fallback): the active source's player,
   else (room off) the music player, else the video player plays it as
   usual media: what played is paused, the announcement plays, then what
   played starts again where it was (best effort: a queue may be lost) and
   the player's volume comes back.

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
from .position import Position

if TYPE_CHECKING:
    from .controller import RoomController, Source

_LOGGER = logging.getLogger(__name__)

ANNOUNCE_VOLUME = "announce_volume"  # in extra: 0..100 (Music Assistant's)
SETTLE_SECONDS = 2  # the room holds still this long after the announcement
PLAY_TIMEOUT = 300  # s: an announcement that is not over by then is taken as over
START_SECONDS = 10  # fallback: the announcement (or what played) starts within this
_NOT_READY = {STATE_OFF, "standby", "unavailable", "unknown"}
_PLAYING = {"playing", "buffering"}
_HOLDS = {"playing", "buffering", "paused"}  # something to come back to


def announce_volume(volume: float | None, cap: float | None) -> int | None:
    """The announcement's volume (0..100) for the player: asked for, at most
    the night mode's limit (also when none was asked for)."""
    if cap is not None:
        volume = cap if volume is None else min(volume, cap)
    return None if volume is None else round(max(0.0, min(1.0, volume)) * 100)



class RoomAnnouncer:
    """Announcements in one room (they may overlap: all state is counted)."""

    def __init__(self, ctrl: RoomController) -> None:
        self.ctrl = ctrl
        self.hass = ctrl.hass
        self._protected: dict[str, int] = {}  # players an announcement acts on
        self._held: dict[str, int] = {}  # roles kept on for an announcement
        self._running = 0  # announcements in the room (until their settle time is over)
        self._brought_up: set[str] = set()  # roles that were off: they go off again after
        self._woken: set[str] = set()  # players that were off: they go off again after
        self._fades = 0.0  # a preset fade held still (s it had left)
        self._night = 0.0  # the night's fade back held still (s it had left)

    # -------------------------------------------------------------- queries

    @property
    def active(self) -> bool:
        return self._running > 0

    def protects(self, entity_id: str | None) -> bool:
        """An announcement acts on this player right now: what it reports is no wish."""
        return entity_id is not None and self._protected.get(entity_id, 0) > 0

    def holds(self, role) -> bool:
        return self._held.get(role.key, 0) > 0

    def can(self, src: Source) -> bool:
        """The player announces itself (Home Assistant's announcements)."""
        return self.ctrl.player_supports(src, MediaPlayerEntityFeature.MEDIA_ANNOUNCE)

    @property
    def available(self) -> bool:
        """Announcements are offered: a player announces, or plays media (fallback)."""
        return any(
            self.can(s) or self.ctrl.player_supports(s, MediaPlayerEntityFeature.PLAY_MEDIA)
            for s in self.ctrl.sources.values()
        )

    def choose(self) -> Source | None:
        """The player that announces (see the module's docstring)."""
        ctrl = self.ctrl
        active = [s for s in ctrl.sources.values() if ctrl.source_active(s)]
        if active:
            current = ctrl.active_source if ctrl.active_source in active else active[0]
            if self.can(current):
                return current
            return next((s for s in ctrl.sources.values() if s is not current and self.can(s)), None)
        for key in (SOURCE_AUDIO, SOURCE_VIDEO):
            src = ctrl.sources.get(key)
            if src is not None and self.can(src):
                return src
        return None

    def choose_fallback(self) -> Source | None:
        """No player announces: the active source's player, else the music player,
        else the video player (one that plays media)."""
        ctrl = self.ctrl
        plays = [s for s in ctrl.sources.values() if ctrl.player_supports(s, MediaPlayerEntityFeature.PLAY_MEDIA)]
        active = [s for s in plays if ctrl.source_active(s)]
        if active:
            return ctrl.active_source if ctrl.active_source in active else active[0]
        return next((s for key in (SOURCE_AUDIO, SOURCE_VIDEO) for s in plays if s.key == key), None)

    def fallback_volume(self, volume: float | str | None) -> float | None:
        """The fallback's volume for the player (0..1): asked for, else the room
        volume; at night at most the limit. With a volume device the device has
        the room's volume: only a number asked for is set on the player."""
        if self.ctrl.volume_device is not None:
            return None if volume is None or isinstance(volume, str) else volume
        level = self.ctrl.volume.preset_value(volume) if isinstance(volume, str) else volume
        if level is None:
            level = self.ctrl.volume.value
        cap = self.ctrl.night.cap
        return level if cap is None or level is None else min(level, cap)

    def skipped(self, ignore_quiet: bool) -> bool:
        """A quiet room: nothing from outside (unless asked to ignore it)."""
        return self.ctrl.quiet and not ignore_quiet

    def volume(self, volume: float | str | None) -> int | None:
        """The announcement's volume (0..100) for this room's player: asked for
        (0..1, or a preset of the room), at night at most the limit. With a
        volume device the room's volume is the device's: presets and the limit
        are its, not the player's (a preset is not used for the player)."""
        if self.ctrl.volume_device is not None:
            return announce_volume(None if isinstance(volume, str) else volume, None)
        level = self.ctrl.volume.preset_value(volume) if isinstance(volume, str) else volume
        return announce_volume(level, self.ctrl.night.cap)

    def film(self, src: Source) -> Source | None:
        """The film that plays while another player announces."""
        video = self.ctrl.sources.get(SOURCE_VIDEO)
        if video is None or video is src or not self.ctrl.source_active(video):
            return None
        return video if self.ctrl.source_playing(video) else None

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
        native = src is not None
        if src is None:
            src = self.choose_fallback()
        if src is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="announce_not_supported",
                translation_placeholders={"room": ctrl.entry.title},
            )
        data = {**(extra or {})}
        fallback_level: float | None = None
        if native:
            if (level := self.volume(volume)) is not None:  # (a bad preset fails before anything)
                data[ANNOUNCE_VOLUME] = level
        else:
            fallback_level = self.fallback_volume(volume)
        film = self.film(src)
        roles = self._roles_for(src)
        protected: list[str] = []
        undo_film: Callable[[], Awaitable[None]] | None = None
        play: asyncio.Task | None = None

        async def _finish() -> None:
            if undo_film is not None:
                await undo_film()
            self.end_later(roles, protected)

        self.begin(roles)
        try:
            await self._power_up(src, roles)
            protected = [src.player, *([film.player] if film else [])]
            self.protect(protected)
            undo_film = await self._film_before(film) if film else None
            # its own task: the room holds still until the player's system is
            # through, also if our caller gives up (e.g. an automation restarts)
            if native:
                play = self.hass.async_create_task(self._play(src.player, media_id, media_type, data))
            else:
                play = self.hass.async_create_task(self._play_fallback(src, media_id, media_type, fallback_level))
            await asyncio.shield(play)
        finally:
            if play is not None and not play.done():
                play.add_done_callback(lambda _task: self.ctrl._spawn(_finish()))
            else:
                await _finish()

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
        # returns once the player's system is through (announcement and restore);
        # a player that hangs does not keep the room still for ever
        try:
            async with asyncio.timeout(PLAY_TIMEOUT):
                await self.hass.services.async_call(MP_DOMAIN, "play_media", data, blocking=True)
        except TimeoutError:
            _LOGGER.warning("%s: announcement on %s not over after %s s", self.ctrl.entry.title, player, PLAY_TIMEOUT)

    async def _play_fallback(self, src: Source, media_id: str, media_type: str, level: float | None) -> None:
        """The player cannot announce: pause what plays, play the announcement as
        usual media, then start what played again where it was (best effort)."""
        ctrl = self.ctrl
        _LOGGER.info("%s: announcement on %s (it does not announce: played as media)", ctrl.entry.title, src.player)
        state = self.hass.states.get(src.player)
        before: dict[str, Any] | None = None
        if state is not None and state.state in _HOLDS:
            position = Position.of(self.hass, src.player)
            before = {
                "id": state.attributes.get(ATTR_MEDIA_CONTENT_ID),
                "type": state.attributes.get(ATTR_MEDIA_CONTENT_TYPE),
                "seconds": position.seconds if position is not None else None,
                "paused": state.state == "paused",
            }
            if state.state != "paused" and ctrl.player_supports(src, MediaPlayerEntityFeature.PAUSE):
                await ctrl._player(src, "media_pause")
        out, _src = ctrl.player_output(src.player)
        volume_before = out.level() if out is not None else None
        if level is not None and out is not None and out.can_set:
            await ctrl._player(src, "volume_set", {"volume_level": level})
        try:
            async with asyncio.timeout(PLAY_TIMEOUT):
                await ctrl._player(src, "play_media", {"media_content_id": media_id, "media_content_type": media_type})
                await self._played(src, media_id)
        except TimeoutError:
            _LOGGER.warning("%s: announcement on %s not over after %s s", ctrl.entry.title, src.player, PLAY_TIMEOUT)
        finally:
            await self._restore(src, before)
            if level is not None and volume_before is not None and out is not None and out.can_set:
                await ctrl._player(src, "volume_set", {"volume_level": volume_before})

    async def _played(self, src: Source, media_id: str) -> None:
        """Until the announcement has played: it starts (within START_SECONDS),
        then ends (the player no longer plays it; its duration as a bound)."""
        ctrl = self.ctrl

        def _state():
            return self.hass.states.get(src.player)

        def _started() -> bool:
            st = _state()
            return st is not None and (
                st.attributes.get(ATTR_MEDIA_CONTENT_ID) == media_id or st.state in _PLAYING
            )

        if not await ctrl._wait_until(_started, START_SECONDS):
            return
        st = _state()
        duration = st.attributes.get("media_duration") if st is not None else None
        bound = float(duration) + 5 if isinstance(duration, (int, float)) and duration > 0 else PLAY_TIMEOUT

        def _over() -> bool:
            now = _state()
            return now is None or now.state not in _PLAYING or now.attributes.get(ATTR_MEDIA_CONTENT_ID) not in (
                media_id,
                st.attributes.get(ATTR_MEDIA_CONTENT_ID) if st is not None else None,
            )

        await ctrl._wait_until(_over, bound)

    async def _restore(self, src: Source, before: dict[str, Any] | None) -> None:
        """What played starts again where it was (paused stays paused); else the
        player stops."""
        ctrl = self.ctrl
        if before is None or not before["id"] or not before["type"]:
            if ctrl.player_state(src) in _PLAYING and ctrl.player_supports(src, MediaPlayerEntityFeature.STOP):
                await ctrl._player(src, "media_stop")
            return
        await ctrl._player(src, "play_media", {"media_content_id": before["id"], "media_content_type": before["type"]})
        playing = await ctrl._wait_until(lambda: ctrl.player_state(src) in _PLAYING, START_SECONDS)
        seconds = before["seconds"]
        if playing and seconds and ctrl.player_supports(src, MediaPlayerEntityFeature.SEEK):
            await ctrl._player(src, "media_seek", {"seek_position": round(seconds, 1)})
        if before["paused"] and ctrl.player_supports(src, MediaPlayerEntityFeature.PAUSE):
            await ctrl._player(src, "media_pause")

    def _roles_for(self, src: Source) -> list[str]:
        """The roles kept on for the announcement (automatic power only)."""
        ctrl = self.ctrl
        if not ctrl.auto_power or ctrl.manual:
            return []
        return [k for k, r in ctrl.roles.items() if src.key in r.sources and ctrl.night.role_enabled(k)]

    async def _power_up(self, src: Source, roles: list[str]) -> None:
        """The devices the player needs come up (if off); a player that is off is woken."""
        ctrl = self.ctrl
        timeout = float(ctrl.setting(CONF_START_TIMEOUT))
        if roles:
            await ctrl.async_update("announce")
            wanted = [ctrl.roles[k] for k in roles]
            if not await ctrl._wait_until(lambda: all(ctrl.role_fully_on(r) for r in wanted), timeout):
                _LOGGER.warning("%s: devices not up for the announcement, announcing anyway", ctrl.entry.title)
        if ctrl.player_state(src) in _NOT_READY and ctrl.player_supports(src, MediaPlayerEntityFeature.TURN_ON):
            self._woken.add(src.key)
            await ctrl._source_on(src)
            await ctrl._wait_until(lambda: ctrl.player_state(src) not in _NOT_READY, timeout)

    async def _film_before(self, video: Source) -> Callable[[], Awaitable[None]] | None:
        """The film meanwhile: paused (turned down if it cannot pause), overlaid
        or turned down; returns what undoes it afterwards."""
        ctrl = self.ctrl
        mode = ctrl.setting(CONF_ANNOUNCE_VIDEO)
        if mode == ANNOUNCE_PAUSE and ctrl.player_supports(video, MediaPlayerEntityFeature.PAUSE):
            await ctrl._player(video, "media_pause")

            async def _resume() -> None:
                if ctrl.player_state(video) == "paused":
                    await ctrl._player(video, "media_play")

            return _resume
        if mode in (ANNOUNCE_PAUSE, ANNOUNCE_LOWER):
            out, _src = ctrl.player_output(video.player)
            level = out.level() if out is not None else None
            if out is None or level is None or not out.can_set:
                return None
            lower = max(0.0, min(100.0, float(ctrl.setting(CONF_ANNOUNCE_VIDEO_LOWER)))) / 100
            down = round(level * (1 - lower), 3)
            await ctrl._player(video, "volume_set", {"volume_level": down})

            async def _back() -> None:
                # back, unless someone set another volume meanwhile (the lowered
                # one may not even be reported yet)
                current = out.level()
                if current is None or min(abs(current - down), abs(current - level)) <= out.tolerance:
                    await ctrl._player(video, "volume_set", {"volume_level": level})

            return _back
        return None  # overlaid

    # ------------------------------------------------------- holding still

    @callback
    def begin(self, roles: list[str]) -> None:
        """An announcement starts: its roles are kept on; the first holds the
        room's fades still."""
        ctrl = self.ctrl
        for key in roles:
            if (role := ctrl.roles.get(key)) is not None and not ctrl.role_on(role):
                self._brought_up.add(key)
        if self._running == 0:
            self._fades = ctrl.volume.async_pause_fades()
            self._night = ctrl.night.async_pause_ramps()
            ctrl.wake.async_announce_begin()
        self._running += 1
        for key in roles:
            self._held[key] = self._held.get(key, 0) + 1

    @callback
    def protect(self, players: Iterable[str]) -> None:
        for player in players:
            self._protected[player] = self._protected.get(player, 0) + 1

    @callback
    def end_later(self, roles: list[str], players: list[str]) -> None:
        """The players' last reports (the restore) may come a moment after the
        call returned: the room holds still a little longer."""

        @callback
        def _end(_now=None) -> None:
            self._end(roles, players)

        async_call_later(self.hass, SETTLE_SECONDS, _end)

    @callback
    def _end(self, roles: list[str], players: list[str]) -> None:
        for player in players:
            if (count := self._protected.get(player, 0)) > 1:
                self._protected[player] = count - 1
            else:
                self._protected.pop(player, None)
        for key in roles:
            if (count := self._held.get(key, 0)) > 1:
                self._held[key] = count - 1
            else:
                self._held.pop(key, None)
        self._running = max(0, self._running - 1)
        if self._running or self.ctrl._stopped:
            return
        self._protected.clear()
        self._held.clear()
        self.ctrl._spawn(self._catch_up())

    @callback
    def async_room_off(self) -> None:
        """The room is turned off during an announcement: what it keeps on
        goes off once it is over."""
        if self.active:
            self._brought_up |= set(self._held)
            self._woken |= set(self.ctrl.sources)

    async def _catch_up(self) -> None:
        """All announcements are over: what came up only for them goes off
        again (no run-down), and the room goes on where it held still."""
        ctrl = self.ctrl
        fades, night, self._fades, self._night = self._fades, self._night, 0.0, 0.0
        brought_up, self._brought_up = self._brought_up, set()
        woken, self._woken = self._woken, set()
        for key in woken:
            src = ctrl.sources.get(key)
            if src is not None and not ctrl.source_playing(src) and ctrl.player_state(src) not in _NOT_READY:
                src.suppressed = True
                await ctrl._source_off(src)
        for key in brought_up:
            role = ctrl.roles.get(key)
            if role is not None and not ctrl.role_demand(role):
                role.force_off = True
        if self.active:
            return  # a new one began meanwhile: it holds the room still now
        ctrl.sleep.async_announce_done()
        ctrl.wake.async_announce_done()
        await ctrl.volume.async_resume_fades(fades)
        if night:
            await ctrl.night.async_resume_ramps(night)
        await ctrl.async_update("announced")
        if (hub := ctrl.hub) is not None:
            hub.async_schedule("announced")


# ------------------------------------------------------------- several rooms


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
    rooms = [r for r in dict.fromkeys(rooms) if not r.announce.skipped(ignore_quiet)]
    by_group: dict[str, list[RoomController]] = {}
    alone: list[RoomController] = []
    errors: list[Exception] = []
    for room in list(rooms):
        try:
            room.announce.volume(volume)  # a preset the room does not offer: its own error
        except ServiceValidationError as err:
            errors.append(err)
            rooms.remove(room)
    for room in rooms:
        group = _playing_group(hass, room)
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
    errors += [e for e in results if isinstance(e, Exception)]
    for err in errors[1:]:
        _LOGGER.warning("Announcement: %s", err)
    if errors:
        raise errors[0]  # (the other rooms had theirs)


def _playing_group(hass: HomeAssistant, room: RoomController) -> str | None:
    """The playing group whose member announces for the room (only when the
    music player announces and no film plays: the group knows nothing of it)."""
    src = room.announce.choose()
    if src is None or src.key != SOURCE_AUDIO or room.announce.film(src) is not None:
        return None
    for group in src.backend.groups_of(src.player):
        state = hass.states.get(group)
        if (
            state is not None
            and state.state == "playing"
            and int(state.attributes.get("supported_features", 0) or 0) & MediaPlayerEntityFeature.MEDIA_ANNOUNCE
        ):
            return group
    return None


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
    data = {**(extra or {})}
    if levels:
        data[ANNOUNCE_VOLUME] = min(levels)
    players = {r: [r.sources[SOURCE_AUDIO].player] for r in rooms}
    for room in rooms:
        room.announce.begin([])
        room.announce.protect(players[room])
    try:
        await rooms[0].announce._play(group, media_id, media_type, data)
    finally:
        for room in rooms:
            room.announce.end_later([], players[room])


# ------------------------------------------------------------------ action


def _volume(value: Any) -> float | str:
    """0..1 or a preset name (not a bool)."""
    if isinstance(value, bool):
        raise vol.Invalid("expected 0..1 or a preset")
    if isinstance(value, str) and value in PRESETS:
        return value
    return vol.All(vol.Coerce(float), vol.Range(min=0, max=1))(value)


ANNOUNCE_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Exclusive("message", "what"): cv.string,
        vol.Exclusive(ATTR_MEDIA_CONTENT_ID, "what"): cv.string,
        vol.Optional(ATTR_MEDIA_CONTENT_TYPE, default="music"): cv.string,
        vol.Optional("tts_entity"): cv.entity_id,
        vol.Optional("language"): cv.string,
        vol.Optional("volume"): _volume,
        vol.Optional(ATTR_IGNORE_QUIET, default=False): cv.boolean,
        vol.Optional("pre_announce"): cv.boolean,
    }
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """better_media_player.announce: one or more rooms, coordinated."""
    from .hub import room_of  # noqa: PLC0415 (hub imports the controller, which imports this)

    async def _announce(call: ServiceCall) -> None:
        data = call.data
        if "message" not in data and ATTR_MEDIA_CONTENT_ID not in data:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="announce_nothing")
        rooms = list(
            dict.fromkeys(
                room
                for entity_id in sorted(await async_extract_entity_ids(call))
                if entity_id.startswith(f"{MP_DOMAIN}.") and (room := room_of(hass, entity_id)) is not None
            )
        )
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
