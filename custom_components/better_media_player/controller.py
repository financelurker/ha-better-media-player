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
from dataclasses import dataclass, field
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
    CONF_AUDIO_POWER,
    CONF_BASS,
    CONF_DISPLAY,
    CONF_DISPLAY_POWER,
    CONF_OCCUPANCY,
    CONF_PRESENCE_RUN_DOWN,
    CONF_PREWARM,
    CONF_SPEAKERS,
    DEVICE_DOMAINS,
    ROLE_ACTIONS,
    SWITCH_DOMAINS,
    CONF_START_TIMEOUT,
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
    VOLUME_DEVICE,
)
from .hub import get_hub, get_rooms
from .sleep import REASON_ROOM_OFF, SleepTimer
from .night import NightMode
from .source_event import SourceWatch
from .output import DeviceOutput, PlayerOutput
from .volume import RoomVolume
from .wake import PHASE_STARTING, REASON_ROOM_OFF as WAKE_ROOM_OFF, WakeAlarm

_LOGGER = logging.getLogger(__name__)

# Order in which roles are switched on / off
ON_ORDER = (ROLE_VIDEO_HOST, ROLE_AUDIO_HOST, ROLE_DISPLAY, ROLE_AUDIO, ROLE_BASS)
OFF_ORDER = (ROLE_BASS, ROLE_AUDIO, ROLE_DISPLAY, ROLE_VIDEO_HOST, ROLE_AUDIO_HOST)

COMMAND_RETRY_SECONDS = 30
ACTION_ID = f"{DOMAIN}_action"  # virtual id of a role's own on/off actions
DEVICE_OFF_SECONDS = 60  # a role's plugs wait at most this long for its devices to go off
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
    prefix: str | None = None  # of its settings on the device ("display": display_on_wait, ...)
    power: list[str] | None = None  # power sensors (wait for standby)
    actions: dict[str, tuple[str, str]] = field(default_factory=dict)  # own on/off actions per virtual id
    # runtime
    last_demand: float | None = None
    force_off: bool = False
    stop_since: float | None = None
    low_since: float | None = None
    devices_off_since: float | None = None  # its devices were told to go off


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
        for conf, role_key, sources, run_down, prefix, power in (
            (CONF_DISPLAY, ROLE_DISPLAY, (SOURCE_VIDEO,), "display_run_down", "display", CONF_DISPLAY_POWER),
            (CONF_SPEAKERS, ROLE_AUDIO, both, "audio_run_down", "audio", CONF_AUDIO_POWER),
            (CONF_BASS, ROLE_BASS, both, "audio_run_down", None, None),
        ):
            # plugs and devices; own on/off actions count as one more device (assumed state)
            entities = list(o.get(conf) or [])
            on_k, off_k = ROLE_ACTIONS[conf]
            actions = {}
            if o.get(on_k) and o.get(off_k):
                actions[f"{ACTION_ID}.{role_key}"] = (o[on_k], o[off_k])
                entities.append(f"{ACTION_ID}.{role_key}")
            if entities:
                self.roles[role_key] = Role(
                    key=role_key,
                    entities=entities,
                    sources=sources,
                    run_down_key=run_down,
                    prefix=prefix,
                    power=list(o.get(power) or []) if power else None,
                    actions=actions,
                )
        for role_key, src_key in ((ROLE_VIDEO_HOST, SOURCE_VIDEO), (ROLE_AUDIO_HOST, SOURCE_AUDIO)):
            src = self.sources.get(src_key)
            if src is not None and src.host:
                self.roles[role_key] = Role(
                    key=role_key,
                    entities=[src.host],
                    sources=(src_key,),
                    run_down_key="host_run_down",
                    prefix="host",
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
        # devices left off until the next start (turned off by hand while needed,
        # or did not become available)
        self.held: set[str] = set()
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
        self.source_watch = SourceWatch(self)
        self._sleep_off_pending = False
        self.turning_off = False
        self._was_on: bool | None = None  # None: not looked yet (no power-on after a restart)

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
            self._spawn(self.volume.async_started())
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
        self.source_watch.async_stop()
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
    def _save_now(self) -> None:
        self._store.async_delay_save(lambda: self.data, 0)

    # ----------------------------------------------------------------- events

    @callback
    def _on_state_event(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        new = event.data["new_state"]
        old = event.data["old_state"]
        self._changed.set()

        if self.volume_device is not None and entity_id == self.volume_device.id:
            self.volume.async_on_device(old, new)
            self.night.async_on_device(new)

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
            was_on, is_on = self._on(entity_id, old), self._on(entity_id, new)
            if (
                old.state not in NOT_AVAILABLE
                and new.state not in NOT_AVAILABLE
                and was_on != is_on
                and not self._is_own_change(entity_id, new)
            ):
                self._outside_change(entity_id, is_on)

        if self._started:
            self._spawn(self.async_update(entity_id))

    @callback
    def _on_tick(self, _now_dt: Any) -> None:
        if self._started:
            self._spawn(self.async_update("tick"))

    @callback
    def _outside_change(self, entity_id: str, on: bool) -> None:
        """A power device was switched by someone else.

        A device (TV, AVR) turned off while needed stays off until the next start,
        everything else goes on automatically; anything else (a plug, a device
        turned on) is the user's: manual mode until everything is off."""
        demanded = any(entity_id in r.entities and self.role_demand(r) for r in self.roles.values())
        if self.kind(entity_id) == "device" and not on:
            if demanded:
                _LOGGER.info("%s: %s turned off from outside, left off until the next start", self.entry.title, entity_id)
                self.held.add(entity_id)
            return
        _LOGGER.info(
            "%s: %s switched %s from outside, switching to manual mode",
            self.entry.title,
            entity_id,
            "on" if on else "off",
        )
        self.manual = True

    def _is_own_change(self, entity_id: str, new: State) -> bool:
        ctx = new.context
        if ctx is not None and (ctx.id in self._own_contexts or ctx.parent_id in self._own_contexts):
            return True
        now = _now()
        for on, ts in self._recent_commands.get(entity_id, ()):
            if self._on(entity_id, new) == on and now - ts < OWN_CHANGE_SECONDS:
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

    def setting(self, key: str) -> Any:
        """A value set on the device (config entity), else its default."""
        return self.data.get(key, ENTITY_DEFAULTS.get(key))

    def role_on_wait(self, role: Role) -> float:
        if role.prefix is None:
            return 0.0
        return float(self.setting(f"{role.prefix}_on_wait") or 0)

    def role_standby(self, role: Role) -> bool:
        """Wait for standby before its plug goes off (only with power sensors)."""
        return (
            bool(role.power)
            and role.prefix in ("display", "audio")
            and any(self.kind(e) == "plug" for e in role.entities)
            and bool(self.setting(f"{role.prefix}_standby"))
        )

    @property
    def room_active(self) -> bool:
        """A player of the room is used (or the room is being turned on): its
        controls make sense (see source_event.py for the room's source)."""
        return self.sequence_running or self.source_watch.active

    @property
    def visibility_ready(self) -> bool:
        """Started, and the room's source is known (its players have reported)."""
        return self._started and self.source_watch.ready

    @property
    def has_power(self) -> bool:
        return any(self.is_on(e) for e in self.power_entities)

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
            return self._group_counts(src.player)
        return True

    def _group_counts(self, player: str) -> bool:
        """Group playback counts, except follow me in a quiet room (unless it wakes there)."""
        hub = _hub(self.hass)
        groups = self.groups_of(player)
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

    @staticmethod
    def kind(entity_id: str) -> str:
        """plug (switched power), device (turns itself on/off) or action (own on/off actions)."""
        domain = entity_id.split(".", 1)[0]
        if domain == ACTION_ID:
            return "action"
        return "plug" if domain in SWITCH_DOMAINS else "device"

    @staticmethod
    def _on(entity_id: str, state: State | None) -> bool:
        if state is None:
            return False
        if entity_id.startswith("media_player."):
            return state.state in POWERED_STATES
        return state.state == STATE_ON

    def is_on(self, entity_id: str) -> bool:
        if self.kind(entity_id) == "action":
            return bool(self.data.get("assumed", {}).get(entity_id))  # what was last sent
        return self._on(entity_id, self._state(entity_id))

    def is_available(self, entity_id: str) -> bool:
        if self.kind(entity_id) == "action":
            return True
        state = self._state(entity_id)
        return state is not None and state.state not in NOT_AVAILABLE

    def role_on(self, role: Role) -> bool:
        return any(self.is_on(e) or self._pending_on(e) for e in role.entities)

    def _pending_on(self, entity_id: str) -> bool:
        """Turn-on was sent recently; slow devices (Zigbee & co.) report later."""
        last = self._last_command.get(entity_id)
        return last is not None and last[0] and _now() - last[1] < COMMAND_RETRY_SECONDS

    def role_fully_on(self, role: Role) -> bool:
        """Everything of the role is on (a device behind a plug of its role only once
        it is there and on; something unavailable otherwise does not hold it up)."""
        plugged = any(self.kind(e) == "plug" and self.is_on(e) for e in role.entities)
        return all(
            self.is_on(e)
            or self._pending_on(e)
            or e in self.held
            or (not self.is_available(e) and not (plugged and self.kind(e) == "device"))
            for e in role.entities
        )

    def devices_on(self, role: Role) -> bool:
        """A device of the role (not needed by another role) is still on: its plugs wait."""
        needed = self._needed_elsewhere(role)
        return any(
            self.kind(e) != "plug" and e not in needed and self.is_on(e) and self.is_available(e)
            for e in role.entities
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
            # the room comes on: one of its players is used (plugs alone, e.g. follow
            # me's prewarm or a plug switched by hand, are no "on"; the player's start
            # takes the room volume a moment later); off only counts once everything
            # is off and has reported (no dropout, no restart)
            on = any(self.source_active(s) for s in self.sources.values())
            if on and self._was_on is False:
                self.volume.async_power_on()  # the power-on preset
            if on:
                self._was_on = True
            elif self._fully_off():
                self._was_on = False
                self.held.clear()  # the next start switches them again
            await self.sleep.async_apply_restores()
            self.source_watch.async_check()
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
                role.devices_off_since = None
                continue
            if self.role_demand(role):
                role.devices_off_since = None
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
            # devices first; their plugs once they are off (or did not within
            # DEVICE_OFF_SECONDS: the plug is what cuts them then), and in standby
            await self._switch_devices_off(role)
            if role.devices_off_since is None:
                role.devices_off_since = now
            if self.devices_on(role) and now - role.devices_off_since < DEVICE_OFF_SECONDS:
                holding.append(role)
                continue
            if self.role_standby(role) and not self._standby_reached(role, now):
                holding.append(role)
                continue
            await self._switch_plugs_off(role)
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
        if known and all(v <= self.setting(f"{role.prefix}_standby_w") for v in known):
            if role.low_since is None:
                role.low_since = now
        else:
            role.low_since = None
        if role.low_since is not None and now - role.low_since >= STANDBY_HOLD_SECONDS:
            return True
        return now - role.stop_since >= self.setting(f"{role.prefix}_standby_max") * 60

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
        service = SERVICE_TURN_ON if on else SERVICE_TURN_OFF
        kind = self.kind(entity_id)
        if kind == "action":
            # no feedback: the command is trusted
            role = next(r for r in self.roles.values() if entity_id in r.actions)
            await self._run_action(role.actions[entity_id][0 if on else 1], on)
            self.data.setdefault("assumed", {})[entity_id] = on
            self._save()
            return
        domain = entity_id.split(".", 1)[0]
        if domain not in (*SWITCH_DOMAINS, *DEVICE_DOMAINS):
            domain = "homeassistant"
        await self._call(domain, service, {ATTR_ENTITY_ID: entity_id})

    def _needed_elsewhere(self, role: Role) -> set[str]:
        """What another (still demanded) role needs: never cut."""
        return {e for r in self.roles.values() if r is not role and self.role_demand(r) for e in r.entities}

    async def _switch_role(self, role: Role, on: bool, force: bool = False) -> None:
        """On: plugs first, then the devices behind them. Off: devices first, the
        plugs only once the devices are off."""
        if on:
            for entity_id in role.entities:
                if self.kind(entity_id) == "plug":
                    await self._switch(entity_id, True, force)
            for entity_id in role.entities:
                if self.kind(entity_id) != "plug" and entity_id not in self.held:
                    await self._switch(entity_id, True, force)
            role.last_demand = _now()
            return
        await self._switch_devices_off(role, force)
        if not self.devices_on(role):
            await self._switch_plugs_off(role, force)
        # else: _auto_off cuts the plugs once the devices are off
        # force_off / standby state are reset in _auto_off once the role is really off

    async def _role_up(self, role: Role) -> None:
        """Plugs on, then the devices behind them once they are there."""
        await self._switch_role(role, True, force=True)
        if not any(self.kind(e) == "plug" for e in role.entities):
            return
        waiting = [e for e in role.entities if self.kind(e) == "device" and e not in self.held and not self.is_available(e)]
        if not waiting:
            return
        timeout = float(self.setting(CONF_START_TIMEOUT))
        if not await self._wait_until(lambda: all(self.is_available(e) for e in waiting), timeout):
            for entity_id in waiting:
                if not self.is_available(entity_id):
                    _LOGGER.warning(
                        "%s: %s did not become available, left off until the next start", self.entry.title, entity_id
                    )
                    self.held.add(entity_id)
        await self._switch_role(role, True, force=True)

    async def _switch_devices_off(self, role: Role, force: bool = False) -> None:
        needed = self._needed_elsewhere(role)
        for entity_id in role.entities:
            if self.kind(entity_id) != "plug" and entity_id not in needed:
                await self._switch(entity_id, False, force)

    async def _switch_plugs_off(self, role: Role, force: bool = False) -> None:
        needed = self._needed_elsewhere(role)
        for entity_id in role.entities:
            if self.kind(entity_id) == "plug" and entity_id not in needed:
                await self._switch(entity_id, False, force)

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
                    switched.append(role)
                    await self._role_up(role)
                    await self._sleep(self.role_on_wait(role))
                else:
                    role.last_demand = _now()

            if src is not None and src.key in needed_sources:
                # one deadline for the whole player start
                deadline = _now() + float(self.setting(CONF_START_TIMEOUT))
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
                    switched.append(role)
                    await self._role_up(role)
                    await self._sleep(self.role_on_wait(role))
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
        self.held.clear()

    async def async_turn_on(self, source: str | None = None) -> None:
        """Explicit wish: power the room up with source (default: last used)."""
        self._reset_overrides()
        key = source or self.last_source
        if key not in self.sources:
            key = next(iter(self.sources))
        self.source_watch.note_request(key)
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
            self.source_watch.note_request(key)
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

    async def async_set_volume_preset(self, preset: str | None) -> None:
        await self.volume.async_set_preset(preset)

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


class _StartFailed(Exception):
    """Start sequence could not be completed."""
