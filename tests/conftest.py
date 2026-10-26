"""Test fixtures: a fake home with plugs and media players."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.media_player import MediaPlayerEntityFeature as F
from homeassistant.core import HomeAssistant, ServiceRegistry, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from custom_components.better_media_player.const import (
    CONF_FOLLOW_PLAYER,
    CONF_OCCUPANCY,
    ENTRY_TYPE_HUB,
    ENTITY_DEFAULTS,
    HUB_ENTITY_DEFAULTS,
    CONF_AUDIO_PLAYER,
    CONF_BASS,
    CONF_DISPLAY,
    CONF_ENTRY_TYPE,
    CONF_SPEAKERS,
    CONF_VIDEO_PLAYER,
    DEFAULTS,
    DOMAIN,
    ENTRY_TYPE_ROOM,
)

KODI = "media_player.kodi"
MUSIC = "media_player.office_player"
GROUP = "media_player.all_players"
TV = "switch.tv_plug"
SPEAKERS = "switch.speaker_plug"
BASS = "switch.bass_plug"
TV_POWER = "sensor.tv_power"
FOLLOW = "media_player.all_players_follow"
KITCHEN = "media_player.kitchen_player"
KITCHEN_SPK = "switch.kitchen_speakers"
LIVING_OCC = "binary_sensor.living_occupancy"
KITCHEN_OCC = "binary_sensor.kitchen_occupancy"
GROUPS = (GROUP, FOLLOW)
AVR = "media_player.avr"  # volume device behind the speaker plug
AMP = "number.amp_volume"  # volume device as a number (-80..0 dB)
LG = "media_player.lg_tv"  # a TV that turns itself on/off (role device)
HARMONY = "remote.harmony"  # a remote as a role device
IR_ON, IR_OFF = "script.ir_sub_on", "script.ir_sub_off"  # own on/off actions
KODI2 = "media_player.kodi_bedroom"  # a second video player (bedroom)
BED_TV = "switch.bedroom_tv"
AVR_FEATURES = int(F.VOLUME_SET | F.VOLUME_MUTE | F.VOLUME_STEP | F.TURN_ON | F.TURN_OFF)

KODI_FEATURES = int(
    F.TURN_ON | F.TURN_OFF | F.PLAY | F.PAUSE | F.STOP | F.VOLUME_SET | F.VOLUME_MUTE | F.SEEK
    | F.PLAY_MEDIA | F.BROWSE_MEDIA | F.MEDIA_ENQUEUE
)
MA_FEATURES = int(
    F.PAUSE | F.SEEK | F.VOLUME_SET | F.VOLUME_MUTE | F.PREVIOUS_TRACK | F.NEXT_TRACK
    | F.PLAY_MEDIA | F.VOLUME_STEP | F.STOP | F.PLAY | F.GROUPING
    | F.BROWSE_MEDIA | F.SEARCH_MEDIA | F.MEDIA_ENQUEUE
)


def _item(title, media_type, media_id, can_play=True, can_expand=False, children=None, media_class="video"):
    return {
        "title": title, "media_class": media_class, "media_content_type": media_type,
        "media_content_id": media_id, "can_play": can_play, "can_expand": can_expand,
        "can_search": False, "thumbnail": f"/thumb/{media_id}", "children_media_class": None,
        "not_shown": 0, "children": children or [],
    }


# what the fake players show in their media browsers
BROWSE_TREES = {
    ("media_player.kodi", None, None): _item("Kodi", "library", "library", False, True, [
        _item("Queue", "better_kodi_queue", "better_kodi://queue", False, True, media_class="playlist"),
        _item("Movies", "library", "movies", False, True, media_class="directory"),
    ], media_class="directory"),
    ("media_player.kodi", "library", "movies"): _item("Movies", "library", "movies", False, True, [
        _item("Heat", "movie", "123", media_class="movie"),
    ], media_class="directory"),
    ("media_player.kodi", "better_kodi_queue", "better_kodi://queue"): _item(
        "Queue", "better_kodi_queue", "better_kodi://queue", False, True, [
            _item("▶ Heat", "better_kodi_queue", "better_kodi://queue/0", media_class="movie"),
        ], media_class="playlist"),
    ("media_player.office_player", None, None): _item("Music Assistant", "music", "", False, True, [
        _item("Playlists", "playlists", "playlists", False, True, media_class="directory"),
    ], media_class="directory"),
    ("media_player.office_player", "playlists", "playlists"): _item("Playlists", "playlists", "playlists", False, True, [
        _item("Road trip", "playlist", "library://playlist/1", True, True, media_class="playlist"),
        _item("All Summer Long", "track", "library://track/9", media_class="track"),
    ], media_class="directory"),
}
SEARCH_RESULTS = {
    "media_player.office_player": [_item("All Summer Long", "track", "library://track/9", media_class="track")],
}
MA_QUEUE = {
    "queue_id": "office", "active": True, "items": 12,
    "current_item": {"queue_item_id": "q1", "name": "Kid Rock - All Summer Long",
                     "media_item": {"name": "All Summer Long", "image": "http://ma/img/1.jpg", "uri": "library://track/9"}},
    "next_item": {"queue_item_id": "q2", "name": "Kid Rock - Bawitdaba",
                  "media_item": {"name": "Bawitdaba", "image": None, "uri": "library://track/10"}},
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def _live_position(state) -> float:
    position = float(state.attributes.get("media_position") or 0)
    updated = state.attributes.get("media_position_updated_at")
    if state.state == "playing" and updated is not None:
        position += (dt_util.utcnow() - updated).total_seconds()
    return position


@dataclass
class FakeHome:
    hass: HomeAssistant
    calls: list[tuple[str, str, str]] = field(default_factory=list)
    played: list[tuple[str, str, dict]] = field(default_factory=list)
    transferred: list[tuple[str, str]] = field(default_factory=list)
    handovers: list[tuple[str, str, bool]] = field(default_factory=list)
    kodi_boots: bool = False  # turn_on makes Kodi idle immediately
    kodi_suspends: bool = True  # turn_off makes Kodi off immediately
    avr_memory: float = 0.4  # the AVR's own volume when its power comes back
    avr_steps: int = 0  # >0: the AVR reports its volume in 1/avr_steps steps
    transfer_restarts: bool = False  # a moved queue starts its track from the beginning
    transfer_reports: bool = True  # the target reports its position right away
    transfer_track: str | None = None  # the target is at another track by then
    clock_skew: float = 0.0  # s the clock of Music Assistant's server is ahead of HA's
    lg_on_plug: bool = False  # the LG TV hangs on the TV plug (unavailable without power)
    lg_obeys: bool = True  # the LG TV follows turn_on / turn_off
    volume_lag: float = 0.0  # s a player reports a volume it was set to (in order)
    ma_power: bool = False  # the music players have Music Assistant's (fake) power control
    ma_announce: bool = False  # the music players announce (Music Assistant)
    announce_hold: Any = None  # an asyncio.Event: an announcement lasts until it is set
    announcements: list[tuple[str, dict]] = field(default_factory=list)

    def now(self):
        """The time on Music Assistant's server."""
        return dt_util.utcnow() + timedelta(seconds=self.clock_skew)

    def avr(self, state: str, **attrs: Any) -> None:
        self.set(AVR, state, supported_features=AVR_FEATURES, friendly_name="AV Receiver", **attrs)

    def amp(self, value: float, **attrs: Any) -> None:
        self.set(AMP, str(value), min=-80.0, max=0.0, step=1.0, friendly_name="Amp", **attrs)

    def set(self, entity_id: str, state: str, **attrs: Any) -> None:
        old = self.hass.states.get(entity_id)
        base = dict(old.attributes) if old else {}
        base.update(attrs)
        self.hass.states.async_set(entity_id, state, base)

    def kodi(self, state: str, **attrs: Any) -> None:
        self.set(KODI, state, supported_features=KODI_FEATURES, friendly_name="Kodi", **attrs)

    def _ma_features(self) -> int:
        features = MA_FEATURES | (int(F.TURN_ON | F.TURN_OFF) if self.ma_power else 0)
        return features | (int(F.MEDIA_ANNOUNCE) if self.ma_announce else 0)

    async def announce(self, entity_id: str, data: dict) -> None:
        """Music Assistant's announcement: the player plays it (at the given
        volume), then everything is as before; the call returns after that."""
        self.calls.append(("media_player", "announce", entity_id))
        self.announcements.append((entity_id, dict(data)))
        st = self.hass.states.get(entity_id)
        before = (st.state, dict(st.attributes))
        level = (data.get("extra") or {}).get("announce_volume")
        attrs = dict(st.attributes)
        if level is not None:
            attrs["volume_level"] = level / 100
        if st.state == "playing":
            self.hass.states.async_set(entity_id, "paused", st.attributes)  # the music holds
        self.hass.states.async_set(entity_id, "playing", attrs)
        if self.announce_hold is not None:
            await self.announce_hold.wait()
        self.hass.states.async_set(entity_id, *before)

    def music(self, state: str, **attrs: Any) -> None:
        self.set(MUSIC, state, supported_features=self._ma_features(), friendly_name="Office Player",
                 mass_player_type="player", **attrs)

    def group(self, state: str, members: list[str], entity_id: str = GROUP, **attrs: Any) -> None:
        name = "All Players" if entity_id == GROUP else "All Players (follow)"
        self.set(entity_id, state, group_members=members, mass_player_type="group", friendly_name=name,
                 supported_features=MA_FEATURES, **attrs)
        if state in ("playing", "paused"):
            for m in members:
                self.set(m, state)

    def kitchen(self, state: str, **attrs: Any) -> None:
        self.set(KITCHEN, state, supported_features=self._ma_features(), friendly_name="Kitchen Player",
                 mass_player_type="player", **attrs)

    def members(self, group: str) -> list[str]:
        st = self.hass.states.get(group)
        return list(st.attributes.get("group_members", [])) if st else []

    def services(self, entity_id: str | None = None) -> list[tuple[str, str, str]]:
        return [c for c in self.calls if entity_id is None or c[2] == entity_id]

    def on(self, entity_id: str) -> bool:
        st = self.hass.states.get(entity_id)
        return st is not None and st.state == "on"

    # ------------------------------------------------------------ simulation
    def respond(self, service: str, entity_id: str, data: dict) -> dict:
        from homeassistant.exceptions import HomeAssistantError

        self.calls.append(("media_player", service, entity_id))
        if service == "browse_media":
            key = (entity_id, data.get("media_content_type"), data.get("media_content_id"))
            if key not in BROWSE_TREES:
                raise HomeAssistantError(f"unknown {key}")
            return {entity_id: BROWSE_TREES[key]}
        if service == "search_media":
            return {entity_id: {"result": SEARCH_RESULTS.get(entity_id, [])}}
        return {entity_id: dict(MA_QUEUE)}

    def handle(self, domain: str, service: str, entity_id: str, data: dict, context) -> bool:
        st = self.hass.states.get(entity_id)
        if st is None:
            return False
        self.calls.append((domain, service, entity_id))
        self.played.append((service, entity_id, dict(data)))
        if entity_id.startswith(("switch.", "light.", "input_boolean.")):
            if service in ("turn_on", "turn_off"):
                self.hass.states.async_set(
                    entity_id, "on" if service == "turn_on" else "off", st.attributes, context=context
                )
                if entity_id == TV and self.lg_on_plug:
                    # the TV comes up (off) with power, and is gone without
                    self.hass.states.async_set(LG, "off" if service == "turn_on" else "unavailable", {})
                avr = self.hass.states.get(AVR)
                if entity_id == SPEAKERS and avr is not None:
                    # the AVR hangs on the speaker plug: its own last volume comes back
                    if service == "turn_on":
                        self.avr("on", volume_level=self.avr_memory, is_volume_muted=False)
                    else:
                        if avr.attributes.get("volume_level") is not None:
                            self.avr_memory = avr.attributes["volume_level"]
                        self.hass.states.async_set(AVR, "off", {"supported_features": AVR_FEATURES})
            return True
        if domain in ("number", "input_number") and service == "set_value":
            self.set(entity_id, str(float(data["value"])))
            return True
        if entity_id == AVR:
            vol = st.attributes.get("volume_level") or 0
            if service == "volume_set":
                v = data["volume_level"]
                if self.avr_steps:
                    v = round(v * self.avr_steps) / self.avr_steps
                self.set(AVR, st.state, volume_level=round(v, 4))
            elif service in ("volume_up", "volume_down"):
                self.set(AVR, st.state, volume_level=round(min(1, max(0, vol + (0.05 if service == "volume_up" else -0.05))), 3))
            elif service == "volume_mute":
                self.set(AVR, st.state, is_volume_muted=data["is_volume_muted"])
            return True
        if entity_id in (LG, HARMONY) and service in ("turn_on", "turn_off"):
            if self.lg_obeys or entity_id != LG:
                self.hass.states.async_set(entity_id, "on" if service == "turn_on" else "off", st.attributes, context=context)
            return True
        if domain == "better_kodi" and service == "handover":
            # the playback goes on at the destination; the source stops unless kept
            self.handovers.append((entity_id, data["destination"], data.get("keep_source_playing", False)))
            self.set(data["destination"], "playing", media_title=st.attributes.get("media_title"))
            if not data.get("keep_source_playing"):
                self.set(entity_id, "idle")
            return True
        if domain == "music_assistant" and service == "transfer_queue":
            # the queue moves: the target plays it (or holds it paused), the source stops
            source = data.get("source_player")
            self.transferred.append((source, entity_id))
            track = {}
            if source and (src := self.hass.states.get(source)) and src.attributes.get("media_content_id"):
                position = 0.0 if self.transfer_restarts else _live_position(src)
                track = {
                    "media_content_id": self.transfer_track or src.attributes["media_content_id"],
                    "media_duration": src.attributes.get("media_duration"),
                }
                if self.transfer_reports:
                    track.update(media_position=position, media_position_updated_at=self.now())
            if source:
                self.set(source, "idle")
            state = "playing" if data.get("auto_play", True) else "paused"
            if entity_id in GROUPS:
                if self.members(entity_id):
                    self.set(entity_id, self.hass.states.get(entity_id).state, **track)
                    self.group(state, self.members(entity_id), entity_id)
            else:
                self.set(entity_id, state, **track)
            return True
        if domain == "media_player":
            if entity_id in (KODI, KODI2) and service == "turn_on" and self.kodi_boots:
                self.set(entity_id, "idle", supported_features=KODI_FEATURES)
            elif entity_id in (KODI, KODI2) and service == "turn_off" and self.kodi_suspends:
                self.set(entity_id, "off")
            elif entity_id in (MUSIC, KITCHEN) and service == "turn_on" and self.ma_power:
                if st.state == "off":
                    self.set(entity_id, "idle")
            elif entity_id in (MUSIC, KITCHEN) and service == "turn_off" and self.ma_power:
                self._leave_groups(entity_id)
                self.set(entity_id, "off")  # Music Assistant stops it too
            elif service == "unjoin":
                self._leave_groups(entity_id)
                self.set(entity_id, "idle")
            elif service == "join" and entity_id in GROUPS:
                g = self.hass.states.get(entity_id)
                for p in data.get("group_members", []):
                    self._leave_groups(p, except_group=entity_id)  # MA moves it silently
                    members = self.members(entity_id)
                    if p not in members:
                        members.append(p)
                    self.set(entity_id, g.state, group_members=members)
                    self.set(p, g.state if g.state in ("playing", "paused") else "idle")
            elif service == "volume_set" and self.volume_lag > 0:
                level = data["volume_level"]

                @callback
                def _report(_now, entity_id=entity_id, level=level) -> None:
                    cur = self.hass.states.get(entity_id)
                    self.set(entity_id, cur.state, volume_level=level)

                async_call_later(self.hass, self.volume_lag, _report)
            elif service == "volume_set":
                self.set(entity_id, st.state, volume_level=data["volume_level"])
            elif service in ("media_play", "play_media") and entity_id in GROUPS:
                if self.members(entity_id):
                    self.group("playing", self.members(entity_id), entity_id)
            elif service == "media_seek":
                self.set(entity_id, st.state, media_position=float(data["seek_position"]),
                         media_position_updated_at=self.now())
            elif service == "media_stop":
                self.set(entity_id, "idle")
            elif service == "media_pause":
                self.set(entity_id, "paused")
            return True
        return True

    def _leave_groups(self, player: str, except_group: str | None = None) -> None:
        for gid in GROUPS:
            if gid == except_group:
                continue
            g = self.hass.states.get(gid)
            if g is None or player not in g.attributes.get("group_members", []):
                continue
            members = [m for m in g.attributes["group_members"] if m != player]
            if members:
                self.set(gid, g.state, group_members=members)
            else:
                # Music Assistant pauses a group when its last member leaves
                self.set(gid, "paused" if g.state == "playing" else g.state, group_members=[], volume_level=0)


@pytest.fixture
def home(hass: HomeAssistant):
    fake = FakeHome(hass)
    original = ServiceRegistry.async_call

    async def fake_call(self, domain, service, service_data=None, blocking=False, context=None, *a, **kw):
        ids = (service_data or {}).get("entity_id")
        if isinstance(ids, str) and ids.split(".")[1] in {
            "tv_plug", "speaker_plug", "bass_plug", "kodi", "office_player", "kodi_box_plug", "wol",
            "all_players", "all_players_follow", "kitchen_player", "kitchen_speakers", "avr", "amp_volume",
            "lg_tv", "harmony", "ir_sub_on", "ir_sub_off", "kodi_bedroom", "bedroom_tv",
        }:
            if service in ("browse_media", "search_media", "get_queue"):
                return fake.respond(service, ids, service_data or {})
            if service == "play_media" and (service_data or {}).get("announce"):
                return await fake.announce(ids, service_data)
            if fake.handle(domain, service, ids, service_data, context):
                return None
        return await original(self, domain, service, service_data, blocking, context, *a, **kw)

    with patch.object(ServiceRegistry, "async_call", fake_call):
        for plug in (TV, SPEAKERS, BASS):
            hass.states.async_set(plug, "off")
        fake.kodi("off")
        fake.music("idle")
        yield fake


def room_options(**overrides: Any) -> dict:
    """Scenario 3: Kodi + music player, TV, shared speakers, subwoofer."""
    opts = {
        **DEFAULTS,
        CONF_VIDEO_PLAYER: KODI,
        CONF_AUDIO_PLAYER: MUSIC,
        CONF_DISPLAY: [TV],
        CONF_SPEAKERS: [SPEAKERS],
        CONF_BASS: [BASS],
    }
    opts.update(overrides)
    return {k: v for k, v in opts.items() if v is not None}


def _version(options: dict, device_keys: dict) -> int:
    """Values set on the device in a test come as options of the version before:
    the migration moves them to the device (as for an upgrade)."""
    return 2 if any(k in options for k in device_keys) else 3


async def setup_room(hass: HomeAssistant, **overrides: Any) -> MockConfigEntry:
    options = room_options(**overrides)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Wohnzimmer",
        data={CONF_ENTRY_TYPE: ENTRY_TYPE_ROOM},
        options=options,
        version=_version(options, ENTITY_DEFAULTS),
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


# ------------------------------------------------------------- follow me

async def setup_hub(hass: HomeAssistant, **overrides) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Better Media Player",
        data={CONF_ENTRY_TYPE: ENTRY_TYPE_HUB},
        options={k: v for k, v in {CONF_FOLLOW_PLAYER: FOLLOW, **overrides}.items() if v is not None},
        version=_version(overrides, HUB_ENTITY_DEFAULTS),
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def setup_kitchen(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Kitchen",
        data={CONF_ENTRY_TYPE: ENTRY_TYPE_ROOM},
        options={**DEFAULTS, CONF_AUDIO_PLAYER: KITCHEN, CONF_SPEAKERS: [KITCHEN_SPK], CONF_OCCUPANCY: [KITCHEN_OCC]},
        version=3,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.fixture
async def house(hass, home):
    """Living room (Kodi + music, offset -10) and kitchen (music only), both with occupancy."""
    hass.states.async_set(LIVING_OCC, "off")
    hass.states.async_set(KITCHEN_OCC, "off")
    hass.states.async_set(KITCHEN_SPK, "off")
    home.kitchen("idle")
    home.group("idle", [], FOLLOW)
    home.group("idle", [], GROUP)
    await setup_room(hass, occupancy=[LIVING_OCC], volume_offset=-10)
    await setup_kitchen(hass)
    await setup_hub(hass)
    return home




async def start_follow(hass: HomeAssistant, home: FakeHome, base: float = 60, offset: float = -10) -> None:
    """Music plays in the living room at base + offset; follow me is started there
    (that volume becomes follow me's base volume)."""
    home.music("playing", volume_level=round((base + offset) / 100, 3))
    await hass.async_block_till_done()
    await hass.services.async_call(DOMAIN, "start_follow", {"entity_id": "media_player.wohnzimmer"}, blocking=True)
    await hass.async_block_till_done()
