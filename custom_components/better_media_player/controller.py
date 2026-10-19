"""Control logic of Better Media Player.

One controller per room (config entry). It watches the room's media players and
power devices, decides which power devices are needed ("demand"), switches them
in a safe order and keeps a room-level status.

Terminology
-----------
source   A media player of the room. "video" (e.g. Kodi) needs display + audio,
         "audio" (e.g. a Music Assistant / sendspin player) needs audio only.
role     A group of power devices with the same job: display, audio, bass and
         the optional host plugs that power a player's own hardware.
demand   A role is demanded while a source that needs it is active.
run-down After the demand ends, a role stays on for its run-down time.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.components.media_player import (
    DOMAIN as MP_DOMAIN,
)
from homeassistant.components.media_player import (
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import (
    Context,
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AUDIO_ON_WAIT,
    CONF_AUDIO_POWER,
    CONF_AUDIO_STANDBY,
    CONF_AUDIO_STANDBY_MAX,
    CONF_AUDIO_STANDBY_W,
    CONF_BASS,
    CONF_DISPLAY,
    CONF_DISPLAY_ON_WAIT,
    CONF_DISPLAY_POWER,
    CONF_DISPLAY_STANDBY,
    CONF_DISPLAY_STANDBY_MAX,
    CONF_DISPLAY_STANDBY_W,
    CONF_HOST_ON_WAIT,
    CONF_OCCUPANCY,
    CONF_PRESENCE_RUN_DOWN,
    CONF_PREWARM,
    CONF_SPEAKERS,
    CONF_START_TIMEOUT,
    CONF_VOLUME_OFFSET,
    DEFAULTS,
    DOMAIN,
    FOLLOW_INACTIVE,
    POLICY_FOLLOW,
    POLICY_IGNORE,
    ROLE_AUDIO,
    ROLE_AUDIO_HOST,
    ROLE_BASS,
    ROLE_DISPLAY,
    ROLE_VIDEO_HOST,
    SOURCE_AUDIO,
    SOURCE_CONF,
    SOURCE_VIDEO,
    SOURCES,
    STANDBY_HOLD_SECONDS,
    STATUS_DISABLED,
    STATUS_ERROR,
    STATUS_MANUAL,
    STATUS_OFF,
    STATUS_ON,
    STATUS_PLAYING,
    STATUS_RUN_DOWN,
    STATUS_STARTING,
    STATUS_STOPPING,
    STORAGE_VERSION,
    TICK_SECONDS,
)
from .hub import get_hub, get_rooms
from .sleep import REASON_ROOM_OFF, SleepTimer
from .night import NightMode
from .volume import RoomVolume
from .wake import PHASE_STARTING, REASON_ROOM_OFF as WAKE_ROOM_OFF, WakeAlarm

_LOGGER = logging.getLogger(__name__)

# Order in which roles are switched on / off
ON_ORDER = (ROLE_VIDEO_HOST, ROLE_AUDIO_HOST, ROLE_DISPLAY, ROLE_AUDIO, ROLE_BASS)
OFF_ORDER = (ROLE_BASS, ROLE_AUDIO, ROLE_DISPLAY, ROLE_VIDEO_HOST, ROLE_AUDIO_HOST)

COMMAND_RETRY_SECONDS = 30
OWN_CHANGE_SECONDS = 30
OWN_CONTEXTS = 50

PLAYING_STATES = {MediaPlayerState.PLAYING, MediaPlayerState.BUFFERING}
POWERED_STATES = {
    MediaPlayerState.ON,
    MediaPlayerState.IDLE,
    MediaPlayerState.PLAYING,
    MediaPlayerState.PAUSED,
    MediaPlayerState.BUFFERING,
}
IDLE_STATES = {MediaPlayerState.ON, MediaPlayerState.IDLE}
GROUP_ACTIVE_STATES = {MediaPlayerState.PLAYING, MediaPlayerState.PAUSED, MediaPlayerState.BUFFERING}
# a suppressed source is released only when it has really stopped
VIDEO_STOPPED = {STATE_OFF, MediaPlayerState.STANDBY}
AUDIO_STOPPED = {STATE_OFF, MediaPlayerState.STANDBY, MediaPlayerState.IDLE, MediaPlayerState.ON, MediaPlayerState.PAUSED}
NOT_AVAILABLE = {STATE_UNAVAILABLE, STATE_UNKNOWN}


def _hub(hass: HomeAssistant):
    return get_hub(hass)


def _now() -> float:
    return dt_util.utcnow().timestamp()


def _float(state: State | None) -> float | None:
    if state is None or state.state in NOT_AVAILABLE:
        return None
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return None


@dataclass
class Role:
    """Power devices with the same job."""

    key: str
    entities: list[str]
    sources: tuple[str, ...]  # sources whose activity demands this role
    run_down_key: str  # key in controller.data (minutes)
    on_wait: float = 0.0
    power: list[str] | None = None
    standby: bool = False
    standby_w: float = 0.0
    standby_max: float = 0.0  # minutes
    # runtime
    last_demand: float | None = None
    force_off: bool = False
    stop_since: float | None = None
    low_since: float | None = None


@dataclass
class Source:
    """A media player of the room."""

    key: str
    player: str
    on_action: str | None
    off_action: str | None
    host: str | None
    # runtime
    suppressed: bool = False
    idle_since: float | None = None


class RoomController:
    """Brain of a room."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.opts: dict[str, Any] = {**DEFAULTS, **entry.data, **entry.options}
        o = self.opts

        self.sources: dict[str, Source] = {}
        for key in SOURCES:
            player_k, on_k, off_k, host_k = SOURCE_CONF[key]
            if o.get(player_k):
                self.sources[key] = Source(
                    key=key,
                    player=o[player_k],
                    on_action=o.get(on_k),
                    off_action=o.get(off_k),
                    host=o.get(host_k),
                )

        self.roles: dict[str, Role] = {}
        both = tuple(s for s in (SOURCE_VIDEO, SOURCE_AUDIO) if s in self.sources)
        if o.get(CONF_DISPLAY):
            self.roles[ROLE_DISPLAY] = Role(
                key=ROLE_DISPLAY,
                entities=list(o[CONF_DISPLAY]),
                sources=(SOURCE_VIDEO,),
                run_down_key="display_run_down",
                on_wait=float(o[CONF_DISPLAY_ON_WAIT]),
                power=list(o.get(CONF_DISPLAY_POWER) or []),
                standby=bool(o[CONF_DISPLAY_STANDBY]) and bool(o.get(CONF_DISPLAY_POWER)),
                standby_w=float(o[CONF_DISPLAY_STANDBY_W]),
                standby_max=float(o[CONF_DISPLAY_STANDBY_MAX]),
            )
        if o.get(CONF_SPEAKERS):
            self.roles[ROLE_AUDIO] = Role(
                key=ROLE_AUDIO,
                entities=list(o[CONF_SPEAKERS]),
                sources=both,
                run_down_key="audio_run_down",
                on_wait=float(o[CONF_AUDIO_ON_WAIT]),
                power=list(o.get(CONF_AUDIO_POWER) or []),
                standby=bool(o[CONF_AUDIO_STANDBY]) and bool(o.get(CONF_AUDIO_POWER)),
                standby_w=float(o[CONF_AUDIO_STANDBY_W]),
                standby_max=float(o[CONF_AUDIO_STANDBY_MAX]),
            )
        if o.get(CONF_BASS):
            self.roles[ROLE_BASS] = Role(
                key=ROLE_BASS,
                entities=list(o[CONF_BASS]),
                sources=both,
                run_down_key="audio_run_down",
            )
        for role_key, src_key in ((ROLE_VIDEO_HOST, SOURCE_VIDEO), (ROLE_AUDIO_HOST, SOURCE_AUDIO)):
            src = self.sources.get(src_key)
            if src is not None and src.host:
                self.roles[role_key] = Role(
                    key=role_key,
                    entities=[src.host],
                    sources=(src_key,),
                    run_down_key="host_run_down",
                    on_wait=float(o[CONF_HOST_ON_WAIT]),
                )

        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self.data: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._changed = asyncio.Event()
        self._unsubs: list[Callable[[], None]] = []
        self._listeners: list[Callable[[], None]] = []
        self._started = False
        self._stopped = False
        self._seq_task: asyncio.Task | None = None
        self._side_tasks: set[asyncio.Task] = set()
        self._own_contexts: deque[str] = deque(maxlen=OWN_CONTEXTS)
        self._last_command: dict[str, tuple[bool, float]] = {}
        # recent commands per entity, to recognise late device reports as our own
        self._recent_commands: dict[str, deque[tuple[bool, float]]] = {}

        self.status = STATUS_OFF
        self.manual = False
        self.error: str | None = None

        # follow me (v0.2)
        self.occupancy: list[str] = list(o.get(CONF_OCCUPANCY) or [])
        self.prewarm = False
        self.follow_state = FOLLOW_INACTIVE

        self.sleep = SleepTimer(self)
        self.volume = RoomVolume(self)
        self.night = NightMode(self)
        self.wake = WakeAlarm(self)
        self._sleep_off_pending = False
        self.turning_off = False

    # ------------------------------------------------------------------ setup

    @property
    def power_entities(self) -> list[str]:
        """All switched power devices of the room (unique)."""
        seen: list[str] = []
        for role in self.roles.values():
            for ent in role.entities:
                if ent not in seen:
                    seen.append(ent)
        return seen

    async def async_start(self) -> None:
        stored = await self._store.async_load() or {}
        default_source = SOURCE_VIDEO if SOURCE_VIDEO in self.sources else SOURCE_AUDIO
        self.data = {
            "auto_power": True,
            "last_source": default_source,
            **stored,
        }
        if self.data.get("last_source") not in self.sources:
            self.data["last_source"] = default_source
        # run-down values: option changes win, otherwise keep the number entity value
        for key in (
            "display_run_down",
            "audio_run_down",
            "host_run_down",
            "video_idle_timeout",
            "night_video_idle_timeout",
            "night_display_run_down",
            "night_max_volume",
        ):
            configured = float(self.opts[key])
            if self.data.get(f"cfg_{key}") != configured or key not in self.data:
                self.data[key] = configured
                self.data[f"cfg_{key}"] = configured

        tracked: list[str] = [s.player for s in self.sources.values()]
        tracked += self.power_entities
        for role in self.roles.values():
            tracked += role.power or []
        self._unsubs.append(
            async_track_state_change_event(self.hass, list(dict.fromkeys(tracked)), self._on_state_event)
        )
        self._unsubs.append(
            async_track_time_interval(self.hass, self._on_tick, timedelta(seconds=TICK_SECONDS))
        )

        @callback
        def _started(_hass: HomeAssistant) -> None:
            self._started = True
            self._spawn(self.async_update("start"))
            self.sleep.async_started()
            self._spawn(self.night.async_started())
            self._spawn(self.wake.async_started())

        self._unsubs.append(async_at_started(self.hass, _started))
        self.sleep.async_start()

        get_rooms(self.hass)[self.entry.entry_id] = self
        if (hub := _hub(self.hass)) is not None:
            hub.async_refresh_tracking()
            hub.async_schedule("room_added")

    async def async_stop(self) -> None:
        self._started = False
        self._stopped = True
        get_rooms(self.hass).pop(self.entry.entry_id, None)
        if (hub := _hub(self.hass)) is not None:
            hub.async_refresh_tracking()
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self.sleep.async_stop()
        self.volume.async_stop()
        self.night.async_stop()
        self.wake.async_stop()
        seq = self._seq_task
        self._cancel_sequence()
        if seq is not None:
            try:
                await seq
            except BaseException:  # noqa: BLE001 - cancelled or failed, both fine here
                pass
        for task in list(self._side_tasks):
            task.cancel()
        await self._store.async_save(self.data)

    @callback
    def _spawn(self, coro) -> None:
        if self._stopped:
            coro.close()
            return
        task = self.hass.async_create_task(coro, eager_start=False)
        self._side_tasks.add(task)
        task.add_done_callback(self._side_tasks.discard)

    # -------------------------------------------------------------- listeners

    @callback
    def async_add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    @callback
    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(lambda: self.data, 10)

    @callback
    def _save_now(self) -> None:
        self._store.async_delay_save(lambda: self.data, 0)

    # ----------------------------------------------------------------- events

    @callback
    def _on_state_event(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        new = event.data["new_state"]
        old = event.data["old_state"]
        self._changed.set()

        for src in self.sources.values():
            if entity_id != src.player:
                continue
            self.volume.async_on_state(src, old, new)
            self.night.async_on_state(src, new)
            self.wake.async_on_state(src, old, new)
            was_playing = old is not None and old.state in PLAYING_STATES
            is_playing = new is not None and new.state in PLAYING_STATES
            if is_playing and not was_playing:
                # someone started playback: this is a fresh wish, even after "off"
                src.suppressed = False
                if self.data.get("last_source") != src.key:
                    self.data["last_source"] = src.key
                    self._save()
                if self.error:
                    self.error = None

        if self._started and entity_id in self.power_entities and new is not None and old is not None:
            if (
                old.state in (STATE_ON, STATE_OFF)
                and new.state in (STATE_ON, STATE_OFF)
                and old.state != new.state
                and not self._is_own_change(entity_id, new)
            ):
                _LOGGER.info(
                    "%s: %s switched %s from outside, switching to manual mode",
                    self.entry.title,
                    entity_id,
                    new.state,
                )
                self.manual = True

        if self._started:
            self._spawn(self.async_update(entity_id))

    @callback
    def _on_tick(self, _now_dt: Any) -> None:
        if self._started:
            self._spawn(self.async_update("tick"))

    def _is_own_change(self, entity_id: str, new: State) -> bool:
        ctx = new.context
        if ctx is not None and (ctx.id in self._own_contexts or ctx.parent_id in self._own_contexts):
            return True
        now = _now()
        for on, ts in self._recent_commands.get(entity_id, ()):
            if (new.state == STATE_ON) == on and now - ts < OWN_CHANGE_SECONDS:
                return True
        return False

    # ---------------------------------------------------------------- reading

    def _state(self, entity_id: str | None) -> State | None:
        return self.hass.states.get(entity_id) if entity_id else None

    def player_state(self, src: Source) -> str | None:
        state = self._state(src.player)
        return state.state if state is not None else None

    def player_available(self, src: Source) -> bool:
        return self.player_state(src) not in (None, *NOT_AVAILABLE)

    def player_supports(self, src: Source, feature: MediaPlayerEntityFeature) -> bool:
        state = self._state(src.player)
        if state is None:
            return False
        return bool(int(state.attributes.get(ATTR_SUPPORTED_FEATURES, 0) or 0) & feature)

    def source_playing(self, src: Source) -> bool:
        return self.player_state(src) in PLAYING_STATES

    def source_active_raw(self, src: Source) -> bool:
        """Active from the player's point of view (ignores suppression)."""
        state = self.player_state(src)
        if src.key == SOURCE_VIDEO:
            return state in POWERED_STATES
        return state in PLAYING_STATES

    def source_active(self, src: Source) -> bool:
        if not self.source_active_raw(src) or src.suppressed:
            return False
        if src.key == SOURCE_AUDIO:
            return self._group_policy_allows(src.player)
        return True

    def _group_policy_allows(self, player: str) -> bool:
        """Playback via a group counts only if the hub's policy for it is not 'ignore'."""
        hub = _hub(self.hass)
        if hub is None:
            return True  # no central settings: every group counts (v0.1 behaviour)
        groups = self.groups_of(player)
        if not groups:
            return True  # direct playback
        policies = {hub.policy(g) for g in groups}
        if self.quiet:
            policies.discard(POLICY_FOLLOW)  # quiet room: follow music does not count
        return bool(policies - {POLICY_IGNORE})

    def source_ready(self, src: Source) -> bool:
        """Started far enough to be used."""
        state = self.player_state(src)
        if state in (None, *NOT_AVAILABLE):
            return False
        if src.key == SOURCE_VIDEO or self.player_supports(src, MediaPlayerEntityFeature.TURN_ON):
            return state in POWERED_STATES
        return True

    def is_on(self, entity_id: str) -> bool:
        state = self._state(entity_id)
        return state is not None and state.state == STATE_ON

    def is_available(self, entity_id: str) -> bool:
        state = self._state(entity_id)
        return state is not None and state.state not in NOT_AVAILABLE

    def role_on(self, role: Role) -> bool:
        return any(self.is_on(e) or self._pending_on(e) for e in role.entities)

    def _pending_on(self, entity_id: str) -> bool:
        """Turn-on was sent recently; slow devices (Zigbee & co.) report later."""
        last = self._last_command.get(entity_id)
        return last is not None and last[0] and _now() - last[1] < COMMAND_RETRY_SECONDS

    def role_fully_on(self, role: Role) -> bool:
        return all(
            self.is_on(e) or not self.is_available(e) or self._pending_on(e) for e in role.entities
        )

    def role_demand(self, role: Role) -> bool:
        if not self.night.role_enabled(role.key):
            return False  # night mode: no subwoofer
        if self.wake.holds(role):
            return True  # snoozed: the room stays ready
        if self.prewarm and role.key in (ROLE_AUDIO, ROLE_BASS, ROLE_AUDIO_HOST) and not self.quiet:
            return True
        return any(
            self.source_active(self.sources[s]) for s in role.sources if s in self.sources
        )

    # ------------------------------------------------------------- follow me

    @property
    def audio_player(self) -> str | None:
        src = self.sources.get(SOURCE_AUDIO)
        return src.player if src else None

    @property
    def follow_capable(self) -> bool:
        return self.audio_player is not None and bool(self.occupancy)

    @property
    def follow_tracked_entities(self) -> list[str]:
        out = list(self.occupancy)
        out += [s.player for s in self.sources.values()]
        return out

    @property
    def quiet(self) -> bool:
        return bool(self.data.get("quiet", False))

    @property
    def video_active(self) -> bool:
        src = self.sources.get(SOURCE_VIDEO)
        # suppressed (turned off, still suspending) or idle-timed-out video does not count
        return src is not None and self.source_active(src)

    @property
    def audio_playing_directly(self) -> bool:
        src = self.sources.get(SOURCE_AUDIO)
        return (
            src is not None
            and self.source_playing(src)
            and not self.groups_of(src.player)
        )

    @property
    def prewarm_enabled(self) -> bool:
        return bool(self.opts[CONF_PREWARM])

    @property
    def presence_run_down(self) -> float:
        return float(self.opts[CONF_PRESENCE_RUN_DOWN]) * 60.0

    @property
    def volume_offset(self) -> float:
        return float(self.opts[CONF_VOLUME_OFFSET])

    def presence(self) -> str:
        """'present', 'empty' or 'unknown' (all sensors unavailable)."""
        known = False
        for entity_id in self.occupancy:
            state = self._state(entity_id)
            if state is None or state.state in NOT_AVAILABLE:
                continue
            known = True
            if state.state == STATE_ON:
                return "present"
        return "empty" if known else "unknown"

    @callback
    def set_follow_state(self, state: str) -> None:
        if self.follow_state != state:
            self.follow_state = state
            self._notify()

    @callback
    def set_prewarm(self, on: bool) -> None:
        if self.prewarm == on:
            return
        self.prewarm = on
        if self._started:
            self._spawn(self.async_update("prewarm"))

    def groups_of(self, player: str) -> list[str]:
        """Media players (e.g. Music Assistant groups) that currently contain player."""
        out = []
        for state in self.hass.states.async_all(MP_DOMAIN):
            if state.entity_id == player:
                continue
            if state.state not in GROUP_ACTIVE_STATES:
                continue  # static groups list their members even while off
            members = state.attributes.get("group_members") or []
            if player in members:
                out.append(state.entity_id)
        return out

    # ------------------------------------------------------------- properties

    @property
    def auto_power(self) -> bool:
        return bool(self.data.get("auto_power", True))

    @property
    def last_source(self) -> str:
        return self.data.get("last_source", SOURCE_VIDEO)

    def run_down(self, role: Role) -> float:
        key = role.run_down_key
        if key == "display_run_down" and self.night.active:
            key = "night_display_run_down"
        return float(self.data.get(key, 0.0)) * 60.0

    @property
    def hub(self):
        return _hub(self.hass)

    @property
    def sequence_running(self) -> bool:
        return self._seq_task is not None and not self._seq_task.done()

    @property
    def active_source(self) -> Source | None:
        """The source that controls commands, volume and media info."""
        if not self.sources:
            return None
        playing = [s for s in self.sources.values() if self.source_playing(s)]
        if len(playing) == 1:
            return playing[0]
        last = self.sources.get(self.last_source)
        if len(playing) > 1:
            return last if last in playing else playing[0]
        active = [s for s in self.sources.values() if self.source_active(s)]
        if len(active) == 1:
            return active[0]
        return last or next(iter(self.sources.values()))

    @property
    def room_on(self) -> bool:
        return (
            self.sequence_running
            or any(self.is_on(e) for e in self.power_entities)
            or any(self.source_active(s) for s in self.sources.values())
        )

    def demand(self, role_key: str) -> bool:
        role = self.roles.get(role_key)
        return role is not None and self.role_demand(role)

    # ------------------------------------------------------------- evaluation

    async def async_update(self, reason: str = "") -> None:
        async with self._lock:
            now = _now()
            self._update_sources(now)
            for role in self.roles.values():
                if self.role_demand(role):
                    role.last_demand = now
                    role.force_off = False
                    role.stop_since = None
                elif role.last_demand is None:
                    # first look (e.g. after a restart): start the run-down now
                    role.last_demand = now

            if self.manual and not any(self.is_on(e) for e in self.power_entities):
                # manual mode ends when everything is off; what is still active
                # must not switch the room straight back on
                self.manual = False
                for src in self.sources.values():
                    if self.source_active_raw(src):
                        src.suppressed = True

            if not self.sequence_running and not self.manual:
                if self.auto_power:
                    await self._auto_on(now)
                await self._auto_off(now)
            self._update_status()
            await self.sleep.async_apply_restores()
            if self.sleep.active and self._started and self._fully_off():
                # everything went off by itself: the timer has nothing left to do
                self._spawn_sleep_cancel()
            if (
                self.wake.active
                and self.wake.phase != PHASE_STARTING
                and self._started
                and self._fully_off()
            ):
                self._spawn(self.wake.async_finish(WAKE_ROOM_OFF))
        self._notify()

    def _fully_off(self) -> bool:
        """Room off, and every device has reported (not still unknown after a start)."""
        if self.room_on:
            return False
        ids = [s.player for s in self.sources.values()] + self.power_entities
        return all(self.is_available(e) for e in ids)

    @callback
    def _spawn_sleep_cancel(self) -> None:
        if self._sleep_off_pending:
            return
        self._sleep_off_pending = True

        async def _cancel() -> None:
            try:
                await self.sleep.async_cancel(REASON_ROOM_OFF)
            finally:
                self._sleep_off_pending = False

        self._spawn(_cancel())

    def _update_sources(self, now: float) -> None:
        for src in self.sources.values():
            stopped = VIDEO_STOPPED if src.key == SOURCE_VIDEO else AUDIO_STOPPED
            if src.suppressed and self.player_state(src) in stopped:
                src.suppressed = False
            if (
                src.key == SOURCE_VIDEO
                and self.source_active(src)
                and self.player_state(src) in IDLE_STATES
            ):
                if src.idle_since is None:
                    src.idle_since = now
                key = "night_video_idle_timeout" if self.night.active else "video_idle_timeout"
                timeout = float(self.data.get(key, 0)) * 60
                if (
                    self.auto_power
                    and timeout > 0
                    and now - src.idle_since >= timeout
                ):
                    _LOGGER.info("%s: %s idle for too long, turning it off", self.entry.title, src.player)
                    src.idle_since = None
                    src.suppressed = True
                    self._spawn(self._source_off(src))
            else:
                src.idle_since = None

    async def _auto_on(self, now: float) -> None:
        missing = [
            r for r in self.roles.values() if self.role_demand(r) and not self.role_fully_on(r)
        ]
        if missing:
            self._start_sequence(None)

    async def _auto_off(self, now: float) -> None:
        """Switch roles off in OFF_ORDER.

        A role waits while an earlier role of the same source is still on and not
        needed (run-down, standby wait, or its off command is still on its way),
        so e.g. the player's hardware is never cut before the display.
        """
        holding: list[Role] = []
        for key in OFF_ORDER:
            role = self.roles.get(key)
            if role is None:
                continue
            if not self.role_on(role):
                if not self._off_recent(role, now):
                    # keep force_off a little longer: a slow device may still
                    # report a late "on" from an earlier command
                    role.force_off = False
                role.stop_since = None
                role.low_since = None
                continue
            if self.role_demand(role):
                continue
            blocked = any(set(h.sources) & set(role.sources) for h in holding)
            due = role.force_off or (
                self.auto_power
                and role.last_demand is not None
                and now - role.last_demand >= self.run_down(role)
            )
            if not due or blocked:
                holding.append(role)
                continue
            if role.standby and not self._standby_reached(role, now):
                holding.append(role)
                continue
            await self._switch_role(role, False)
            if self.role_on(role) and self._off_recent(role, now):
                holding.append(role)

    def _off_recent(self, role: Role, now: float) -> bool:
        """An off command for this role is still on its way."""
        for entity_id in role.entities:
            last = self._last_command.get(entity_id)
            if last is not None and not last[0] and now - last[1] < 2 * COMMAND_RETRY_SECONDS:
                return True
        return False

    def _standby_reached(self, role: Role, now: float) -> bool:
        """Wait until the device went to standby on its own (or max time)."""
        if role.stop_since is None:
            role.stop_since = now
            role.low_since = None
        values = [_float(self._state(e)) for e in role.power or []]
        known = [v for v in values if v is not None]
        if known and all(v <= role.standby_w for v in known):
            if role.low_since is None:
                role.low_since = now
        else:
            role.low_since = None
        if role.low_since is not None and now - role.low_since >= STANDBY_HOLD_SECONDS:
            return True
        return now - role.stop_since >= role.standby_max * 60

    def _update_status(self) -> None:
        if self.sequence_running:
            self.status = STATUS_STARTING
        elif self.error:
            self.status = STATUS_ERROR
        elif self.manual:
            self.status = STATUS_MANUAL
        elif any(r.stop_since is not None and self.role_on(r) for r in self.roles.values()):
            self.status = STATUS_STOPPING
        elif not self.auto_power:
            self.status = STATUS_DISABLED
        elif any(self.source_playing(s) and not s.suppressed for s in self.sources.values()):
            self.status = STATUS_PLAYING
        elif any(self.role_demand(r) for r in self.roles.values()) or any(
            self.source_active(s) for s in self.sources.values()
        ):
            self.status = STATUS_ON
        elif any(self.is_on(e) for e in self.power_entities):
            self.status = STATUS_RUN_DOWN
        else:
            self.status = STATUS_OFF

    # --------------------------------------------------------------- commands

    async def _switch(self, entity_id: str, on: bool, force: bool = False) -> None:
        if not self.is_available(entity_id):
            return
        if self.is_on(entity_id) == on and not (not on and self._pending_on(entity_id)):
            return
        now = _now()
        last = self._last_command.get(entity_id)
        if not force and last is not None and last[0] == on and now - last[1] < COMMAND_RETRY_SECONDS:
            return
        self._last_command[entity_id] = (on, now)
        self._recent_commands.setdefault(entity_id, deque(maxlen=4)).append((on, now))
        domain = entity_id.split(".", 1)[0]
        if domain not in ("switch", "light", "input_boolean"):
            domain = "homeassistant"
        await self._call(domain, SERVICE_TURN_ON if on else SERVICE_TURN_OFF, {ATTR_ENTITY_ID: entity_id})

    async def _switch_role(self, role: Role, on: bool, force: bool = False) -> None:
        needed: set[str] = set()
        if not on:
            # never cut a device that another (still demanded) role needs
            needed = {
                e
                for r in self.roles.values()
                if r is not role and self.role_demand(r)
                for e in r.entities
            }
        for entity_id in role.entities:
            if not on and entity_id in needed:
                continue
            await self._switch(entity_id, on, force)
        if on:
            role.last_demand = _now()
        # force_off / standby state are reset in _auto_off once the role is really off

    async def _call(self, domain: str, service: str, data: dict) -> bool:
        ctx = Context()
        self._own_contexts.append(ctx.id)
        _LOGGER.debug("%s: %s.%s %s", self.entry.title, domain, service, data)
        try:
            await self.hass.services.async_call(domain, service, data, blocking=True, context=ctx)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("%s: %s.%s %s failed: %s", self.entry.title, domain, service, data, err)
            return False
        return True

    async def _run_action(self, entity_id: str, on: bool) -> None:
        domain = entity_id.split(".", 1)[0]
        data = {ATTR_ENTITY_ID: entity_id}
        if domain == "script":
            await self._call("script", SERVICE_TURN_ON, data)
        elif domain in ("button", "input_button"):
            await self._call(domain, "press", data)
        elif domain == "scene":
            await self._call("scene", SERVICE_TURN_ON, data)
        elif domain == "automation":
            await self._call("automation", "trigger", data)
        else:
            await self._call("homeassistant", SERVICE_TURN_ON if on else SERVICE_TURN_OFF, data)

    async def _player(self, src: Source, service: str, extra: dict | None = None) -> bool:
        if service == "volume_set" and extra and "volume_level" in extra:
            # the night mode's limit holds for everything we set
            extra = {**extra, "volume_level": self.night.limit(float(extra["volume_level"]))}
            self.volume.note_own(src.player, extra["volume_level"])
        return await self._call(MP_DOMAIN, service, {ATTR_ENTITY_ID: src.player, **(extra or {})})

    async def _source_on(self, src: Source) -> None:
        if self.player_supports(src, MediaPlayerEntityFeature.TURN_ON):
            await self._player(src, SERVICE_TURN_ON)
        elif src.on_action:
            await self._run_action(src.on_action, True)

    async def _source_off(self, src: Source) -> None:
        """Stop a source: leave groups, stop playback, power it down if possible."""
        if src.key == SOURCE_AUDIO:
            groups = self.groups_of(src.player)
            if (
                groups
                and self.player_state(src) in GROUP_ACTIVE_STATES
                and self.player_supports(src, MediaPlayerEntityFeature.GROUPING)
            ):
                _LOGGER.info("%s: removing %s from %s", self.entry.title, src.player, groups)
                await self._player(src, "unjoin")
            elif self.source_playing(src):
                if self.player_supports(src, MediaPlayerEntityFeature.STOP):
                    await self._player(src, "media_stop")
                elif self.player_supports(src, MediaPlayerEntityFeature.PAUSE):
                    await self._player(src, "media_pause")
        if self.player_state(src) in (STATE_OFF, None, *NOT_AVAILABLE):
            return
        if self.player_supports(src, MediaPlayerEntityFeature.TURN_OFF):
            await self._player(src, SERVICE_TURN_OFF)
        elif src.off_action:
            await self._run_action(src.off_action, False)
        elif src.key == SOURCE_VIDEO and self.source_playing(src):
            if self.player_supports(src, MediaPlayerEntityFeature.STOP):
                await self._player(src, "media_stop")

    # -------------------------------------------------------------- sequences

    @callback
    def _cancel_sequence(self) -> None:
        if self._seq_task is not None and not self._seq_task.done():
            self._seq_task.cancel()
        self._seq_task = None

    @callback
    def _start_sequence(self, source: str | None) -> None:
        if self._stopped:
            return
        if self.sequence_running:
            if source is None:
                return
            self._cancel_sequence()
        # background task: may wait for a slow player, must not block HA
        self._seq_task = self.hass.async_create_background_task(
            self._run_start(source), f"{DOMAIN}_{self.entry.entry_id}_start", eager_start=False
        )
        self._update_status()
        self._notify()

    async def _wait_until(self, cond: Callable[[], bool], timeout: float) -> bool:
        deadline = _now() + timeout
        while not cond():
            remaining = deadline - _now()
            if remaining <= 0:
                return False
            self._changed.clear()
            try:
                await asyncio.wait_for(self._changed.wait(), remaining)
            except TimeoutError:
                if _now() >= deadline:
                    return cond()
        return True

    async def _sleep(self, seconds: float) -> None:
        if seconds > 0:
            await asyncio.sleep(seconds)

    async def _run_start(self, source: str | None) -> None:
        """Power the room up in a safe order.

        source=None: automatic start, only switches roles with demand.
        source=<key>: explicit start of that source, including the player itself.
        """
        switched: list[Role] = []
        src = self.sources.get(source) if source else None
        try:
            if src is not None:
                needed_sources = {src.key}
                roles = [
                    r
                    for k in ON_ORDER
                    if (r := self.roles.get(k)) is not None and src.key in r.sources and self.night.role_enabled(k)
                ]
            else:
                needed_sources = set()
                roles = [
                    r for k in ON_ORDER if (r := self.roles.get(k)) is not None and self.role_demand(r)
                ]

            before_player = [r for r in roles if r.key in (ROLE_VIDEO_HOST, ROLE_AUDIO_HOST, ROLE_DISPLAY)]
            after_player = [r for r in roles if r not in before_player]

            for role in before_player:
                if not self.night.role_enabled(role.key):
                    continue
                if not self.role_fully_on(role):
                    await self._switch_role(role, True, force=True)
                    switched.append(role)
                    await self._sleep(role.on_wait)
                else:
                    role.last_demand = _now()

            if src is not None and src.key in needed_sources:
                # one deadline for the whole player start
                deadline = _now() + float(self.opts[CONF_START_TIMEOUT])
                if src.host and not await self._wait_until(
                    lambda: self.player_available(src), deadline - _now()
                ):
                    raise _StartFailed(f"{src.player} did not become available")
                if not self.source_ready(src):
                    await self._source_on(src)
                    if not await self._wait_until(lambda: self.source_ready(src), deadline - _now()):
                        raise _StartFailed(f"{src.player} did not start")

            for role in after_player:
                if not self.night.role_enabled(role.key):
                    continue  # night mode switched on during the start
                if not self.role_fully_on(role):
                    await self._switch_role(role, True, force=True)
                    switched.append(role)
                    await self._sleep(role.on_wait)
                else:
                    role.last_demand = _now()
            self.error = None
        except _StartFailed as err:
            _LOGGER.warning("%s: start failed: %s", self.entry.title, err)
            self.error = str(err)
            for role in reversed(switched):
                role.force_off = True  # retried by _auto_off until really off
                await self._switch_role(role, False, force=True)
        finally:
            if asyncio.current_task() is self._seq_task:
                self._seq_task = None
            if not self._stopped:
                self._update_status()
                self._notify()
            if self._started and not self._stopped:
                self._spawn(self.async_update("sequence_done"))

    # ----------------------------------------------------------- user actions

    def _reset_overrides(self) -> None:
        self.manual = False
        self.error = None

    async def async_turn_on(self, source: str | None = None) -> None:
        """Explicit wish: power the room up with source (default: last used)."""
        self._reset_overrides()
        key = source or self.last_source
        if key not in self.sources:
            key = next(iter(self.sources))
        src = self.sources[key]
        src.suppressed = False
        for role in self.roles.values():
            if key in role.sources:
                role.force_off = False
                role.stop_since = None
        if self.data.get("last_source") != key:
            self.data["last_source"] = key
            self._save()
        self._start_sequence(key)

    async def async_select_source(self, source: str) -> None:
        """Switch to source; the other source is stopped."""
        if source not in self.sources:
            return
        await self._stop_others(source)
        await self.async_turn_on(source)

    def source_powered(self, key: str) -> bool:
        """The source is ready and everything it needs has power."""
        src = self.sources[key]
        return (
            not src.suppressed
            and self.source_ready(src)
            and all(
                self.role_fully_on(r)
                for r in self.roles.values()
                if key in r.sources and self.night.role_enabled(r.key)
            )
        )

    async def async_play_on(self, key: str, data: dict, room_mode: str | None = None) -> None:
        """Play something on one of the room's players (from the room browser or search).

        Playing now (play / replace / no enqueue) switches the room to that player:
        the other player is stopped and the power sequence runs first, so e.g. a
        movie picked while Kodi sleeps turns on the TV, wakes Kodi and then plays.
        Next / add only enqueue on the player and change nothing else.
        """
        src = self.sources.get(key)
        if src is None:
            raise HomeAssistantError(f"Unknown source: {key}")
        enqueue = room_mode or data.get("enqueue")
        if enqueue is None or str(enqueue) in ("play", "replace"):
            await self._stop_others(key)
            if not self.source_powered(key):
                await self.async_turn_on(key)
                task = self._seq_task
                if task is not None:
                    # shielded: if our caller (e.g. a voice command) gives up, the
                    # room's power sequence still finishes or rolls back properly
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        if not task.cancelled():
                            raise  # the caller was cancelled, not the sequence
                        raise HomeAssistantError(f"{self.entry.title}: start was interrupted") from None
                if self.error:
                    raise HomeAssistantError(f"{self.entry.title}: {self.error}")
            elif self.data.get("last_source") != key:
                self.data["last_source"] = key
                self._save()
            if (
                key == SOURCE_AUDIO
                and self.groups_of(src.player)
                and self.player_supports(src, MediaPlayerEntityFeature.GROUPING)
            ):
                # play here, not on the whole group (independent of Music
                # Assistant's 'play media overrides group' setting)
                await self._player(src, "unjoin")
            # played here on its own: with the room's volume from the first second
            await self.volume.async_apply(src, left_group=True)
        ctx = Context()
        self._own_contexts.append(ctx.id)
        await self.hass.services.async_call(
            MP_DOMAIN, "play_media", {ATTR_ENTITY_ID: src.player, **data}, blocking=True, context=ctx
        )

    async def _stop_others(self, source: str) -> None:
        for other in self.sources.values():
            if other.key == source or not self.source_active_raw(other):
                continue
            other.suppressed = True
            if other.key == SOURCE_VIDEO:
                await self._source_off(other)
            else:
                # music: leave a group, otherwise just pause
                if (
                    self.groups_of(other.player)
                    and self.player_state(other) in GROUP_ACTIVE_STATES
                    and self.player_supports(other, MediaPlayerEntityFeature.GROUPING)
                ):
                    await self._player(other, "unjoin")
                elif self.player_supports(other, MediaPlayerEntityFeature.PAUSE):
                    await self._player(other, "media_pause")

    async def async_turn_off(self) -> None:
        """Explicit "room off": stop sources, then power down in order."""
        self.turning_off = True
        try:
            await self._turn_off()
        finally:
            self.turning_off = False

    async def _turn_off(self) -> None:
        await self.wake.async_finish(WAKE_ROOM_OFF)
        self._cancel_sequence()
        self._reset_overrides()
        if (hub := _hub(self.hass)) is not None:
            hub.room_turned_off(self)
        self.prewarm = False
        # flag everything first: state events during the awaits below must not
        # start the room again
        for src in self.sources.values():
            if self.source_active_raw(src) or self.groups_of(src.player):
                src.suppressed = True
        for role in self.roles.values():
            if self.role_on(role):
                role.force_off = True
        for src in self.sources.values():
            await self._source_off(src)
        # after the stop: a fade's volume comes back only once the player is quiet
        await self.sleep.async_cancel(REASON_ROOM_OFF)
        for role in self.roles.values():
            if self.role_on(role):
                role.force_off = True
        self._cancel_sequence()
        await self.async_update("turn_off")

    async def async_set_quiet(self, on: bool) -> None:
        """Quiet mode: the room takes no part in follow; speakers off right away."""
        self.data["quiet"] = on
        self._save()
        if on:
            self.prewarm = False
            hub = _hub(self.hass)
            if hub is not None:
                await hub.async_update("quiet")
            for key in (ROLE_BASS, ROLE_AUDIO):
                role = self.roles.get(key)
                if role is not None and self.role_on(role):
                    role.force_off = True  # honoured once nothing else needs the speakers
        elif (hub := _hub(self.hass)) is not None:
            hub.async_schedule("quiet")
        await self.async_update("quiet")

    async def async_set_night_mode(self, on: bool) -> None:
        await self.night.async_set(on)

    async def async_set_auto_power(self, on: bool) -> None:
        self.data["auto_power"] = on
        if on:
            self.manual = False
        self._save()
        await self.async_update("auto_power")

    async def async_set_number(self, key: str, value: float) -> None:
        self.data[key] = float(value)
        self._save()
        await self.async_update(key)

    async def async_set_sleep_timer(self, minutes: float, fade_minutes: float | None = None) -> None:
        if minutes > 0 and not self.room_on:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="room_off", translation_placeholders={"room": self.entry.title}
            )
        await self.sleep.async_set(minutes, fade_minutes)

    async def async_extend_sleep_timer(self, minutes: float = 15) -> None:
        if not self.room_on:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="room_off", translation_placeholders={"room": self.entry.title}
            )
        await self.sleep.async_extend(minutes)

    async def async_media_call(self, service: str, data: dict | None = None) -> None:
        """Forward a media command to the active source."""
        src = self.active_source
        if src is None:
            return
        if service == "volume_set" and data and "volume_level" in data:
            self.volume.learn_wish(src, data["volume_level"])
        await self._player(src, service, data)


class _StartFailed(Exception):
    """Start sequence could not be completed."""
