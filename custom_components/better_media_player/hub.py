"""Central settings entry: group policies and "follow me".

The hub knows the Music Assistant group players and their policy:

always  room players playing in this group power their speakers (v0.1 behaviour)
follow  the group follows the people: while it plays, all follow rooms are
        prewarmed (speakers on) and rooms join / leave by occupancy
ignore  playback in this group does not power anything (default for unlisted
        groups once a hub exists)

Follow rules (agreed in the design discussion):
- join when presence is detected, volume set first: base volume + room offset
- leave after the room's presence run-down (counted from when it became both
  member and empty); the last leave makes Music Assistant pause the group
- never take a room away from another group or from direct playback
- rooms with video (Kodi), quiet mode, or turned off this session are left out;
  after video ends the room comes back (and resumes a group it left paused)
- presence unknown (sensor unavailable) changes nothing
- members that are not backed by presence (manual add, fallback start, resume)
  are kept while nobody is detected in any eligible room ("override")
- HA restart while playing: members are kept, no overrides
- only one follow group plays at a time; older ones are emptied (they pause)

Session state lives in hass.data so that reloading the central entry (e.g. after
changing its options) does not lose it.
"""

from __future__ import annotations

import asyncio
import logging
import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components.media_player import DOMAIN as MP_DOMAIN
from homeassistant.components.media_player import MediaPlayerState
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Context, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ALWAYS_GROUPS,
    CONF_DEFAULT_VOLUME,
    CONF_FOLLOW_GROUPS,
    DOMAIN,
    FOLLOW_ACTIVE,
    FOLLOW_EXCLUDED_BUSY,
    FOLLOW_EXCLUDED_OFF,
    FOLLOW_EXCLUDED_QUIET,
    FOLLOW_EXCLUDED_VIDEO,
    FOLLOW_INACTIVE,
    FOLLOW_OVERRIDE,
    FOLLOW_UNKNOWN,
    FOLLOW_WAITING,
    HUB_DEFAULTS,
    POLICY_ALWAYS,
    POLICY_FOLLOW,
    POLICY_IGNORE,
    STORAGE_VERSION,
    TICK_SECONDS,
)

if TYPE_CHECKING:
    from .controller import RoomController

_LOGGER = logging.getLogger(__name__)

PLAYING = {MediaPlayerState.PLAYING, MediaPlayerState.BUFFERING}
UNCERTAIN = {None, STATE_UNAVAILABLE, STATE_UNKNOWN}
HARD_EXCLUSIONS = (FOLLOW_EXCLUDED_QUIET, FOLLOW_EXCLUDED_VIDEO, FOLLOW_EXCLUDED_OFF)

PRESENT = "present"
EMPTY = "empty"
UNKNOWN = "unknown"

COMMAND_REPEAT_SECONDS = 30  # do not repeat the same join/unjoin within this time
START_GRACE_SECONDS = 30  # after start_follow, MA may report "playing" late


def _now() -> float:
    return dt_util.utcnow().timestamp()


def get_hub(hass: HomeAssistant) -> FollowManager | None:
    return hass.data.get(DOMAIN, {}).get("hub")


def get_rooms(hass: HomeAssistant) -> dict[str, RoomController]:
    return hass.data.setdefault(DOMAIN, {}).setdefault("rooms", {})


@dataclass
class FollowSession:
    """State of the follow group that currently plays (survives hub reloads)."""

    group: str | None = None
    started_at: float = 0.0
    backed: set[str] = field(default_factory=set)  # members backed by presence
    opted_out: set[str] = field(default_factory=set)  # turned off this session
    video_left: set[str] = field(default_factory=set)  # left because of video
    member_since: dict[str, float] = field(default_factory=dict)
    empty_since: dict[str, float] = field(default_factory=dict)

    def reset(self, group: str | None) -> None:
        self.group = group
        self.started_at = _now()
        self.backed.clear()
        self.opted_out.clear()
        self.video_left.clear()
        self.member_since.clear()
        self.empty_since.clear()


class FollowManager:
    """Brain of the central entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.opts: dict[str, Any] = {**HUB_DEFAULTS, **entry.options}
        self.follow_groups: list[str] = list(self.opts.get(CONF_FOLLOW_GROUPS) or [])
        self.always_groups: list[str] = list(self.opts.get(CONF_ALWAYS_GROUPS) or [])
        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self.data: dict[str, Any] = {}
        self.session: FollowSession = hass.data.setdefault(DOMAIN, {}).setdefault("session", FollowSession())
        self._lock = asyncio.Lock()
        self._unsubs: list[Callable[[], None]] = []
        self._track_unsub: Callable[[], None] | None = None
        self._listeners: list[Callable[[], None]] = []
        self._tasks: set[asyncio.Task] = set()
        self._queued = False
        self._started = False
        self._stopped = False
        self._playing_since: dict[str, int] = {}  # start order, newest = highest
        self._start_counter = 0
        self._commands: dict[str, tuple[str, float]] = {}
        # groups that may already have been playing before HA (re)started
        self._maybe_restored: set[str] = set()
        self.active_group: str | None = None

    # ------------------------------------------------------------------ setup

    async def async_start(self) -> None:
        self.data = {"volumes": {}, **(await self._store.async_load() or {})}
        for group in self.follow_groups:
            if self.group_state(group) in PLAYING | UNCERTAIN and self.session.group != group:
                self._maybe_restored.add(group)
        self._unsubs.append(
            async_track_time_interval(self.hass, self._on_tick, timedelta(seconds=TICK_SECONDS))
        )
        self.async_refresh_tracking()

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
        for task in list(self._tasks):
            task.cancel()
        for room in get_rooms(self.hass).values():
            room.set_prewarm(False)
            room.set_follow_state(FOLLOW_INACTIVE)
        await self._store.async_save(self.data)

    @callback
    def async_refresh_tracking(self) -> None:
        """(Re)subscribe to groups, room players and occupancy sensors."""
        if self._stopped:
            return
        if self._track_unsub:
            self._track_unsub()
        tracked = list(self.follow_groups) + list(self.always_groups)
        for room in get_rooms(self.hass).values():
            tracked += room.follow_tracked_entities
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
        entity_id = event.data["entity_id"]
        new, old = event.data["new_state"], event.data["old_state"]
        if entity_id in self.follow_groups and new is not None:
            if new.state in PLAYING and (old is None or old.state not in PLAYING):
                self._mark_started(entity_id)
            if new.state not in PLAYING | UNCERTAIN:
                self._maybe_restored.discard(entity_id)  # seen stopped: next start is new
        self.async_schedule(entity_id)

    def _mark_started(self, group: str) -> None:
        self._start_counter += 1
        self._playing_since[group] = self._start_counter

    @callback
    def _on_tick(self, _now: Any) -> None:
        self.async_schedule("tick")

    # ---------------------------------------------------------------- policy

    def policy(self, group: str) -> str:
        if group in self.follow_groups:
            return POLICY_FOLLOW
        if group in self.always_groups:
            return POLICY_ALWAYS
        return POLICY_IGNORE

    def group_members(self, group: str | None) -> list[str]:
        state = self.hass.states.get(group) if group else None
        return list(state.attributes.get("group_members") or []) if state else []

    def group_state(self, group: str | None) -> str | None:
        state = self.hass.states.get(group) if group else None
        return state.state if state else None

    def _member_rooms(self, group: str) -> list[RoomController]:
        members = self.group_members(group)
        return [r for r in self.follow_rooms() if r.audio_player in members]

    def base_volume(self, group: str) -> float:
        """Group volume without the room offsets, in %.

        Music Assistant reports the group volume as the members' average, which
        already contains their offsets; subtracting the average offset keeps the
        base stable from session to session.
        """
        state = self.hass.states.get(group)
        vol = state.attributes.get("volume_level") if state else None
        rooms = self._member_rooms(group)
        if vol is not None and float(vol) > 0 and self.group_members(group):
            offset = statistics.fmean([r.volume_offset for r in rooms]) if rooms else 0.0
            return max(0.0, min(100.0, float(vol) * 100 - offset))
        stored = self.data.get("volumes", {}).get(group)
        if stored:
            return float(stored)
        return float(self.opts[CONF_DEFAULT_VOLUME])

    def _remember_volume(self, group: str) -> None:
        state = self.hass.states.get(group)
        vol = state.attributes.get("volume_level") if state else None
        if vol is None or float(vol) <= 0 or not self.group_members(group):
            return
        base = round(self.base_volume(group), 1)
        if self.data["volumes"].get(group) != base:
            self.data["volumes"][group] = base
            self._store.async_delay_save(lambda: self.data, 10)

    # ----------------------------------------------------------------- rooms

    def follow_rooms(self) -> list[RoomController]:
        return [r for r in get_rooms(self.hass).values() if r.follow_capable]

    def room_exclusion(self, room: RoomController, group: str) -> str | None:
        """Why this room cannot take part in the follow session right now."""
        if room.quiet:
            return FOLLOW_EXCLUDED_QUIET
        if room.video_active:
            return FOLLOW_EXCLUDED_VIDEO
        if self.session.group == group and room.entry.entry_id in self.session.opted_out:
            return FOLLOW_EXCLUDED_OFF
        player = room.audio_player
        if player is None:
            return FOLLOW_EXCLUDED_BUSY
        # never take a room away from another group or from direct playback
        for other in room.groups_of(player):
            if other != group and other not in self.follow_groups:
                return FOLLOW_EXCLUDED_BUSY
        if player not in self.group_members(group) and room.audio_playing_directly:
            return FOLLOW_EXCLUDED_BUSY
        return None

    # -------------------------------------------------------------- evaluation

    async def async_update(self, reason: str = "") -> None:
        async with self._lock:
            self._queued = False
            await self._evaluate()
        self._notify()

    def _resumable(self, group: str) -> bool:
        """Paused and empty only because the last room left for video."""
        return (
            self.session.group == group
            and self.group_state(group) == MediaPlayerState.PAUSED
            and not self.group_members(group)
            and bool(self.session.video_left)
        )

    async def _evaluate(self) -> None:
        now = _now()
        s = self.session
        rooms = self.follow_rooms()
        playing = [g for g in self.follow_groups if self.group_state(g) in PLAYING]
        group: str | None = None
        if playing:
            group = max(playing, key=lambda g: self._playing_since.get(g, 0))
        elif s.group in self.follow_groups and self._resumable(s.group):
            group = s.group

        for g in self.follow_groups:
            self._remember_volume(g)

        # session end
        if s.group and s.group != group:
            keep = (
                self.group_state(s.group) in UNCERTAIN  # MA reconnecting: no decisions
                or now - s.started_at < START_GRACE_SECONDS  # MA reports the start late
            )
            if keep and not playing:
                self.active_group = s.group
                return
            if s.group not in playing:
                _LOGGER.debug("Follow session of %s ended", s.group)
                s.reset(None)
        # session start
        if group is not None and s.group != group:
            restored = group in self._maybe_restored
            self._maybe_restored.discard(group)
            _LOGGER.info("Follow session started on %s%s", group, " (restored)" if restored else "")
            s.reset(group)
            if restored:
                s.backed = {r.entry.entry_id for r in self._member_rooms(group)}
        self.active_group = group

        # only one follow group at a time: empty the older ones (they pause)
        for other in playing:
            if other != group:
                for member in self.group_members(other):
                    await self._unjoin(member, f"{other} replaced by {group}")

        if group is None:
            for room in get_rooms(self.hass).values():
                room.set_prewarm(False)
                room.set_follow_state(FOLLOW_INACTIVE)
            return

        group_playing = self.group_state(group) in PLAYING
        members = self.group_members(group)
        presences = {r.entry.entry_id: r.presence() for r in rooms}
        exclusions = {r.entry.entry_id: self.room_exclusion(r, group) for r in rooms}
        anyone = any(
            presences[rid] == PRESENT and exclusions[rid] is None for rid in presences
        )

        for room in rooms:
            rid = room.entry.entry_id
            presence = presences[rid]
            excluded = exclusions[rid]
            member = room.audio_player in members

            room.set_prewarm(group_playing and room.prewarm_enabled and excluded not in HARD_EXCLUSIONS)

            if member:
                s.member_since.setdefault(rid, now)
                if excluded in HARD_EXCLUSIONS:
                    if excluded == FOLLOW_EXCLUDED_VIDEO:
                        s.video_left.add(rid)
                    await self._leave(room, f"room {excluded}")
                    room.set_follow_state(excluded)
                    continue
                if presence == PRESENT:
                    s.backed.add(rid)
                    s.empty_since.pop(rid, None)
                    room.set_follow_state(FOLLOW_ACTIVE)
                    continue
                if presence == UNKNOWN:
                    s.empty_since.pop(rid, None)
                    room.set_follow_state(FOLLOW_UNKNOWN)
                    continue
                # member, nobody there
                if rid not in s.backed:
                    if not anyone:
                        room.set_follow_state(FOLLOW_OVERRIDE)
                        continue
                    # override ends once presence is seen elsewhere: normal rules from now
                    s.backed.add(rid)
                    s.empty_since[rid] = now
                since = max(s.empty_since.setdefault(rid, now), s.member_since[rid])
                if now - since >= room.presence_run_down:
                    await self._leave(room, "nobody there")
                    room.set_follow_state(FOLLOW_WAITING)
                else:
                    room.set_follow_state(FOLLOW_ACTIVE)
                continue

            # not a member
            s.member_since.pop(rid, None)
            s.empty_since.pop(rid, None)
            s.backed.discard(rid)
            if rid in s.video_left and excluded != FOLLOW_EXCLUDED_VIDEO and presence != PRESENT:
                s.video_left.discard(rid)
            if excluded:
                room.set_follow_state(excluded)
            elif presence == PRESENT and (group_playing or rid in s.video_left):
                await self._join(room, group)
                s.backed.add(rid)
                if not group_playing:
                    await self._call("media_play", {ATTR_ENTITY_ID: group})
                s.video_left.discard(rid)
                room.set_follow_state(FOLLOW_ACTIVE)
            elif presence == UNKNOWN:
                room.set_follow_state(FOLLOW_UNKNOWN)
            else:
                room.set_follow_state(FOLLOW_WAITING)

        for room in get_rooms(self.hass).values():
            if room not in rooms:
                room.set_prewarm(False)

    # --------------------------------------------------------------- actions

    async def _call(self, service: str, data: dict) -> bool:
        try:
            await self.hass.services.async_call(MP_DOMAIN, service, data, blocking=True, context=Context())
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("media_player.%s %s failed: %s", service, data, err)
            return False
        return True

    def _recent(self, player: str, action: str) -> bool:
        last = self._commands.get(player)
        return last is not None and last[0] == action and _now() - last[1] < COMMAND_REPEAT_SECONDS

    async def _join(self, room: RoomController, group: str, base: float | None = None) -> None:
        player = room.audio_player
        if self._recent(player, f"join:{group}"):
            return  # MA has not reported the last join yet
        self._commands[player] = (f"join:{group}", _now())
        base = self.base_volume(group) if base is None else base
        volume = max(0.0, min(100.0, base + room.volume_offset))
        _LOGGER.info("Follow: %s joins %s at %.0f%%", room.entry.title, group, volume)
        await self._call("volume_set", {ATTR_ENTITY_ID: player, "volume_level": volume / 100})
        await self._call("join", {ATTR_ENTITY_ID: group, "group_members": [player]})

    async def _unjoin(self, player: str, why: str) -> None:
        if self._recent(player, "unjoin"):
            return
        self._commands[player] = ("unjoin", _now())
        _LOGGER.info("Follow: %s leaves its group (%s)", player, why)
        await self._call("unjoin", {ATTR_ENTITY_ID: player})

    async def _leave(self, room: RoomController, why: str) -> None:
        rid = room.entry.entry_id
        self.session.empty_since.pop(rid, None)
        self.session.backed.discard(rid)
        await self._unjoin(room.audio_player, f"{room.entry.title}: {why}")

    @callback
    def room_turned_off(self, room: RoomController) -> None:
        """'Room off' during a session: keep the room out until the session ends."""
        if self.session.group is not None:
            self.session.opted_out.add(room.entry.entry_id)

    async def async_start_follow(
        self,
        group: str | None = None,
        volume: float | None = None,
        media_content_id: str | None = None,
        media_content_type: str | None = None,
        fallback_rooms: list[RoomController] | None = None,
    ) -> None:
        """Start (or extend) a follow session: join present rooms, then play."""
        group = group or (self.follow_groups[0] if self.follow_groups else None)
        if group is None or group not in self.follow_groups:
            raise HomeAssistantError(f"{group} is not configured as follow group")
        s = self.session
        async with self._lock:
            base = None
            if volume is not None:
                base = round(float(volume) * 100, 1)
                self.data["volumes"][group] = base
            if s.group != group:
                s.reset(group)  # new session (a running one keeps its 'room off' rooms)
            self._maybe_restored.discard(group)
            s.started_at = _now()
            members = self.group_members(group)
            eligible = [r for r in self.follow_rooms() if self.room_exclusion(r, group) is None]
            targets = [r for r in eligible if r.presence() == PRESENT]
            for room in targets:
                s.backed.add(room.entry.entry_id)
            if not targets and not members:
                capable = [r for r in (fallback_rooms or []) if r.follow_capable]
                if len(capable) < len(fallback_rooms or []):
                    _LOGGER.warning("Fallback rooms without occupancy sensors are ignored")
                targets = [r for r in capable if self.room_exclusion(r, group) is None]
            if not targets and not members:
                raise HomeAssistantError("No room to start follow in: nobody detected and no fallback room")
            for room in targets:
                if room.audio_player not in members:
                    await self._join(room, group, base)
            self._mark_started(group)
            if media_content_id:
                ok = await self._call(
                    "play_media",
                    {
                        ATTR_ENTITY_ID: group,
                        "media_content_id": media_content_id,
                        "media_content_type": media_content_type or "music",
                    },
                )
            else:
                ok = await self._call("media_play", {ATTR_ENTITY_ID: group})
            if not ok:
                raise HomeAssistantError(f"Could not start playback on {group}")
        self.async_schedule("start_follow")

    # ---------------------------------------------------------------- status

    @property
    def follow_members(self) -> list[str]:
        if self.active_group is None:
            return []
        return [r.entry.title for r in self._member_rooms(self.active_group)]
