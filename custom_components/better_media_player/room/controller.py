"""Control logic of Better Media Player.

One controller per room (config entry). It watches the room's media players and
power devices, evaluates what the room needs (its power devices: power.py, the
power-on sequence: sequence.py), runs the room's actions (handing over:
handover.py) and keeps a room-level status.

source   A media player of the room; any number, one plays at a time. Its kind:
         "video" (e.g. Kodi) needs picture and sound, "audio" (e.g. a Music
         Assistant / sendspin player) the sound only.
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
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from ..const import (
    CONF_IDLE_TIMEOUT,
    CONF_NIGHT_IDLE_TIMEOUT,
    EVENT_RUN_DOWN,
    KIND_NAMES,
    DOMAIN,
    PRESETS,
    SOURCE_AUDIO,
    SOURCE_VIDEO,
    STATUS_DISABLED,
    STATUS_ERROR,
    STATUS_MANUAL,
    STATUS_OFF,
    STATUS_ON,
    STATUS_PLAYING,
    STATUS_KEPT_ON,
    STATUS_RUN_DOWN,
    STATUS_STARTING,
    STATUS_STOPPING,
    STORAGE_VERSION,
    TICK_SECONDS,
)
from ..model import OPT_MUSIC_SOURCE, OPT_START_SOURCE, ROOM_SETTINGS, source_kind, sources_of, volume_entity
from ..follow import get_hub
from . import get_rooms
from .sleep import REASON_ROOM_OFF, SleepTimer
from .announce import RoomAnnouncer
from ..players import backend_for
from ..players.base import PlayerBackend
from .night import NightMode
from .source_event import SourceWatch
from .output import DeviceOutput, PlayerOutput
from .volume import RoomVolume
from .wake import PHASE_STARTING, REASON_ROOM_OFF as WAKE_ROOM_OFF, WakeAlarm
from .follow import RoomFollow
from .handover import RoomHandover
from ..devices.const import NOT_AVAILABLE, POWERED_STATES
from .power import GROUP_ACTIVE_STATES, RoomPower
from .sequence import PowerSequence
from .. import clock

_LOGGER = logging.getLogger(__name__)

# the player waits at most this long for its sound devices, the picture as long for
# the player to go off (then each goes anyway)
PLAYER_OFF_SECONDS = 60
# a hold is shown (sensors, events) once it lasted this long (not between two items)
HOLD_SETTLE_SECONDS = 10
# a player another one runs on (Kodi with Sendspin in it) that plays while none of
# them does for this long plays something of its own
ALONE_SECONDS = 1.0
GONE_SECONDS = 60.0  # ... while one of them is not there (Music Assistant reconnecting)
# after a restart, what is on stays on this long for a player that has not reported yet
FIRST_LOOK_SECONDS = 300
OWN_CONTEXTS = 50

PLAYING_STATES = {MediaPlayerState.PLAYING, MediaPlayerState.BUFFERING}
# a suppressed source is released only when it has really stopped
# ("standby": for players that still report it; its MediaPlayerState member is deprecated)
VIDEO_STOPPED = {STATE_OFF, STATE_STANDBY}
AUDIO_STOPPED = {STATE_OFF, STATE_STANDBY, MediaPlayerState.IDLE, MediaPlayerState.ON, MediaPlayerState.PAUSED}


DEFAULT_SOURCE_NAMES = {SOURCE_VIDEO: "Video", SOURCE_AUDIO: "Music"}


@dataclass
class Source:
    """A media player of the room."""

    key: str
    kind: str  # SOURCE_VIDEO / SOURCE_AUDIO: what it needs, when it is active
    player: str
    on_action: str | None
    off_action: str | None
    host: str | None
    onoff: str  # how the room switches it (model.SOURCE_ONOFF)
    backend: PlayerBackend  # what its system can do (players/)
    runs_on: str | None = None  # the source or device it runs on (Sendspin in Kodi, HEOS on a receiver)
    # runtime
    # a player others run on: on for itself (else for them: on is no use of its own,
    # its playing is theirs); alone_since: it plays while none of them does
    own: bool = True
    alone_since: float | None = None
    suppressed: bool = False
    # in use: it plays, or nothing plays since hold_since for less than the idle
    # timeout (expired: it ran out; None: not known yet, the first look)
    in_use: bool | None = None
    hold_since: float | None = None
    expired: bool = False
    expired_at: float | None = None
    begun: bool = False  # the room started it and it has not been on yet (e.g. "off" until it plays)
    off_sent_at: float | None = None  # the room turned it off (once, until it is off)
    going_off: bool = False  # its idle timeout ran out: off after its sound devices, before the picture


class RoomController:
    """Brain of a room."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.opts: dict[str, Any] = dict(entry.options)  # the setup (model.py)

        self.sources: dict[str, Source] = {}
        for key, s in sources_of(self.opts).items():
            kind = source_kind(s)
            self.sources[key] = Source(
                key=key,
                kind=kind,
                player=s["player"],
                on_action=s.get("on_action"),
                off_action=s.get("off_action"),
                host=s.get("host"),
                onoff=s.get("onoff") or "auto",
                backend=backend_for(hass, s["player"], kind),
                runs_on=s.get("runs_on") or None,
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
        self._alone_checks: dict[str, Callable[[], None]] = {}  # (a player others run on: _later)
        self._own_contexts: deque[str] = deque(maxlen=OWN_CONTEXTS)

        self.status = STATUS_OFF
        self.manual = False
        # the run-down: when each device running down goes off (clock time); the
        # devices a room off is switching off (not a run-down)
        self.run_down_at: dict[str, float] = {}
        # switched off by the room, not run down: key -> reason (all_off, room_off, start_failed)
        self._not_run_down: dict[str, str] = {}
        self._loaded_at = clock.now()  # (a player not reported yet: what is on stays a while)
        self._run_down_seen = False  # (the first look after the start tells nothing)
        self._hold_seen: dict[str, float] = {}  # since when a device is only held (nothing plays)
        self.error: str | None = None

        self.follow = RoomFollow(self)

        # where the room's volume is heard
        device = volume_entity(self.opts)
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
        default_source = next((k for k, s in self.sources.items() if s.kind == SOURCE_VIDEO), next(iter(self.sources), SOURCE_AUDIO))
        self.data = {
            "auto_power": True,
            "last_source": default_source,
            **stored,
        }
        if "wake_controls" in self.data:  # stored under the old name (wake controls only)
            self.data["controls"] = self.data.pop("wake_controls")
        if self.data.get("last_source") not in self.sources:
            self.data["last_source"] = default_source
        # the room's settings: first values from the defaults
        for key, value in ROOM_SETTINGS.items():
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
        tracked += self.power.devices.entities
        for node in self.power.nodes.values():
            tracked += node.power or []
            if node.inputs is not None and node.inputs.entity:
                tracked.append(node.inputs.entity)  # its input: checked and watched for changes by hand
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
        for unsub in self._alone_checks.values():
            unsub()
        self._alone_checks.clear()
        self.sleep.async_stop()
        self.volume.async_stop()
        self.night.async_stop()
        self.wake.async_stop()
        for src in self.sources.values():
            src.backend.async_stop()
        self.source_watch.async_stop()
        self.power.unregister()
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

    async def async_save_now(self) -> None:
        """Written before it returns (e.g. before the room is reloaded)."""
        await self._store.async_save(self.data)

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
                and not src.suppressed
            ):
                src.in_use = True  # turned on (in Music Assistant, say): its devices come up
                src.hold_since = None
                src.expired = False
            if new is None or new.state in (STATE_OFF, *PLAYING_STATES, *NOT_AVAILABLE):
                src.off_sent_at = None  # off, or in use again: the next idle timeout turns it off again
                src.going_off = False
            was_playing = old is not None and old.state in PLAYING_STATES
            is_playing = new is not None and new.state in PLAYING_STATES
            if is_playing and not was_playing:
                # someone started playback: this is a fresh wish, even after "off"
                src.suppressed = False
                if self.data.get("last_source") != src.key and not self.serving(src):  # (its playing is theirs)
                    self.data["last_source"] = src.key
                    self._save()
                if self.error:
                    self.error = None

        if self._started and entity_id in self.power.devices.entities and new is not None and old is not None:
            was_on, is_on = self.power.devices.state_on(entity_id, old), self.power.devices.state_on(entity_id, new)
            if (
                # a device may be gone while off (behind its plug, deep standby)
                (old.state not in NOT_AVAILABLE or self.power.devices.kind(entity_id) == "device")
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
        """A setting of the room (set in the panel or with set_setting), else its default."""
        return self.data.get(key, ROOM_SETTINGS.get(key))

    @property
    def room_active(self) -> bool:
        """A player of the room is used (or the room is being turned on): its
        controls make sense (see source_event.py for the room's source)."""
        return self.sequence.running or self.source_watch.active

    @property
    def visibility_ready(self) -> bool:
        """Started, and the room's source is known (its players have reported)."""
        return self._started and self.source_watch.ready

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
        return self.player_state(src) in PLAYING_STATES and not self.serving(src)

    def power_controlled(self, src: Source) -> bool:
        """A music player with an on/off of its own (e.g. Music Assistant's
        power control, also its fake one) that the room switches."""
        return (
            src.kind == SOURCE_AUDIO
            and src.onoff in ("auto", "player")
            and self.player_supports(src, MediaPlayerEntityFeature.TURN_ON)
            and self.player_supports(src, MediaPlayerEntityFeature.TURN_OFF)
        )

    def switched_by(self, src: Source, on: bool) -> str | None:
        """How the room switches the player on or off: "player", "action" or None."""
        feature = MediaPlayerEntityFeature.TURN_ON if on else MediaPlayerEntityFeature.TURN_OFF
        action = src.on_action if on else src.off_action
        if src.onoff == "player" or (src.onoff == "auto" and self.player_supports(src, feature)):
            return "player" if self.player_supports(src, feature) else None
        if src.onoff in ("auto", "actions") and action:
            return "action"
        return None

    def idle_timeout(self) -> float:
        """s nothing may play before the room goes off (0: never)."""
        key = CONF_NIGHT_IDLE_TIMEOUT if self.night.active else CONF_IDLE_TIMEOUT
        return float(self.setting(key) or 0) * 60

    def hold_end(self, src: Source) -> float | None:
        """When the player's hold ends (nothing plays): None while it plays, or
        while nothing ends it (no idle timeout, kept on, automatic power off)."""
        timeout = self.idle_timeout()
        if not src.in_use or src.hold_since is None or timeout <= 0 or self.keep_on or not self.auto_power:
            return None
        return src.hold_since + timeout

    def player_going_off(self, src: Source) -> bool:
        """The player's idle timeout ran out: the room turns it off after its sound
        devices, and it is not off yet."""
        if not src.going_off or self.switched_by(src, False) is None:
            return False
        if self.player_state(src) not in POWERED_STATES:
            return False
        # (sent after its sound devices, at most a minute after the idle timeout)
        return src.off_sent_at is None or clock.now() - src.off_sent_at < PLAYER_OFF_SECONDS

    def not_reported(self, src: Source) -> bool:
        """After a restart, the player has not reported yet (for a while)."""
        return src.in_use is None and clock.now() - self._loaded_at < FIRST_LOOK_SECONDS

    def player_shutting_down(self, src: Source) -> bool:
        """No longer in use but still on, and the room turns it off: its plug waits."""
        if src.in_use or self.switched_by(src, False) is None or self.player_state(src) not in POWERED_STATES:
            return False
        if self.serving(src) and any(self._used(g) for g in self.guests(src)):
            return False  # (on for them)
        if src.off_sent_at is not None:
            return clock.now() - src.off_sent_at < PLAYER_OFF_SECONDS
        return src.going_off or not src.suppressed

    @callback
    def begin_use(self, src: Source) -> None:
        """The room starts the player: in use (nothing plays yet: it holds, also while
        it still reports off)."""
        src.in_use = True
        src.hold_since = None
        src.expired = False
        src.begun = True
        if self.guests(src):
            src.own = True  # started for itself (one that runs on it: _update_host)

    @callback
    def end_use(self, src: Source) -> None:
        """The player is no longer the room's (e.g. follow me left the room): its
        devices go off without waiting for the idle timeout."""
        src.in_use = False
        src.hold_since = None
        src.expired = False
        src.begun = False

    def source_active_raw(self, src: Source) -> bool:
        """Active from the player's point of view (ignores suppression)."""
        return src.backend.is_active(self.player_state(src))

    def source_active(self, src: Source) -> bool:
        if not self.source_active_raw(src) or src.suppressed or self.serving(src):
            return False
        if src.kind == SOURCE_AUDIO:
            return self.follow.group_counts(src)
        return True

    def source_ready(self, src: Source) -> bool:
        """Started far enough to be used."""
        state = self.player_state(src)
        if state in (None, *NOT_AVAILABLE):
            return False
        if self.switched_by(src, True) is None:
            return True  # the room does not switch it on: it is woken by playing
        if src.kind == SOURCE_VIDEO or self.player_supports(src, MediaPlayerEntityFeature.TURN_ON):
            return state in POWERED_STATES
        return True

    # ------------------------------------------------------------- what plays

    def guests(self, src: Source) -> list[Source]:
        """The players that run on this one."""
        return [g for g in self.sources.values() if g.runs_on == src.key]

    def host_of(self, src: Source) -> Source | None:
        """The player this one runs on (None: its own hardware, or a device)."""
        return self.sources.get(src.runs_on) if src.runs_on else None

    def serving(self, src: Source) -> bool:
        """On for the players that run on it, not for itself."""
        return not src.own and bool(self.guests(src))

    def of_kind(self, kind: str) -> list[Source]:
        return [s for s in self.sources.values() if s.kind == kind]

    @property
    def music(self) -> Source | None:
        """The room's music player for follow me, announcements and a wake: the
        one that plays (on its own before one in a group), else one in a group, else
        a wake's, else one in use, else the one chosen in the setup, else the first."""
        music = self.of_kind(SOURCE_AUDIO)
        if len(music) < 2:
            return next(iter(music), None)
        wake = (self.data.get("wake") or {}).get("source")
        for test in (
            lambda s: self.source_playing(s) and not s.backend.groups_of(s.player),
            self.source_playing,
            lambda s: bool(s.backend.groups_of(s.player, idle=True)),
            lambda s: s.key == wake,
            lambda s: bool(s.in_use),
        ):
            if (src := next((s for s in music if test(s)), None)) is not None:
                return src
        chosen = self.sources.get(self.opts.get(OPT_MUSIC_SOURCE) or "")
        return chosen if chosen is not None and chosen.kind == SOURCE_AUDIO else next(iter(music), None)

    @property
    def video(self) -> Source | None:
        """The room's video player for announcements: the one that is active, else
        the one in use, else the first."""
        video = self.of_kind(SOURCE_VIDEO)
        for test in (self.source_active, lambda s: bool(s.in_use)):
            if (src := next((s for s in video if test(s)), None)) is not None:
                return src
        return next(iter(video), None)

    @property
    def audio_player(self) -> str | None:
        src = self.music
        return src.player if src else None

    @property
    def quiet(self) -> bool:
        return bool(self.data.get("quiet", False))

    @property
    def video_active(self) -> bool:
        # suppressed (turned off, still suspending) or idle-timed-out video does not count
        return any(self.source_active(src) for src in self.of_kind(SOURCE_VIDEO))

    @property
    def audio_playing_directly(self) -> bool:
        return any(self.source_playing(src) and not src.backend.groups_of(src.player) for src in self.of_kind(SOURCE_AUDIO))

    # ------------------------------------------------------------- properties

    @property
    def auto_power(self) -> bool:
        return bool(self.data.get("auto_power", True))

    @property
    def last_source(self) -> str:
        return self.data.get("last_source", SOURCE_VIDEO)

    def source_name(self, src: Source) -> str:
        """Its player's name (as the room's source list shows it)."""
        state = self.hass.states.get(src.player)
        if state is not None and state.attributes.get("friendly_name"):
            return str(state.attributes["friendly_name"])
        return DEFAULT_SOURCE_NAMES.get(src.key) or src.key

    @property
    def wake_source(self) -> str | None:
        """A wake without a source: the one the room starts with if it is set up,
        else the music player, else the video player."""
        if self.opts.get(OPT_START_SOURCE) in self.sources:
            return self.opts[OPT_START_SOURCE]
        src = self.music or self.video
        return src.key if src else None

    @property
    def start_source(self) -> str:
        """The source the room starts with: the one set up, else the one used last."""
        chosen = self.opts.get(OPT_START_SOURCE)
        return chosen if chosen in self.sources else self.last_source

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
    def input_source(self) -> str | None:
        """The source whose inputs are wanted: the one starting, else the one in use."""
        if self.sequence.running and self.sequence.source is not None:
            return self.sequence.source
        src = self.active_source
        return src.key if src is not None and self.source_active(src) else None

    @property
    def room_on(self) -> bool:
        return (
            self.sequence.running
            or any(self.power.devices.is_on(e) for e in self.power.devices.entities)
            or any(self.source_active(s) for s in self.sources.values())
        )

    # ------------------------------------------------------------- evaluation

    async def async_update(self, reason: str = "") -> None:
        async with self._lock:
            now = clock.now()
            self.power.devices.settle()
            self._update_sources(now)
            for node in self.power.nodes.values():
                if self.power.needed(node):
                    node.last_demand = now
                    node.force_off = False
                    node.stop_since = None
                elif node.last_demand is None:
                    # first look (e.g. after a restart): start the run-down now
                    node.last_demand = now

            if self.manual and not any(self.power.devices.is_on(e) for e in self.power.devices.entities):
                # manual mode ends when everything is off; what is still active
                # must not switch the room straight back on
                self.manual = False
                for src in self.sources.values():
                    if self.source_active_raw(src):
                        src.suppressed = True

            if not self.sequence.running and not self.manual:
                if self.auto_power and self.power.devices.missing():
                    self.sequence.start(None)
                await self.power.devices.async_auto_off(now, self.power.off_order)
            self._track_run_down(now)
            if not self.manual:
                # the inputs follow the source in use (chosen outside the lock: an
                # input's pause must not hold the room up)
                self.power.want_inputs(self.input_source)
                self._spawn(self.power.devices.async_apply_inputs())
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
            if self.guests(src):
                self._update_host(src, now)
        for src in self.sources.values():
            stopped = VIDEO_STOPPED if src.kind == SOURCE_VIDEO else AUDIO_STOPPED
            if src.suppressed and self.player_state(src) in stopped:
                src.suppressed = False
            if not self.announce.protects(src.player):  # (an announcement is no use)
                self._update_use(src, now)
            if src.kind == SOURCE_VIDEO or self.guests(src):
                self._video_follows(src)
            if self.power_controlled(src):
                self._power_follows(src)

    def _update_host(self, src: Source, now: float) -> None:
        """A player others run on: for them while one of them is used (the room
        started one, or one plays); for itself once it plays on its own for a
        moment (a film chosen in Kodi), when the room starts it, or when it comes
        on with none of them used."""
        state = self.player_state(src)
        guests = [g for g in self.guests(src) if not g.suppressed]  # (one the room stopped is no wish)
        playing = any(self.player_state(g) in PLAYING_STATES for g in guests)
        first = src.in_use is None
        # the first look (a restart, a reload): one of them holds, or was the room's last
        held = first and any(
            self.player_state(g) in (*PLAYING_STATES, MediaPlayerState.PAUSED)
            or (self.player_state(g) in (None, *NOT_AVAILABLE, *POWERED_STATES) and self.data.get("last_source") == g.key)
            for g in guests
        )
        if playing or any(g.begun for g in guests) or (held and state in POWERED_STATES):
            src.own, src.alone_since = False, None
            guest = next((g for g in guests if self.player_state(g) in PLAYING_STATES), None)
            if guest is not None and self.data.get("last_source") == src.key:
                self.data["last_source"] = guest.key  # (its playing was reported first)
                self._save()
            return
        if state in PLAYING_STATES and not src.own and not src.begun:
            # plays while none of them does: its own, after a moment (a while when one of
            # them is not there: Music Assistant reconnecting, not loaded yet)
            gone = any(self.player_state(g) in (None, *NOT_AVAILABLE) for g in guests)
            wait = GONE_SECONDS if gone else ALONE_SECONDS
            if src.alone_since is None:
                src.alone_since = now
                self._later(src, wait + 0.1)
            elif now - src.alone_since >= wait:
                _LOGGER.info("%s: %s plays something of its own", self.entry.title, src.player)
                src.own, src.alone_since = True, None
                self.begin_use(src)
                if self.data.get("last_source") != src.key:
                    self.data["last_source"] = src.key
                    self._save()
            else:
                self._later(src, src.alone_since + wait - now + 0.1)
            return
        src.alone_since = None
        if state in (STATE_OFF, STATE_STANDBY, None) and not any(g.in_use for g in guests):
            src.own = True  # off: on by itself next time, it is for itself

    def _used(self, src: Source) -> bool:
        """In use, or about to be (playing, started; the first look: paused). Not one
        the room stopped."""
        state = self.player_state(src)
        return not src.suppressed and (
            bool(src.in_use) or src.begun or state in PLAYING_STATES or (src.in_use is None and state == MediaPlayerState.PAUSED)
        )

    @callback
    def _later(self, src: Source, seconds: float) -> None:
        """Look again in a moment whether the player plays on its own (one look at a time)."""
        if self._stopped:
            return
        if (old := self._alone_checks.pop(src.key, None)) is not None:
            old()

        @callback
        def _again(_now: Any) -> None:
            self._alone_checks.pop(src.key, None)
            if not self._stopped:
                self._spawn(self.async_update("host_alone"))

        self._alone_checks[src.key] = async_call_later(self.hass, seconds, _again)

    def _update_use(self, src: Source, now: float) -> None:
        """In use: it plays, or nothing plays (paused, stopped, its menu) for less
        than the idle timeout. A player that is off, or that the room stopped, is not.
        A player only on for the ones that run on it: never itself; once none of
        them is used any more it goes off (after their sound)."""
        state = self.player_state(src)
        if self.serving(src):
            src.in_use, src.hold_since, src.begun = False, None, False
            if any(self._used(g) for g in self.guests(src)) or state in PLAYING_STATES:
                src.expired, src.going_off = False, False  # (playing: theirs, or its own in a moment)
            elif state in POWERED_STATES and not src.expired and self.switched_by(src, False) is not None:
                _LOGGER.info("%s: %s goes off with what runs on it", self.entry.title, src.player)
                src.expired, src.expired_at = True, now
                src.going_off = self.switched_by(src, False) is not None
            return
        first = src.in_use is None
        if self.source_active(src) and self.source_playing(src):
            src.in_use, src.hold_since, src.expired, src.begun = True, None, False, False
            return
        if first and state in (None, *NOT_AVAILABLE):
            return  # (not reported yet, e.g. after a restart: the first look is its first state)
        if src.suppressed or (src.kind == SOURCE_AUDIO and not self.follow.group_counts(src)):
            self.end_use(src)
            return
        if state in (STATE_OFF, STATE_STANDBY, None) and not src.begun:
            self.end_use(src)  # turned off: off means off
            return
        if state in POWERED_STATES:
            src.begun = False
        if src.kind == SOURCE_VIDEO:
            # on (its menu, paused) is in use; unavailable for a moment goes on
            candidate = self.source_active(src) or src.begun or (bool(src.in_use) and state in NOT_AVAILABLE)
        elif first:
            # the first look (a restart): paused or stopped with its speakers on (or none)
            sound = self.power.sound_nodes(src.key)
            candidate = state in POWERED_STATES and (not sound or any(self.power.devices.node_on(n) for n in sound))
        else:
            candidate = bool(src.in_use) or src.expired or src.begun  # it played (or was turned on)
        if not candidate:
            self.end_use(src)
            return
        if src.hold_since is None or self.keep_on or not self.auto_power:
            src.hold_since = now  # (kept on: the idle timeout counts from when it ends)
        timeout = self.idle_timeout()
        if not src.expired and timeout > 0 and now - src.hold_since >= timeout:
            _LOGGER.info("%s: nothing played on %s for %d min, going off", self.entry.title, src.player, timeout // 60)
            src.expired = True
            src.expired_at = now
            if src.kind == SOURCE_VIDEO and self.switched_by(src, False) is not None:
                src.going_off = True  # (off after its sound devices: _video_follows)
        src.in_use = not src.expired

    def _video_follows(self, src: Source) -> None:
        """The video player's idle timeout ran out: its sound devices go off first,
        then the player, then the picture (power.waits)."""
        if (
            not src.expired
            or src.suppressed  # (sent already)
            or src.off_sent_at is not None
            or not self._started
            or not self.auto_power
            or self.manual
            or self.keep_on
            or self.sequence.running
            or self.player_state(src) not in POWERED_STATES
        ):
            return
        # (only what the room switches off itself; at most a minute: then it goes anyway)
        sound = [
            n for n in self.power.nodes.values()
            if n.device is not None and not n.picture and src.key in n.sources and self.power.devices.off_by_owner(n)
        ]
        waiting = clock.now() - (src.expired_at or 0) < PLAYER_OFF_SECONDS
        if waiting and any(self.power.devices.node_on(n) and not self.power.needed(n) for n in sound):
            return
        _LOGGER.info("%s: %s goes off after its sound devices", self.entry.title, src.player)
        src.suppressed = True
        self._spawn(self._source_off(src))

    def _power_follows(self, src: Source) -> None:
        """A music player with power control goes off after its devices: once it is
        no longer in use (its idle timeout ran out, follow me left) and they are
        off (without devices: at once). Not in a group (the
        group decides), nor while its speakers are on or needed (manual mode, a
        wake's snooze)."""
        automatic = self.auto_power and not self.manual and not self.sequence.running and not self.keep_on
        audio_nodes = [n for n in self.power.nodes.values() if src.key in n.sources and n.device is not None]
        if (
            src.off_sent_at is not None
            or not self._started
            or not automatic
            or src.in_use
            or self.player_state(src) not in POWERED_STATES
            or self.source_playing(src)
            or src.backend.groups_of(src.player)
            # not yet reported (e.g. after a restart): no "off" to follow
            or not all(self.power.devices.is_available(e) for n in audio_nodes for e in n.entities if self.power.devices.kind(e) == "plug")
            or any(self.power.devices.node_on(n) or self.power.needed(n) for n in audio_nodes)
        ):
            return
        _LOGGER.info("%s: %s goes off after its speakers", self.entry.title, src.player)
        src.off_sent_at = clock.now()
        self._spawn(self._player(src, SERVICE_TURN_OFF))

    def _update_status(self) -> None:
        if self.sequence.running:
            self.status = STATUS_STARTING
        elif self.error:
            self.status = STATUS_ERROR
        elif self.manual:
            self.status = STATUS_MANUAL
        elif any(n.stop_since is not None and self.power.devices.node_on(n) for n in self.power.nodes.values()):
            self.status = STATUS_STOPPING
        elif not self.auto_power:
            self.status = STATUS_DISABLED
        elif any(self.source_playing(s) and not s.suppressed for s in self.sources.values()):
            self.status = STATUS_PLAYING
        elif (
            self.keep_on
            and any(self.power.devices.is_on(e) for e in self.power.devices.entities)
            and not any(self.power.needed_playing(n) for n in self.power.nodes.values())
        ):
            self.status = STATUS_KEPT_ON  # nothing plays: only kept on
        elif any(self.power.needed(n) for n in self.power.nodes.values()) or any(
            self.source_active(s) for s in self.sources.values()
        ):
            self.status = STATUS_ON
        elif any(self.power.devices.is_on(e) for e in self.power.devices.entities):
            self.status = STATUS_RUN_DOWN
        else:
            self.status = STATUS_OFF

    # --------------------------------------------------------------- commands


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
        how = self.switched_by(src, True)
        if how == "player":
            await self._player(src, SERVICE_TURN_ON)
        elif how == "action":
            await self._run_action(src.on_action, True)

    async def _source_off(self, src: Source) -> None:
        """Stop a source: leave groups, stop playback, power it down if possible."""
        self.end_use(src)
        if src.kind == SOURCE_AUDIO:
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
        how = self.switched_by(src, False)
        if how is not None:
            src.off_sent_at = clock.now()  # (its plug and the picture wait for it)
        if how == "player":
            await self._player(src, SERVICE_TURN_OFF)
        elif how == "action":
            await self._run_action(src.off_action, False)
        elif src.kind == SOURCE_VIDEO and self.source_playing(src):
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
        """Explicit wish: power the room up with source (default: the one it starts with)."""
        self._reset_overrides()
        key = source or (self.start_source if not self.room_on else self.last_source)
        if key not in self.sources:
            key = next(iter(self.sources))
        self.source_watch.note_request(key)
        src = self.sources[key]
        src.suppressed = False
        for node in self.power.nodes.values():
            if key in node.sources:
                node.force_off = False
                node.stop_since = None
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
                self.power.devices.node_fully_on(n)
                for n in self.power.nodes.values()
                if key in n.sources and self.night.enabled(n)
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
            src.kind == SOURCE_AUDIO
            and src.backend.groups_of(src.player)
            and self.player_supports(src, MediaPlayerEntityFeature.GROUPING)
        ):
            # play here, not on the whole group (independent of Music
            # Assistant's 'play media overrides group' setting)
            await self._unjoin(src)

    # ---------------------------------------------------------------- sources

    def player_state_of(self, entity_id: str) -> str | None:
        state = self._state(entity_id)
        return state.state if state else None

    async def _stop_others(self, source: str) -> None:
        for other in self.sources.values():
            if other.key == source:
                continue
            self.end_use(other)  # (also one that holds, paused)
            if other.key == self.sources[source].runs_on:
                # the player it runs on stays on (for it): only what it plays itself stops
                own, other.own = other.own, False
                self.begin_use(self.sources[source])
                if own and self.player_state(other) in PLAYING_STATES and self.player_supports(other, MediaPlayerEntityFeature.STOP):
                    await self._player(other, "media_stop")
                continue
            if not self.source_active_raw(other):
                continue
            other.suppressed = True
            if other.kind == SOURCE_VIDEO:
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

    async def async_turn_off(self, all_off: bool = False) -> None:
        """Room off: stop sources, then power down in order.

        all_off: the user's "All off" (button, the room player's off), which also
        ends "Keep on". Otherwise (sleep timer, wake, handover) the sources stop and,
        while the room is kept on, its devices stay on."""
        self.turning_off = True
        try:
            if all_off and self.keep_on:
                self.data["keep_on"] = False
                self._save()
            await self._turn_off("all_off" if all_off else "room_off")
        finally:
            self.turning_off = False

    def _cut(self, reason: str) -> None:
        """Everything on goes off (a room off), unless the room is kept on."""
        if self.keep_on:
            return
        for node in self.power.nodes.values():
            if self.power.devices.node_on(node):
                node.force_off = True
                self._not_run_down[node.key] = reason

    def not_run_down(self, nodes, reason: str) -> None:
        """The room switches these off, not their run-down (e.g. a failed start)."""
        for node in nodes:
            self._not_run_down[node.key] = reason

    async def _turn_off(self, reason: str) -> None:
        await self.wake.async_finish(WAKE_ROOM_OFF)
        self.sequence.cancel()
        self._reset_overrides()
        self.volume.async_off()
        self.announce.async_room_off()
        if (hub := get_hub(self.hass)) is not None:
            hub.room_turned_off(self)
        self.follow.prewarm = False
        # flag everything first: state events during the awaits below must not
        # start the room again
        for src in self.sources.values():
            if self.source_active_raw(src) or src.backend.groups_of(src.player):
                src.suppressed = True
        self._cut(reason)
        for src in self.sources.values():
            await self._source_off(src)
        # after the stop: a fade's volume comes back only once the player is quiet
        await self.sleep.async_cancel(REASON_ROOM_OFF)
        self._cut(reason)
        self.sequence.cancel()
        await self.async_update("turn_off")

    async def async_set_quiet(self, on: bool) -> None:
        """Quiet mode: the room takes no part in follow; speakers off right away."""
        self.data["quiet"] = on
        self._save()
        if on:
            self.follow.prewarm = False
            hub = get_hub(self.hass)
            if hub is not None:
                await hub.async_update("quiet")
            for node in self.power.sound_nodes():
                if self.power.devices.node_on(node):
                    node.force_off = True  # honoured once nothing else needs the speakers
        elif (hub := get_hub(self.hass)) is not None:
            hub.async_schedule("quiet")
        await self.async_update("quiet")

    async def async_set_night_mode(self, on: bool) -> None:
        await self.night.async_set(on)

    @property
    def keep_on(self) -> bool:
        """Keep on: nothing is switched off automatically (until turned off or All off)."""
        return bool(self.data.get("keep_on"))

    async def async_set_keep_on(self, on: bool) -> None:
        self.data["keep_on"] = on
        self._save()
        if on:
            # an automatic off on its way stops (one already switching its devices
            # off goes through; quiet still cuts the speakers)
            quiet = {n.key for n in self.power.sound_nodes()} if self.quiet else set()
            for node in self.power.nodes.values():
                if node.key not in self._not_run_down and node.key not in quiet and node.devices_off_since is None:
                    node.force_off = False
                    node.stop_since = None
        else:
            # what nothing needs goes off now, a player's plug waits from now (not
            # from its last need, maybe hours ago); a player that does not play
            # holds anew for its idle timeout
            now = clock.now()
            for node in self.power.nodes.values():
                if node.last_demand is not None:
                    node.last_demand = max(node.last_demand, now)
            self.night.async_cut_disabled()  # devices off at night go now
        await self.async_update("keep_on")

    def node_name(self, key: str) -> str:
        node = self.power.nodes[key]
        if node.device is None:
            src = self.sources[node.sources[0]]
            kind = "Video player hardware" if src.kind == SOURCE_VIDEO else "Music player hardware"
            return kind if src.key in (SOURCE_VIDEO, SOURCE_AUDIO) else f"{kind} ({self.source_name(src)})"
        return node.device.get("name") or KIND_NAMES.get(node.device["kind"], key)

    @callback
    def _track_run_down(self, now: float) -> None:
        """Which devices run down and when they go off; its triggers.

        A device runs down while it is on and only players that do not play need
        it (its time: the end of their idle timeout), or while nothing needs it
        and it waits to go off (its time: its last need; a player's plug: plus its
        off_wait). Waiting after that (standby, the player first) keeps the time
        until it is off."""
        devices = self.power.devices
        nodes = self.power.nodes
        cut = dict(self._not_run_down)
        self._not_run_down = {
            k: r for k, r in cut.items() if k in nodes and devices.node_on(nodes[k]) and not self.power.needed(nodes[k])
        }
        before = self.run_down_at
        runs: dict[str, float] = {}
        held: dict[str, float] = {}
        if self.auto_power and not self.manual and not self.keep_on:
            for key, node in nodes.items():
                if (
                    key in self._not_run_down
                    or node.last_demand is None
                    or not devices.node_on(node)
                    or not devices.switches_off(node)  # (never switched off by the room)
                    # a start brings it up: not needed until its player is there
                    or (self.sequence.running and key not in before)
                ):
                    continue
                if self.power.needed(node):
                    if (end := self.power.hold_end(node)) is not None:
                        # nothing plays: off when the idle timeout ends (shown once it
                        # lasted a moment: not between two items)
                        held[key] = self._hold_seen.get(key, now)
                        if key in before or not self._run_down_seen or now - held[key] >= HOLD_SETTLE_SECONDS:
                            runs[key] = end
                    continue
                runs[key] = min(before.get(key, now), now) if node.force_off else node.last_demand + self.power.run_down(node)
        self.run_down_at = runs
        self._hold_seen = held
        if not self._run_down_seen:
            # the first look after the start (a restart: the run-down starts anew,
            # what ran down before is no "start")
            self._run_down_seen = self._started
            return
        # (to the second, as the sensors show it)
        at = lambda t: dt_util.utc_from_timestamp(int(t)).isoformat()  # noqa: E731
        # new, or its time changed while it holds (e.g. night mode: a shorter idle timeout)
        started = {
            k: at(t) for k, t in runs.items()
            if k not in before or (k in held and int(t) != int(before[k]))
        }
        if started:
            self.async_fire(EVENT_RUN_DOWN, {"type": "started", "devices": started})
        gone = [k for k in before if k not in runs]
        is_off = lambda k: k not in nodes or not devices.node_on(nodes[k])  # noqa: E731
        # went off after its run-down (not by hand before, nor by a room off)
        ran_down = [k for k in gone if is_off(k) and k not in cut and now >= before[k] - 1]
        for key in ran_down:
            self.async_fire(EVENT_RUN_DOWN, {"type": "off", "device": key, "name": self.node_name(key) if key in nodes else key})
        kept = [k for k in gone if k not in ran_down]
        if kept:
            if self.keep_on:
                reason = "kept_on"
            elif any(k in nodes and self.power.needed_playing(nodes[k]) for k in kept):
                reason = "needed"
            elif reasons := [cut[k] for k in kept if k in cut]:
                reason = "all_off" if "all_off" in reasons else reasons[0]
            elif self.manual:
                reason = "manual"
            elif any(is_off(k) for k in kept):
                reason = "switched_off"  # (by hand, before its time)
            else:
                reason = "automatic_off"
            self.async_fire(EVENT_RUN_DOWN, {"type": "cancelled", "devices": kept, "reason": reason})

    async def async_set_auto_power(self, on: bool) -> None:
        self.data["auto_power"] = on
        if on:
            self.manual = False
        self._save()
        await self.async_update("auto_power")

    async def async_set_setting(self, key: str, value: Any) -> None:
        """A setting of the room (checked by the caller)."""
        self.data[key] = value
        self._save()
        await self.async_update(key)
        if key == "night_max_volume":
            await self.night.async_limit_changed()
        elif key.startswith("preset_") and (name := key.removeprefix("preset_")) in PRESETS:
            await self.volume.async_preset_changed(name)

    async def async_set_device_setting(self, device_id: str, key: str, value: Any) -> None:
        """A setting of one of the room's devices (checked by the caller)."""
        self.data.setdefault("devices", {}).setdefault(device_id, {})[key] = value
        self._save()
        if key == "night_off":
            self.night.async_cut_disabled()  # set off at night during the night: off now, as with night mode
        await self.async_update(key)

    async def async_set_source_setting(self, source: str, key: str, value: Any) -> None:
        """A setting of a player for the plug of its hardware (checked by the caller)."""
        self.data.setdefault("sources", {}).setdefault(source, {})[key] = value
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

