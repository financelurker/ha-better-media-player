"""Central settings entry: group policies and "follow me".

The hub knows the Music Assistant group players and their policy:

always  room players playing in this group power their speakers (v0.1 behaviour)
follow  the group follows the people: while it plays, all follow rooms are
        prewarmed (speakers on) and rooms join / leave by occupancy
ignore  playback in this group does not power anything (default for unlisted
        groups once a hub exists)

Follow rules (agreed in the design discussion):
- join when presence is detected, volume set first: group volume + room offset
- leave after the room's presence run-down; the last leave makes Music
  Assistant pause the group ("house empty")
- never take a room away from another group or from direct playback
- rooms with video (Kodi) or quiet mode are left out; a room turned off during a
  session stays out until the session ends
- presence unknown (sensor unavailable) changes nothing
- rooms that are members without presence (manual or fallback start) stay while
  no presence is detected anywhere ("override")
- only one follow group plays at a time
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components.media_player import DOMAIN as MP_DOMAIN
from homeassistant.components.media_player import MediaPlayerState
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import (
    Context,
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
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
SESSION = {MediaPlayerState.PLAYING, MediaPlayerState.BUFFERING, MediaPlayerState.PAUSED}

PRESENT = "present"
EMPTY = "empty"
UNKNOWN = "unknown"


def _now() -> float:
    return dt_util.utcnow().timestamp()


def get_hub(hass: HomeAssistant) -> FollowManager | None:
    return hass.data.get(DOMAIN, {}).get("hub")


def get_rooms(hass: HomeAssistant) -> dict[str, RoomController]:
    return hass.data.setdefault(DOMAIN, {}).setdefault("rooms", {})


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
        self._lock = asyncio.Lock()
        self._unsubs: list[Callable[[], None]] = []
        self._track_unsub: Callable[[], None] | None = None
        self._listeners: list[Callable[[], None]] = []
        self._tasks: set[asyncio.Task] = set()
        self._started = False
        self._stopped = False

        # session state of the follow group that currently plays
        self.active_group: str | None = None
        self._session_group: str | None = None
        self._playing_since: dict[str, float] = {}
        self.opted_out: set[str] = set()  # room entry ids turned off this session
        self.override: set[str] = set()  # room entry ids kept without presence
        self._empty_since: dict[str, float] = {}
        self._first_eval = True

    # ------------------------------------------------------------------ setup

    async def async_start(self) -> None:
        self.data = {"volumes": {}, **(await self._store.async_load() or {})}
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
        if self._stopped or not self._started:
            return
        task = self.hass.async_create_task(self.async_update(reason), eager_start=False)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @callback
    def _on_state_event(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        new, old = event.data["new_state"], event.data["old_state"]
        if entity_id in self.follow_groups and new is not None:
            if new.state in PLAYING and (old is None or old.state not in PLAYING):
                self._playing_since[entity_id] = _now()
        self.async_schedule(entity_id)

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

    def group_members(self, group: str) -> list[str]:
        state = self.hass.states.get(group)
        return list(state.attributes.get("group_members") or []) if state else []

    def group_state(self, group: str) -> str | None:
        state = self.hass.states.get(group)
        return state.state if state else None

    def _pick_active_group(self) -> str | None:
        playing = [g for g in self.follow_groups if self.group_state(g) in PLAYING]
        if not playing:
            return None
        return max(playing, key=lambda g: self._playing_since.get(g, 0.0))

    def _session_open(self, group: str | None) -> bool:
        return (
            group is not None
            and self.group_state(group) in SESSION
            and bool(self.group_members(group))
        )

    def group_volume(self, group: str) -> float:
        """Current group volume in %, the last one above 0, or the default."""
        state = self.hass.states.get(group)
        vol = state.attributes.get("volume_level") if state else None
        if vol is not None and float(vol) > 0:
            return float(vol) * 100
        stored = self.data.get("volumes", {}).get(group)
        if stored:
            return float(stored)
        return float(self.opts[CONF_DEFAULT_VOLUME])

    def _remember_volume(self, group: str) -> None:
        state = self.hass.states.get(group)
        vol = state.attributes.get("volume_level") if state else None
        if vol is not None and float(vol) > 0 and self.group_members(group):
            pct = round(float(vol) * 100, 1)
            if self.data["volumes"].get(group) != pct:
                self.data["volumes"][group] = pct
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
        if room.entry.entry_id in self.opted_out:
            return FOLLOW_EXCLUDED_OFF
        player = room.audio_player
        if player is None:
            return FOLLOW_EXCLUDED_BUSY
        # never take a room away from another group or from direct playback
        for other in room.groups_of(player):
            if other != group and not (other in self.follow_groups):
                return FOLLOW_EXCLUDED_BUSY
        if player not in self.group_members(group) and room.audio_playing_directly:
            return FOLLOW_EXCLUDED_BUSY
        return None

    # -------------------------------------------------------------- evaluation

    async def async_update(self, reason: str = "") -> None:
        async with self._lock:
            await self._evaluate()
        self._notify()

    async def _evaluate(self) -> None:
        now = _now()
        rooms = self.follow_rooms()
        group = self._pick_active_group()

        # session bookkeeping (a session lasts while the group plays or is paused)
        if self._session_group and not self._session_open(self._session_group):
            _LOGGER.debug("Follow session of %s ended", self._session_group)
            self._session_group = None
            self.opted_out.clear()
            self.override.clear()
        if group is not None and self._session_group != group:
            self._start_session(group, rooms, restored=self._first_eval)
        self._first_eval = False
        self.active_group = group

        for g in self.follow_groups:
            self._remember_volume(g)

        if group is None:
            for room in rooms:
                room.set_prewarm(False)
                room.set_follow_state(FOLLOW_INACTIVE)
            for room in get_rooms(self.hass).values():
                if room not in rooms:
                    room.set_prewarm(False)
            return

        members = self.group_members(group)
        presences = {r.entry.entry_id: r.presence() for r in rooms}
        anyone = any(p == PRESENT for p in presences.values())
        if anyone:
            self.override.clear()

        for room in rooms:
            rid = room.entry.entry_id
            player = room.audio_player
            presence = presences[rid]
            if presence == EMPTY:
                self._empty_since.setdefault(rid, now)
            else:
                self._empty_since.pop(rid, None)
            member = player in members
            excluded = self.room_exclusion(room, group)

            # prewarm: all follow rooms while the group plays, except quiet/video/off
            room.set_prewarm(
                room.prewarm_enabled
                and excluded not in (FOLLOW_EXCLUDED_QUIET, FOLLOW_EXCLUDED_VIDEO, FOLLOW_EXCLUDED_OFF)
            )

            if member:
                if excluded in (FOLLOW_EXCLUDED_QUIET, FOLLOW_EXCLUDED_VIDEO, FOLLOW_EXCLUDED_OFF):
                    await self._leave(room, group, f"room {excluded}")
                    room.set_follow_state(excluded)
                elif presence == UNKNOWN:
                    room.set_follow_state(FOLLOW_UNKNOWN)
                elif presence == PRESENT:
                    room.set_follow_state(FOLLOW_ACTIVE)
                elif rid in self.override:
                    room.set_follow_state(FOLLOW_OVERRIDE)
                else:
                    since = self._empty_since.get(rid, now)
                    if now - since >= room.presence_run_down:
                        await self._leave(room, group, "nobody there")
                        room.set_follow_state(FOLLOW_WAITING)
                    else:
                        room.set_follow_state(FOLLOW_ACTIVE)
            else:
                if excluded:
                    room.set_follow_state(excluded)
                elif presence == PRESENT:
                    await self._join(room, group)
                    room.set_follow_state(FOLLOW_ACTIVE)
                elif presence == UNKNOWN:
                    room.set_follow_state(FOLLOW_UNKNOWN)
                else:
                    room.set_follow_state(FOLLOW_WAITING)

        for room in get_rooms(self.hass).values():
            if room not in rooms:
                room.set_prewarm(False)

    def _start_session(self, group: str, rooms: list[RoomController], restored: bool) -> None:
        """A follow group started playing (or HA restarted while it plays)."""
        _LOGGER.info("Follow session started on %s", group)
        self._session_group = group
        self.opted_out.clear()
        if restored:
            # HA restart while playing: keep everything, no overrides
            return
        members = self.group_members(group)
        for room in rooms:
            if room.audio_player in members and room.presence() == EMPTY:
                self.override.add(room.entry.entry_id)

    # --------------------------------------------------------------- actions

    async def _call(self, service: str, data: dict) -> None:
        try:
            await self.hass.services.async_call(MP_DOMAIN, service, data, blocking=True, context=Context())
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("media_player.%s %s failed: %s", service, data, err)

    async def _join(self, room: RoomController, group: str) -> None:
        volume = max(0.0, min(100.0, self.group_volume(group) + room.volume_offset))
        _LOGGER.info("Follow: %s joins %s at %.0f%%", room.entry.title, group, volume)
        await self._call("volume_set", {ATTR_ENTITY_ID: room.audio_player, "volume_level": volume / 100})
        await self._call("join", {ATTR_ENTITY_ID: group, "group_members": [room.audio_player]})

    async def _leave(self, room: RoomController, group: str, why: str) -> None:
        _LOGGER.info("Follow: %s leaves %s (%s)", room.entry.title, group, why)
        self._empty_since.pop(room.entry.entry_id, None)
        self.override.discard(room.entry.entry_id)
        await self._call("unjoin", {ATTR_ENTITY_ID: room.audio_player})

    @callback
    def room_turned_off(self, room: RoomController) -> None:
        """'Room off' during a session: keep the room out until the session ends."""
        if self._session_group and room.audio_player in self.group_members(self._session_group):
            self.opted_out.add(room.entry.entry_id)

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
        async with self._lock:
            if volume is not None:
                self.data["volumes"][group] = round(float(volume) * 100, 1)
            rooms = self.follow_rooms()
            members = self.group_members(group)
            candidates = [r for r in rooms if self.room_exclusion(r, group) is None]
            targets = [r for r in candidates if r.presence() == PRESENT]
            override = False
            if not targets and not members:
                targets = [r for r in (fallback_rooms or []) if self.room_exclusion(r, group) is None]
                override = True
            if not targets and not members:
                raise HomeAssistantError("No room to start follow in: nobody detected and no fallback room")
            self._session_group = group
            self.opted_out.clear()
            self._first_eval = False
            for room in targets:
                if room.audio_player in members:
                    continue
                await self._join(room, group)
                if override:
                    self.override.add(room.entry.entry_id)
            self._playing_since[group] = _now()
            if media_content_id:
                await self._call(
                    "play_media",
                    {
                        ATTR_ENTITY_ID: group,
                        "media_content_id": media_content_id,
                        "media_content_type": media_content_type or "music",
                    },
                )
            else:
                await self._call("media_play", {ATTR_ENTITY_ID: group})
        self.async_schedule("start_follow")

    # ---------------------------------------------------------------- status

    @property
    def follow_members(self) -> list[str]:
        if self.active_group is None:
            return []
        members = self.group_members(self.active_group)
        return [r.entry.title for r in self.follow_rooms() if r.audio_player in members]
