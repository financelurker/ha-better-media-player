"""Test fixtures: a fake home with plugs and media players."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.media_player import MediaPlayerEntityFeature as F
from homeassistant.core import HomeAssistant, ServiceRegistry

from custom_components.better_media_player.const import (
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

KODI_FEATURES = int(
    F.TURN_ON | F.TURN_OFF | F.PLAY | F.PAUSE | F.STOP | F.VOLUME_SET | F.VOLUME_MUTE | F.SEEK
)
MA_FEATURES = int(
    F.PAUSE | F.SEEK | F.VOLUME_SET | F.VOLUME_MUTE | F.PREVIOUS_TRACK | F.NEXT_TRACK
    | F.PLAY_MEDIA | F.VOLUME_STEP | F.STOP | F.PLAY | F.GROUPING
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@dataclass
class FakeHome:
    hass: HomeAssistant
    calls: list[tuple[str, str, str]] = field(default_factory=list)
    kodi_boots: bool = False  # turn_on makes Kodi idle immediately
    kodi_suspends: bool = True  # turn_off makes Kodi off immediately

    def set(self, entity_id: str, state: str, **attrs: Any) -> None:
        old = self.hass.states.get(entity_id)
        base = dict(old.attributes) if old else {}
        base.update(attrs)
        self.hass.states.async_set(entity_id, state, base)

    def kodi(self, state: str, **attrs: Any) -> None:
        self.set(KODI, state, supported_features=KODI_FEATURES, friendly_name="Kodi", **attrs)

    def music(self, state: str, **attrs: Any) -> None:
        self.set(MUSIC, state, supported_features=MA_FEATURES, friendly_name="Office Player",
                 mass_player_type="player", **attrs)

    def group(self, state: str, members: list[str], entity_id: str = GROUP, **attrs: Any) -> None:
        name = "All Players" if entity_id == GROUP else "All Players (follow)"
        self.set(entity_id, state, group_members=members, mass_player_type="group", friendly_name=name,
                 supported_features=MA_FEATURES, **attrs)
        if state in ("playing", "paused"):
            for m in members:
                self.set(m, state)

    def kitchen(self, state: str, **attrs: Any) -> None:
        self.set(KITCHEN, state, supported_features=MA_FEATURES, friendly_name="Kitchen Player",
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
    def handle(self, domain: str, service: str, entity_id: str, data: dict, context) -> bool:
        st = self.hass.states.get(entity_id)
        if st is None:
            return False
        self.calls.append((domain, service, entity_id))
        if entity_id.startswith(("switch.", "light.", "input_boolean.")):
            if service in ("turn_on", "turn_off"):
                self.hass.states.async_set(
                    entity_id, "on" if service == "turn_on" else "off", st.attributes, context=context
                )
            return True
        if domain == "media_player":
            if entity_id == KODI and service == "turn_on" and self.kodi_boots:
                self.kodi("idle")
            elif entity_id == KODI and service == "turn_off" and self.kodi_suspends:
                self.kodi("off")
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
            elif service == "volume_set":
                self.set(entity_id, st.state, volume_level=data["volume_level"])
            elif service in ("media_play", "play_media") and entity_id in GROUPS:
                if self.members(entity_id):
                    self.group("playing", self.members(entity_id), entity_id)
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
            "all_players", "all_players_follow", "kitchen_player", "kitchen_speakers",
        }:
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


async def setup_room(hass: HomeAssistant, **overrides: Any) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Wohnzimmer",
        data={CONF_ENTRY_TYPE: ENTRY_TYPE_ROOM},
        options=room_options(**overrides),
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
