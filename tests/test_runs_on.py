"""A player that runs on another player (a Sendspin player in Kodi) or on a device
(HEOS on a receiver): started after it, through its cables, and the other player's
"playing" is its own while it plays."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.better_media_player.const import CONF_ENTRY_TYPE, DOMAIN, ENTRY_TYPE_ROOM, EVENT_SOURCE_CHANGED
from custom_components.better_media_player.panel.edit import SETUP_SCHEMA, check_room, clean_setup
from custom_components.better_media_player.panel.view import routes_of

from .conftest import HARMONY, KITCHEN, KODI, MUSIC, SPEAKERS, TV, device
from .helpers import HOST, ROOM, calls_to, tick

SENDSPIN = KITCHEN  # (a Music Assistant player: the one in Kodi)
CABLES = [
    {"from": "video", "to": "display", "signals": ["picture"]},
    {"from": "video", "to": "speakers", "signals": ["sound"]},
]


def _sources(**more):
    return [
        {"id": "video", "player": KODI, "host": HOST},
        {"id": "kodi_music", "kind": "audio", "player": SENDSPIN, "runs_on": "video", **more},
    ]


async def _room(hass, freezer, sources=None, devices=None, cables=None, entry_id=None, **opts):
    entry = MockConfigEntry(
        domain=DOMAIN,
        **({"entry_id": entry_id} if entry_id else {}),
        title="Living Room",
        data={CONF_ENTRY_TYPE: ENTRY_TYPE_ROOM},
        options={
            "sources": sources or _sources(),
            "devices": devices or [device("display", "display", TV), device("speakers", "speakers", SPEAKERS)],
            "cables": CABLES if cables is None else cables,
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


def _off(hass, home, plug="off"):
    hass.states.async_set(HOST, plug)
    home.kodi("off")
    home.kitchen("unavailable")


async def test_it_is_started_after_the_player_it_runs_on(hass, home, freezer):
    _off(hass, home)
    home.kodi_boots = True
    entry = await _room(hass, freezer)
    await hass.services.async_call("media_player", "select_source", {"entity_id": ROOM, "source": "kodi_music"}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert home.on(HOST) and calls_to(home, KODI, "turn_on"), "the plug, then Kodi woken"
    assert entry.runtime_data.sequence.running, "waits until the Sendspin player is there"
    home.kitchen("idle")
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert not entry.runtime_data.sequence.running and not entry.runtime_data.error
    assert home.on(SPEAKERS) and not home.on(TV), "its sound through Kodi's cable; no picture"


async def test_one_that_does_not_come_says_so(hass, home, freezer):
    _off(hass, home)
    home.kodi_boots = True
    entry = await _room(hass, freezer)
    await hass.services.async_call("media_player", "select_source", {"entity_id": ROOM, "source": "kodi_music"}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 100, step=5)
    assert entry.runtime_data.error == f"{SENDSPIN} did not become available"


async def test_its_playing_is_not_a_film(hass, home, freezer):
    _off(hass, home, plug="on")
    entry = await _room(hass, freezer)
    ctrl = entry.runtime_data
    events = async_capture_events(hass, EVENT_SOURCE_CHANGED)
    home.kodi("idle")
    home.kitchen("idle")
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert home.on(TV), "Kodi on by itself: a film (as ever)"
    home.kitchen("playing")
    home.kodi("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert ctrl.serving(ctrl.sources["video"])
    assert home.on(SPEAKERS) and not home.on(TV), "music in Kodi: the TV goes off"
    assert ctrl.active_source.key == "kodi_music" and ctrl.last_source == "kodi_music"
    video = ctrl.sources["video"]
    assert not ctrl.source_playing(video) and not ctrl.video_active, "no film: e.g. follow me takes the room"
    assert hass.states.get(ROOM).attributes["source"] == "Kitchen Player"
    from custom_components.better_media_player.room.live import room_live

    assert room_live(ctrl)["sources"]["video"]["serving"] == ["kodi_music"], "the panel: on for it"
    assert [e.data["source"] for e in events][-1] == "kodi_music"


async def test_kodi_playing_first_waits_a_moment(hass, home, freezer):
    """Kodi reports playing a moment before the Sendspin player: no film for that moment."""
    _off(hass, home, plug="on")
    entry = await _room(hass, freezer)
    await hass.services.async_call("media_player", "select_source", {"entity_id": ROOM, "source": "kodi_music"}, blocking=True)
    home.kodi("idle")
    home.kitchen("idle")
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    home.kodi("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 0.5, step=0.5)
    assert not home.on(TV)
    home.kitchen("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert not home.on(TV) and entry.runtime_data.serving(entry.runtime_data.sources["video"])


async def test_kodi_playing_on_its_own_is_a_film(hass, home, freezer):
    _off(hass, home, plug="on")
    entry = await _room(hass, freezer)
    ctrl = entry.runtime_data
    home.kodi("idle")
    home.kitchen("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert not home.on(TV)
    home.kitchen("idle")
    home.kodi("playing")  # a film chosen in Kodi
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert not ctrl.serving(ctrl.sources["video"]) and home.on(TV)
    assert ctrl.active_source.key == "video"


async def test_kodi_in_its_menu_after_the_music_is_no_film(hass, home, freezer):
    _off(hass, home, plug="on")
    entry = await _room(hass, freezer)
    events = async_capture_events(hass, EVENT_SOURCE_CHANGED)
    home.kodi("playing")
    home.kitchen("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    home.kodi("idle")
    home.kitchen("paused")
    await hass.async_block_till_done()
    await tick(hass, freezer, 20)
    assert not home.on(TV) and home.on(SPEAKERS), "the music holds; Kodi's menu is no film"
    assert "video" not in [e.data["source"] for e in events], "no source change to video either"
    assert entry.runtime_data.sources["kodi_music"].in_use


async def test_kodi_goes_off_with_the_music(hass, home, freezer):
    _off(hass, home, plug="on")
    entry = await _room(hass, freezer)
    home.kodi("playing")
    home.kitchen("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    home.kodi("idle")
    home.kitchen("paused")
    await hass.async_block_till_done()
    await tick(hass, freezer, 31 * 60, step=30)
    assert not home.on(SPEAKERS)
    order = [c[2] for c in home.calls if c[1] == "turn_off" and c[2] in (KODI, SPEAKERS)]
    assert order == [SPEAKERS, KODI], "Kodi goes off after the sound (and not before)"
    await tick(hass, freezer, 3 * 60, step=30)
    assert not home.on(HOST), "its plug after its off_wait"
    assert not home.on(TV)
    assert entry.runtime_data.sources["video"].own, "off: on by itself next time is a film"


async def test_starting_it_while_a_film_plays_keeps_kodi_on(hass, home, freezer):
    _off(hass, home, plug="on")
    await _room(hass, freezer)
    home.kodi("playing")
    home.kitchen("idle")
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    await hass.services.async_call("media_player", "select_source", {"entity_id": ROOM, "source": "kodi_music"}, blocking=True)
    await hass.async_block_till_done()
    assert calls_to(home, KODI, "media_stop") and not calls_to(home, KODI, "turn_off")


async def test_after_a_restart_paused_music_in_kodi_is_no_film(hass, home, freezer):
    hass.states.async_set(HOST, "on")
    hass.states.async_set(SPEAKERS, "on")
    home.kodi("idle")
    home.kitchen("paused")
    entry = await _room(hass, freezer)
    await tick(hass, freezer, 5)
    assert not home.on(TV)
    assert entry.runtime_data.serving(entry.runtime_data.sources["video"])


async def test_its_plug_is_the_one_of_kodi(hass, home, freezer):
    entry = await _room(hass, freezer)
    power = entry.runtime_data.power
    assert power.nodes["video_host"].sources == ("video", "kodi_music")
    assert power.routes["kodi_music"].nodes == {"speakers"}


async def test_its_route_goes_on_from_kodi(hass, home, freezer):
    mixer = {**device("mix", "mixer"), "power": None}
    cables = [{"from": "video", "to": "mix", "signals": ["sound"]}, {"from": "mix", "to": "speakers", "signals": ["sound"]}]
    entry = await _room(hass, freezer, devices=[device("display", "display", TV), mixer, device("speakers", "speakers", SPEAKERS)], cables=cables)
    route = entry.runtime_data.power.routes["kodi_music"]
    assert route.nodes == {"mix", "speakers"} and not route.direct, "through Kodi's cables, not fed directly"


async def test_on_a_device(hass, home, freezer):
    """HEOS on a receiver: the receiver comes on first, then the player is waited for."""
    hass.states.async_set(HARMONY, "off")  # (the receiver, as a device that switches itself)
    home.music("unavailable")
    sources = [{"id": "video", "player": KODI}, {"id": "heos", "kind": "audio", "player": MUSIC, "runs_on": "receiver"}]
    devices = [device("display", "display", TV), device("receiver", "receiver", devices=[HARMONY]), device("speakers", "speakers", SPEAKERS)]
    cables = [{"from": "video", "to": "display", "signals": ["picture"]}, {"from": "receiver", "to": "speakers", "signals": ["sound"]}]
    entry = await _room(hass, freezer, sources=sources, devices=devices, cables=cables)
    assert entry.runtime_data.power.routes["heos"].nodes == {"receiver", "speakers"}
    await hass.services.async_call("media_player", "select_source", {"entity_id": ROOM, "source": "heos"}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert hass.states.get(HARMONY).state == "on" and entry.runtime_data.sequence.running
    home.music("idle")
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert not entry.runtime_data.sequence.running and home.on(SPEAKERS) and not home.on(TV)


# ------------------------------------------------------------------ the panel


def _draft(sources, devices=None, cables=None):
    return SETUP_SCHEMA({
        "sources": sources,
        "devices": devices or [device("display", "display", TV), {**device("mix", "mixer"), "power": None}],
        "cables": cables or [],
        "volume": {"where": "players"},
    })


def _codes(hass, setup):
    return {p["code"] for p in check_room(hass, None, setup) if p["error"]}


def test_what_it_runs_on_is_checked(hass, home):
    assert _codes(hass, _draft(_sources())) == set()
    assert "runs_on_itself" in _codes(hass, _draft([{"id": "a", "kind": "audio", "player": MUSIC, "runs_on": "a"}]))
    assert "runs_on_missing" in _codes(hass, _draft([{"id": "a", "kind": "audio", "player": MUSIC, "runs_on": "gone"}]))
    chain = [*_sources(), {"id": "c", "kind": "audio", "player": MUSIC, "runs_on": "kodi_music"}]
    assert "runs_on_guest" in _codes(hass, _draft(chain))
    passive = [{"id": "a", "kind": "audio", "player": MUSIC, "runs_on": "mix"}]
    assert "runs_on_passive" in _codes(hass, _draft(passive))
    cable = [{"from": "kodi_music", "to": "display", "signals": ["sound"]}]
    assert "guest_cable" in _codes(hass, _draft(_sources(), cables=cable))


def test_no_on_off_hint_for_it(hass, home):
    """It comes up with what it runs on: its own on/off is no matter."""
    hass.states.async_set(SENDSPIN, "idle", {"supported_features": 0})
    problems = check_room(hass, None, _draft(_sources(onoff="player")))
    assert "player_no_onoff" not in {p["code"] for p in problems}


def test_it_has_no_plug_of_its_own(hass):
    setup = clean_setup(_draft(_sources(host="switch.x")))
    assert setup["sources"][1]["host"] is None and setup["sources"][1]["runs_on"] == "video"
    assert "runs_on" not in setup["sources"][0]


def test_its_route_in_the_panel(hass):
    routes = routes_of(
        [{"id": "video", "needs": ["picture", "sound"]}, {"id": "kodi_music", "needs": ["sound"], "runs_on": "video"}],
        [device("display", "display", TV), device("speakers", "speakers", SPEAKERS)],
        CABLES,
    )
    assert routes["kodi_music"]["devices"] == ["speakers"] and routes["kodi_music"]["cables"] == [1]


# ------------------------------------------------------------------ the review's cases


def _stored(hass_storage, entry_id, **data):
    from custom_components.better_media_player.const import STORAGE_VERSION

    hass_storage[f"{DOMAIN}.{entry_id}"] = {"version": STORAGE_VERSION, "minor_version": 1, "key": f"{DOMAIN}.{entry_id}", "data": data}


async def test_playing_on_it_while_a_film_plays_keeps_kodi_on(hass, home, freezer):
    """From the browser (a wake, a handover alike): the film stops, Kodi stays on for it."""
    _off(hass, home, plug="on")
    entry = await _room(hass, freezer)
    ctrl = entry.runtime_data
    home.kodi("playing")
    home.kitchen("idle")
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    home.plays_media = True
    home.report_lag = 2.0
    await ctrl.async_play_on("kodi_music", {"media_content_id": "library://x", "media_content_type": "music"})
    await hass.async_block_till_done()
    await tick(hass, freezer, 6)
    assert not calls_to(home, KODI, "turn_off")
    assert not [c for c in home.calls if c[2] == SPEAKERS and c[1] == "turn_off"]


async def test_choosing_kodi_while_the_music_plays(hass, home, freezer):
    _off(hass, home, plug="on")
    home.report_lag = 2.0
    entry = await _room(hass, freezer)
    ctrl = entry.runtime_data
    home.kodi("playing")
    home.kitchen("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 4)
    await hass.services.async_call("media_player", "select_source", {"entity_id": ROOM, "source": "video"}, blocking=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 4)
    home.kodi("idle")  # Kodi in its menu (the stream stopped)
    await hass.async_block_till_done()
    await tick(hass, freezer, 120, step=5)
    assert not ctrl.serving(ctrl.sources["video"]), "Kodi chosen: for itself"
    assert home.on(TV) and not calls_to(home, KODI, "turn_off")


async def test_music_assistant_dropping_out_is_no_film(hass, home, freezer):
    _off(hass, home, plug="on")
    entry = await _room(hass, freezer)
    home.kodi("playing")
    home.kitchen("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    home.kitchen("unavailable")  # Music Assistant reconnects; Kodi plays on
    await hass.async_block_till_done()
    await tick(hass, freezer, 30, step=5)
    assert not home.on(TV)
    await tick(hass, freezer, 40, step=5)
    assert home.on(TV), "gone for long: what Kodi plays is its own"
    assert entry.runtime_data.last_source == "video"


async def test_a_restart_while_music_assistant_is_not_loaded(hass, home, freezer, hass_storage):
    _stored(hass_storage, "room1", last_source="kodi_music")
    hass.states.async_set(HOST, "on")
    hass.states.async_set(SPEAKERS, "on")
    home.kodi("playing")
    home.kitchen("unavailable")
    await _room(hass, freezer, entry_id="room1")
    assert not home.on(TV) and not calls_to(home, TV, "turn_on")
    home.kitchen("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert not home.on(TV)


async def test_a_reload_while_the_music_holds(hass, home, freezer, hass_storage):
    _stored(hass_storage, "room1", last_source="kodi_music")
    hass.states.async_set(HOST, "on")
    hass.states.async_set(SPEAKERS, "on")
    home.kodi("idle")
    home.kitchen("idle")
    await _room(hass, freezer, entry_id="room1")
    await tick(hass, freezer, 5)
    assert not home.on(TV)


async def test_a_kodi_the_room_does_not_switch_off(hass, home, freezer, caplog):
    import logging

    caplog.set_level(logging.INFO)
    _off(hass, home, plug="on")
    sources = _sources()
    sources[0].update(onoff="none", host=None)
    entry = await _room(hass, freezer, sources=sources)
    home.kodi("playing")
    home.kitchen("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    home.kodi("idle")
    home.kitchen("idle")
    await hass.async_block_till_done()
    await tick(hass, freezer, 40 * 60, step=30)
    assert caplog.text.count("goes off after its sound devices") == 0
    home.kodi("playing")  # a film chosen in Kodi
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert home.on(TV) and not entry.runtime_data.sources["video"].suppressed


async def test_follow_me_prewarms_kodi_s_plug_for_the_music_in_kodi(hass, home, freezer):
    entry = await _room(hass, freezer, music_source="kodi_music")
    power = entry.runtime_data.power
    assert power.prewarms(power.nodes["video_host"]) and power.prewarms(power.nodes["speakers"])


async def test_kodi_echoing_the_music_s_volume_is_no_wish(hass, home, freezer):
    """Sendspin in Kodi sets Kodi's volume: Kodi's reports during a preset's fade
    are no change of the room's volume (the preset stays)."""
    from .helpers import PRESET, select_preset, set_setting

    _off(hass, home, plug="on")
    entry = await _room(hass, freezer)
    home.kodi("playing", volume_level=0.3)
    home.kitchen("playing", volume_level=0.3)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    await set_setting(hass, "preset_fade", 20)
    await select_preset(hass, "loud")
    for _ in range(6):  # (Kodi follows each step of the Sendspin player's fade)
        await tick(hass, freezer, 5, step=1)
        home.kodi("playing", volume_level=hass.states.get(SENDSPIN).attributes["volume_level"])
        await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert hass.states.get(PRESET).state == "loud"
    assert abs(hass.states.get(SENDSPIN).attributes["volume_level"] - entry.runtime_data.volume.presets["loud"]) < 0.01
