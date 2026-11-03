"""The panel's live state: what a room does, sent on every change; the rooms' lines; the room's buttons."""

from __future__ import annotations

from types import SimpleNamespace

from homeassistant.setup import async_setup_component

from custom_components.better_media_player.panel.live import ws_room_live, ws_rooms_live

from .conftest import BASS, SPEAKERS, TV, setup_room
from .helpers import tick

LIVE = "better_media_player/room/live"
LINES = "better_media_player/rooms/live"
ACT = "better_media_player/room/act"


async def _client(hass, hass_ws_client):
    assert await async_setup_component(hass, "media_player", {})
    return await hass_ws_client()


async def _subscribe(client, **msg):
    await client.send_json_auto_id(msg)
    res = await client.receive_json()
    assert res["success"], res
    return (await client.receive_json())["event"]


class Conn:
    """A websocket connection that writes down what is sent (no waiting on a socket)."""

    def __init__(self):
        self.user = SimpleNamespace(is_admin=True)
        self.subscriptions = {}
        self.events = []

    def send_result(self, msg_id, result=None):
        pass

    def send_message(self, msg):
        self.events.append(msg["event"])

    def close(self):
        for unsub in self.subscriptions.values():
            unsub()


def _listen(hass, handler, **msg):
    conn = Conn()
    handler(hass, conn, {"id": 1, **msg})
    return conn


async def _until(conn, hass, freezer, test, steps=20):
    """Time goes on until a message passes the test."""
    for _ in range(steps):
        if conn.events and test(conn.events[-1]):
            return conn.events[-1]
        await tick(hass, freezer, 1)
    raise AssertionError(f"no such message: {conn.events[-1] if conn.events else None}")


async def test_a_room_off(hass, home, hass_ws_client):
    client = await _client(hass, hass_ws_client)
    entry = await setup_room(hass)
    live = await _subscribe(client, type=LIVE, entry_id=entry.entry_id)
    assert live["loaded"] and live["status"] == "off"
    assert set(live["sources"]) == {"video", "audio"} and set(live["nodes"]) == {"display", "speakers", "subwoofer"}
    tv = live["nodes"]["display"]
    assert tv["on"] is False and tv["needed"] is False and tv["why"] == [] and tv["off_at"] is None
    assert tv["entities"][TV] == {"kind": "plug", "state": "off", "on": False}


async def test_playing_then_paused(hass, home, freezer):
    entry = await setup_room(hass)
    conn = _listen(hass, ws_room_live, type=LIVE, entry_id=entry.entry_id)
    home.kodi("playing")
    await hass.async_block_till_done()
    live = await _until(conn, hass, freezer, lambda m: m["sources"]["video"]["playing"])
    assert live["status"] == "playing"
    assert live["nodes"]["display"]["why"] == ["plays:video"] and live["nodes"]["display"]["on"]
    assert live["nodes"]["speakers"]["why"] == ["plays:video"]
    home.kodi("paused")
    await hass.async_block_till_done()
    live = await _until(conn, hass, freezer, lambda m: m["nodes"]["display"]["off_at"] is not None)
    video = live["sources"]["video"]
    assert video["in_use"] and not video["playing"] and video["hold_end"] == video["since"] + 30 * 60
    assert live["nodes"]["display"]["why"] == ["hold:video"]
    assert live["nodes"]["display"]["off_at"] == int(video["hold_end"]) or abs(live["nodes"]["display"]["off_at"] - video["hold_end"]) < 1


async def test_the_rooms_lines(hass, home, freezer):
    entry = await setup_room(hass)
    conn = _listen(hass, ws_rooms_live, type=LINES)
    lines = conn.events[0]
    assert lines["rooms"][entry.entry_id] == {"status": "off", "playing": None, "holding": None, "starting": None, "off_at": None, "error": None}
    home.music("playing")
    await hass.async_block_till_done()
    lines = await _until(conn, hass, freezer, lambda m: m["rooms"][entry.entry_id]["playing"] == "audio")
    home.music("paused")
    await hass.async_block_till_done()
    lines = await _until(conn, hass, freezer, lambda m: m["rooms"][entry.entry_id]["off_at"] is not None)
    assert lines["rooms"][entry.entry_id]["holding"] == "audio"
    conn.close()
    n = len(conn.events)
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert len(conn.events) == n, "closed: nothing more"


async def test_keep_on_and_all_off_from_the_panel(hass, home, hass_ws_client, freezer):
    client = await _client(hass, hass_ws_client)
    entry = await setup_room(hass)
    home.music("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    for action, kept in (("keep_on", True), ("release", False), ("keep_on", True)):
        await client.send_json_auto_id({"type": ACT, "entry_id": entry.entry_id, "action": action})
        assert (await client.receive_json())["success"]
        assert entry.runtime_data.keep_on is kept, action
    await client.send_json_auto_id({"type": ACT, "entry_id": entry.entry_id, "action": "all_off"})
    assert (await client.receive_json())["success"]
    await tick(hass, freezer, 5)
    assert not home.on(SPEAKERS) and not home.on(BASS)
    assert not entry.runtime_data.keep_on, "All off ends keep on"


async def test_waiting_for_cec_is_told(hass, home, freezer):
    """A TV that Kodi turns on over CEC: the room waits, and says how long it still waits."""
    from .conftest import LG, device, setup_room_devices

    hass.states.async_set(LG, "off")
    home.lg_obeys = False
    entry = await setup_room_devices(
        hass,
        [device("display", "display", devices=[LG], control={"on": "cec", "off": "bmp", "fallback": True, "timeout": 15})],
        [{"from": "video", "to": "display", "signals": ["picture"]}],
    )
    conn = _listen(hass, ws_room_live, type=LIVE, entry_id=entry.entry_id)
    home.kodi("playing")
    await hass.async_block_till_done()
    live = await _until(conn, hass, freezer, lambda m: m["nodes"]["display"]["cec"])
    cec = live["nodes"]["display"]["cec"][0]
    assert cec["entity"] == LG and cec["on"] is True and 0 < cec["left"] <= 15 and cec["fallback"] is True


async def test_a_reloaded_room_is_followed(hass, home, freezer):
    entry = await setup_room(hass)
    conn = _listen(hass, ws_room_live, type=LIVE, entry_id=entry.entry_id)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    home.kodi("playing")
    await hass.async_block_till_done()
    live = await _until(conn, hass, freezer, lambda m: m.get("sources", {}).get("video", {}).get("playing"))
    assert live["nodes"]["display"]["on"]


async def test_at_most_twice_a_second(hass, home, freezer):
    entry = await setup_room(hass)
    conn = _listen(hass, ws_room_live, type=LIVE, entry_id=entry.entry_id)
    n = len(conn.events)
    for state in ("playing", "paused", "playing", "paused"):  # four changes at once
        home.music(state)
        await hass.async_block_till_done()
    assert len(conn.events) - n <= 1, "the first at once, the rest later"
    await tick(hass, freezer, 1)
    assert conn.events[-1]["sources"]["audio"]["playing"] is False, "the last state follows"


async def test_the_cec_wait_counts_down(hass, home, freezer):
    from .conftest import LG, device, setup_room_devices

    hass.states.async_set(LG, "off")
    home.lg_obeys = False
    entry = await setup_room_devices(
        hass,
        [device("display", "display", devices=[LG], control={"on": "cec", "off": "bmp", "fallback": False, "timeout": 15})],
        [{"from": "video", "to": "display", "signals": ["picture"]}],
    )
    conn = _listen(hass, ws_room_live, type=LIVE, entry_id=entry.entry_id)
    home.kodi("playing")
    await hass.async_block_till_done()
    first = (await _until(conn, hass, freezer, lambda m: m["nodes"]["display"]["cec"]))["nodes"]["display"]["cec"][0]
    assert first["fallback"] is False
    later = await _until(conn, hass, freezer, lambda m: m["nodes"]["display"]["cec"] and m["nodes"]["display"]["cec"][0]["left"] <= first["left"] - 4)
    assert later["nodes"]["display"]["cec"][0]["left"] > 0


async def test_the_input_is_told(hass, home, freezer):
    from .conftest import LG, device, setup_room_devices

    hass.states.async_set(LG, "off", {"source": "HDMI 1", "source_list": ["HDMI 1", "HDMI 2"]})
    entry = await setup_room_devices(
        hass,
        [device("display", "display", devices=[LG], inputs={"entity": LG, "actions": {}, "pause": 0})],
        [{"from": "video", "to": "display", "signals": ["picture"], "input": "HDMI 2"}],
    )
    conn = _listen(hass, ws_room_live, type=LIVE, entry_id=entry.entry_id)
    home.kodi("playing")
    await hass.async_block_till_done()
    live = await _until(conn, hass, freezer, lambda m: (m["nodes"]["display"]["input"] or {}).get("state") == "done")
    assert live["nodes"]["display"]["input"] == {"wanted": "HDMI 2", "current": "HDMI 2", "mode": "bmp", "state": "done", "entity": LG}


async def test_the_line_of_a_room_that_plays_says_nothing_of_a_pause(hass, home, freezer):
    """Kodi paused, then music plays: the room plays (no "paused · off in …")."""
    entry = await setup_room(hass)
    conn = _listen(hass, ws_rooms_live, type=LINES)
    home.kodi("playing")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    home.kodi("paused")
    await hass.async_block_till_done()
    await tick(hass, freezer, 15)
    home.music("playing")
    await hass.async_block_till_done()
    line = (await _until(conn, hass, freezer, lambda m: m["rooms"][entry.entry_id]["playing"] == "audio"))["rooms"][entry.entry_id]
    assert line["holding"] is None and line["off_at"] is None



async def test_a_reloaded_room_is_followed_at_once(hass, home, freezer):
    """After a reload the subscription listens to the new room (its changes come at once)."""
    entry = await setup_room(hass)
    conn = _listen(hass, ws_room_live, type=LIVE, entry_id=entry.entry_id)
    old = entry.runtime_data
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    new = entry.runtime_data
    assert new is not old
    listening = len(new._listeners)
    await tick(hass, freezer, 3)  # (the check)
    assert len(new._listeners) == listening + 1, "listens to the new room"
    assert conn.events


async def test_closing_lets_the_room_go(hass, home, freezer):
    entry = await setup_room(hass)
    before = len(entry.runtime_data._listeners)
    conn = _listen(hass, ws_room_live, type=LIVE, entry_id=entry.entry_id)
    assert len(entry.runtime_data._listeners) == before + 1
    conn.close()
    assert len(entry.runtime_data._listeners) == before
