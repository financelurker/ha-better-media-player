// Better Media Player: the configuration panel.
// Rooms on the left, the room as a drawing in the middle, details on the right.
// Rooms are drafts (editor.js) until saved: one bar saves or discards all changes.
// The routes and the checks come from the integration (the same rules it works by).

import { LitElement, html, svg, css, nothing } from "./vendor/lit.js";
import { translator } from "./i18n.js";
import { W, H, PASSIVE, SINKS, nodesOf, arrange, unstack, curve, labelSpot, bounds, iconPath } from "./diagram.js";
import { DEVICE_KINDS, SOURCE_KINDS, addCable, addDevice, addSource, canConnect, directToCables, draftOf, needsOf, newRoom, payload, removeDevice, removeSource, same } from "./editor.js";
import { renderInspector, renderOverview } from "./inspector.js";
import { renderCentral, renderSettings } from "./settings.js";
import { liveBox, liveRoute, roomLine, roomSentence, tileStatus } from "./live.js";

const PANEL_URL = "better-media-player";

// the unit a setting's number is in
const UNITS = {
  idle_timeout: "min", night_idle_timeout: "min", night_max_volume: "%", night_fade_in: "min",
  preset_quiet: "%", preset_normal: "%", preset_loud: "%", preset_party: "%", preset_fade: "s",
  announce_video_lower: "%", start_timeout: "s", sleep_fade: "min",
  snooze_minutes: "min", presence_run_down: "min", volume_offset: "pts",
  off_wait: "min", on_wait: "s", standby_w: "W", standby_max: "min",
  default_volume: "%", hide_delay: "s",
};
const clone = (x) => JSON.parse(JSON.stringify(x));

class BmpPanel extends LitElement {
  static properties = {
    hass: { attribute: false },
    narrow: { type: Boolean, reflect: true },
    route: { attribute: false },
    _config: { state: true },
    _error: { state: true },
    _notice: { state: true },
    _page: { state: true },
    _roomId: { state: true },
    _tab: { state: true },
    _selected: { state: true },
    _source: { state: true },
    _tick: { state: true },
    _armed: { state: true },
    _dialog: { state: true },
    _saving: { state: true },
    _live: { state: true },
    _lines: { state: true },
    _liveOn: { state: true },
  };

  constructor() {
    super();
    this._config = null;
    this._error = null; // loading failed: the panel cannot show anything
    this._notice = null; // something else failed: shown above the drawing
    this._page = "room";
    this._tab = "wiring";
    this._roomId = null;
    this._selected = null; // {type: "node", id} | {type: "cable", index}
    this._source = null; // null: all routes
    this._armed = null; // a cable being drawn: from this tile
    this._dialog = null; // {type: "add"} | {type: "delete", room}
    this._saving = false;
    this._tick = 0;
    this._t = (k) => k;
    this._lang = null;
    this._drafts = {}; // per room: what is being edited
    this._base = {}; // per room: as loaded (what "unsaved" is measured against)
    this._central = null;
    this._centralBase = null;
    this._nodes = {}; // per room: the tiles with their places
    this._views = {}; // per room: pan and zoom
    this._timers = {};
    this._units = UNITS;
    this._drag = null;
    this._pan = null;
    this._onMove = this._onMove.bind(this);
    this._onUp = this._onUp.bind(this);
    // the live state: the open room's (room/live), every room's line (rooms/live)
    this._live = null;
    this._lines = {};
    this._liveOn = true;
    try { this._liveOn = localStorage.getItem("bmp-live") !== "0"; } catch { /* (no storage) */ }
    this._offset = 0; // Home Assistant's clock against the browser's (s)
    this._livePaused = false; // a route picked: Live waits for this view (not remembered)
    this._liveFails = 0; // subscriptions that failed in a row (then tried less often)
    this._liveSub = null; // {entry_id, unsub: Promise}
    this._linesSub = null;
    this._onDeleteKey = this._onDeleteKey.bind(this);
    this._onBeforeUnload = (e) => { if (this._dirtyRooms().length || this._centralDirty()) e.preventDefault(); };
  }

  connectedCallback() {
    super.connectedCallback();
    window.addEventListener("pointermove", this._onMove);
    window.addEventListener("pointerup", this._onUp);
    window.addEventListener("beforeunload", this._onBeforeUnload);
    this.addEventListener("keydown", this._onDeleteKey);
    // counting down (only while something counts)
    this._ticker = setInterval(() => { if (this._counting()) this.requestUpdate(); }, 1000);
  }

  disconnectedCallback() {
    clearInterval(this._ticker);
    this.removeEventListener("keydown", this._onDeleteKey);
    this._unsubscribe("_liveSub");
    this._unsubscribe("_linesSub");
    window.removeEventListener("pointermove", this._onMove);
    window.removeEventListener("pointerup", this._onUp);
    window.removeEventListener("beforeunload", this._onBeforeUnload);
    super.disconnectedCallback();
  }

  willUpdate(changed) {
    if (!changed.has("hass") || !this.hass) return;
    if (this.hass.language !== this._lang) this._loadTexts(this.hass.language);
    if (!this._config && !this._loading && !this._error) this._load(); // after a failure: only on "Retry"
  }

  async _loadTexts(language) {
    this._lang = language;
    this._t = await translator(language);
    this._nodes = {}; // the tiles' names (kinds) in the language
    this.requestUpdate();
  }

  async _load(select = null) {
    this._loading = true;
    try {
      const config = await this.hass.callWS({ type: "better_media_player/config/get" });
      this._config = config;
      this._drafts = {};
      this._base = {};
      this._nodes = {};
      for (const room of config.rooms) {
        this._base[room.entry_id] = draftOf(room);
        this._drafts[room.entry_id] = { ...draftOf(room), routes: room.routes, problems: [] };
        this._refresh(this._drafts[room.entry_id], 0);
      }
      const c = config.central;
      this._centralBase = c ? { follow_player: c.follow_player, settings: c.settings ? clone(c.settings) : null } : null;
      this._central = c ? { ...clone(this._centralBase), problems: [] } : null;
      // the room asked for: /better-media-player/<entry id> (a device's link) or ?config_entry=<id>
      const wanted = select || (this.route?.path || "").replace(/^\//, "") || new URLSearchParams(location.search).get("config_entry") || "";
      const rooms = config.rooms;
      if (wanted && rooms.some((r) => r.entry_id === wanted)) this._roomId = wanted;
      else if (!rooms.some((r) => r.entry_id === this._roomId)) this._roomId = rooms[0]?.entry_id ?? null;
      if (!rooms.length) this._page = "central";
      this._selected = null;
      this._error = null;
    } catch (err) {
      this._error = err?.message || String(err);
    } finally {
      this._loading = false;
    }
  }

  // ------------------------------------------------------------ drafts

  get _room() {
    return this._drafts[this._roomId] ?? null;
  }

  _rooms() {
    return Object.values(this._drafts).sort((a, b) => Number(a.isNew) - Number(b.isNew) || a.name.localeCompare(b.name));
  }

  _dirtyRooms() {
    return Object.values(this._drafts).filter((d) => d.isNew || !this._base[d.entry_id] || !same(d, this._base[d.entry_id]));
  }

  _centralDirty() {
    return !!this._central && JSON.stringify([this._central.follow_player, this._central.settings]) !== JSON.stringify([this._centralBase.follow_player, this._centralBase.settings]);
  }

  // a change to a room's draft: drawn at once, routes and checks follow from the integration
  _change(d, fn) {
    fn();
    this._retile(d);
    this._refresh(d);
    this._tick++;
  }

  _centralChanged() {
    this._central.problems = [];
    this._tick++;
  }

  // the tiles anew, where they were (new ones below what they would cover)
  _retile(d) {
    const before = this._nodes[d.entry_id];
    if (!before) return;
    const at = Object.fromEntries(before.map((n) => [n.id, [n.x, n.y]]));
    const placed = new Set(before.filter((n) => n.placed).map((n) => n.id));
    const nodes = arrange(nodesOf(d, this._t), d.cables, at);
    const fresh = new Set(nodes.filter((n) => !at[n.id]).map((n) => n.id));
    for (const n of nodes) n.placed = placed.has(n.id);
    this._nodes[d.entry_id] = unstack(nodes, fresh);
  }

  // routes and problems of the draft, from the integration (a moment after the last change)
  _refresh(d, delay = 250) {
    clearTimeout(this._timers[d.entry_id]);
    this._timers[d.entry_id] = setTimeout(async () => {
      const ask = (d._asked = (d._asked || 0) + 1); // a slower answer to an older draft does not count
      const body = payload(d);
      const local = d.sources.filter((s) => !s.player).map((s) => ({ error: true, code: "player_missing", source: s.id }));
      body.setup.sources = body.setup.sources.filter((s) => s.player);
      for (const k of ["start_source", "music_source"]) { // (one without its entity yet: told above)
        if (!body.setup.sources.some((s) => s.id === body.setup[k])) body.setup[k] = null;
      }
      try {
        const sources = body.setup.sources.map((s) => ({ ...s, needs: needsOf(s.kind) }));
        const [routes, checked] = await Promise.all([
          this.hass.callWS({ type: "better_media_player/route", sources, devices: body.setup.devices, cables: body.setup.cables }),
          this.hass.callWS({ type: "better_media_player/validate", ...(d.isNew ? {} : { entry_id: d.entry_id }), setup: body.setup, settings: body.settings }),
        ]);
        if (ask !== d._asked) return;
        d.routes = routes.routes;
        d.problems = [...local, ...checked.problems];
      } catch (err) {
        if (ask !== d._asked) return;
        d.problems = [...local, { error: true, code: "invalid", message: err?.message || String(err) }];
      }
      this._tick++;
    }, delay);
  }

  _problemText(x) {
    const d = this._room;
    const name = (id) => {
      const dev = d?.devices.find((v) => v.id === id);
      if (dev) return dev.name || this._t(`kind.${dev.kind}`);
      const s = d?.sources.find((v) => v.id === id);
      return s ? this._sourceLabel(s) : id;
    };
    return this._t(`problem.${x.code}`, { ...x, device: x.device ? name(x.device) : "", source: x.source ? name(x.source) : "", message: x.message || "" });
  }

  _offersOnoff(player) {
    const st = this.hass.states[player];
    return st ? Boolean((st.attributes.supported_features || 0) & 128) : null; // TURN_ON
  }

  // ------------------------------------------------------------ helpers

  _view() {
    return (this._views[this._roomId] ??= { tx: 0, ty: 0, k: 1, fitted: false });
  }

  _tiles(room) {
    if (!this._nodes[room.entry_id]) this._nodes[room.entry_id] = arrange(nodesOf(room, this._t), room.cables, room.layout);
    return this._nodes[room.entry_id];
  }

  _go(page, roomId = this._roomId) {
    this._livePaused = false;
    this._page = page;
    this._roomId = roomId;
    this._selected = null;
    this._source = null;
    this._armed = null;
    const path = page === "room" && roomId && !this._drafts[roomId]?.isNew ? `/${PANEL_URL}/${roomId}` : `/${PANEL_URL}`;
    if (location.pathname !== path) history.replaceState(history.state, "", path);
  }

  _moreInfo(entityId) {
    this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true }));
  }

  _count(what, n) {
    return this._t(n === 1 ? `count.${what}_one` : `count.${what}`, { n });
  }

  _counts(room) {
    return { devices: this._count("devices", room.sources.length + room.devices.length), cables: this._count("cables", room.cables.length) };
  }

  _sourceName(s) {
    return s.name || (s.player ? this.hass.states[s.player]?.attributes.friendly_name || s.player : this._t(`source.${s.kind}`));
  }

  _sourceLabel(s) {
    return `${this._t(`source.${s.kind}`)} · ${this._sourceName(s)}`;
  }

  // ------------------------------------------------------------ live state

  _serverNow() {
    return Date.now() / 1000 + this._offset;
  }

  _counting() {
    const live = this._live;
    // (a CEC wait comes from Home Assistant every 2 s: no need to count it here)
    return (live?.loaded && (Object.values(live.nodes).some((n) => n.off_at) || Object.values(live.sources).some((s) => s.hold_end)))
      || Object.values(this._lines).some((l) => l.off_at);
  }

  _unsubscribe(key) {
    const sub = this[key];
    this[key] = null;
    sub?.unsub.then((unsub) => unsub()).catch(() => {});
  }

  _subscribe(key, msg, onMessage) {
    const unsub = this.hass.connection.subscribeMessage((m) => {
      if (this[key]?.unsub !== unsub) return; // (a late message of one given up)
      this._liveFails = 0;
      if (m.now != null) this._offset = m.now - Date.now() / 1000;
      onMessage(m);
    }, msg);
    // failed (e.g. an older integration): tried again later, less often each time
    unsub.catch(() => {
      if (this[key]?.unsub !== unsub) return;
      this._liveFails++;
      setTimeout(() => { if (this[key]?.unsub === unsub) { this[key] = null; this.requestUpdate(); } }, Math.min(60000, 2000 * 2 ** this._liveFails));
    });
    this[key] = { entry_id: msg.entry_id ?? null, unsub };
  }

  // the subscriptions follow what is shown: the open room's while its drawing is
  // shown with "Live" on, every room's line while the panel is open
  _syncLive() {
    if (!this.hass?.connection || !this._config) return;
    if (!this._linesSub) this._subscribe("_linesSub", { type: "better_media_player/rooms/live" }, (m) => { this._lines = m.rooms || {}; });
    const room = this._room;
    const want = this._liveOn && !this._livePaused && this._page === "room" && this._tab === "wiring" && room && !room.isNew ? room.entry_id : null;
    if ((this._liveSub?.entry_id ?? null) === want) return;
    this._unsubscribe("_liveSub");
    this._live = null;
    if (want) this._subscribe("_liveSub", { type: "better_media_player/room/live", entry_id: want }, (m) => { this._live = { ...m, entry_id: want }; });
  }

  // the live state for this room's drawing (none: Live off, a route picked, a
  // cable being drawn, its setup changed and not saved)
  _liveFor(room) {
    if (!this._liveOn || this._livePaused || this._source || this._armed || !room || room.isNew || this._live?.entry_id !== room.entry_id) return null;
    if (this._setupChanged(room)) return null;
    return this._live;
  }

  // the switch: remembered in the browser
  _setLive(on) {
    this._liveOn = on;
    this._livePaused = false;
    if (on) this._source = null;
    try { localStorage.setItem("bmp-live", on ? "1" : "0"); } catch { /* (no storage) */ }
  }

  async _act(room, action) {
    try {
      await this.hass.callWS({ type: "better_media_player/room/act", entry_id: room.entry_id, action });
    } catch (err) {
      this._notice = err?.message || String(err);
    }
  }

  // ------------------------------------------------------------ render

  render() {
    const t = this._t;
    if (this._error)
      return html`<div class="message">${t("error", { error: this._error })}
        <button class="chip" @click=${() => { this._error = null; this._load(); }}>${t("retry")}</button></div>`;
    if (!this._config) return html`<div class="message">${t("loading")}</div>`;
    const room = this._room;
    this._lv = room ? this._liveFor(room) : null; // (once per drawing)
    return html`
      <div class="app">
        <header class="toolbar">
          <ha-menu-button .hass=${this.hass} .narrow=${this.narrow}></ha-menu-button>
          <h1>${t("title")}</h1>
        </header>
        ${this._renderSaveBar()}
        ${this._notice ? html`<div class="notice" role="alert">${this._notice}<button class="chip" @click=${() => { this._notice = null; }}>${t("dismiss")}</button></div>` : html`<div></div>`}
        <div class="main ${this._page === "room" && this._tab === "wiring" ? "details" : ""}">
          ${this._renderNav()}
          ${this._page === "central"
            ? html`<section class="stage"><div class="page">${renderCentral(this)}</div></section>`
            : room
              ? this._renderRoom(room)
              : html`<section class="stage"><div class="message">${t("no_rooms")}</div></section>`}
        </div>
      </div>
      ${this._renderDialog()}
    `;
  }

  _renderSaveBar() {
    const t = this._t;
    const dirty = this._dirtyRooms();
    const central = this._centralDirty();
    if (!dirty.length && !central) return html`<div class="savebar idle">${t("save.idle")}</div>`;
    const errors = dirty.flatMap((d) => (d.problems || []).filter((x) => x.error).map((x) => ({ d, x })));
    // a room whose setup changed is set up again: one that plays says so
    const restart = dirty.filter((d) => !d.isNew && this._setupChanged(d) && d.sources.some((s) => this.hass.states[s.player]?.state === "playing"));
    return html`<div class="savebar" role="region" aria-label=${t("save.label")}>
      <span class="msg">${errors.length
        ? html`<b class="err">${t(errors.length === 1 ? "save.errors_one" : "save.errors", { n: errors.length })}</b> ${errors[0].d.name}: ${this._problemTextIn(errors[0].d, errors[0].x)}`
        : t("save.unsaved")}</span>
      ${restart.length ? html`<span class="savewarn">${t("save.restart", { room: restart.map((d) => d.name).join(", ") })}</span>` : nothing}
      <button class="btn" ?disabled=${this._saving} @click=${() => this._discard()}>${t("save.discard")}</button>
      <button class="btn primary" ?disabled=${this._saving || errors.length > 0} @click=${() => this._save()}>${this._saving ? t("save.saving") : t("save.save")}</button>
    </div>`;
  }

  _problemTextIn(d, x) {
    const keep = this._roomId;
    this._roomId = d.entry_id;
    const text = this._problemText(x);
    this._roomId = keep;
    return text;
  }

  _setupChanged(d) {
    const a = payload(d), b = payload(this._base[d.entry_id]);
    return JSON.stringify([a.name, a.setup]) !== JSON.stringify([b.name, b.setup]); // (the area alone: no restart)
  }

  _renderNav() {
    const t = this._t;
    return html`<aside>
      <div class="side-title">${t("title")}</div>
      <button class="nav ${this._page === "central" ? "active" : ""}" @click=${() => this._go("central")}>
        <b>${t("nav.general")}</b><small>${t("nav.general_sub")}</small>
      </button>
      <div class="side-title">${t("nav.rooms")}</div>
      ${this._rooms().map((r) => {
        const errors = (r.problems || []).filter((x) => x.error).length;
        const dirty = r.isNew || !same(r, this._base[r.entry_id]);
        return html`<button class="nav ${this._page === "room" && r.entry_id === this._roomId ? "active" : ""}" @click=${() => this._go("room", r.entry_id)}>
          <b>${r.name}${dirty ? html` <span class="dot" title=${t("save.unsaved")}>•</span>` : nothing}</b>
          ${(() => { const l = this._liveOn && !r.isNew ? roomLine(this, r, this._lines[r.entry_id]) : null; return l ? html`<span class="rl ${l.cls}"><i></i>${l.text}</span>` : nothing; })()}
          <small>${t("nav.room_sub", this._counts(r))}${r.loaded ? "" : html` · <span class="warn">${t("nav.not_loaded")}</span>`}${errors ? html` · <span class="err">${this._count("errors", errors)}</span>` : nothing}</small>
        </button>`;
      })}
      <button class="nav add" @click=${() => this._addRoom()}><b>+ ${t("nav.add_room")}</b></button>
    </aside>`;
  }

  _renderRoom(room) {
    const t = this._t;
    return html`
      <section class="stage ${this._tab}">
        <div class="tabs" role="tablist">
          ${["wiring", "settings"].map(
            (tab) => html`<button role="tab" aria-selected=${String(this._tab === tab)} @click=${() => { this._tab = tab; this._selected = null; this._armed = null; }}>${t(`tab.${tab}`)}</button>`
          )}
        </div>
        ${this._tab === "settings"
          ? html`<div class="page">${renderSettings(this, room)}</div>`
          : html`${this._renderSources(room)}${this._renderProblems(room)}${this._renderDiagram(room)}${this._renderRoute(room)}`}
      </section>
      ${this._tab === "wiring" ? html`<section class="inspector">
        ${this._selected ? html`<button class="close" title=${t("close")} aria-label=${t("close")} @click=${() => { this._selected = null; }}>×</button>` : nothing}
        ${this._selected?.type === "node" && this._lv ? liveBox(this, room, this._lv, this._selected.id) : nothing}
        ${this._selected ? renderInspector(this, room) : renderOverview(this, room)}</section>` : nothing}
    `;
  }

  _renderProblems(room) {
    const list = room.problems || [];
    if (!list.length) return html`<div></div>`; // (keeps the drawing in its row)
    return html`<div class="problems strip">${list.map((x) => html`<div class=${x.error ? "err" : ""}>${x.error ? "● " : "○ "}${this._problemText(x)}</div>`)}</div>`;
  }

  _renderSources(room) {
    const t = this._t;
    const live = !room.isNew;
    const pick = (id) => { this._source = id; if (live) this._livePaused = true; };
    return html`<div class="sources">
      ${live ? html`<label class="livesw" title=${t("live.switch_hint")}>
        <input type="checkbox" role="switch" .checked=${this._liveOn && !this._livePaused} @change=${(e) => this._setLive(e.target.checked)}>
        ${this._lv ? html`<span class="livedot"></span>` : nothing}<span>${t("live.switch")}</span></label>` : nothing}
      <span class="lbl">${t("routes.label")}</span>
      ${room.sources.filter((s) => s.player).map(
        (s) => html`<button class="chip" aria-pressed=${String(!this._lv && this._source === s.id)} @click=${() => pick(s.id)}>${this._sourceLabel(s)}</button>`
      )}
      <button class="chip" aria-pressed=${String(!this._lv && !this._source)} @click=${() => pick(null)}>${t("routes.all")}</button>
      <button class="btn small add" @click=${() => { this._dialog = { type: "add" }; }}>+ ${t("add.button")}</button>
      <span class="legend">
        <span><i class="picture"></i>${t("legend.picture")}</span>
        <span><i class="sound"></i>${t("legend.sound")}</span>
        <span><i class="direct"></i>${t("legend.direct")}</span>
      </span>
    </div>`;
  }

  _renderDiagram(room) {
    const t = this._t;
    const nodes = this._tiles(room);
    const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
    const live = this._lv;
    const inUse = live ? liveRoute(room, live) : null;
    const route = this._source ? room.routes?.[this._source] : inUse ? room.routes?.[inUse.key] : null;
    const onRoute = this._source ? (route ? new Set([this._source, ...route.devices]) : null) : inUse ? inUse.tiles : null;
    const v = this._view();
    // direct feeds: a source to a device no cable reaches
    const directs = new Map();
    for (const s of room.sources) {
      if ((this._source && s.id !== this._source) || (!this._source && inUse && s.id !== inUse.key)) continue;
      const from = s.runs_on || s.id; // (one that runs on something: its feeds come from there)
      for (const d of room.routes?.[s.id]?.direct || []) directs.set(`${from}>${d}`, [from, d]);
    }
    const taken = nodes.map((n) => [n.x - 8, n.y - 10, n.x + W + 8, n.y + H + (live ? 26 : 4)]);
    const labels = [];
    const feeding = new Set(room.cables.map((c) => c.from)); // tiles a cable leaves
    const cables = room.cables.map((c, i) => {
      const a = byId[c.from], b = byId[c.to];
      if (!a || !b) return nothing;
      const d = curve(a.x + W, a.y + H / 2, b.x, b.y + H / 2);
      const dim = route && !route.cables.includes(i);
      const sel = this._selected?.type === "cable" && this._selected.index === i;
      const both = c.signals.length === 2;
      const cls = `cable ${both ? "picture" : c.signals[0]}${dim ? " dim" : ""}${sel ? " sel" : ""}`;
      if (c.input || c.mode === "cec") {
        const text = (c.input ?? "") + (c.mode === "cec" ? (c.input ? " · CEC" : "CEC") : "");
        const w = text.length * 7 + 14;
        const [x, y] = labelSpot(a.x + W, a.y + H / 2, b.x, b.y + H / 2, w, 22, taken);
        taken.push([x - w / 2, y - 11, x + w / 2, y + 11]);
        labels.push(svg`<g class="clabel${c.mode === "cec" ? " cec" : ""}${dim ? " dim" : ""}" transform="translate(${x} ${y})" @click=${(e) => this._select(e, { type: "cable", index: i })}>
          <rect x=${-w / 2} y="-11" width=${w} height="22" rx="6"></rect><text x="0" y="4" text-anchor="middle">${text}</text></g>`);
      }
      return svg`<path d=${d} class=${cls}></path>
        ${both ? svg`<path d=${d} class="cable sound${dim ? " dim" : ""}" transform="translate(0 4)"></path>` : nothing}
        ${inUse?.flowing && inUse.cables.has(i) ? svg`<path d=${d} class="flow"></path>` : nothing}
        <path d=${d} class="cable-hit" tabindex="0" role="button" aria-label=${`${t("insp.cable")} ${a.name} → ${b.name}`}
          @click=${(e) => this._select(e, { type: "cable", index: i })}
          @keydown=${(e) => e.key === "Enter" && this._select(e, { type: "cable", index: i })}></path>`;
    });
    return html`<div class="canvas-wrap ${this._armed ? "armed" : ""}" tabindex="0" @wheel=${this._onWheel} @keydown=${this._onKey}>
      <svg id="canvas" @pointerdown=${this._onPanStart} @click=${this._onCanvasClick} aria-label=${t("tab.wiring")}>
        <g id="world" transform="translate(${v.tx} ${v.ty}) scale(${v.k})">
          <g>${nodes.filter((n) => n.runsOn && byId[n.runsOn]).map((n) => {
            const h = byId[n.runsOn], x1 = n.x + W / 2, y1 = n.y + H, x2 = h.x + W / 2, y2 = h.y;
            return svg`<path class="cable runs${onRoute && !onRoute.has(n.id) ? " dim" : ""}" d=${`M${x1} ${y1} C${x1} ${y1 + 40} ${x2} ${y2 - 50} ${x2} ${y2}`}></path>`;
          })}${[...directs.values()].map(([from, to]) => {
            const a = byId[from], b = byId[to];
            const d = a && b ? curve(a.x + W, a.y + H / 2, b.x, b.y + H / 2) : null;
            return d ? svg`<path class="cable direct" d=${d}></path>${inUse?.flowing ? svg`<path d=${d} class="flow"></path>` : nothing}` : nothing;
          })}${cables}</g>
          <g>${nodes.map((n) => this._renderTile(room, n, onRoute && !onRoute.has(n.id), feeding.has(n.id), live ? tileStatus(this, room, live, n.id) : null))}</g>
          <g>${labels}</g>
        </g>
      </svg>
      <div class="zoombar" role="toolbar">
        <button title=${t("zoom.in")} aria-label=${t("zoom.in")} @click=${() => this._zoomBy(1.2)}><svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg></button>
        <button title=${t("zoom.out")} aria-label=${t("zoom.out")} @click=${() => this._zoomBy(1 / 1.2)}><svg viewBox="0 0 24 24"><path d="M5 12h14"/></svg></button>
        <div class="pct">${Math.round(v.k * 100)}%</div>
        <button title=${t("zoom.fit")} aria-label=${t("zoom.fit")} @click=${() => this._fit()}><svg viewBox="0 0 24 24"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg></button>
        <button title=${t("zoom.actual")} aria-label=${t("zoom.actual")} @click=${() => this._zoomBy(1 / v.k)}><svg viewBox="0 0 24 24"><path d="M7 8v8M11 9.5v.01M11 14.5v.01M15 8v8"/></svg></button>
        <button title=${t("zoom.arrange")} aria-label=${t("zoom.arrange")} @click=${() => this._rearrange(room)}><svg viewBox="0 0 24 24"><path d="M4 5h6v5H4zM14 14h6v5h-6zM10 7.5h3a2 2 0 0 1 2 2V14"/></svg></button>
      </div>
      <div class="navhint">${this._armed ? t("cable.armed") : t("nav_hint")}</div>
    </div>`;
  }

  _renderTile(room, n, dim, feeds, status = null) {
    const sel = this._selected?.type === "node" && this._selected.id === n.id;
    const passive = PASSIVE.includes(n.kind) || (n.device && !n.device.power);
    const name = n.name.length > 19 ? `${n.name.slice(0, 18)}…` : n.name;
    const on = n.runsOn && (room.sources.find((x) => x.id === n.runsOn) || room.devices.find((x) => x.id === n.runsOn));
    const raw = on ? this._t("tile.runs_on", { name: on.kind && room.devices.includes(on) ? on.name || this._t(`kind.${on.kind}`) : this._sourceName(on) }) : n.sub;
    const sub = raw.length > 23 ? `${raw.slice(0, 22)}…` : raw;
    const cec = n.device?.control?.on === "cec" && n.device.power?.devices?.length;
    const roomVolume = room.volume.where === "device" && room.volume.device === n.id && n.device?.volume;
    const armed = this._armed === n.id;
    const target = this._armed && canConnect(room, this._armed, n.id);
    const errors = (room.problems || []).some((x) => x.error && (x.device === n.id || x.source === n.id));
    return svg`<g class="node${sel ? " sel" : ""}${dim ? " dim" : ""}${passive ? " passive" : ""}${target ? " target" : ""}${errors ? " bad" : ""}" transform="translate(${n.x} ${n.y})"
        tabindex="0" role="button" aria-label=${status ? `${n.name}: ${status.text}` : n.name}
        @pointerdown=${(e) => this._onDragStart(e, n)}
        @click=${(e) => this._tileClick(e, room, n)}
        @keydown=${(e) => e.key === "Enter" && this._tileClick(e, room, n)}>
      <rect class="box" width=${W} height=${H} rx="10"></rect>
      <path class="icon" d=${iconPath(n.kind)} transform="translate(12 20)"></path>
      <text x="46" y="27" class="name">${name}</text>
      <text x="46" y="46" class="sub">${sub}</text>
      ${cec ? svg`<g class="badge" transform="translate(${W - 44} -8)"><rect width="36" height="16" rx="7"></rect><text x="18" y="11.5" text-anchor="middle">CEC</text></g>` : nothing}
      ${roomVolume ? svg`<g class="badge vol" transform="translate(8 -8)"><rect width="84" height="16" rx="7"></rect><text x="42" y="11.5" text-anchor="middle">${this._t("insp.room_volume")}</text></g>` : nothing}
      ${status ? (() => { const max = Math.floor((W + 36 - 28) / 6.1), text = status.text.length > max ? `${status.text.slice(0, max - 1)}…` : status.text; const w = Math.max(48, text.length * 6.1 + 28); return svg`<g class="pill ${status.cls}" transform="translate(${(W - w) / 2} ${H + 6})"><rect width=${w} height="18" rx="9"></rect><circle cx="11" cy="9" r="3.5"></circle><text x="20" y="12.8">${text}</text><title>${status.text}</title></g>`; })() : nothing}
      ${n.kind !== "player" ? svg`<circle class="port" cx="0" cy=${H / 2} r="6"></circle>` : nothing}
      ${n.runsOn ? nothing : feeds || !SINKS.includes(n.kind) || armed
        ? svg`<circle class="port out${armed ? " armed" : ""}" cx=${W} cy=${H / 2} r="7" tabindex="0" role="button" aria-label=${this._t("cable.from", { device: n.name })}
            @pointerdown=${(e) => e.stopPropagation()} @click=${(e) => this._arm(e, n)}><title>${this._t("cable.from", { device: n.name })}</title></circle>`
        : svg`<circle class="port out faint" cx=${W} cy=${H / 2} r="6" tabindex="0" role="button" aria-label=${this._t("cable.from", { device: n.name })}
            @pointerdown=${(e) => e.stopPropagation()} @click=${(e) => this._arm(e, n)}><title>${this._t("cable.from", { device: n.name })}</title></circle>`}
    </g>`;
  }

  _renderRoute(room) {
    const t = this._t;
    const src = room.sources.find((s) => s.id === this._source);
    if (this._armed) return html`<div class="route">${t("cable.armed")}</div>`;
    const live = this._lv;
    if (live) {
      const st = roomSentence(this, room, live);
      const said = roomSentence(this, room, live, true).text; // (a screen reader: whole minutes, not every second)
      return html`<div class="route live ${st.cls}"><span class="sr" aria-live="polite">${said}</span><span class="livedot"></span><span aria-hidden="true">${st.text}</span>
        ${(st.acts || []).length ? html`<span class="acts">${st.acts.map((a) => html`<button class="btn small" @click=${() => this._act(room, a)}>${{ keep_on: t("live.act_keep_on"), release: t("live.act_release"), all_off: t("live.act_all_off") }[a]}</button>`)}</span>` : nothing}</div>`;
    }
    if (this._liveOn && !this._source && !room.isNew && this._setupChanged(room))
      return html`<div class="route live note">${t("live.paused_unsaved")}</div>`;
    if (!src || !room.routes?.[src.id])
      return html`<div class="route">${t("route.pick")}</div>`;
    const r = room.routes[src.id];
    const devices = (r.order || []).map((id) => room.devices.find((d) => d.id === id)).filter(Boolean);
    const name = (d) => d.name || t(`kind.${d.kind}`);
    const step = (d) => {
      const inp = r.inputs[d.id];
      return html`<span class="step"><b>${name(d)}</b>${d.control?.on === "cec" && d.power?.devices?.length ? html` <span class="tag cec">${t("route.on_cec")}</span>` : nothing}${inp
        ? html` <span class="tag in">${inp.input}${inp.mode === "cec" ? " · CEC" : ""}</span>`
        : r.direct.includes(d.id) ? html` <span class="tag">${t("route.direct")}</span>` : nothing}</span>`;
    };
    // one that runs on another player (woken first) or on a device (on first)
    const host = room.sources.find((x) => x.id === src.runs_on);
    const first = (d) => r.picture.includes(d.id) || d.id === src.runs_on;
    return html`<div class="route"><b>${t("route.starts", { source: this._sourceLabel(src) })}</b>
      ${devices.filter(first).map(step)}
      ${host ? html`<span class="step"><b>${this._sourceName(host)}</b> <span class="tag">${t("route.woken")}</span></span>` : nothing}
      <span class="step"><b>${this._sourceName(src)}</b> <span class="tag">${t(src.runs_on ? "route.waits_there" : "route.woken")}</span></span>
      ${devices.filter((d) => !first(d)).map(step)}</div>`;
  }

  _renderDialog() {
    const t = this._t;
    const dlg = this._dialog;
    if (!dlg) return nothing;
    const close = () => { this._dialog = null; };
    if (dlg.type === "delete") {
      return html`<div class="dlg-back" @click=${(e) => e.target === e.currentTarget && close()}><div class="dlg" role="dialog" aria-modal="true" aria-labelledby="dlg-t">
        <h3 id="dlg-t">${t("delete.title", { room: dlg.room.name })}</h3>
        <p>${dlg.room.isNew ? t("delete.new") : t("delete.text")}</p>
        <div class="actions"><button class="btn" @click=${close}>${t("delete.cancel")}</button>
          <button class="btn danger" @click=${() => this._delete(dlg.room)}>${t("delete.confirm")}</button></div></div></div>`;
    }
    const d = this._room;
    const pick = (fn) => { this._change(d, fn); close(); };
    return html`<div class="dlg-back" @click=${(e) => e.target === e.currentTarget && close()}><div class="dlg" role="dialog" aria-modal="true" aria-labelledby="dlg-t">
      <h3 id="dlg-t">${t("add.title")}</h3>
      <div class="pick-list">
        <div class="side-title">${t("add.players")}</div>
        ${SOURCE_KINDS.map((k) => html`<button @click=${() => pick(() => {
            const first = !d.sources.some((s) => s.kind === k); // (a known player of the area is proposed for the first of a kind)
            const s = addSource(d, k, (first && this._config.suggested?.[d.area_id]?.[k]) || null);
            s.offers_onoff = s.player ? this._offersOnoff(s.player) : null;
            this._selected = { type: "node", id: s.id };
          })}>
          <b>${t(`source.${k}`)}</b><small>${t(`add.player_${k}`)}</small></button>`)}
        <div class="side-title">${t("add.devices")}</div>
        ${DEVICE_KINDS.map((k) => html`<button @click=${() => pick(() => { const dev = addDevice(d, k, this._config.defaults); this._selected = { type: "node", id: dev.id }; })}>
          <b>${t(`kind.${k}`)}</b><small>${t(`add.kind_${k}`)}</small></button>`)}
        <button @click=${() => pick(() => { const dev = addDevice(d, "speakers", this._config.defaults, { passive: true }); this._selected = { type: "node", id: dev.id }; })}>
          <b>${t("add.passive_speakers")}</b><small>${t("add.kind_passive_speakers")}</small></button>
      </div>
      <div class="actions"><button class="btn" @click=${close}>${t("delete.cancel")}</button></div></div></div>`;
  }

  // ------------------------------------------------------------ editing

  _tileClick(e, room, n) {
    e.stopPropagation();
    if (this._justDragged) return;
    if (this._armed) {
      if (canConnect(room, this._armed, n.id)) {
        const from = this._armed;
        this._armed = null;
        this._change(room, () => { this._selected = { type: "cable", index: addCable(room, from, n.id) }; });
      } else this._armed = null;
      return;
    }
    this._selected = { type: "node", id: n.id };
  }

  _arm(e, n) {
    e.stopPropagation();
    this._armed = this._armed === n.id ? null : n.id;
    this._focusDrawing();
  }

  _addRoom() {
    const d = newRoom(this._t("nav.new_room"), this._config.defaults.room);
    this._drafts[d.entry_id] = d;
    this._go("room", d.entry_id);
    this._tab = "settings";
  }

  _askDelete(d) {
    this._dialog = { type: "delete", room: d };
  }

  async _delete(d) {
    this._dialog = null;
    if (d.isNew) {
      delete this._drafts[d.entry_id];
      this._go(this._rooms().length ? "room" : "central", this._rooms()[0]?.entry_id ?? null);
      return;
    }
    try {
      await this.hass.callWS({ type: "better_media_player/room/delete", entry_id: d.entry_id });
      // the other rooms' unsaved changes stay (and are checked anew: a plug of the deleted room is free now)
      const keep = this._dirtyRooms().filter((x) => x.entry_id !== d.entry_id);
      await this._reloadKeeping(keep, this._centralDirty() ? this._central : null);
      if (keep.length) this._notice = this._t("delete.others_kept");
      this._tab = "wiring";
    } catch (err) {
      this._notice = this._t("delete.failed", { error: err?.message || String(err) });
    }
  }

  _discard() {
    for (const id of Object.keys(this._drafts)) {
      if (this._drafts[id].isNew) delete this._drafts[id];
      else this._drafts[id] = { ...clone(this._base[id]), routes: this._drafts[id].routes, problems: [] };
      if (this._drafts[id]) this._refresh(this._drafts[id], 0);
    }
    if (this._central) this._central = { ...clone(this._centralBase), problems: [] };
    this._nodes = {};
    this._selected = null;
    this._armed = null;
    if (!this._drafts[this._roomId]) this._go(this._rooms().length ? "room" : "central", this._rooms()[0]?.entry_id ?? null);
    this._tick++;
  }

  async _save() {
    this._saving = true;
    let select = this._drafts[this._roomId]?.isNew ? null : this._roomId;
    const pending = this._dirtyRooms();
    let central = this._centralDirty() ? this._central : null;
    let notice = null;
    try {
      while (pending.length) {
        const d = pending[0];
        const body = payload(d, this._base[d.entry_id]);
        const res = d.isNew
          ? await this.hass.callWS({ type: "better_media_player/room/create", name: body.name, area_id: body.area_id, setup: body.setup, settings: body.settings })
          : await this.hass.callWS({ type: "better_media_player/room/save", entry_id: d.entry_id, version: d.version, ...body });
        if (!res.saved) {
          d.problems = res.problems;
          notice = this._t("save.refused", { room: d.name });
          break;
        }
        pending.shift();
        if (d.isNew && d.entry_id === this._roomId) select = res.entry_id;
      }
      if (!notice && central) {
        const res = await this.hass.callWS({
          type: "better_media_player/central/save",
          follow_player: central.follow_player || null,
          // only what was changed here (an action may have changed others meanwhile)
          settings: Object.fromEntries(Object.entries(central.settings || {}).filter(([k, v]) => JSON.stringify(v) !== JSON.stringify(this._centralBase.settings?.[k]))),
        });
        if (res.saved) central = null;
        else {
          central.problems = res.problems;
          notice = this._t("save.refused", { room: this._t("nav.general") });
        }
      }
    } catch (err) {
      notice = err?.code === "changed" && pending.length
        ? this._t("save.changed", { room: pending[0].name })
        : this._t("save.failed", { error: err?.message || String(err) });
    }
    // what was saved is read back; what was not stays as it was being edited
    await this._reloadKeeping(pending, central, select);
    this._notice = notice;
    this._saving = false;
  }

  // the panel read anew, with these drafts (not saved) kept as they are
  async _reloadKeeping(drafts, central = null, select = null) {
    await this._load(select);
    for (const d of drafts) {
      if (!d.isNew && !this._base[d.entry_id]) continue; // (gone meanwhile)
      this._drafts[d.entry_id] = d;
      this._refresh(d, 0);
    }
    if (central && this._central) this._central = central;
    if (select && this._drafts[select]) this._roomId = select;
    this._tick++;
  }

  // ------------------------------------------------------------ interaction

  _select(e, what) {
    e.stopPropagation();
    if (this._justDragged) return;
    this._armed = null;
    this._selected = what;
  }

  _onCanvasClick(e) {
    if (this._justPanned || e.target.id !== "canvas") return;
    this._selected = null;
    this._armed = null;
  }

  _world() {
    return this.renderRoot.querySelector("#world");
  }

  _toWorld(e) {
    const svgEl = this.renderRoot.querySelector("#canvas");
    const p = svgEl.createSVGPoint();
    p.x = e.clientX;
    p.y = e.clientY;
    return p.matrixTransform(this._world().getScreenCTM().inverse());
  }

  _onDragStart(e, n) {
    if (e.button !== 0 || !e.isPrimary) return;
    e.preventDefault();
    e.stopPropagation();
    this._focusDrawing();
    const pt = this._toWorld(e);
    this._drag = { n, dx: pt.x - n.x, dy: pt.y - n.y, moved: false };
  }

  _focusDrawing() {  // (pointerdown is prevented: no text selection, so no focus by itself)
    const wrap = this.renderRoot.querySelector(".canvas-wrap");
    if (!wrap) return;
    wrap.dataset.pointer = "";  // focused by the mouse: no focus ring (that is for the keyboard)
    wrap.addEventListener("blur", () => delete wrap.dataset.pointer, { once: true });
    wrap.focus({ preventScroll: true });
  }

  _onPanStart(e) {
    if (e.button !== 0 || !e.isPrimary || e.target.id !== "canvas") return;
    e.preventDefault();
    this._focusDrawing();
    const v = this._view();
    this._pan = { x: e.clientX, y: e.clientY, tx: v.tx, ty: v.ty, moved: false };
  }

  _onMove(e) {
    if (this._drag) {
      const pt = this._toWorld(e);
      const nx = Math.round((pt.x - this._drag.dx) / 10) * 10, ny = Math.round((pt.y - this._drag.dy) / 10) * 10;
      const n = this._drag.n;
      if (nx !== n.x || ny !== n.y) {
        n.x = nx;
        n.y = ny;
        n.placed = true;
        this._drag.moved = true;
        this._tick++;
      }
    } else if (this._pan) {
      const dx = e.clientX - this._pan.x, dy = e.clientY - this._pan.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) this._pan.moved = true;
      this._setView(this._pan.tx + dx, this._pan.ty + dy, this._view().k);
    }
  }

  _onUp() {
    if (this._drag?.moved) {
      this._justDragged = true;
      setTimeout(() => { this._justDragged = false; }, 0);
      this._saveLayout(this._room);
    }
    if (this._pan?.moved) {
      this._justPanned = true;
      setTimeout(() => { this._justPanned = false; }, 0);
    }
    this._drag = null;
    this._pan = null;
  }

  _onWheel(e) {
    e.preventDefault();
    const v = this._view();
    const b = e.currentTarget.getBoundingClientRect();
    if (e.ctrlKey || e.metaKey) this._zoomAt(Math.exp(-e.deltaY * 0.0025), e.clientX - b.left, e.clientY - b.top);
    else this._setView(v.tx - e.deltaX, v.ty - e.deltaY, v.k);
  }

  // keys while the drawing has the focus (not across Home Assistant: it has shortcuts of its own)
  // Del / Backspace: the picked cable, device or player goes (an unsaved change, like
  // its "Remove" button); not while typing in a field
  _onDeleteKey(e) {
    if ((e.key !== "Delete" && e.key !== "Backspace") || e.repeat || e.ctrlKey || e.metaKey || e.altKey) return;
    const room = this._room;
    const sel = this._selected;
    if (!room || !sel || this._page !== "room" || this._tab !== "wiring" || this._dialog) return;
    const typing = e.composedPath().some((el) => el instanceof HTMLElement
      && (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) || el.isContentEditable || el.tagName === "HA-SELECTOR" || el.tagName?.startsWith?.("HA-TEXTFIELD")));
    if (typing) return;
    e.preventDefault();
    this._removeSelected(room, sel);
  }

  _removeSelected(room, sel) {
    if (sel.type === "cable") {
      if (!room.cables[sel.index]) return;
      this._change(room, () => { room.cables.splice(sel.index, 1); });
    } else if (room.sources.some((x) => x.id === sel.id)) {
      this._change(room, () => removeSource(room, sel.id));
    } else if (room.devices.some((x) => x.id === sel.id)) {
      this._change(room, () => removeDevice(room, sel.id));
    } else return;
    this._selected = null;
    this.renderRoot.querySelector(".canvas-wrap")?.focus({ preventScroll: true });
  }

  _onKey(e) {
    if (e.target !== e.currentTarget) return;
    const keys = { "+": () => this._zoomBy(1.2), "=": () => this._zoomBy(1.2), "-": () => this._zoomBy(1 / 1.2), 0: () => this._fit(), 1: () => this._zoomBy(1 / this._view().k) };
    const act = keys[e.key];
    if (act) {
      e.preventDefault();
      act();
    } else if (e.key === "Escape") {
      this._selected = null;
      this._armed = null;
    }
  }

  _setView(tx, ty, k) {
    const v = this._view();
    v.tx = tx;
    v.ty = ty;
    v.k = Math.min(2.5, Math.max(0.25, k));
    this._tick++;
  }

  _zoomAt(factor, cx, cy) {
    const v = this._view();
    const k = Math.min(2.5, Math.max(0.25, v.k * factor));
    this._setView(cx - (cx - v.tx) * (k / v.k), cy - (cy - v.ty) * (k / v.k), k);
  }

  _zoomBy(factor) {
    const b = this.renderRoot.querySelector(".canvas-wrap")?.getBoundingClientRect();
    if (b) this._zoomAt(factor, b.width / 2, b.height / 2);
  }

  _fit() {
    const room = this._room;
    const wrap = this.renderRoot.querySelector(".canvas-wrap");
    if (!room || !wrap) return;
    const b = wrap.getBoundingClientRect();
    if (!b.width || !b.height) return;
    const box = bounds(this._tiles(room));
    if (!box) return this._setView(40, 40, 1);
    const w = b.width - 64, h = b.height - 68; // clear of the zoom buttons, the hint and the tiles' live lines
    const k = Math.max(b.width < 600 ? 0.5 : 0, Math.min(1.2, w / (box.x1 - box.x0), h / (box.y1 - box.y0)));
    // wider than the screen (narrow, not smaller than readable): from the left, the rest by dragging
    const tx = (box.x1 - box.x0) * k > w ? -box.x0 * k : (w - (box.x1 - box.x0) * k) / 2 - box.x0 * k;
    this._setView(tx, (h - (box.y1 - box.y0) * k) / 2 - box.y0 * k, k);
  }

  updated() {
    this._syncLive();
    if (this._page === "room" && this._tab === "wiring" && this._room && !this._view().fitted && this.renderRoot.querySelector(".canvas-wrap")) {
      this._view().fitted = true;
      requestAnimationFrame(() => this._fit());
    }
  }

  // where the tiles sit: kept at once (only for the eye, nothing reloads); a room
  // not saved yet or not loaded keeps it until it is
  async _saveLayout(room) {
    if (!room) return;
    const layout = Object.fromEntries(this._tiles(room).filter((n) => n.placed).map((n) => [n.id, [n.x, n.y]]));
    room.layout = layout;
    if (room.isNew || !room.loaded) return;
    try {
      await this.hass.callWS({ type: "better_media_player/layout/save", entry_id: room.entry_id, layout });
    } catch (err) {
      this._notice = this._t("save_failed", { error: err?.message || String(err) });
    }
  }

  // the dotted lines as cables (an unsaved change), then everything placed anew
  _takeOver(room) {
    this._change(room, () => directToCables(room));
    this._rearrange(room);
  }

  async _rearrange(room) {
    for (const n of this._tiles(room)) n.placed = false;
    room.layout = {};
    this._nodes[room.entry_id] = arrange(nodesOf(room, this._t), room.cables, {});
    this._tick++;
    requestAnimationFrame(() => this._fit());
    await this._saveLayout(room);
  }

  // ------------------------------------------------------------ styles

  static styles = css`
    :host {
      display: block;
      /* the window's height: Home Assistant's panel frame gives none (2026.10) */
      height: 100vh;
      height: 100dvh;
      --bmp-picture: #1e88e5;
      --bmp-sound: #ef6c00;
      --bmp-cec: #7e57c2;
      --bmp-bg: var(--primary-background-color, #f4f6f8);
      --bmp-card: var(--card-background-color, #fff);
      --bmp-line: var(--divider-color, #e0e4e8);
      --bmp-text: var(--primary-text-color, #212121);
      --bmp-muted: var(--secondary-text-color, #6b7680);
      --bmp-primary: var(--primary-color, #03a9f4);
      --bmp-chip: var(--secondary-background-color, #eef3f7);
      --bmp-error: var(--error-color, #db4437);
      background: var(--bmp-bg);
      color: var(--bmp-text);
      font-family: var(--ha-font-family-body, Roboto, "Noto Sans", system-ui, sans-serif);
      font-size: 14px;
    }
    * { box-sizing: border-box; }
    button { font: inherit; color: inherit; cursor: pointer; }
    input, select { font: inherit; color: inherit; background: var(--bmp-card); border: 1px solid var(--bmp-line); border-radius: 6px; padding: 6px 8px; min-width: 0; }
    input:focus, select:focus { outline: 2px solid var(--bmp-primary); outline-offset: -1px; }
    input.mono { font-family: var(--code-font-family, ui-monospace, monospace); }
    hr { border: 0; border-top: 1px solid var(--bmp-line); margin: 14px 0; }
    .message { padding: 24px; color: var(--bmp-muted); }
    .app { display: grid; grid-template-rows: var(--header-height, 56px) auto auto 1fr; height: 100%; }
    .toolbar { display: flex; align-items: center; gap: 8px; padding: 0 16px; background: var(--app-header-background-color, var(--bmp-primary)); color: var(--app-header-text-color, #fff); }
    .toolbar h1 { font-size: 20px; font-weight: 400; margin: 0; }
    .notice { display: flex; align-items: center; gap: 12px; justify-content: space-between; padding: 8px 16px; background: var(--warning-color, #f9a825); color: #000; }
    .savebar { display: flex; align-items: center; gap: 12px; padding: 8px 16px; background: var(--bmp-card); border-bottom: 1px solid var(--bmp-line); flex-wrap: wrap; }
    .savebar.idle { color: var(--bmp-muted); font-size: 13px; }
    .savebar .msg { flex: 1; min-width: 200px; color: var(--bmp-muted); }
    .savebar .savewarn { color: var(--warning-color, #b26a00); font-size: 13px; max-width: 520px; }
    .btn { border: 1px solid var(--bmp-line); background: var(--bmp-card); border-radius: 18px; padding: 6px 16px; font-weight: 500; }
    .btn.primary { background: var(--bmp-primary); border-color: var(--bmp-primary); color: #fff; }
    .btn.danger { color: var(--bmp-error); border-color: color-mix(in srgb, var(--bmp-error) 40%, transparent); }
    .btn.small { padding: 4px 12px; font-size: 13px; }
    .btn:disabled { opacity: .45; cursor: not-allowed; }
    .btnrow { display: flex; gap: 8px; flex-wrap: wrap; }
    .err { color: var(--bmp-error); }
    .zoombar button:disabled { opacity: .4; cursor: default; }
    .main { display: grid; grid-template-columns: 220px 1fr; min-height: 0; }
    .main.details { grid-template-columns: 220px 1fr 340px; }
    .inspector { position: relative; }
    .close { position: absolute; top: 8px; right: 8px; width: 32px; height: 32px; border: 0; border-radius: 50%; background: none; font-size: 20px; color: var(--bmp-muted); }
    .close:hover { background: var(--bmp-chip); color: var(--bmp-text); }
    aside, .inspector { background: var(--bmp-card); overflow: auto; }
    aside { border-right: 1px solid var(--bmp-line); padding: 12px 0; }
    .inspector { border-left: 1px solid var(--bmp-line); padding: 16px; }
    .side-title { font-size: 12px; text-transform: uppercase; letter-spacing: .06em; color: var(--bmp-muted); padding: 12px 16px 8px; }
    .nav { display: flex; flex-direction: column; gap: 2px; width: 100%; text-align: left; border: 0; background: none; padding: 10px 16px; border-left: 3px solid transparent; }
    .nav:hover { background: var(--bmp-chip); }
    .nav.active { border-left-color: var(--bmp-primary); background: var(--bmp-chip); }
    .nav b { font-weight: 500; }
    .nav small { color: var(--bmp-muted); }
    .nav.add b { color: var(--bmp-primary); }
    .nav .dot { color: var(--bmp-primary); }
    .warn { color: var(--warning-color, #f9a825); }
    .stage { display: grid; grid-template-rows: auto auto auto 1fr auto; min-width: 0; min-height: 0; }
    .stage.settings { grid-template-rows: auto 1fr; }
    .tabs { display: flex; gap: 4px; padding: 0 16px; background: var(--bmp-card); border-bottom: 1px solid var(--bmp-line); }
    .tabs button { border: 0; background: none; padding: 12px 14px 10px; border-bottom: 3px solid transparent; color: var(--bmp-muted); font-weight: 500; }
    .tabs button[aria-selected="true"] { color: var(--bmp-primary); border-bottom-color: var(--bmp-primary); }
    .page { overflow: auto; padding: 20px 16px; display: grid; gap: 16px; align-content: start; align-items: start; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); }
    .card { background: var(--bmp-card); border: 1px solid var(--bmp-line); border-radius: var(--ha-card-border-radius, 12px); padding: 16px 18px; display: grid; gap: 10px; align-content: start; }
    .card.wide { grid-column: 1 / -1; }
    .card h3 { margin: 0; font-size: 16px; font-weight: 500; }
    .card h4 { margin: 8px 0 0; font-size: 13px; font-weight: 500; color: var(--bmp-muted); text-transform: uppercase; letter-spacing: .04em; }
    .settings { display: grid; gap: 0; }
    .row { display: grid; grid-template-columns: 1fr auto; gap: 12px; align-items: center; padding: 5px 0; border-bottom: 1px solid var(--bmp-line); }
    .row:last-child { border-bottom: 0; }
    .row input[type="number"] { width: 90px; text-align: right; }
    .num { display: inline-flex; align-items: center; gap: 6px; }
    .unit { color: var(--bmp-muted); font-size: 12px; min-width: 24px; }
    .toggle { width: 18px; height: 18px; }
    .sources { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 12px 16px; border-bottom: 1px solid var(--bmp-line); background: var(--bmp-card); }
    .sources .add { margin-left: 8px; }
    .lbl { color: var(--bmp-muted); }
    .chip { border: 1px solid var(--bmp-line); background: var(--bmp-chip); border-radius: 16px; padding: 5px 12px; }
    .chip[aria-pressed="true"] { background: var(--bmp-primary); border-color: var(--bmp-primary); color: #fff; }
    .legend { margin-left: auto; display: flex; gap: 14px; color: var(--bmp-muted); font-size: 12px; align-items: center; flex-wrap: wrap; }
    .legend i { display: inline-block; width: 22px; height: 0; border-top: 3px solid; vertical-align: middle; margin-right: 6px; }
    .legend i.picture { border-color: var(--bmp-picture); }
    .legend i.sound { border-color: var(--bmp-sound); border-top-style: dashed; }
    .legend i.direct { border-color: var(--bmp-muted); border-top-style: dotted; border-top-width: 2px; }
    .problems { padding: 8px 12px; border-radius: 8px; background: color-mix(in srgb, var(--warning-color, #f9a825) 14%, transparent); font-size: 13px; display: grid; gap: 4px; margin-bottom: 12px; }
    .problems.strip { margin: 0; border-radius: 0; border-bottom: 1px solid var(--bmp-line); padding: 8px 16px; max-height: 120px; overflow: auto; }
    .canvas-wrap:focus { outline: none; }
    .canvas-wrap:focus-visible:not([data-pointer]) { outline: 2px solid var(--bmp-primary); outline-offset: -2px; }
    .canvas-wrap { position: relative; overflow: hidden; min-height: 0; background-image: radial-gradient(var(--bmp-line) 1px, transparent 1px); background-size: 20px 20px; }
    svg#canvas { display: block; width: 100%; height: 100%; user-select: none; -webkit-user-select: none; touch-action: none; cursor: grab; }
    .armed svg#canvas { cursor: crosshair; }
    .zoombar { position: absolute; right: 12px; bottom: 12px; display: flex; flex-direction: column; gap: 6px; }
    .zoombar button { width: 36px; height: 36px; border-radius: 10px; border: 1px solid var(--bmp-line); background: var(--bmp-card); display: grid; place-items: center; }
    .zoombar button:hover { border-color: var(--bmp-primary); }
    .zoombar svg { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
    .zoombar .pct { font-size: 11px; color: var(--bmp-muted); text-align: center; }
    .navhint { position: absolute; left: 12px; bottom: 12px; font-size: 12px; color: var(--bmp-muted); background: var(--bmp-card); border: 1px solid var(--bmp-line); border-radius: 8px; padding: 4px 8px; }
    .route { padding: 10px 16px; border-top: 1px solid var(--bmp-line); background: var(--bmp-card); color: var(--bmp-muted); min-height: 44px; }
    .route b { color: var(--bmp-text); font-weight: 500; }
    .step { display: inline-flex; align-items: center; gap: 6px; }
    .step + .step::before { content: "→"; margin: 0 8px; color: var(--bmp-muted); }
    .tag { font-size: 11px; border-radius: 8px; padding: 1px 6px; background: var(--bmp-chip); color: var(--bmp-muted); }
    .tag.cec { color: var(--bmp-cec); }
    .tag.room { color: var(--bmp-primary); }
    .tag.in { font-family: var(--code-font-family, ui-monospace, monospace); }
    .node { cursor: grab; }
    .node .box { fill: var(--bmp-card); stroke: var(--bmp-line); stroke-width: 1.5; }
    .node.sel .box { stroke: var(--bmp-primary); stroke-width: 2.5; }
    .node.bad .box { stroke: var(--bmp-error); }
    .node.target .box { stroke: var(--bmp-primary); stroke-dasharray: 4 3; stroke-width: 2; }
    .node.dim { opacity: .3; }
    .node.passive .box { stroke-dasharray: 5 4; }
    .node .name { fill: var(--bmp-text); font-size: 14px; font-weight: 500; }
    .node .sub { fill: var(--bmp-muted); font-size: 11.5px; font-family: var(--code-font-family, ui-monospace, monospace); }
    .node .icon { fill: none; stroke: var(--bmp-muted); stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; }
    .badge rect { fill: var(--bmp-cec); }
    .badge.vol rect { fill: var(--bmp-primary); }
    .badge text { fill: #fff; font-size: 10.5px; font-weight: 500; }
    .port { fill: var(--bmp-card); stroke: var(--bmp-muted); stroke-width: 1.5; }
    .port.out { cursor: crosshair; }
    .port.out:hover, .port.armed { fill: var(--bmp-primary); stroke: var(--bmp-primary); }
    .port.faint { opacity: .45; }
    .cable { fill: none; stroke-width: 3; }
    .cable.picture { stroke: var(--bmp-picture); }
    .cable.sound { stroke: var(--bmp-sound); stroke-dasharray: 7 5; }
    .cable.sel { stroke-width: 5; }
    .cable.dim { opacity: .15; }
    .cable.direct { stroke: var(--bmp-muted); stroke-width: 1.5; stroke-dasharray: 2 5; opacity: .7; }
    .cable.runs { stroke: var(--bmp-muted); stroke-width: 2.5; stroke-dasharray: 1 6; stroke-linecap: round; fill: none; }
    .cable-hit { fill: none; stroke: transparent; stroke-width: 14; cursor: pointer; }
    .clabel { cursor: pointer; }
    .clabel rect { fill: var(--bmp-card); stroke: var(--bmp-line); }
    .clabel text { font-size: 11.5px; font-family: var(--code-font-family, ui-monospace, monospace); fill: var(--bmp-text); }
    .clabel.cec rect { stroke: var(--bmp-cec); }
    .clabel.dim { opacity: .2; }
    .sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
    .livesw { display: inline-flex; align-items: center; gap: 6px; padding-right: 12px; margin-right: 4px; border-right: 1px solid var(--bmp-line); color: var(--bmp-muted); cursor: pointer; }
    .livesw input { width: 16px; height: 16px; margin: 0; accent-color: var(--bmp-primary); }
    .livedot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: var(--bmp-error); margin-right: 6px; vertical-align: middle; animation: blink 1.6s ease-in-out infinite; }
    @keyframes blink { 50% { opacity: .25; } }
    .route.live { color: var(--bmp-text); background: color-mix(in srgb, var(--bmp-primary) 7%, var(--bmp-card)); }
    .route.live.note { background: color-mix(in srgb, var(--warning-color, #f9a825) 12%, var(--bmp-card)); }
    .route.live.bad { background: color-mix(in srgb, var(--bmp-error) 9%, var(--bmp-card)); }
    .route .acts { display: inline-flex; gap: 6px; margin-left: 10px; vertical-align: middle; }
    .pill rect { fill: var(--bmp-card); stroke: var(--bmp-line); }
    .pill text { font-size: 11px; fill: var(--bmp-text); font-weight: 500; }
    .pill circle { fill: var(--bmp-muted); }
    .pill.off text { fill: var(--bmp-muted); font-weight: 400; }
    .pill.on circle, .pill.play circle { fill: var(--success-color, #43a047); }
    .pill.play rect { stroke: var(--success-color, #43a047); }
    .pill.wait circle { fill: var(--warning-color, #f9a825); animation: blink 1s ease-in-out infinite; }
    .pill.wait rect { stroke: var(--warning-color, #f9a825); }
    .pill.down circle { fill: var(--bmp-primary); }
    .pill.err rect { stroke: var(--bmp-error); } .pill.err circle { fill: var(--bmp-error); } .pill.err text { fill: var(--bmp-error); }
    .pill.manual rect { stroke: var(--warning-color, #f9a825); } .pill.manual circle { fill: var(--warning-color, #f9a825); }
    .flow { fill: none; stroke: #fff; stroke-width: 2; stroke-dasharray: 2 14; stroke-linecap: round; opacity: .85; pointer-events: none; animation: flow .9s linear infinite; }
    @keyframes flow { to { stroke-dashoffset: -16; } }
    .rl { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; }
    .rl i, .lv i { width: 7px; height: 7px; border-radius: 50%; background: var(--bmp-muted); display: inline-block; flex: none; }
    .rl.play i, .rl.on i, .lv.play i, .lv.on i { background: var(--success-color, #43a047); }
    .rl.wait i, .rl.manual i, .lv.wait i, .lv.manual i { background: var(--warning-color, #f9a825); }
    .rl.down i, .lv.down i { background: var(--bmp-primary); }
    .rl.err, .lv.err { color: var(--bmp-error); } .rl.err i, .lv.err i { background: var(--bmp-error); }
    .livebox { border: 1px solid var(--bmp-line); border-radius: 10px; padding: 10px 12px; margin: 0 32px 16px 0; display: grid; gap: 3px; font-size: 13px; }
    .livebox .lv { display: inline-flex; align-items: center; gap: 6px; }
    .livebox b { font-weight: 500; }
    .livebox small { color: var(--bmp-muted); overflow-wrap: anywhere; }
    @media (prefers-reduced-motion: reduce) { .flow { display: none; } .livedot, .pill.wait circle { animation: none; } }
    .takeover { border: 1px solid color-mix(in srgb, var(--bmp-primary) 45%, var(--bmp-line)); background: color-mix(in srgb, var(--bmp-primary) 8%, var(--bmp-card)); border-radius: 10px; padding: 12px 14px; margin-bottom: 16px; display: grid; gap: 8px; }
    .takeover p { margin: 0; font-size: 13px; }
    .takeover .btn { justify-self: start; }
    .inspector h2 { font-size: 18px; font-weight: 500; margin: 0 0 2px; padding-right: 32px; }
    .kind { color: var(--bmp-muted); margin-bottom: 16px; }
    .field { display: grid; gap: 6px; margin-bottom: 14px; }
    .field > .lbl { font-size: 12px; }
    .field small { font-size: 12px; }
    .hint { font-size: 12.5px; margin: -6px 0 14px; }
    .pick { display: flex; gap: 6px; align-items: center; }
    .pick input { flex: 1; font-family: var(--code-font-family, ui-monospace, monospace); font-size: 12.5px; }
    .x { border: 0; background: none; color: var(--bmp-muted); font-size: 18px; line-height: 1; padding: 2px 6px; border-radius: 50%; }
    .x:hover { background: var(--bmp-chip); color: var(--bmp-text); }
    .chipent { display: flex; align-items: center; justify-content: space-between; gap: 8px; background: var(--bmp-chip); border-radius: 6px; padding: 4px 4px 4px 8px; }
    .chipent span { display: grid; min-width: 0; }
    .chipent small { font-family: var(--code-font-family, ui-monospace, monospace); color: var(--bmp-muted); overflow-wrap: anywhere; }
    .check { display: flex; gap: 8px; align-items: center; margin-bottom: 12px; }
    .seg { display: inline-flex; flex-wrap: wrap; border: 1px solid var(--bmp-line); border-radius: 18px; overflow: hidden; width: fit-content; }
    .seg button { border: 0; background: none; padding: 5px 12px; }
    .seg button + button { border-left: 1px solid var(--bmp-line); }
    .seg button[aria-pressed="true"] { background: var(--bmp-primary); color: #fff; }
    .pair { display: grid; grid-template-columns: auto 1fr auto; gap: 8px; align-items: center; font-size: 12.5px; }
    .pair.add { grid-template-columns: 1fr 1.4fr auto; }
    .pair code, .pair span { font-family: var(--code-font-family, ui-monospace, monospace); overflow-wrap: anywhere; }
    .ent { display: block; text-align: left; font-family: var(--code-font-family, ui-monospace, monospace); font-size: 12.5px; background: var(--bmp-chip); border: 0; border-radius: 6px; padding: 6px 8px; overflow-wrap: anywhere; }
    .inputs { display: flex; flex-wrap: wrap; gap: 6px; }
    .inputs span { font-family: var(--code-font-family, ui-monospace, monospace); font-size: 12px; background: var(--bmp-chip); border-radius: 6px; padding: 3px 7px; }
    .muted { color: var(--bmp-muted); }
    .dlg-back { position: fixed; inset: 0; background: rgba(0, 0, 0, .35); display: grid; place-items: center; z-index: 10; padding: 16px; }
    .dlg { background: var(--bmp-card); border-radius: 14px; padding: 20px; width: min(460px, 100%); max-height: 85vh; overflow: auto; box-shadow: 0 10px 40px rgba(0, 0, 0, .25); }
    .dlg h3 { margin: 0 0 10px; font-weight: 500; }
    .dlg .actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
    .pick-list { display: grid; gap: 4px; }
    .pick-list .side-title { padding: 8px 0 2px; }
    .pick-list button { display: grid; text-align: left; border: 1px solid var(--bmp-line); background: none; border-radius: 8px; padding: 8px 12px; }
    .pick-list button:hover { border-color: var(--bmp-primary); }
    .pick-list small { color: var(--bmp-muted); }
    @media (max-width: 1100px) { .main { grid-template-columns: 180px 1fr; } .main.details { grid-template-columns: 180px 1fr 300px; } }
    @media (max-width: 820px) {
      .main { display: block; overflow: auto; }
      aside { display: flex; gap: 8px; align-items: center; overflow-x: auto; padding: 8px 16px; border-right: 0; border-bottom: 1px solid var(--bmp-line); }
      .side-title { display: none; }
      .nav { flex: 0 0 auto; width: auto; flex-direction: row; padding: 6px 14px; border: 1px solid var(--bmp-line); border-radius: 16px; background: var(--bmp-chip); white-space: nowrap; }
      .nav small, .nav .rl { display: none; }
      .nav.active { background: var(--bmp-primary); border-color: var(--bmp-primary); color: #fff; }
      .stage, .stage.settings { display: block; }
      .canvas-wrap { height: 60vh; min-height: 360px; }
      .page { grid-template-columns: 1fr; }
      .legend, .navhint { display: none; }
      .inspector { border-left: 0; border-top: 1px solid var(--bmp-line); }
    }
  `;
}

if (!customElements.get("bmp-panel")) customElements.define("bmp-panel", BmpPanel);
