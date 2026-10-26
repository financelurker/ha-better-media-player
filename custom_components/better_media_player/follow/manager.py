"""Central settings entry: follow me (the session; its volume: volume.py, a follow wake: wake.py).

The whole installation has one follow-me player: a Music Assistant group
player with dynamic members. Follow me is started explicitly in a room where
music plays (button, action or a follow wake): the room joins the follow-me
player and its queue moves there. From then on the music follows the people:

- all follow rooms are prewarmed (speakers on) while it plays
- join when presence is detected, volume set first: base volume + room offset
- leave after the room's presence run-down (counted from when it became both
  member and empty); the last leave makes Music Assistant pause the group
- never take a room away from another group or from direct playback
- rooms with video (Kodi), quiet mode, or turned off this session are left out;
  after video ends the room comes back (and resumes a group it left paused)
- presence unknown (sensor unavailable) changes nothing
- members that are not backed by presence (resume, restart) are kept while
  nobody is detected in any eligible room ("override")

Stopping it (button or action) brings the music back to one room: the room it
is stopped in if that room plays along, else the room it was started in. Every
other room stops. It also ends by itself when the follow-me player stops, or
when it paused because everybody left.

Any other group (Music Assistant groups started in its app, a party group)
simply counts for the rooms that play in it.

The session state lives in hass.data so that reloading the central entry does
not lose it; "follow me on" and the room it was started in are stored.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components.media_player import DOMAIN as MP_DOMAIN
from homeassistant.components.media_player import MediaPlayerState
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_FRIENDLY_NAME,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Context, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store

from .. import clock
from ..const import (
    CONF_FOLLOW_PLAYER,
    DOMAIN,
    FOLLOW_ACTIVE,
    FOLLOW_EXCLUDED_BUSY,
    FOLLOW_EXCLUDED_OFF,
    FOLLOW_EXCLUDED_QUIET,
    FOLLOW_EXCLUDED_VIDEO,
    FOLLOW_HELD_OUT,
    FOLLOW_INACTIVE,
    FOLLOW_OVERRIDE,
    FOLLOW_SNOOZED,
    FOLLOW_UNKNOWN,
    FOLLOW_WAITING,
    FOLLOW_WAKING,
    HUB_ENTITY_DEFAULTS,
    SOURCE_AUDIO,
    SOURCE_VIDEO,
    STORAGE_VERSION,
    TICK_SECONDS,
)
from ..players.music_assistant import MusicAssistant
from ..players.position import Position
from ..room import get_rooms
from . import EMPTY, PLAYING, PRESENT, START_GRACE_SECONDS, UNKNOWN
from .volume import FollowVolume
from .wake import FollowWake

if TYPE_CHECKING:
    from ..room.controller import RoomController

_LOGGER = logging.getLogger(__name__)

UNCERTAIN = {None, STATE_UNAVAILABLE, STATE_UNKNOWN}
HARD_EXCLUSIONS = (FOLLOW_EXCLUDED_QUIET, FOLLOW_EXCLUDED_VIDEO, FOLLOW_EXCLUDED_OFF, FOLLOW_SNOOZED)

COMMAND_REPEAT_SECONDS = 30  # do not repeat the same command within this time
VIDEO_RESUME_MAX_SECONDS = 3 * 3600  # resume after video only within this time


@dataclass
class FollowSession:
    """State of the running follow-me session (survives hub reloads)."""

    group: str | None = None
    started_at: float = 0.0
    started_by_us: bool = False  # MA may report "playing" late
    backed: set[str] = field(default_factory=set)  # members backed by presence
    leaving: set[str] = field(default_factory=set)  # unjoin sent, MA not done yet
    opted_out: set[str] = field(default_factory=set)  # turned off this session
    video_left: dict[str, float] = field(default_factory=dict)  # last room left for video
    member_since: dict[str, float] = field(default_factory=dict)
    empty_since: dict[str, float] = field(default_factory=dict)
    # occupied when a follow wake started this session: out until their presence went
    held_out: set[str] = field(default_factory=set)

    def reset(self, group: str | None, by_us: bool = False) -> None:
        self.group = group
        self.started_at = clock.now()
        self.started_by_us = by_us
        self.backed.clear()
        self.leaving.clear()
        self.opted_out.clear()
        self.video_left.clear()
        self.member_since.clear()
        self.empty_since.clear()
        self.held_out.clear()


class FollowManager:
    """Brain of the central entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.opts: dict[str, Any] = dict(entry.options)
        self.follow_player: str | None = self.opts.get(CONF_FOLLOW_PLAYER)
        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self.data: dict[str, Any] = {}
        self.session: FollowSession = hass.data.setdefault(DOMAIN, {}).setdefault("session", FollowSession())
        self._lock = asyncio.Lock()
        self._unsubs: list[Callable[[], None]] = []
        self._track_unsub: Callable[[], None] | None = None
        self.ma = MusicAssistant(hass)  # how groups and queues work
        self._listeners: list[Callable[[], None]] = []
        self._tasks: set[asyncio.Task] = set()
        self._queued = False
        self._started = False
        self._stopped = False
        self._commands: dict[tuple[str, str], float] = {}  # (player, action) -> last sent
        self.volume = FollowVolume(self)
        self.wake = FollowWake(self)

    # ------------------------------------------------------------------ setup

    def setting(self, key: str) -> Any:
        """A value set on the central device (config entity), else its default."""
        return self.data.get(key, HUB_ENTITY_DEFAULTS[key])

    async def async_set_setting(self, key: str, value: Any) -> None:
        self.data[key] = value
        self._store.async_delay_save(lambda: self.data, 0)
        self._notify()
        self._notify_rooms()  # e.g. the dynamic controls of every room

    async def async_start(self) -> None:
        self.data = {"volumes": {}, **(await self._store.async_load() or {})}
        if self.data.get("active") and self.data.get("player") != self.follow_player:
            # follow me ran on another player (changed in the options): it is not taken over
            _LOGGER.info("Follow me ended (the follow-me player changed)")
            self.data["active"] = False
            self.data.pop("player", None)
            self.data.pop("origin", None)
            self.session.reset(None)
            self._store.async_delay_save(lambda: self.data, 0)
        self._unsubs.append(
            async_track_time_interval(self.hass, self._on_tick, timedelta(seconds=TICK_SECONDS))
        )
        self.async_refresh_tracking()
        self._notify_rooms()  # their follow me buttons depend on the central entry

        @callback
        def _started(_hass: HomeAssistant) -> None:
            self._started = True
            self.async_schedule("start")

        self._unsubs.append(async_at_started(self.hass, _started))

    async def async_stop(self) -> None:
        self._stopped = True
        self._started = False
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._track_unsub:
            self._track_unsub()
            self._track_unsub = None
        self.ma.async_stop()
        self.volume.stop()
        for task in list(self._tasks):
            task.cancel()
        for room in get_rooms(self.hass).values():
            room.follow.set_prewarm(False)
            room.follow.set_state(FOLLOW_INACTIVE)
            room.notify()
        await self._store.async_save(self.data)

    @callback
    def async_refresh_tracking(self) -> None:
        """(Re)subscribe to the groups, room players and occupancy sensors."""
        if self._stopped:
            return
        if self._track_unsub:
            self._track_unsub()
        tracked = [*([self.follow_player] if self.follow_player else []), *self.ma.groups()]
        for room in get_rooms(self.hass).values():
            tracked += room.follow.tracked_entities
        tracked = list(dict.fromkeys(tracked))
        self._track_unsub = (
            async_track_state_change_event(self.hass, tracked, self._on_state_event) if tracked else None
        )

    @callback
    def async_add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    @callback
    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    @callback
    def async_schedule(self, reason: str = "") -> None:
        """Queue one evaluation; further requests while queued are merged."""
        if self._stopped or not self._started or self._queued:
            return
        self._queued = True
        task = self.hass.async_create_task(self.async_update(reason), eager_start=False)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @callback
    def _on_state_event(self, event: Event[EventStateChangedData]) -> None:
        self.async_schedule(event.data["entity_id"])

    @callback
    def _on_tick(self, _now: Any) -> None:
        self.async_schedule("tick")

    # ------------------------------------------------------------------ state

    @property
    def visibility_ready(self) -> bool:
        return self._started

    @property
    def active(self) -> bool:
        """Follow me is on."""
        return bool(self.data.get("active")) and self.follow_player is not None

    @property
    def origin(self) -> RoomController | None:
        """The room follow me was started in."""
        return get_rooms(self.hass).get(self.data.get("origin") or "")

    def follows(self, group: str) -> bool:
        """Playback in this group follows the people (not just plays)."""
        return self.active and group == self.follow_player

    def _member_rooms(self, group: str) -> list[RoomController]:
        members = self.ma.members(group)
        return [r for r in self.follow_rooms() if r.audio_player in members]

    def can_start(self, room: RoomController) -> bool:
        """Follow me can be started in this room now: music plays on its own player."""
        return self.start_blocker(room) is None

    def _name(self, entity_id: str | None) -> str:
        state = self.hass.states.get(entity_id) if entity_id else None
        return str(state.attributes.get(ATTR_FRIENDLY_NAME) or entity_id) if state else str(entity_id)

    def start_blocker(self, room: RoomController) -> tuple[str, dict[str, str]] | None:
        """Why follow me cannot be started in this room right now (None: it can):
        a translation key of the error and its placeholders."""
        if self.follow_player is None:
            return "no_follow_player", {}
        if self.active:
            return "follow_running", {}
        names = {"room": room.entry.title}
        if not room.follow.capable:
            return "start_not_follow_room", names
        if room.quiet:
            return "start_quiet", names
        if room.video_active:
            return "start_video", {**names, "player": self._name(room.sources[SOURCE_VIDEO].player)}
        if room.audio_playing_directly:
            return None
        player = room.audio_player
        names["player"] = self._name(player)
        if player in self.ma.members(self.follow_player):
            if self.ma.state(self.follow_player) in PLAYING:
                return None  # already in the follow-me player, which plays without follow me
            # left in the follow-me player, which does not play (e.g. paused)
            return "start_follow_paused", {**names, "group": self._name(self.follow_player)}
        groups = self.ma.groups_of(player)
        if groups:
            return "start_in_group", {**names, "group": ", ".join(self._name(g) for g in groups)}
        return "start_not_playing", names

    # ----------------------------------------------------------------- rooms

    def follow_rooms(self) -> list[RoomController]:
        return [r for r in get_rooms(self.hass).values() if r.follow.capable]

    def room_exclusion(self, room: RoomController, group: str) -> str | None:
        """Why this room cannot take part in the follow session right now."""
        if room.wake.follow_snoozed:
            return FOLLOW_SNOOZED
        if room.quiet and not room.wake.follow_holding:
            return FOLLOW_EXCLUDED_QUIET  # a follow wake holds even a quiet room
        if room.video_active:
            return FOLLOW_EXCLUDED_VIDEO
        if self.session.group == group and room.entry.entry_id in self.session.opted_out:
            return FOLLOW_EXCLUDED_OFF
        if self.session.group == group and room.entry.entry_id in self.session.held_out:
            return FOLLOW_HELD_OUT
        player = room.audio_player
        if player is None:
            return FOLLOW_EXCLUDED_BUSY
        # never take a room away from another group or from direct playback
        if any(other != group for other in self.ma.groups_of(player)):
            return FOLLOW_EXCLUDED_BUSY
        if player not in self.ma.members(group) and room.audio_playing_directly:
            return FOLLOW_EXCLUDED_BUSY
        return None

    # ----------------------------------------------------------- start / stop

    async def async_follow_start(self, room: RoomController) -> None:
        """Follow me from this room: it joins the follow-me player, its queue moves there."""
        async with self._lock:
            if (blocker := self.start_blocker(room)) is not None:
                key, placeholders = blocker
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key=key, translation_placeholders=placeholders
                )
            group, player = self.follow_player, room.audio_player
            # the volume the music had in this room becomes follow me's volume (at
            # night the room's own wish, not the limited one); with a volume device
            # the player's level says nothing: follow me keeps its last volume
            if room.volume_device is not None:
                volume = None
            elif room.night.player_cap is not None:
                volume = room.volume.value
            else:
                volume = self.volume.player_volume(player)
            if volume is not None and volume > 0:
                self.data["volumes"][group] = round(max(0.0, min(100.0, volume * 100 - room.follow.volume_offset)), 1)
            self._activate(room)
            if room.follow.presence() == PRESENT:
                self.session.backed.add(room.entry.entry_id)
            # else: kept as override while nobody is detected anywhere (a sensor
            # may miss the listener)
            if room.audio_playing_directly:
                # join first: an empty group cannot play; then the queue moves over
                # (also as a member left in the idle follow-me player from before)
                self._commands.pop((player, f"join:{group}"), None)
                position = self.ma.position(player)  # (joining may stop the player)
                ok = await self._join(room, group, keep_volume=True)
                ok = ok and await self._transfer(player, group, position=position)
                if not ok:
                    self._commands.pop((player, "unjoin"), None)
                    await self._unjoin(player, "follow me could not start")
                    await self._deactivate("the queue could not be moved")
                    raise HomeAssistantError(f"Could not move the queue of {player} to {group}")
        _LOGGER.info("Follow me started in %s", room.entry.title)
        self.async_schedule("follow_start")

    async def async_follow_stop(self, room: RoomController | None = None, to: RoomController | None = None) -> None:
        """Follow me off: the music goes back to one room (to: this one, e.g. a
        handover), the others stop."""
        async with self._lock:
            if not self.active:
                raise ServiceValidationError(translation_domain=DOMAIN, translation_key="follow_not_running")
            group = self.follow_player
            members = self.ma.members(group)
            playing = self.ma.state(group) in PLAYING  # paused stays paused in the room
            if to is not None:
                target = to
            else:
                target = room if room is not None and room.audio_player in members else self.origin
            for wake_room in get_rooms(self.hass).values():
                if wake_room.wake.follows and wake_room.wake.active:
                    await wake_room.wake.async_finish("stopped")
            position = self.ma.position(group)  # (the group stops with its last member)
            for member in members:
                await self._unjoin(member, "follow me stopped")
            moved = True
            if target is not None and target.audio_player:
                moved = await self._transfer(group, target.audio_player, auto_play=playing, position=position)
            await self._deactivate(f"stopped in {room.entry.title}" if room else "stopped")
            if not moved:
                raise HomeAssistantError(f"Could not move the queue of {group} to {target.audio_player}")
        self.async_schedule("follow_stop")

    @callback
    def _activate(self, origin: RoomController) -> None:
        self.data["active"] = True
        self.data["player"] = self.follow_player
        self.data["origin"] = origin.entry.entry_id
        self.session.reset(self.follow_player, by_us=True)
        self._store.async_delay_save(lambda: self.data, 0)
        self._notify_rooms()

    async def _deactivate(self, why: str) -> None:
        _LOGGER.info("Follow me ended (%s)", why)
        self.data["active"] = False
        self.data.pop("player", None)
        self.data.pop("origin", None)
        self.session.reset(None)
        self._store.async_delay_save(lambda: self.data, 0)
        for room in get_rooms(self.hass).values():
            room.follow.set_prewarm(False)
            room.follow.set_state(FOLLOW_INACTIVE)
        self._notify_rooms()
        self._notify()

    @callback
    def _notify_rooms(self) -> None:
        """The rooms' follow me buttons depend on it."""
        for room in get_rooms(self.hass).values():
            room.notify()

    async def _transfer(
        self, source: str, target: str, auto_play: bool = True, position: Position | None = None
    ) -> bool:
        """Move the queue; the track goes on where it was (position, read before)."""
        return await self.ma.transfer(source, target, auto_play, position)

    # -------------------------------------------------------------- evaluation

    async def async_update(self, reason: str = "") -> None:
        async with self._lock:
            self._queued = False
            await self._evaluate()
        self._notify()

    def _resumable(self, group: str) -> bool:
        """Paused because the last room left for video (recently); resume pending.

        Also true while that room is already back in the group but Music Assistant
        has not resumed yet (join reported late, play not yet effective).
        """
        now = clock.now()
        s = self.session
        if s.group != group or self.ma.state(group) != MediaPlayerState.PAUSED:
            return False
        recent = {rid for rid, ts in s.video_left.items() if now - ts < VIDEO_RESUME_MAX_SECONDS}
        if not recent:
            return False
        members = self.ma.members(group)
        if not members:
            return True
        member_ids = {r.entry.entry_id for r in self._member_rooms(group)}
        return member_ids <= recent

    def _wake_waits(self, group: str) -> bool:
        """A follow wake room snoozes or was dropped: the session waits for it."""
        return any(
            (r.wake.follow_snoozed or r.wake.follow_holding)
            for r in get_rooms(self.hass).values()
        )

    def _session_goes_on(self, group: str, now: float) -> bool:
        s = self.session
        state = self.ma.state(group)
        if state in PLAYING or (s.started_by_us and now - s.started_at < START_GRACE_SECONDS):
            return True
        if self._resumable(group) or self._wake_waits(group):
            return True
        # paused by someone: the session goes on, nothing changes
        return state == MediaPlayerState.PAUSED and bool(self.ma.members(group))

    async def _evaluate(self) -> None:
        if any(r.announce.active for r in get_rooms(self.hass).values()):
            return  # an announcement takes players out of groups for a moment (again after it)
        now = clock.now()
        self.wake.track_arrivals(now)
        s = self.session
        await self.volume.async_night_groups()

        group = self.follow_player if self.active else None
        if group is None:
            if s.group is not None:
                s.reset(None)
            for room in get_rooms(self.hass).values():
                room.follow.set_prewarm(False)
                room.follow.set_state(FOLLOW_INACTIVE)
            return
        if self.ma.state(group) in UNCERTAIN:
            return  # MA reconnecting: no decisions
        if s.group != group:
            # follow me was on before a restart: members are kept, no overrides
            _LOGGER.info("Follow me goes on after a restart")
            s.reset(group)
            s.backed = {r.entry.entry_id for r in self._member_rooms(group)}
        if not self._session_goes_on(group, now):
            await self._deactivate("the follow-me player stopped")
            return
        self.volume.remember(group)

        rooms = self.follow_rooms()
        presences = {r.entry.entry_id: r.follow.presence() for r in rooms}
        exclusions = {r.entry.entry_id: self.room_exclusion(r, group) for r in rooms}

        group_playing = self.ma.state(group) in PLAYING
        resumable = self._resumable(group)
        if not group_playing and not resumable and not (s.started_by_us and now - s.started_at < START_GRACE_SECONDS):
            # paused by someone: membership stays as it is, prewarm runs down
            members = self.ma.members(group)
            for room in get_rooms(self.hass).values():
                room.follow.set_prewarm(False)
                if room.wake.follow_snoozed:
                    room.follow.set_state(FOLLOW_SNOOZED)
                elif (
                    room.wake.follow_holding
                    and room.wake.phase != "starting"
                    and room.audio_player not in members
                ):
                    # the wake room was dropped (e.g. the player reconnected): back in, play on
                    await self._join(room, group, keep_volume=True)
                    await self._resume(group)
                    room.follow.set_state(FOLLOW_WAKING)
            return

        members = self.ma.members(group)
        anyone = any(presences[rid] == PRESENT and exclusions[rid] is None for rid in presences)

        for room in rooms:
            rid = room.entry.entry_id
            presence = presences[rid]
            excluded = exclusions[rid]
            member = room.audio_player in members

            if rid in s.held_out and presence == EMPTY:
                s.held_out.discard(rid)  # their presence went: from now on normal follow
                excluded = exclusions[rid] = self.room_exclusion(room, group)

            room.follow.set_prewarm(group_playing and room.follow.prewarm_enabled and excluded not in HARD_EXCLUSIONS)

            if rid in s.held_out and member and not room.wake.follow_holding:
                await self._leave(room, "held out by a follow wake")
                room.follow.set_state(FOLLOW_HELD_OUT)
                continue
            if room.wake.follow_holding:
                await self.wake.async_room(room, group, member, presence, now)
                continue

            if member:
                s.member_since.setdefault(rid, now)
                if excluded in HARD_EXCLUSIONS:
                    if excluded == FOLLOW_EXCLUDED_VIDEO and members == [room.audio_player]:
                        s.video_left[rid] = now  # the last room left for video: resume later
                    await self._leave(room, f"room {excluded}")
                    room.follow.set_state(excluded)
                    continue
                if group_playing:
                    s.video_left.pop(rid, None)  # resumed and back in the group
                elif rid in s.video_left and resumable:
                    await self._resume(group)  # back in the group, MA not playing yet
                if rid in s.leaving:
                    if presence == PRESENT:
                        s.leaving.discard(rid)  # came back; MA may still remove it, then it rejoins
                    else:
                        await self._leave(room, "still leaving")  # retried after the repeat window
                        room.follow.set_state(FOLLOW_WAITING)
                        continue
                if presence == PRESENT:
                    s.backed.add(rid)
                    s.empty_since.pop(rid, None)
                    room.follow.set_state(FOLLOW_ACTIVE)
                    continue
                if presence == UNKNOWN:
                    s.empty_since.pop(rid, None)
                    room.follow.set_state(FOLLOW_UNKNOWN)
                    continue
                # member, nobody there
                if rid not in s.backed:
                    if not anyone:
                        room.follow.set_state(FOLLOW_OVERRIDE)
                        continue
                    # override ends once presence is seen elsewhere: normal rules from now
                    s.backed.add(rid)
                    s.empty_since[rid] = now
                since = max(s.empty_since.setdefault(rid, now), s.member_since[rid])
                if now - since >= room.follow.presence_run_down:
                    await self._leave(room, "nobody there")
                    room.follow.set_state(FOLLOW_WAITING)
                else:
                    room.follow.set_state(FOLLOW_ACTIVE)
                continue

            # not a member
            s.member_since.pop(rid, None)
            s.empty_since.pop(rid, None)
            s.backed.discard(rid)
            s.leaving.discard(rid)
            if rid in s.video_left and excluded != FOLLOW_EXCLUDED_VIDEO and presence == EMPTY:
                s.video_left.pop(rid, None)  # video over, nobody there: no resume
            if excluded:
                room.follow.set_state(excluded)
            elif presence == PRESENT and (group_playing or rid in s.video_left):
                await self._join(room, group)
                if not group_playing:
                    await self._resume(group)
                room.follow.set_state(FOLLOW_ACTIVE)
            elif presence == UNKNOWN:
                room.follow.set_state(FOLLOW_UNKNOWN)
            else:
                room.follow.set_state(FOLLOW_WAITING)

        for room in get_rooms(self.hass).values():
            if room not in rooms:
                room.follow.set_prewarm(False)

    # --------------------------------------------------------------- actions

    async def _call(self, service: str, data: dict) -> bool:
        try:
            await self.hass.services.async_call(MP_DOMAIN, service, data, blocking=True, context=Context())
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("media_player.%s %s failed: %s", service, data, err)
            return False
        return True

    @callback
    def _spawn(self, coro) -> None:
        if self._stopped:
            coro.close()
            return
        task = self.hass.async_create_task(coro, eager_start=False)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @callback
    def _save(self) -> None:
        self._save_later()

    @callback
    def _save_later(self) -> None:
        self._store.async_delay_save(lambda: self.data, 10)

    def _due(self, target: str, action: str) -> bool:
        """True if this command was not sent within the repeat window; marks it sent."""
        now = clock.now()
        last = self._commands.get((target, action))
        if last is not None and now - last < COMMAND_REPEAT_SECONDS:
            return False
        self._commands[(target, action)] = now
        # an opposite command lifts the block (leave, then come back right away)
        if action == "unjoin":
            for key in [k for k in self._commands if k[0] == target and k[1].startswith("join:")]:
                del self._commands[key]
        elif action.startswith("join:"):
            self._commands.pop((target, "unjoin"), None)
        return True

    async def _join(
        self,
        room: RoomController,
        group: str,
        volume_override: float | None = None,
        keep_volume: bool = False,
    ) -> bool:
        player = room.audio_player
        if not self._due(player, f"join:{group}"):
            return True  # MA has not reported the last join yet
        src = room.sources[SOURCE_AUDIO]
        if room.power_controlled(src) and room.player_state(src) == STATE_OFF:
            await room._player(src, SERVICE_TURN_ON)  # its own on/off (e.g. Music Assistant's fake power)
        if keep_volume:
            _LOGGER.info("Follow: %s joins %s", room.entry.title, group)
            return await self.ma.join(group, player)
        if volume_override is not None:
            volume = volume_override * 100  # a wake room: its start volume
        else:
            volume = max(0.0, min(100.0, self.volume.base_volume(group) + room.follow.volume_offset))
        volume = room.night.player_limit(volume / 100) * 100
        _LOGGER.info("Follow: %s joins %s at %.0f%%", room.entry.title, group, volume)
        room.volume.note_own(player, volume / 100)  # follow's volume, not a wish for the room
        room.volume.async_cancel_fade(player)  # the group's volume from now on
        await self._call("volume_set", {ATTR_ENTITY_ID: player, "volume_level": volume / 100})
        return await self.ma.join(group, player)

    async def _resume(self, group: str) -> None:
        if self._due(group, "play"):
            await self.ma.resume(group)

    async def _unjoin(self, player: str, why: str) -> None:
        if not self._due(player, "unjoin"):
            return
        _LOGGER.info("Follow: %s leaves its group (%s)", player, why)
        await self.ma.unjoin(player)

    async def _leave(self, room: RoomController, why: str) -> None:
        # stays 'backed' until MA really removed it, so it can never turn into an override
        self.session.leaving.add(room.entry.entry_id)
        self.session.empty_since.pop(room.entry.entry_id, None)
        await self._unjoin(room.audio_player, f"{room.entry.title}: {why}")

    @callback
    def room_turned_off(self, room: RoomController) -> None:
        """'Room off' during a session: keep the room out until the session ends."""
        if self.session.group is not None:
            self.session.opted_out.add(room.entry.entry_id)

    # ---------------------------------------------------------------- status

    @property
    def follow_members(self) -> list[str]:
        if not self.active:
            return []
        return [r.entry.title for r in self._member_rooms(self.follow_player)]
