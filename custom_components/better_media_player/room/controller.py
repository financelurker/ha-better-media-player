"""Control logic of Better Media Player.

One controller per room (config entry). It watches the room's media players and
power devices, evaluates what the room needs (its power devices: power.py, the
power-on sequence: sequence.py), runs the room's actions and keeps a
room-level status.

source   A media player of the room. "video" (e.g. Kodi) needs display + audio,
         "audio" (e.g. a Music Assistant / sendspin player) needs audio only.
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
    STATE_STANDBY,
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
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store

from ..const import (
    CONF_AUDIO_RUN_DOWN,
    CONF_OCCUPANCY,
    CONF_PRESENCE_RUN_DOWN,
    CONF_PREWARM,
    CONF_VOLUME_CONTROL,
    CONF_VOLUME_ENTITY,
    CONF_VOLUME_OFFSET,
    DEFAULTS,
    DOMAIN,
    ENTITY_DEFAULTS,
    FOLLOW_INACTIVE,
    ROLE_AUDIO,
    ROLE_AUDIO_HOST,
    ROLE_BASS,
    SOURCE_AUDIO,
    SOURCE_CONF,
    SOURCE_VIDEO,
    SOURCES,
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
    VOLUME_DEVICE,
)
from ..hub import get_hub, get_rooms
from .sleep import REASON_ROOM_OFF, SleepTimer
from .announce import RoomAnnouncer
from ..players import backend_for
from ..players.base import PlayerBackend
from .night import NightMode
from .source_event import SourceWatch
from .output import DeviceOutput, PlayerOutput
from .volume import RoomVolume
from .wake import PHASE_STARTING, REASON_ROOM_OFF as WAKE_ROOM_OFF, WakeAlarm
from .handover import RoomHandover
from .power import NOT_AVAILABLE, POWERED_STATES, RoomPower
from .sequence import PowerSequence
from .. import clock

_LOGGER = logging.getLogger(__name__)

# a music player turned on (power control): its speakers are up at least this long
MIN_ON_SECONDS = 30
OWN_CONTEXTS = 50

PLAYING_STATES = {MediaPlayerState.PLAYING, MediaPlayerState.BUFFERING}
IDLE_STATES = {MediaPlayerState.ON, MediaPlayerState.IDLE}
GROUP_ACTIVE_STATES = {MediaPlayerState.PLAYING, MediaPlayerState.PAUSED, MediaPlayerState.BUFFERING}
# a suppressed source is released only when it has really stopped
# ("standby": for players that still report it; its MediaPlayerState member is deprecated)
VIDEO_STOPPED = {STATE_OFF, STATE_STANDBY}
AUDIO_STOPPED = {STATE_OFF, STATE_STANDBY, MediaPlayerState.IDLE, MediaPlayerState.ON, MediaPlayerState.PAUSED}



@dataclass
class Source:
    """A media player of the room."""

    key: str
    player: str
    on_action: str | None
    off_action: str | None
    host: str | None
    backend: PlayerBackend  # what its system can do (players/)
    # runtime
    suppressed: bool = False
    idle_since: float | None = None
    powered_at: float | None = None  # a music player with power control was turned on
    off_sent: bool = False  # ... and turned off by the room (once, until it is off)


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
                    backend=backend_for(hass, o[player_k], key),
                )

        self.power = RoomPower(self)
        self.sequence = PowerSequence(self)
        self.handover = RoomHandover(self)

        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self.data: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._changed = asyncio.Event()
        self._unsubs: list[Callable[[], None]] = []
        self._listeners: list[Callable[[], None]] = []
        self._started = False
        self._stopped = False
        self._side_tasks: set[asyncio.Task] = set()
        self._own_contexts: deque[str] = deque(maxlen=OWN_CONTEXTS)

        self.status = STATUS_OFF
        self.manual = False
        self.error: str | None = None

        # follow me
        self.occupancy: list[str] = list(o.get(CONF_OCCUPANCY) or [])
        self.prewarm = False
        self.follow_state = FOLLOW_INACTIVE

        # where the room's volume is heard
        device = o.get(CONF_VOLUME_ENTITY) if o.get(CONF_VOLUME_CONTROL) == VOLUME_DEVICE else None
        self.volume_device: DeviceOutput | None = DeviceOutput(self, device) if device else None
        self._outputs = {key: PlayerOutput(self, src) for key, src in self.sources.items()}

        self.sleep = SleepTimer(self)
        self.volume = RoomVolume(self)
        self.night = NightMode(self)
        self.wake = WakeAlarm(self)
        self.announce = RoomAnnouncer(self)
        self.source_watch = SourceWatch(self)
        self._sleep_off_pending = False
        self.turning_off = False
        self._was_on: bool | None = None  # None: not looked yet (no power-on after a restart)

    # ------------------------------------------------------------------ setup

    async def async_start(self) -> None:
        stored = await self._store.async_load() or {}
        default_source = SOURCE_VIDEO if SOURCE_VIDEO in self.sources else SOURCE_AUDIO
        self.data = {
            "auto_power": True,
            "last_source": default_source,
            **stored,
        }
        if "wake_controls" in self.data:  # stored under the old name (wake controls only)
            self.data["controls"] = self.data.pop("wake_controls")
        if self.data.get("last_source") not in self.sources:
            self.data["last_source"] = default_source
        # values set on the device (config entities): first values from the defaults
        for key, value in ENTITY_DEFAULTS.items():
            self.data.setdefault(key, value)
        if self.data.get("volume_preset") is not None and self.data["volume_preset"] not in self.volume.presets:
            self.data.pop("volume_preset")  # no longer offered (options): it does not come back by itself
        # another volume output (players <-> device, another device): the room's
        # volume was learned in another scale, it is learned again
        volume_cfg = self.volume_device.id if self.volume_device is not None else "players"
        if self.data.get("cfg_volume") != volume_cfg:
            if "cfg_volume" in self.data:
                self.data.pop("room_volume", None)
                self.data.pop("player_levels", None)
                # a chosen preset means the same on the new output
                preset = self.volume.preset
                if preset is not None:
                    self.data["room_volume"] = self.volume.presets[preset]
            self.data["cfg_volume"] = volume_cfg

        tracked: list[str] = [s.player for s in self.sources.values()]
        if self.volume_device is not None:
            tracked.append(self.volume_device.id)
        tracked += self.power.entities
        for role in self.power.roles.values():
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
            self._spawn(self.volume.async_started())
            self._spawn(self.wake.async_started())

        self._unsubs.append(async_at_started(self.hass, _started))
        self.sleep.async_start()

        get_rooms(self.hass)[self.entry.entry_id] = self
        if (hub := get_hub(self.hass)) is not None:
            hub.async_refresh_tracking()
            hub.async_schedule("room_added")

    async def async_stop(self) -> None:
        self._started = False
        self._stopped = True
        get_rooms(self.hass).pop(self.entry.entry_id, None)
        if (hub := get_hub(self.hass)) is not None:
            hub.async_refresh_tracking()
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self.sleep.async_stop()
        self.volume.async_stop()
        self.night.async_stop()
        self.wake.async_stop()
        for src in self.sources.values():
            src.backend.async_stop()
        self.source_watch.async_stop()
        seq = self.sequence.task
        self.sequence.cancel()
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
    def notify(self) -> None:
        """Entities update (e.g. the central entry changed something they show)."""
        self._notify()

    @callback
    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(lambda: self.data, 10)

    @callback
    def async_fire(self, event: str, data: dict[str, Any]) -> None:
        """An event of the room (with the room player, the entry and the room's name)."""
        entry = self.entry
        entity_id = er.async_get(self.hass).async_get_entity_id("media_player", DOMAIN, f"{entry.entry_id}_player")
        self.hass.bus.async_fire(event, {"entity_id": entity_id, "entry_id": entry.entry_id, "room": entry.title, **data})

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

        if (
            self.volume_device is not None
            and entity_id == self.volume_device.id
            and not self.announce.protects(entity_id)
        ):
            self.volume.async_on_device(old, new)
            self.night.async_on_device(new)

        for src in self.sources.values():
            if entity_id != src.player:
                continue
            if self.announce.protects(src.player):
                continue  # an announcement: what the player does now is no wish
            self.volume.async_on_state(src, old, new)
            self.night.async_on_state(src, new)
            self.wake.async_on_state(src, old, new)
            if (
                self.power_controlled(src)
                and old is not None
                and new is not None
                and old.state == STATE_OFF
                and new.state in POWERED_STATES
            ):
                src.powered_at = clock.now()  # turned on: its devices come up for a while
            if new is not None and new.state in (STATE_OFF, *PLAYING_STATES):
                src.powered_at = None  # off, or it plays: in use as usual
            if new is None or new.state in (STATE_OFF, *PLAYING_STATES, *NOT_AVAILABLE):
                src.off_sent = False  # off, or in use again: the next run-down turns it off again
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

        if self._started and entity_id in self.power.entities and new is not None and old is not None:
            was_on, is_on = self.power.state_on(entity_id, old), self.power.state_on(entity_id, new)
            if (
                # a device may be gone while off (behind its plug, deep standby)
                (old.state not in NOT_AVAILABLE or self.power.kind(entity_id) == "device")
                and new.state not in NOT_AVAILABLE
                and was_on != is_on
                and not self.power.is_own_change(entity_id, new)
            ):
                self.power.outside_change(entity_id, is_on)

        if self._started:
            self._spawn(self.async_update(entity_id))

    @callback
    def _on_tick(self, _now_dt: Any) -> None:
        if self._started:
            self._spawn(self.async_update("tick"))

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

    def setting(self, key: str) -> Any:
        """A value set on the device (config entity), else its default."""
        return self.data.get(key, ENTITY_DEFAULTS.get(key))

    @property
    def room_active(self) -> bool:
        """A player of the room is used (or the room is being turned on): its
        controls make sense (see source_event.py for the room's source)."""
        return self.sequence.running or self.source_watch.active

    @property
    def visibility_ready(self) -> bool:
        """Started, and the room's source is known (its players have reported)."""
        return self._started and self.source_watch.ready

    @property
    def follow_room(self) -> bool:
        """The room takes part in follow me (follow me is set up)."""
        hub = self.hub
        return self.follow_capable and hub is not None and hub.follow_player is not None

    def vol(self, src: Source) -> PlayerOutput | DeviceOutput:
        """Where this source's volume is heard (room features act there)."""
        return self.volume_device or self._outputs[src.key]

    def player_output(self, entity_id: str | None) -> tuple[PlayerOutput | None, Source | None]:
        """A player's own volume, whatever sets the room's volume."""
        for key, src in self.sources.items():
            if src.player == entity_id:
                return self._outputs[key], src
        return None, None

    def output(self, entity_id: str | None) -> tuple[PlayerOutput | DeviceOutput | None, Source | None]:
        """The output with this id and its source (None for the volume device)."""
        if self.volume_device is not None and entity_id == self.volume_device.id:
            return self.volume_device, None
        for key, src in self.sources.items():
            if src.player == entity_id and self.volume_device is None:
                return self._outputs[key], src
        return None, None

    def source_playing(self, src: Source) -> bool:
        return self.player_state(src) in PLAYING_STATES

    def power_controlled(self, src: Source) -> bool:
        """A music player with an on/off of its own (e.g. Music Assistant's
        power control, also its fake one)."""
        return (
            src.key == SOURCE_AUDIO
            and self.player_supports(src, MediaPlayerEntityFeature.TURN_ON)
            and self.player_supports(src, MediaPlayerEntityFeature.TURN_OFF)
        )

    def _just_turned_on(self, src: Source) -> bool:
        """Turned on (in Music Assistant, say) and nothing plays yet: its devices
        are up for the speaker run-down."""
        return (
            src.powered_at is not None
            and not src.suppressed
            and self.player_state(src) in POWERED_STATES
            and clock.now() - src.powered_at < max(MIN_ON_SECONDS, float(self.setting(CONF_AUDIO_RUN_DOWN)) * 60)
        )

    def source_active_raw(self, src: Source) -> bool:
        """Active from the player's point of view (ignores suppression)."""
        return src.backend.is_active(self.player_state(src))

    def source_active(self, src: Source) -> bool:
        if not self.source_active_raw(src) or src.suppressed:
            return False
        if src.key == SOURCE_AUDIO:
            return self._group_counts(src)
        return True

    def _group_counts(self, src: Source) -> bool:
        """Group playback counts, except follow me in a quiet room (unless it wakes there)."""
        hub = get_hub(self.hass)
        groups = src.backend.groups_of(src.player)
        if hub is None or not groups or not self.quiet or self.wake.follow_holding:
            return True
        return any(not hub.follows(g) for g in groups)

    def source_ready(self, src: Source) -> bool:
        """Started far enough to be used."""
        state = self.player_state(src)
        if state in (None, *NOT_AVAILABLE):
            return False
        if src.key == SOURCE_VIDEO or self.player_supports(src, MediaPlayerEntityFeature.TURN_ON):
            return state in POWERED_STATES
        return True

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
            and not src.backend.groups_of(src.player)
        )

    @property
    def prewarm_enabled(self) -> bool:
        return bool(self.setting(CONF_PREWARM))

    @property
    def presence_run_down(self) -> float:
        return float(self.setting(CONF_PRESENCE_RUN_DOWN)) * 60.0

    @property
    def volume_offset(self) -> float:
        return float(self.setting(CONF_VOLUME_OFFSET))

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

    # ------------------------------------------------------------- properties

    @property
    def auto_power(self) -> bool:
        return bool(self.data.get("auto_power", True))

    @property
    def last_source(self) -> str:
        return self.data.get("last_source", SOURCE_VIDEO)

    @property
    def hub(self):
        return get_hub(self.hass)

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
            self.sequence.running
            or any(self.power.is_on(e) for e in self.power.entities)
            or any(self.source_active(s) for s in self.sources.values())
        )

    # ------------------------------------------------------------- evaluation

    async def async_update(self, reason: str = "") -> None:
        async with self._lock:
            now = clock.now()
            self._update_sources(now)
            for role in self.power.roles.values():
                if self.power.role_demand(role):
                    role.last_demand = now
                    role.force_off = False
                    role.stop_since = None
                elif role.last_demand is None:
                    # first look (e.g. after a restart): start the run-down now
                    role.last_demand = now

            if self.manual and not any(self.power.is_on(e) for e in self.power.entities):
                # manual mode ends when everything is off; what is still active
                # must not switch the room straight back on
                self.manual = False
                for src in self.sources.values():
                    if self.source_active_raw(src):
                        src.suppressed = True

            if not self.sequence.running and not self.manual:
                if self.auto_power and self.power.missing():
                    self.sequence.start(None)
                await self.power.async_auto_off(now)
            self._update_status()
            # the room comes on: one of its players is used (plugs alone, e.g. follow
            # me's prewarm or a plug switched by hand, are no "on"; the player's start
            # takes the room volume a moment later); off only counts once everything
            # is off and has reported (no dropout, no restart)
            on = any(self.source_active(s) for s in self.sources.values())
            if on and self._was_on is False:
                self.volume.async_power_on()  # the power-on preset
            if on:
                self._was_on = True
            elif self.power.fully_off():
                self._was_on = False
                self.power.held.clear()  # the next start switches them again
            await self.sleep.async_apply_restores()
            self.source_watch.async_check()
            self.night.async_check()
            if self.sleep.active and self._started and self.power.fully_off():
                # everything went off by itself: the timer has nothing left to do
                self._spawn_sleep_cancel()
            if (
                self.wake.active
                and self.wake.phase != PHASE_STARTING
                and self._started
                and self.power.fully_off()
            ):
                self._spawn(self.wake.async_finish(WAKE_ROOM_OFF))
        self._notify()

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
            if self.power_controlled(src):
                self._power_follows(src)

    def _power_follows(self, src: Source) -> None:
        """A music player with power control goes off with its devices: turned on
        but nothing played (its run-down is over: the devices go off with it), or
        its speakers ran down (paused, stopped). Not in a group (the group
        decides), nor while its speakers are on or needed (manual mode, a
        wake's snooze)."""
        automatic = self.auto_power and not self.manual and not self.sequence.running
        if src.powered_at is not None and not self._just_turned_on(src):
            src.powered_at = None
            if not self.source_playing(src):
                for role in self.power.roles.values():
                    if SOURCE_AUDIO in role.sources and not self.power.role_demand(role):
                        role.force_off = True  # its run-down is over
        # its speakers (the player's own hardware plug goes off after it)
        audio_roles = [r for r in self.power.roles.values() if SOURCE_AUDIO in r.sources and r.key != ROLE_AUDIO_HOST]
        if (
            src.off_sent
            or not self._started
            or not audio_roles
            or not automatic
            or self.player_state(src) not in POWERED_STATES
            or self.source_playing(src)
            or self._just_turned_on(src)
            or src.backend.groups_of(src.player)
            # not yet reported (e.g. after a restart): no "off" to follow
            or not all(self.power.is_available(e) for r in audio_roles for e in r.entities if self.power.kind(e) == "plug")
            or any(self.power.role_on(r) or self.power.role_demand(r) for r in audio_roles)
        ):
            return
        _LOGGER.info("%s: %s goes off with its speakers", self.entry.title, src.player)
        src.off_sent = True
        self._spawn(self._player(src, SERVICE_TURN_OFF))

    def _update_status(self) -> None:
        if self.sequence.running:
            self.status = STATUS_STARTING
        elif self.error:
            self.status = STATUS_ERROR
        elif self.manual:
            self.status = STATUS_MANUAL
        elif any(r.stop_since is not None and self.power.role_on(r) for r in self.power.roles.values()):
            self.status = STATUS_STOPPING
        elif not self.auto_power:
            self.status = STATUS_DISABLED
        elif any(self.source_playing(s) and not s.suppressed for s in self.sources.values()):
            self.status = STATUS_PLAYING
        elif any(self.power.role_demand(r) for r in self.power.roles.values()) or any(
            self.source_active(s) for s in self.sources.values()
        ):
            self.status = STATUS_ON
        elif any(self.power.is_on(e) for e in self.power.entities):
            self.status = STATUS_RUN_DOWN
        else:
            self.status = STATUS_OFF

    # --------------------------------------------------------------- commands

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
            # the night mode's limit holds for everything we set (with a volume
            # device the limit is the device's: the players keep their level)
            extra = {**extra, "volume_level": self.night.player_limit(float(extra["volume_level"]))}
            self.volume.note_own(src.player, extra["volume_level"])
        return await self._call(MP_DOMAIN, service, {ATTR_ENTITY_ID: src.player, **(extra or {})})

    async def _unjoin(self, src: Source) -> None:
        """Leave the group(s) the player is in."""
        ctx = Context()
        self._own_contexts.append(ctx.id)
        await src.backend.unjoin(src.player, ctx)

    async def _source_on(self, src: Source) -> None:
        if self.player_supports(src, MediaPlayerEntityFeature.TURN_ON):
            await self._player(src, SERVICE_TURN_ON)
        elif src.on_action:
            await self._run_action(src.on_action, True)

    async def _source_off(self, src: Source) -> None:
        """Stop a source: leave groups, stop playback, power it down if possible."""
        if src.key == SOURCE_AUDIO:
            groups = src.backend.groups_of(src.player)
            if (
                groups
                and self.player_state(src) in GROUP_ACTIVE_STATES
                and self.player_supports(src, MediaPlayerEntityFeature.GROUPING)
            ):
                _LOGGER.info("%s: removing %s from %s", self.entry.title, src.player, groups)
                await self._unjoin(src)
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

    async def _wait_until(self, cond: Callable[[], bool], timeout: float) -> bool:
        deadline = clock.now() + timeout
        while not cond():
            remaining = deadline - clock.now()
            if remaining <= 0:
                return False
            self._changed.clear()
            try:
                await asyncio.wait_for(self._changed.wait(), remaining)
            except TimeoutError:
                if clock.now() >= deadline:
                    return cond()
        return True

    # ----------------------------------------------------------- user actions

    def _reset_overrides(self) -> None:
        self.manual = False
        self.error = None
        self.power.held.clear()

    async def async_turn_on(self, source: str | None = None) -> None:
        """Explicit wish: power the room up with source (default: last used)."""
        self._reset_overrides()
        key = source or self.last_source
        if key not in self.sources:
            key = next(iter(self.sources))
        self.source_watch.note_request(key)
        src = self.sources[key]
        src.suppressed = False
        for role in self.power.roles.values():
            if key in role.sources:
                role.force_off = False
                role.stop_since = None
        if self.data.get("last_source") != key:
            self.data["last_source"] = key
            self._save()
        self.sequence.start(key)

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
                self.power.role_fully_on(r)
                for r in self.power.roles.values()
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
            await self.async_prepare(key)
            # played here on its own: with the room's volume from the first second
            await self.volume.async_apply(src, left_group=True)
        ctx = Context()
        self._own_contexts.append(ctx.id)
        await self.hass.services.async_call(
            MP_DOMAIN, "play_media", {ATTR_ENTITY_ID: src.player, **data}, blocking=True, context=ctx
        )

    async def async_prepare(self, key: str) -> None:
        """Make the room ready to play on this source: the other source stops, the
        power sequence runs (and is waited for), the player leaves its group."""
        src = self.sources[key]
        self.source_watch.note_request(key)
        await self._stop_others(key)
        if not self.source_powered(key):
            await self.async_turn_on(key)
            task = self.sequence.task
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
            and src.backend.groups_of(src.player)
            and self.player_supports(src, MediaPlayerEntityFeature.GROUPING)
        ):
            # play here, not on the whole group (independent of Music
            # Assistant's 'play media overrides group' setting)
            await self._unjoin(src)

    # --------------------------------------------------------------- handover

    def player_state_of(self, entity_id: str) -> str | None:
        state = self._state(entity_id)
        return state.state if state else None

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
                    other.backend.groups_of(other.player)
                    and self.player_state(other) in GROUP_ACTIVE_STATES
                    and self.player_supports(other, MediaPlayerEntityFeature.GROUPING)
                ):
                    await self._unjoin(other)
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
        self.sequence.cancel()
        self._reset_overrides()
        self.volume.async_off()
        self.announce.async_room_off()
        if (hub := get_hub(self.hass)) is not None:
            hub.room_turned_off(self)
        self.prewarm = False
        # flag everything first: state events during the awaits below must not
        # start the room again
        for src in self.sources.values():
            if self.source_active_raw(src) or src.backend.groups_of(src.player):
                src.suppressed = True
        for role in self.power.roles.values():
            if self.power.role_on(role):
                role.force_off = True
        for src in self.sources.values():
            await self._source_off(src)
        # after the stop: a fade's volume comes back only once the player is quiet
        await self.sleep.async_cancel(REASON_ROOM_OFF)
        for role in self.power.roles.values():
            if self.power.role_on(role):
                role.force_off = True
        self.sequence.cancel()
        await self.async_update("turn_off")

    async def async_set_quiet(self, on: bool) -> None:
        """Quiet mode: the room takes no part in follow; speakers off right away."""
        self.data["quiet"] = on
        self._save()
        if on:
            self.prewarm = False
            hub = get_hub(self.hass)
            if hub is not None:
                await hub.async_update("quiet")
            for key in (ROLE_BASS, ROLE_AUDIO):
                role = self.power.roles.get(key)
                if role is not None and self.power.role_on(role):
                    role.force_off = True  # honoured once nothing else needs the speakers
        elif (hub := get_hub(self.hass)) is not None:
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
        await self.async_set_setting(key, float(value))

    async def async_set_setting(self, key: str, value: Any) -> None:
        """A value set on the device (config entity)."""
        self.data[key] = value
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

    async def async_set_volume_preset(self, preset: str | None, fade: float | None = None) -> None:
        await self.volume.async_set_preset(preset, fade)

    async def async_media_call(self, service: str, data: dict | None = None) -> None:
        """Forward a media command to the active source."""
        device = self.volume_device
        if device is not None and service in ("volume_set", "volume_up", "volume_down", "volume_mute"):
            await self._device_volume(device, service, data or {})
            return
        src = self.active_source
        if src is None:
            return
        if service == "volume_set" and data and "volume_level" in data:
            self.volume.learn_wish(src, data["volume_level"])
        await self._player(src, service, data)

    async def _device_volume(self, device: DeviceOutput, service: str, data: dict) -> None:
        """Volume commands go to the room's volume device (also while nothing plays)."""
        if service == "volume_set":
            self.volume.learn(float(data["volume_level"]))  # remembered while the device is off
            if device.can_set:
                await device.async_set(float(data["volume_level"]))
        elif service == "volume_mute":
            await device.async_mute(bool(data.get("is_volume_muted")))
        elif device.can_set:
            await device.async_step(service == "volume_up")

