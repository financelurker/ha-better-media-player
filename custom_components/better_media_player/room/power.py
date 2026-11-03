"""The room's devices as power nodes: what it has, and when each is needed.

node     A device of the room (model.py: display, receiver, speakers, …) or the
         hardware plug of one of its players (video_host / audio_host).
demand   A node is demanded while a source whose route it is on is in use: it
         plays, or nothing plays for less than the idle timeout (paused,
         stopped, a menu; the controller's hold). Then it goes off at once; a
         player's plug waits off_wait after the player is off (it shuts down).
order    Players' plugs first, then what carries the picture, then the player,
         then the sound devices: each group from the source on (a receiver before
         the speakers it drives); off the other way round, the player between the
         sound and the picture (a player the room turns off).

The nodes belong to the device layer (devices/), which switches them; the
room owns them and answers what only it knows: demand, run-down, standby, and
what a change from outside means for the room (manual mode, held devices).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.media_player import MediaPlayerState
from homeassistant.core import State, callback

from ..const import CONF_START_TIMEOUT, ROLE_AUDIO_HOST, ROLE_VIDEO_HOST, SOURCE_AUDIO
from ..devices import DeviceGroup, Node, Standby, get_control
from ..devices.const import ACTION_ID
from ..devices.graph import Route, route
from ..devices.model import Edge, Mode, Signal
from ..model import (
    HOST_OF,
    KIND_GIVES,
    NEEDS,
    OPT_CABLES,
    control_of,
    device_setting,
    devices_of,
    inputs_of,
    shows_picture,
    source_setting,
    sources_of,
    switch_order,
)

if TYPE_CHECKING:
    from .controller import RoomController

_LOGGER = logging.getLogger(__name__)

HOSTS = (ROLE_VIDEO_HOST, ROLE_AUDIO_HOST)
PLAYS, HOLD = "plays:", "hold:"  # why a node is needed (RoomPower.reasons)


def edges_of(options: dict) -> list[Edge]:
    """The room's cables."""
    return [
        Edge(
            source=c["from"],
            target=c["to"],
            signals=frozenset(Signal(x) for x in c.get("signals") or ()),
            input=c.get("input") or None,
            mode=Mode(c.get("mode") or Mode.BMP),
        )
        for c in options.get(OPT_CABLES) or []
    ]


# playing or paused: in use (also what a group shares, and what can be handed over)
GROUP_ACTIVE_STATES = {MediaPlayerState.PLAYING, MediaPlayerState.PAUSED, MediaPlayerState.BUFFERING}


@dataclass
class RoomNode(Node):
    """A node of the room: one of its devices, or a player's hardware plug."""

    device: dict | None = None  # the device's setup (None: a player's plug)
    picture: bool = False  # gives or carries the picture: on before the player


class RoomPower:
    """The room's devices: what it has, when they are needed; owner of their nodes."""

    def __init__(self, ctrl: RoomController) -> None:
        self.ctrl = ctrl
        self.name = ctrl.entry.title
        options = ctrl.opts
        edges = edges_of(options)
        every: dict[str, RoomNode] = {}  # also the passive devices (mixer, passive speakers): the routes pass them
        for d in devices_of(options):
            p = d.get("power") or {}
            entities = [*(p.get("plugs") or []), *(p.get("devices") or [])]
            actions = {}
            if p.get("actions"):  # own on/off actions count as one more device (assumed state)
                actions[f"{ACTION_ID}.{d['id']}"] = tuple(p["actions"])
                entities.append(f"{ACTION_ID}.{d['id']}")
            gives = KIND_GIVES.get(d["kind"], frozenset())
            every[d["id"]] = RoomNode(
                key=d["id"],
                entities=entities,
                sources=(),  # from the routes, below
                power=list(p.get("sensors") or []) or None,
                actions=actions,
                gives=gives,
                inputs=inputs_of(d),
                control=control_of(d),
                device=d,
                picture=shows_picture(d, options.get(OPT_CABLES) or []),
            )
        # what each source needs: its route through the cables
        self.routes: dict[str, Route] = {key: route(key, NEEDS[key], every, edges) for key in ctrl.sources}
        for node in every.values():
            node.sources = tuple(s for s, r in self.routes.items() if node.key in r.nodes)
        # what is switched: devices with power (plugs, devices, own actions) and the players' plugs
        self.nodes: dict[str, RoomNode] = {k: n for k, n in every.items() if n.entities}
        for key, src in sources_of(options).items():
            if src.get("host") and key in ctrl.sources:
                self.nodes[HOST_OF[key]] = RoomNode(key=HOST_OF[key], entities=[src["host"]], sources=(key,))
        # the order to switch on: plugs, picture, sound; each from the source on
        cables = list(options.get(OPT_CABLES) or [])
        devs = switch_order([n.device for n in self.nodes.values() if n.device is not None], cables, list(ctrl.sources))
        self.on_order: list[str] = [h for h in HOSTS if h in self.nodes] + devs
        self.off_order: list[str] = list(reversed(devs)) + [h for h in HOSTS if h in self.nodes]
        self.devices: DeviceGroup = get_control(ctrl.hass).register(ctrl.entry.entry_id, self, list(self.nodes.values()))

    # ------------------------------------------------- the room's kinds of nodes

    def sound_nodes(self, source: str = SOURCE_AUDIO) -> list[RoomNode]:
        """The devices on a source's route that do not carry the picture (speakers,
        receiver, subwoofer, …), without the player's plug."""
        return [n for n in self.nodes.values() if n.device is not None and not n.picture and source in n.sources]

    def device_setting(self, node: RoomNode, key: str) -> Any:
        return device_setting(self.ctrl.data, node.device, key)

    def host_setting(self, node: RoomNode, key: str) -> Any:
        """A setting of the player whose hardware plug this is."""
        return source_setting(self.ctrl.data, node.sources[0], key)

    def unregister(self) -> None:
        get_control(self.ctrl.hass).unregister(self.ctrl.entry.entry_id, self.devices)

    # ------------------------------------------------- owner of the nodes

    @property
    def automatic(self) -> bool:
        """Devices go off by themselves: not while the room is kept on."""
        return self.ctrl.auto_power and not self.ctrl.keep_on

    def demanded(self, node: Node) -> bool:
        return self.needed(self.nodes[node.key])

    def enabled(self, node: Node) -> bool:
        return self.ctrl.night.enabled(self.nodes[node.key])

    def on_wait(self, node: Node) -> float:
        n = self.nodes[node.key]
        value = self.device_setting(n, "on_wait") if n.device is not None else self.host_setting(n, "on_wait")
        return float(value or 0)

    def run_down(self, node: Node) -> float:
        """A device goes off once no player uses it; a player's plug waits for the
        player to shut down."""
        n = self.nodes[node.key]
        if n.device is None:
            return float(self.host_setting(n, "off_wait") or 0) * 60.0
        return 0.0

    def waits(self, node: Node) -> bool:
        """The picture goes off after the player: one whose idle timeout ran out
        waits for its sound devices first, then for being off (a TV that the
        player turns off over HDMI-CEC goes with it)."""
        n = self.nodes[node.key]
        return n.picture and any(self.ctrl.player_going_off(self.ctrl.sources[s]) for s in n.sources if s in self.ctrl.sources)

    def standby(self, node: Node) -> Standby | None:
        n = self.nodes[node.key]
        if not self.waits_for_standby(n):
            return None
        return Standby(
            watts=float(self.device_setting(n, "standby_w")),
            max_seconds=float(self.device_setting(n, "standby_max")) * 60,
        )

    def start_timeout(self) -> float:
        return float(self.ctrl.setting(CONF_START_TIMEOUT))

    async def call(self, domain: str, service: str, data: dict) -> bool:
        return await self.ctrl._call(domain, service, data)

    async def run_action(self, action: str, on: bool) -> None:
        await self.ctrl._run_action(action, on)

    def assumed(self, entity_id: str) -> bool:
        return bool(self.ctrl.data.get("assumed", {}).get(entity_id))

    def set_assumed(self, entity_id: str, on: bool) -> None:
        self.ctrl.data.setdefault("assumed", {})[entity_id] = on
        self.ctrl._save()

    async def wait_until(self, cond: Callable[[], bool], timeout: float) -> bool:
        return await self.ctrl._wait_until(cond, timeout)

    # ----------------------------------------------------------------- inputs

    def want_inputs(self, source: str | None) -> None:
        """The inputs of source's route are wanted (None: no source, no wishes)."""
        r = self.routes.get(source) if source else None
        self.devices.want_inputs(source if r else None, r.inputs if r else {})

    # ---------------------------------------------------------------- reading

    @property
    def held(self) -> set[str]:
        """Devices left off until the next start."""
        return self.devices.held

    def needed(self, node: RoomNode) -> bool:
        """Demanded right now."""
        return bool(self.reasons(node))

    def needed_playing(self, node: RoomNode) -> bool:
        """Needed by more than a player that does not play (one that plays, a wake, …)."""
        return any(not r.startswith(HOLD) for r in self.reasons(node))

    def hold_end(self, node: RoomNode) -> float | None:
        """When a needed node goes off: only players that do not play need it (the
        end of their idle timeout); None: needed for longer (it plays, …)."""
        if not self.needed(node):
            return None
        ctrl = self.ctrl
        if self.needed_playing(node):
            return None  # needed by more than a hold
        # (no end: no idle timeout, kept on, automatic power off – for every player alike)
        ends = [e for s in node.sources if (src := ctrl.sources.get(s)) is not None and src.in_use and (e := ctrl.hold_end(src)) is not None]
        if not ends:
            return None
        end = max(ends)
        return end + self.run_down(node) if node.device is None else end

    def reasons(self, node: RoomNode) -> list[str]:
        """Why the node is needed right now (none: it is not): "plays:<source>",
        "hold:<source>" (it does not play, its idle timeout runs), "wake",
        "announce", "prewarm", "shutdown:<source>" (a player's plug while the
        player shuts down), "restart:<source>" (its player has not reported yet)."""
        ctrl = self.ctrl
        if not ctrl.night.enabled(node):
            return []  # night mode: e.g. no subwoofer
        out: list[str] = []
        if ctrl.wake.holds(node):
            out.append("wake")  # snoozed: the room stays ready
        if ctrl.announce.holds(node):
            out.append("announce")
        if ctrl.follow.prewarm and not ctrl.quiet and self.prewarms(node):
            out.append("prewarm")
        for s in node.sources:
            if (src := ctrl.sources.get(s)) is None:
                continue
            if src.in_use:
                out.append(f"{PLAYS if src.hold_since is None else HOLD}{s}")
            if node.device is None and self.devices.node_on(node) and ctrl.player_shutting_down(src):
                out.append(f"shutdown:{s}")  # a player's plug: not while the player is still on
            if self.devices.node_on(node) and ctrl.not_reported(src):
                out.append(f"restart:{s}")  # (after a restart: kept until its player reported)
        return out

    def prewarms(self, node: RoomNode) -> bool:
        """Follow me prewarms the music's sound devices and the music player's plug."""
        return node.key == ROLE_AUDIO_HOST or node in self.sound_nodes()

    def demand(self, picture: bool) -> bool:
        """A device for the picture (else: for the sound) is demanded."""
        return any(n.picture is picture and self.needed(n) for n in self.nodes.values() if n.device is not None)

    def waits_for_standby(self, node: RoomNode) -> bool:
        """Wait for standby before its plug goes off (only with power sensors)."""
        return self.can_standby(node) and bool(self.device_setting(node, "standby"))

    def can_standby(self, node: RoomNode) -> bool:
        """Power sensors and a plug to cut after the standby."""
        return (
            node.device is not None
            and bool(node.power)
            and any(self.devices.kind(e) == "plug" for e in node.entities)
        )

    def fully_off(self) -> bool:
        """Room off, and every device has reported (not still unknown after a start)."""
        ctrl = self.ctrl
        if ctrl.room_on:
            return False
        # devices are often gone while off (behind their plug, deep standby)
        devices = self.devices
        ids = [s.player for s in ctrl.sources.values()] + [e for e in devices.entities if devices.kind(e) == "plug"]
        return all(devices.is_available(e) for e in ids)

    # --------------------------------------------------------- outside changes

    def is_own_change(self, entity_id: str, new: State) -> bool:
        ctx = new.context
        own = self.ctrl._own_contexts
        if ctx is not None and (ctx.id in own or ctx.parent_id in own):
            return True
        return self.devices.recent_command(entity_id, new)

    @callback
    def outside_change(self, entity_id: str, on: bool) -> None:
        """A power device was switched by someone else.

        A device (TV, AVR) turned off while needed stays off until the next start,
        everything else goes on automatically; anything else (a plug, a device
        turned on) is the user's: manual mode until everything is off."""
        ctrl = self.ctrl
        kind = self.devices.kind(entity_id)
        demanded = ctrl.sequence.running or any(
            entity_id in n.entities and self.needed(n) for n in self.nodes.values()
        )
        if kind == "device" and demanded:
            # needed anyway (e.g. woken by another device over HDMI-CEC): stays automatic
            if on:
                self.held.discard(entity_id)
            else:
                _LOGGER.info("%s: %s turned off from outside, left off until the next start", ctrl.entry.title, entity_id)
                self.held.add(entity_id)
                self.devices.forget_command(entity_id)
            return
        if kind == "device" and not on:
            return
        if kind == "device" and self.devices.control_of(entity_id).on is not Mode.BMP:
            return  # switched on by CEC or by hand, as set up: no manual change
        _LOGGER.info(
            "%s: %s switched %s from outside, switching to manual mode",
            ctrl.entry.title,
            entity_id,
            "on" if on else "off",
        )
        ctrl.manual = True
