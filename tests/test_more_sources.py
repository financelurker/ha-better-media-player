"""More players per room: any number of video and music players, each a source of
its own (kind: what it needs), the player the room starts with, the main music player."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from homeassistant.components import automation
from homeassistant.components.device_automation import DeviceAutomationType, async_get_device_automations
from homeassistant.exceptions import ServiceValidationError
from homeassistant.setup import async_setup_component

from custom_components.better_media_player.room import room_device
from custom_components.better_media_player.const import CONF_ENTRY_TYPE, DOMAIN, ENTRY_TYPE_ROOM, EVENT_SOURCE_CHANGED
from custom_components.better_media_player.panel.edit import SETUP_SCHEMA, check_room, clean_setup
from custom_components.better_media_player.room.handover import target_of

from .conftest import KITCHEN, KODI, KODI2, MUSIC, SPEAKERS, TV, device
from .helpers import ROOM, tick

KITCHEN_HOST = "switch.kodi_box_plug"


def _sources(**more):
    return [
        {"id": "video", "player": KODI},
        {"id": "audio", "player": MUSIC},
        {"id": "kitchen", "kind": "audio", "player": KITCHEN, **more},
        {"id": "film2", "kind": "video", "player": KODI2},
    ]


async def _room(hass, freezer, sources=None, **opts):
    hass.states.async_set(KODI2, "off", {"friendly_name": "Kodi Bedroom"})
    hass.states.async_set(KITCHEN, "idle", {"friendly_name": "Kitchen Player", "mass_player_type": "player"})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Living Room",
        data={CONF_ENTRY_TYPE: ENTRY_TYPE_ROOM},
        options={
            "sources": sources or _sources(),
            "devices": [device("display", "display", TV), device("speakers", "speakers", SPEAKERS)],
            "cables": [],
            "volume": {"where": "players"},
            **opts,
        },
        version=4,
        minor_version=2,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    return entry


def _kitchen(home, state):
    home.kitchen(state)


async def test_a_second_music_player_needs_only_the_sound(hass, home, freezer):
    entry = await _room(hass, freezer)
    ctrl = entry.runtime_data
    assert [(s.key, s.kind) for s in ctrl.sources.values()] == [("video", "video"), ("audio", "audio"), ("kitchen", "audio"), ("film2", "video")]
    events = async_capture_events(hass, EVENT_SOURCE_CHANGED)
    _kitchen(home, "playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert home.on(SPEAKERS) and not home.on(TV)
    assert hass.states.get(ROOM).attributes["source"] == "Kitchen Player"
    assert [e.data["source"] for e in events] == ["kitchen"]
    assert hass.states.get("sensor.living_room_status").attributes["active_source"] == "kitchen"


async def test_a_second_video_player_needs_the_picture(hass, home, freezer):
    await _room(hass, freezer)
    home.set(KODI2, "idle", friendly_name="Kodi Bedroom", supported_features=0)
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert home.on(TV) and home.on(SPEAKERS)
    assert "Kodi Bedroom" in hass.states.get(ROOM).attributes["source_list"]


async def test_starting_another_player_stops_the_one_before(hass, home, freezer):
    entry = await _room(hass, freezer)
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    await hass.services.async_call("media_player", "select_source", {"entity_id": ROOM, "source": "Kitchen Player"}, blocking=True)
    await hass.async_block_till_done()
    assert ("media_player", "media_pause", MUSIC) in home.calls
    assert entry.runtime_data.last_source == "kitchen"


async def test_the_room_starts_with_the_player_set_up(hass, home, freezer):
    entry = await _room(hass, freezer, start_source="kitchen")
    ctrl = entry.runtime_data
    ctrl.data["last_source"] = "video"
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert ctrl.last_source == "kitchen" and home.on(SPEAKERS) and not home.on(TV)
    assert ctrl.wake_source == "kitchen"


async def test_without_one_the_room_starts_with_the_last_used(hass, home, freezer):
    entry = await _room(hass, freezer)
    ctrl = entry.runtime_data
    ctrl.data["last_source"] = "kitchen"
    assert ctrl.start_source == "kitchen"
    assert ctrl.wake_source == "audio", "a wake: the main music player (the first)"


async def test_the_main_music_player_while_none_plays(hass, home, freezer):
    entry = await _room(hass, freezer, music_source="kitchen")
    ctrl = entry.runtime_data
    assert ctrl.music.key == "kitchen" and ctrl.audio_player == KITCHEN
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert ctrl.music.key == "audio", "the one that plays"


async def test_without_a_main_one_the_first(hass, home, freezer):
    entry = await _room(hass, freezer)
    assert entry.runtime_data.music.key == "audio"
    assert entry.runtime_data.video.key == "video"


async def test_a_player_s_plug(hass, home, freezer):
    entry = await _room(hass, freezer, sources=_sources(host=KITCHEN_HOST))
    power = entry.runtime_data.power
    assert "kitchen_host" in power.nodes and power.on_order[0] == "kitchen_host"
    assert power.off_order[-1] == "kitchen_host"
    assert entry.runtime_data.node_name("kitchen_host") == "Music player hardware (Kitchen Player)"


async def test_a_trigger_for_each_further_player(hass, home, freezer):
    entry = await _room(hass, freezer)
    dev = room_device(hass, entry.entry_id)
    triggers = (await async_get_device_automations(hass, DeviceAutomationType.TRIGGER, [dev.id]))[dev.id]
    started = sorted(t["subtype"] for t in triggers if t["type"] == "source_started")
    assert started == ["film2", "kitchen"]
    hits = []
    hass.services.async_register("test", "hit", lambda call: hits.append(call.data["s"]))
    assert await async_setup_component(hass, automation.DOMAIN, {automation.DOMAIN: [
        {"trigger": {"platform": "device", "domain": DOMAIN, "device_id": dev.id, "type": "source_started", "subtype": s},
         "action": {"service": "test.hit", "data": {"s": s}}}
        for s in ("kitchen", "film2")
    ]})
    _kitchen(home, "playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert hits == ["kitchen"]


async def test_a_wake_on_another_player(hass, home, freezer):
    entry = await _room(hass, freezer)
    await hass.services.async_call(DOMAIN, "wake", {"entity_id": ROOM, "media_content_id": "x", "media_content_type": "music", "source": "kitchen"}, blocking=True)
    await hass.async_block_till_done()
    assert entry.runtime_data.data["wake"]["source"] == "kitchen"
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "wake", {"entity_id": ROOM, "media_content_id": "x", "media_content_type": "music", "source": "nope"}, blocking=True)


def test_the_handover_target():
    def src(key, kind, ok=True):
        backend = SimpleNamespace(ok=ok)
        backend.handover_to = lambda other: other.ok
        return SimpleNamespace(key=key, kind=kind, backend=backend)

    music = src("audio", "audio")
    dest = SimpleNamespace(sources={s.key: s for s in (src("video", "video"), src("audio", "audio", ok=False), src("kitchen", "audio"))})
    assert target_of(dest, music).key == "kitchen", "the same id cannot take it: one of the kind that can"
    dest.sources["kitchen"].backend.ok = False
    assert target_of(dest, music).key == "audio", "none can: the same id (for the message)"
    dest = SimpleNamespace(sources={"film": src("film", "video")})
    assert target_of(dest, music) is None


# ------------------------------------------------------------------ the panel's checks


def _draft(**setup):
    base = {
        "sources": _sources(),
        "devices": [device("display", "display", TV)],
        "cables": [],
        "volume": {"where": "players"},
    }
    return SETUP_SCHEMA({**base, **setup})


def _codes(hass, setup):
    return {p["code"] for p in check_room(hass, None, setup) if p["error"]}


def test_the_kind_is_stored(hass):
    setup = clean_setup(_draft(start_source="kitchen", music_source=None))
    assert [s["kind"] for s in setup["sources"]] == ["video", "audio", "audio", "video"]
    assert setup["start_source"] == "kitchen" and "music_source" not in setup


def test_player_ids_are_checked(hass, home):
    assert _codes(hass, _draft()) == set()
    assert "bad_id" in _codes(hass, _draft(sources=[*_sources(), {"id": "Bad", "player": "media_player.x"}]))
    assert "bad_id" in _codes(hass, _draft(sources=[*_sources(), {"id": "x_host", "player": "media_player.x"}]))
    assert "dup_id" in _codes(hass, _draft(sources=[*_sources(), {"id": "kitchen", "player": "media_player.x"}]))
    assert "source_kind_fixed" in _codes(hass, _draft(sources=[{"id": "video", "kind": "audio", "player": KODI}]))


def test_a_device_cannot_have_a_player_s_id(hass, home):
    for dev_id in ("kitchen", "kitchen_host", "film2_host", "audio_host"):
        assert "reserved_id" in _codes(hass, _draft(devices=[device(dev_id, "display", TV)])), dev_id


def test_the_chosen_players_must_be_there(hass, home):
    assert "source_missing" in _codes(hass, _draft(start_source="gone"))
    assert "source_missing" in _codes(hass, _draft(music_source="gone"))
    assert "music_source_not_music" in _codes(hass, _draft(music_source="film2"))
    assert _codes(hass, _draft(start_source="film2", music_source="kitchen")) == set()


async def test_a_wake_without_a_source_takes_the_one_the_room_starts_with(hass, home, freezer):
    entry = await _room(hass, freezer, start_source="film2")
    assert entry.runtime_data.wake_source == "film2"


async def test_the_second_video_player_is_the_room_s_source(hass, home, freezer):
    await _room(hass, freezer)
    events = async_capture_events(hass, EVENT_SOURCE_CHANGED)
    home.set(KODI2, "idle")
    await hass.async_block_till_done()
    await tick(hass, freezer, 8)
    assert [e.data["source"] for e in events] == ["film2"]


async def test_the_speakers_of_every_music_player(hass, home, freezer):
    """Quiet cuts the speakers of the music: of any music player (here only a further one)."""
    entry = await _room(hass, freezer, sources=[{"id": "video", "player": KODI}, {"id": "kitchen", "kind": "audio", "player": KITCHEN}])
    assert [n.key for n in entry.runtime_data.power.sound_nodes()] == ["speakers"]


async def test_follow_me_prewarms_the_main_music_player_s_plug(hass, home, freezer):
    sources = [{"id": "audio", "player": MUSIC, "host": "switch.wol"}, {"id": "kitchen", "kind": "audio", "player": KITCHEN, "host": KITCHEN_HOST}]
    entry = await _room(hass, freezer, sources=sources, music_source="kitchen")
    power = entry.runtime_data.power
    assert power.prewarms(power.nodes["kitchen_host"]) and not power.prewarms(power.nodes["audio_host"])


async def test_the_main_music_player_announces(hass, home, freezer):
    from homeassistant.components.media_player import MediaPlayerEntityFeature as F

    entry = await _room(hass, freezer, music_source="kitchen")
    home.set(KITCHEN, "idle", supported_features=int(F.MEDIA_ANNOUNCE | F.PLAY_MEDIA))
    await hass.async_block_till_done()
    assert entry.runtime_data.announce.choose().key == "kitchen"


async def test_a_handover_finds_the_player_of_the_kind(hass, home, freezer):
    living = await _room(hass, freezer)
    other = MockConfigEntry(
        domain=DOMAIN, title="Den", data={CONF_ENTRY_TYPE: ENTRY_TYPE_ROOM}, version=4, minor_version=2,
        options={"sources": [{"id": "radio", "kind": "audio", "player": "media_player.den_player"}], "devices": [], "cables": [], "volume": {"where": "players"}},
    )
    other.add_to_hass(hass)
    assert await hass.config_entries.async_setup(other.entry_id)
    await hass.async_block_till_done()
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    code, _ = living.runtime_data.handover.blocker(other.runtime_data)
    assert code == "handover_not_supported", "the radio is found (its system cannot take it over)"


async def test_the_panel_shows_what_a_player_is_for(hass, home, freezer):
    from custom_components.better_media_player.panel.view import room_view

    entry = await _room(hass, freezer, start_source="film2")
    view = room_view(hass, entry, None)
    assert [(s["id"], s["kind"], s["needs"]) for s in view["sources"]][2:] == [("kitchen", "audio", ["sound"]), ("film2", "video", ["picture", "sound"])]
    assert view["start_source"] == "film2" and view["music_source"] is None


async def test_the_room_s_music_player_is_the_one_in_a_group(hass, home, freezer):
    """Follow me joined the further player (the group pauses): that one is the room's."""
    entry = await _room(hass, freezer)
    home.group("paused", [KITCHEN])
    await hass.async_block_till_done()
    assert entry.runtime_data.music.key == "kitchen"


async def test_one_playing_on_its_own_before_one_in_a_group(hass, home, freezer):
    entry = await _room(hass, freezer)
    home.group("playing", [MUSIC])
    _kitchen(home, "playing")
    await hass.async_block_till_done()
    assert entry.runtime_data.music.key == "kitchen"


async def test_a_wake_s_player_is_the_room_s_music_player(hass, home, freezer):
    entry = await _room(hass, freezer)
    entry.runtime_data.data["wake"] = {"source": "kitchen"}
    assert entry.runtime_data.music.key == "kitchen"


async def test_a_follow_wake_takes_the_music_player(hass, home, freezer, monkeypatch):
    entry = await _room(hass, freezer, start_source="film2")
    keys = []

    async def _wake(key, *args, **kwargs):
        keys.append(key)

    monkeypatch.setattr(entry.runtime_data.wake, "async_wake", _wake)
    for follow in (False, True):
        await hass.services.async_call(DOMAIN, "wake", {"entity_id": ROOM, "media_content_id": "x", "media_content_type": "music", "follow": follow}, blocking=True)
    assert keys == ["film2", "audio"]


async def test_turning_on_a_room_that_is_on_keeps_what_plays(hass, home, freezer):
    entry = await _room(hass, freezer, start_source="film2")
    _kitchen(home, "playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    await hass.services.async_call("media_player", "turn_on", {"entity_id": ROOM}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert entry.runtime_data.last_source == "kitchen" and not home.on(TV)


async def test_a_further_player_s_plug_has_a_name(hass, home, freezer):
    await _room(hass, freezer, sources=_sources(host=KITCHEN_HOST))
    from homeassistant.helpers import entity_registry as er

    entity_id = next(e.entity_id for e in er.async_get(hass).entities.values() if e.unique_id.endswith("kitchen_host_off_at"))
    assert er.async_get(hass).async_get(entity_id).translation_key == "audio_host_named_off_at"
    assert hass.states.get(entity_id) is None or "Kitchen Player" in hass.states.get(entity_id).name


def test_a_player_s_trigger_needs_its_id():
    import voluptuous as vol

    from custom_components.better_media_player.device_trigger import TRIGGER_SCHEMA

    base = {"platform": "device", "domain": DOMAIN, "device_id": "x"}
    TRIGGER_SCHEMA({**base, "type": "source_started", "subtype": "kitchen"})
    TRIGGER_SCHEMA({**base, "type": "source_video"})
    with pytest.raises(vol.Invalid):
        TRIGGER_SCHEMA({**base, "type": "source_started"})


async def test_a_reload_after_the_start_logs_no_error(hass, home, freezer, caplog):
    from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
    from homeassistant.core import CoreState

    hass.set_state(CoreState.not_running)
    entry = await _room(hass, freezer)
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert "Unable to remove unknown job listener" not in caplog.text
