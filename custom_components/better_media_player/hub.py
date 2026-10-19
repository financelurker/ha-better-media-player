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
    CONF_NIGHT_FADE_IN,
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
from .ramp import VolumeRamp

if TYPE_CHECKING:
    from .controller import RoomController

_LOGGER = logging.getLogger(__name__)

PLAYING = {MediaPlayerState.PLAYING, MediaPlayerState.BUFFERING}
UNCERTAIN = {None, STATE_UNAVAILABLE, STATE_UNKNOWN}
HARD_EXCLUSIONS = (FOLLOW_EXCLUDED_QUIET, FOLLOW_EXCLUDED_VIDEO, FOLLOW_EXCLUDED_OFF)

PRESENT = "present"
EMPTY = "empty"
UNKNOWN = "unknown"

COMMAND_REPEAT_SECONDS = 30  # do not repeat the same command within this time
START_GRACE_SECONDS = 30  # after start_follow, MA may report "playing" late
VIDEO_RESUME_MAX_SECONDS = 3 * 3600  # resume after video only within this time


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
    started_by_us: bool = False  # via start_follow: MA may report "playing" late
    backed: set[str] = field(default_factory=set)  # members backed by presence
    leaving: set[str] = field(default_factory=set)  # unjoin sent, MA not done yet
    opted_out: set[str] = field(default_factory=set)  # turned off this session
    video_left: dict[str, float] = field(default_factory=dict)  # last room left for video
    member_since: dict[str, float] = field(default_factory=dict)
    empty_since: dict[str, float] = field(default_factory=dict)

    def reset(self, group: str | None, by_us: bool = False) -> None:
        self.group = group
        self.started_at = _now()
        self.started_by_us = by_us
        self.backed.clear()
        self.leaving.clear()
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
        self._commands: dict[tuple[str, str], float] = {}  # (player, action) -> last sent
        # groups that may already have been playing before HA (re)started
        self._maybe_restored: set[str] = set()
        self.active_group: str | None = None
        # groups limited because all their rooms are in night mode
        self._night_capped: set[str] = set()
        self._group_ramps: dict[str, VolumeRamp] = {}
        self._night_limited: dict[str, set[str]] = {}

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
        for ramp in self._group_ramps.values():
            ramp.stop()
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
        members = self.group_members(group)
        faded = self._faded_members(members)
        if faded:
            # a sleep timer is fading a member: the group average is not the real
            # volume; use the others' own volumes, or what was remembered
            vols = []
            for room in get_rooms(self.hass).values():
                if room.audio_player in members and room.audio_player not in faded:
                    v = self._player_volume(room.audio_player)
                    if v is not None and v > 0:
                        vols.append(v * 100 - room.volume_offset)
            if vols:
                return max(0.0, min(100.0, statistics.fmean(vols)))
            vol = None
        if vol is not None and float(vol) > 0 and members:
            by_player = {r.audio_player: r.volume_offset for r in get_rooms(self.hass).values()}
            offset = statistics.fmean([by_player.get(p, 0.0) for p in members])
            return max(0.0, min(100.0, float(vol) * 100 - offset))
        stored = self.data.get("volumes", {}).get(group)
        if stored:
            return float(stored)
        return float(self.opts[CONF_DEFAULT_VOLUME])

    def _faded_members(self, members: list[str]) -> set[str]:
        """Members whose volume is not a wish right now: a sleep timer lowers
        it, or the night mode limits it."""
        return {
            r.audio_player
            for r in get_rooms(self.hass).values()
            if r.audio_player in members
            and (r.sleep.fading_player(r.audio_player) or r.night.cap is not None)
        }

    def _player_volume(self, player: str) -> float | None:
        state = self.hass.states.get(player)
        value = state.attributes.get("volume_level") if state else None
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _remember_volume(self, group: str) -> None:
        state = self.hass.states.get(group)
        vol = state.attributes.get("volume_level") if state else None
        if vol is None or float(vol) <= 0 or not self.group_members(group):
            return
        if self._faded_members(self.group_members(group)):
            return  # the volume is being lowered by a sleep timer, not set by someone
        if self._member_ramping(self.group_members(group)):
            return  # members fading back from the night mode
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
        """Paused because the last room left for video (recently); resume pending.

        Also true while that room is already back in the group but Music Assistant
        has not resumed yet (join reported late, play not yet effective).
        """
        now = _now()
        s = self.session
        if s.group != group or self.group_state(group) != MediaPlayerState.PAUSED:
            return False
        recent = {rid for rid, ts in s.video_left.items() if now - ts < VIDEO_RESUME_MAX_SECONDS}
        if not recent:
            return False
        members = self.group_members(group)
        if not members:
            return True
        member_ids = {r.entry.entry_id for r in self._member_rooms(group)}
        return member_ids <= recent

    def _pick_group(self, playing: list[str], now: float) -> str | None:
        s = self.session
        if (
            s.group in self.follow_groups
            and s.started_by_us
            and now - s.started_at < START_GRACE_SECONDS
        ):
            return s.group  # just started by us; MA may still report the old group
        if playing:
            return max(playing, key=lambda g: self._playing_since.get(g, 0))
        if s.group in self.follow_groups:
            if self._resumable(s.group):
                return s.group
            if self.group_state(s.group) == MediaPlayerState.PAUSED and self.group_members(s.group):
                return s.group  # paused by someone: the session goes on, nothing changes
        return None

    async def _evaluate(self) -> None:
        now = _now()
        s = self.session
        rooms = self.follow_rooms()
        playing = [g for g in self.follow_groups if self.group_state(g) in PLAYING]
        group = self._pick_group(playing, now)

        await self._night_groups()
        for g in self.follow_groups:
            self._remember_volume(g)

        # session end
        if s.group and s.group != group:
            if self.group_state(s.group) in UNCERTAIN and not playing:
                self.active_group = s.group  # MA reconnecting: no decisions
                return
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

        if group is None:
            for room in get_rooms(self.hass).values():
                room.set_prewarm(False)
                room.set_follow_state(FOLLOW_INACTIVE)
            return

        presences = {r.entry.entry_id: r.presence() for r in rooms}
        exclusions = {r.entry.entry_id: self.room_exclusion(r, group) for r in rooms}

        # only one follow group at a time: empty the older ones (they pause). Rooms
        # that are about to join the new group are moved by Music Assistant itself.
        moving = {
            r.audio_player
            for r in rooms
            if presences[r.entry.entry_id] == PRESENT and exclusions[r.entry.entry_id] is None
        }
        for other in self.follow_groups:
            if other != group and self.group_state(other) in PLAYING:
                for member in self.group_members(other):
                    if member not in moving:
                        await self._unjoin(member, f"{other} replaced by {group}")

        group_playing = self.group_state(group) in PLAYING
        resumable = self._resumable(group)
        if not group_playing and not resumable and not (s.started_by_us and now - s.started_at < START_GRACE_SECONDS):
            # paused by someone: membership stays as it is, prewarm runs down
            for room in get_rooms(self.hass).values():
                room.set_prewarm(False)
            return

        members = self.group_members(group)
        anyone = any(presences[rid] == PRESENT and exclusions[rid] is None for rid in presences)

        for room in rooms:
            rid = room.entry.entry_id
            presence = presences[rid]
            excluded = exclusions[rid]
            member = room.audio_player in members

            room.set_prewarm(group_playing and room.prewarm_enabled and excluded not in HARD_EXCLUSIONS)

            if member:
                s.member_since.setdefault(rid, now)
                if excluded in HARD_EXCLUSIONS:
                    if excluded == FOLLOW_EXCLUDED_VIDEO and members == [room.audio_player]:
                        s.video_left[rid] = now  # the last room left for video: resume later
                    await self._leave(room, f"room {excluded}")
                    room.set_follow_state(excluded)
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
                        room.set_follow_state(FOLLOW_WAITING)
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
            s.leaving.discard(rid)
            if rid in s.video_left and excluded != FOLLOW_EXCLUDED_VIDEO and presence == EMPTY:
                s.video_left.pop(rid, None)  # video over, nobody there: no resume
            if excluded:
                room.set_follow_state(excluded)
            elif presence == PRESENT and (group_playing or rid in s.video_left):
                await self._join(room, group)
                if not group_playing:
                    await self._resume(group)
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

    @callback
    def _spawn(self, coro) -> None:
        if self._stopped:
            coro.close()
            return
        task = self.hass.async_create_task(coro, eager_start=False)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _night_groups(self) -> None:
        """Night mode and the hub's groups.

        - A group whose players all belong to rooms in night mode is limited
          as a whole (to the highest of their limits; each member is limited
          to its own room's anyway).
        - A member whose room leaves the night mode fades back to the group's
          volume for it (follow: the remembered base plus the room's offset;
          always groups: the group's volume from before the night), at once
          if the group does not play. The group volume itself is not raised:
          that would raise the members still in night mode as well.
        """
        by_player = {r.audio_player: r for r in get_rooms(self.hass).values() if r.audio_player}
        free: dict[str, float] = self.data.setdefault("free_volumes", {})
        for group in dict.fromkeys([*self.follow_groups, *self.always_groups]):
            members = self.group_members(group)
            limited = {p for p in members if p in by_player and by_player[p].night.cap is not None}
            was = self._night_limited.get(group, set())
            self._night_limited[group] = limited
            caps = [by_player[p].night.cap if p in by_player else None for p in members]
            cap = max(caps) if members and all(c is not None for c in caps) else None
            vol = self._player_volume(group)
            if cap is not None:
                if vol is not None and vol > cap + 0.011 and self._due(group, "night_cap"):
                    _LOGGER.debug("%s limited to %.2f (night mode)", group, cap)
                    await self._call("volume_set", {ATTR_ENTITY_ID: group, "volume_level": cap})
            elif (
                not limited
                and not self._member_ramping(members)
                and members
                and vol is not None
                and vol > 0
                and free.get(group) != vol
            ):
                free[group] = vol  # the group's volume while nobody limits it
                self._save_later()
            for player in (was - limited) & set(members):
                await self._member_back(group, by_player[player], free.get(group))

    async def _member_back(self, group: str, room: RoomController, free: float | None) -> None:
        """The member's room left the night mode: back to the group's volume."""
        base = self.data.get("volumes", {}).get(group) if group in self.follow_groups else None
        if base is not None:
            target = max(0.0, min(100.0, float(base) + room.volume_offset)) / 100
        elif free is not None:
            target = free
        else:
            return  # nothing known: stays
        player = room.audio_player
        seconds = float(room.opts.get(CONF_NIGHT_FADE_IN, 0)) * 60 if self.group_state(group) in PLAYING else 0
        ramp = self._group_ramps.get(player)
        if ramp is None:
            ramp = self._group_ramps[player] = VolumeRamp(self.hass, player, self._member_setter(room), self._spawn)
        ramp.start(target, seconds)

    def _member_setter(self, room: RoomController):
        async def _set(volume: float) -> None:
            room.volume.note_own(room.audio_player, volume)  # the group's volume, not a room wish
            await self._call("volume_set", {ATTR_ENTITY_ID: room.audio_player, "volume_level": volume})

        return _set

    def _member_ramping(self, members: list[str]) -> bool:
        return any((r := self._group_ramps.get(p)) is not None and r.running for p in members)

    def manages(self, group: str) -> bool:
        return group in self.follow_groups or group in self.always_groups

    @callback
    def _save_later(self) -> None:
        self._store.async_delay_save(lambda: self.data, 10)

    def _due(self, target: str, action: str) -> bool:
        """True if this command was not sent within the repeat window; marks it sent."""
        now = _now()
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

    async def _join(self, room: RoomController, group: str, base: float | None = None) -> None:
        player = room.audio_player
        if not self._due(player, f"join:{group}"):
            return  # MA has not reported the last join yet
        base = self.base_volume(group) if base is None else base
        volume = max(0.0, min(100.0, base + room.volume_offset))
        volume = room.night.limit(volume / 100) * 100
        _LOGGER.info("Follow: %s joins %s at %.0f%%", room.entry.title, group, volume)
        room.volume.note_own(player, volume / 100)  # follow's volume, not a wish for the room
        await self._call("volume_set", {ATTR_ENTITY_ID: player, "volume_level": volume / 100})
        await self._call("join", {ATTR_ENTITY_ID: group, "group_members": [player]})

    async def _resume(self, group: str) -> None:
        if self._due(group, "play"):
            await self._call("media_play", {ATTR_ENTITY_ID: group})

    async def _unjoin(self, player: str, why: str) -> None:
        if not self._due(player, "unjoin"):
            return
        _LOGGER.info("Follow: %s leaves its group (%s)", player, why)
        await self._call("unjoin", {ATTR_ENTITY_ID: player})

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
                s.reset(group, by_us=True)  # new session (a running one keeps its 'room off' rooms)
            self._maybe_restored.discard(group)
            s.started_at = _now()
            s.started_by_us = True
            members = self.group_members(group)
            eligible = [r for r in self.follow_rooms() if self.room_exclusion(r, group) is None]
            targets = [r for r in eligible if r.presence() == PRESENT]
            for room in targets:
                s.backed.add(room.entry.entry_id)
            if not targets:
                # started or resumed while nobody is detected: current members are kept
                # as override until presence shows up somewhere
                for room in self._member_rooms(group):
                    s.backed.discard(room.entry.entry_id)
                    s.empty_since.pop(room.entry.entry_id, None)
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
