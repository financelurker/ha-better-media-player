// Better Media Player: the configuration panel (this release: view only).
// Rooms on the left, the room as a drawing in the middle, details on the right.
// Data from the websocket command better_media_player/config/get; the routes are
// worked out by the integration (the same calculation that switches the room).

import { LitElement, html, svg, css, nothing } from "./vendor/lit.js";
import { translator } from "./i18n.js";
import { W, H, PASSIVE, SINKS, nodesOf, arrange, curve, labelSpot, bounds, iconPath } from "./diagram.js";

const PANEL_URL = "better-media-player";

// the unit a setting's number is in (settings: set_setting / get_settings)
const UNITS = {
  video_idle_timeout: "min", night_video_idle_timeout: "min", night_max_volume: "%", night_fade_in: "min",
  preset_quiet: "%", preset_normal: "%", preset_loud: "%", preset_party: "%", preset_fade: "s",
  announce_video_lower: "%", start_timeout: "s", sleep_fade: "min",
  snooze_minutes: "min", presence_run_down: "min", volume_offset: "pts",
  run_down: "min", night_run_down: "min", on_wait: "s", standby_w: "W", standby_max: "min",
  default_volume: "%", hide_delay: "s",
};
const STANDBY = ["standby", "standby_w", "standby_max"]; // only with power sensors

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
    this._tick = 0;
    this._t = (k) => k;
    this._lang = null;
    this._nodes = {}; // per room: the tiles with their places
    this._views = {}; // per room: pan and zoom
    this._drag = null;
    this._pan = null;
    this._onMove = this._onMove.bind(this);
    this._onUp = this._onUp.bind(this);
  }

  connectedCallback() {
    super.connectedCallback();
    window.addEventListener("pointermove", this._onMove);
    window.addEventListener("pointerup", this._onUp);
  }

  disconnectedCallback() {
    window.removeEventListener("pointermove", this._onMove);
    window.removeEventListener("pointerup", this._onUp);
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
    this._nodes = {};  // the tiles' names (kinds) in the language
    this.requestUpdate();
  }

  async _load() {
    this._loading = true;
    try {
      this._config = await this.hass.callWS({ type: "better_media_player/config/get" });
      this._nodes = {};
      // the room asked for: /better-media-player/<entry id> (a device's link) or ?config_entry=<id>
      const wanted = (this.route?.path || "").replace(/^\//, "") || new URLSearchParams(location.search).get("config_entry") || "";
      const rooms = this._config.rooms;
      if (wanted && rooms.some((r) => r.entry_id === wanted)) this._roomId = wanted;
      else if (!rooms.some((r) => r.entry_id === this._roomId)) this._roomId = rooms[0]?.entry_id ?? null;
      if (!rooms.length) this._page = "central";
      this._error = null;
    } catch (err) {
      this._error = err?.message || String(err);
    } finally {
      this._loading = false;
    }
  }

  // ------------------------------------------------------------ helpers

  get _room() {
    return this._config?.rooms.find((r) => r.entry_id === this._roomId) ?? null;
  }

  _view() {
    return (this._views[this._roomId] ??= { tx: 0, ty: 0, k: 1, fitted: false });
  }

  _tiles(room) {
    if (!this._nodes[room.entry_id]) this._nodes[room.entry_id] = arrange(nodesOf(room, this._t), room.cables, room.layout);
    return this._nodes[room.entry_id];
  }

  _go(page, roomId = this._roomId) {
    this._page = page;
    this._roomId = roomId;
    this._selected = null;
    this._source = null;
    const path = page === "room" && roomId ? `/${PANEL_URL}/${roomId}` : `/${PANEL_URL}`;
    if (location.pathname !== path) history.replaceState(history.state, "", path);
  }

  _moreInfo(entityId) {
    this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true }));
  }

  // "1 device" / "3 devices"
  _count(what, n) {
    return this._t(n === 1 ? `count.${what}_one` : `count.${what}`, { n });
  }

  _counts(room) {
    return { devices: this._count("devices", room.sources.length + room.devices.length), cables: this._count("cables", room.cables.length) };
  }

  _sourceLabel(s) {
    const t = this._t;
    return s.name ? `${t(`source.${s.id}`)} · ${s.name}` : t(`source.${s.id}`);
  }

  // ------------------------------------------------------------ render

  render() {
    const t = this._t;
    if (this._error)
      return html`<div class="message">${t("error", { error: this._error })}
        <button class="chip" @click=${() => { this._error = null; this._load(); }}>${t("retry")}</button></div>`;
    if (!this._config) return html`<div class="message">${t("loading")}</div>`;
    const room = this._room;
    return html`
      <div class="app">
        <header class="toolbar">
          <ha-menu-button .hass=${this.hass} .narrow=${this.narrow}></ha-menu-button>
          <h1>${t("title")}</h1>
        </header>
        <div class="viewonly">${t("view_only")}</div>
        ${this._notice ? html`<div class="notice" role="alert">${this._notice}<button class="chip" @click=${() => { this._notice = null; }}>${t("dismiss")}</button></div>` : nothing}
        <div class="main ${this._page === "room" && this._tab === "wiring" && this._selected ? "details" : ""}">
          ${this._renderNav()}
          ${this._page === "central"
            ? html`<section class="stage"><div class="page">${this._renderCentral()}</div></section>`
            : room
              ? this._renderRoom(room)
              : html`<section class="stage"><div class="message">${t("no_rooms")}</div></section>`}
        </div>
      </div>
    `;
  }

  _renderNav() {
    const t = this._t;
    return html`<aside>
      <div class="side-title">${t("title")}</div>
      <button class="nav ${this._page === "central" ? "active" : ""}" @click=${() => this._go("central")}>
        <b>${t("nav.general")}</b><small>${t("nav.general_sub")}</small>
      </button>
      <div class="side-title">${t("nav.rooms")}</div>
      ${this._config.rooms.map(
        (r) => html`<button class="nav ${this._page === "room" && r.entry_id === this._roomId ? "active" : ""}" @click=${() => this._go("room", r.entry_id)}>
          <b>${r.name}</b>
          <small>${t("nav.room_sub", this._counts(r))}${r.loaded ? "" : html` · <span class="warn">${t("nav.not_loaded")}</span>`}</small>
        </button>`
      )}
    </aside>`;
  }

  _renderRoom(room) {
    const t = this._t;
    return html`
      <section class="stage ${this._tab}">
        <div class="tabs" role="tablist">
          ${["wiring", "settings"].map(
            (tab) => html`<button role="tab" aria-selected=${String(this._tab === tab)} @click=${() => { this._tab = tab; this._selected = null; }}>${t(`tab.${tab}`)}</button>`
          )}
        </div>
        ${this._tab === "settings"
          ? html`<div class="page">${this._renderSettings(room)}</div>`
          : html`${this._renderSources(room)}${this._renderDiagram(room)}${this._renderRoute(room)}`}
      </section>
      ${this._tab === "wiring" && this._selected ? html`<section class="inspector">
        <button class="close" title=${t("close")} aria-label=${t("close")} @click=${() => { this._selected = null; }}>×</button>
        ${this._renderInspector(room)}</section>` : nothing}
    `;
  }

  _renderSources(room) {
    const t = this._t;
    return html`<div class="sources">
      <span class="lbl">${t("routes.label")}</span>
      ${room.sources.map(
        (s) => html`<button class="chip" aria-pressed=${String(this._source === s.id)} @click=${() => { this._source = s.id; }}>${this._sourceLabel(s)}</button>`
      )}
      <button class="chip" aria-pressed=${String(!this._source)} @click=${() => { this._source = null; }}>${t("routes.all")}</button>
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
    const route = this._source ? room.routes[this._source] : null;
    const onRoute = route ? new Set([this._source, ...route.devices]) : null;
    const v = this._view();
    // direct feeds: a source to a device no cable reaches
    const directs = new Map();
    for (const s of room.sources) {
      if (this._source && s.id !== this._source) continue;
      for (const d of room.routes[s.id]?.direct || []) directs.set(`${s.id}>${d}`, [s.id, d]);
    }
    const taken = nodes.map((n) => [n.x - 8, n.y - 10, n.x + W + 8, n.y + H + 4]);
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
        <path d=${d} class="cable-hit" tabindex="0" role="button" aria-label=${`${t("insp.cable")} ${a.name} → ${b.name}`}
          @click=${(e) => this._select(e, { type: "cable", index: i })}
          @keydown=${(e) => e.key === "Enter" && this._select(e, { type: "cable", index: i })}></path>`;
    });
    return html`<div class="canvas-wrap" tabindex="0" @wheel=${this._onWheel} @keydown=${this._onKey}>
      <svg id="canvas" @pointerdown=${this._onPanStart} @click=${this._onCanvasClick} aria-label=${t("tab.wiring")}>
        <g id="world" transform="translate(${v.tx} ${v.ty}) scale(${v.k})">
          <g>${[...directs.values()].map(([from, to]) => {
            const a = byId[from], b = byId[to];
            return a && b ? svg`<path class="cable direct" d=${curve(a.x + W, a.y + H / 2, b.x, b.y + H / 2)}></path>` : nothing;
          })}${cables}</g>
          <g>${nodes.map((n) => this._renderTile(n, onRoute && !onRoute.has(n.id), feeding.has(n.id)))}</g>
          <g>${labels}</g>
        </g>
      </svg>
      <div class="zoombar" role="toolbar">
        <button title=${t("zoom.in")} aria-label=${t("zoom.in")} @click=${() => this._zoomBy(1.2)}><svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg></button>
        <button title=${t("zoom.out")} aria-label=${t("zoom.out")} @click=${() => this._zoomBy(1 / 1.2)}><svg viewBox="0 0 24 24"><path d="M5 12h14"/></svg></button>
        <div class="pct">${Math.round(v.k * 100)}%</div>
        <button title=${t("zoom.fit")} aria-label=${t("zoom.fit")} @click=${() => this._fit()}><svg viewBox="0 0 24 24"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg></button>
        <button title=${t("zoom.actual")} aria-label=${t("zoom.actual")} @click=${() => this._zoomBy(1 / v.k)}><svg viewBox="0 0 24 24"><path d="M7 8v8M11 9.5v.01M11 14.5v.01M15 8v8"/></svg></button>
        <button title=${t("zoom.arrange")} aria-label=${t("zoom.arrange")} ?disabled=${!room.loaded} @click=${() => this._rearrange(room)}><svg viewBox="0 0 24 24"><path d="M4 5h6v5H4zM14 14h6v5h-6zM10 7.5h3a2 2 0 0 1 2 2V14"/></svg></button>
      </div>
      <div class="navhint">${t("nav_hint")}</div>
    </div>`;
  }

  _renderTile(n, dim, feeds) {
    const sel = this._selected?.type === "node" && this._selected.id === n.id;
    const passive = PASSIVE.includes(n.kind);
    const name = n.name.length > 19 ? `${n.name.slice(0, 18)}…` : n.name;
    const sub = n.sub.length > 23 ? `${n.sub.slice(0, 22)}…` : n.sub;
    const cec = n.device?.control?.on === "cec";
    return svg`<g class="node${sel ? " sel" : ""}${dim ? " dim" : ""}${passive ? " passive" : ""}" transform="translate(${n.x} ${n.y})"
        tabindex="0" role="button" aria-label=${n.name}
        @pointerdown=${(e) => this._onDragStart(e, n)}
        @click=${(e) => this._select(e, { type: "node", id: n.id })}
        @keydown=${(e) => e.key === "Enter" && this._select(e, { type: "node", id: n.id })}>
      <rect class="box" width=${W} height=${H} rx="10"></rect>
      <path class="icon" d=${iconPath(n.kind)} transform="translate(12 20)"></path>
      <text x="46" y="27" class="name">${name}</text>
      <text x="46" y="46" class="sub">${sub}</text>
      ${cec ? svg`<g class="badge" transform="translate(${W - 44} -8)"><rect width="36" height="16" rx="7"></rect><text x="18" y="11.5" text-anchor="middle">CEC</text></g>` : nothing}
      ${n.kind !== "player" ? svg`<circle class="port" cx="0" cy=${H / 2} r="6"></circle>` : nothing}
      ${feeds || !SINKS.includes(n.kind) ? svg`<circle class="port" cx=${W} cy=${H / 2} r="6"></circle>` : nothing}
    </g>`;
  }

  _renderRoute(room) {
    const t = this._t;
    const src = room.sources.find((s) => s.id === this._source);
    if (!src)
      return html`<div class="route">${!room.cables.length && room.devices.length ? html`${t("insp.migrated")} ` : nothing}${t("route.pick")} ${t("insp.click")}</div>`;
    const r = room.routes[src.id];
    const devices = r.order.map((id) => room.devices.find((d) => d.id === id)).filter(Boolean);
    const name = (d) => d.name || t(`kind.${d.kind}`);
    const step = (d) => {
      const inp = r.inputs[d.id];
      return html`<span class="step"><b>${name(d)}</b>${d.control?.on === "cec" ? html` <span class="tag cec">${t("route.on_cec")}</span>` : nothing}${inp
        ? html` <span class="tag in">${inp.input}${inp.mode === "cec" ? " · CEC" : ""}</span>`
        : r.direct.includes(d.id) ? html` <span class="tag">${t("route.direct")}</span>` : nothing}</span>`;
    };
    return html`<div class="route"><b>${t("route.starts", { source: this._sourceLabel(src) })}</b>
      ${devices.filter((d) => r.picture.includes(d.id)).map(step)}
      <span class="step"><b>${src.name || src.player}</b> <span class="tag">${t("route.woken")}</span></span>
      ${devices.filter((d) => !r.picture.includes(d.id)).map(step)}</div>`;
  }

  _renderInspector(room) {
    const t = this._t;
    const sel = this._selected;
    if (sel.type === "cable") {
      const c = room.cables[sel.index];
      if (!c) return nothing;
      const nodes = this._tiles(room);
      const a = nodes.find((n) => n.id === c.from), b = nodes.find((n) => n.id === c.to);
      return html`<h2>${a?.name} → ${b?.name}</h2><div class="kind">${t("insp.cable")}</div>
        ${this._field(t("insp.carries"), c.signals.map((s) => t(`legend.${s}`)).join(" + "))}
        ${this._field(t("insp.input_on", { device: b?.name }), c.input || t("insp.no_input"))}
        ${c.input ? this._field(t("insp.chosen_by"), t(`mode.${c.mode}`)) : nothing}`;
    }
    const n = this._tiles(room).find((x) => x.id === sel.id);
    if (!n) return nothing;
    if (n.source) {
      const s = n.source;
      return html`<h2>${n.name}</h2><div class="kind">${t("kind.player")} · ${t(`source.${s.id}`)}</div>
        ${this._entities(t("insp.entity"), [s.player])}
        ${s.host ? this._entities(t("insp.host"), [s.host]) : nothing}
        ${s.on_action ? this._entities(t("insp.actions"), [s.on_action, s.off_action].filter(Boolean)) : nothing}
        ${s.settings ? html`<div class="field"><span class="lbl">${t("insp.host_settings")}</span>${this._settingsList(s.settings)}</div>` : nothing}`;
    }
    const d = n.device;
    const p = d.power || {};
    const routes = room.sources.filter((s) => room.routes[s.id]?.devices.includes(d.id));
    const none = !p.plugs?.length && !p.devices?.length && !p.actions;
    return html`<h2>${n.name}</h2><div class="kind">${t(`kind.${d.kind}`)}</div>
      ${p.plugs?.length ? this._entities(t("insp.plugs"), p.plugs) : nothing}
      ${p.devices?.length ? this._entities(t("insp.devices"), p.devices) : nothing}
      ${p.actions ? this._entities(t("insp.actions"), p.actions) : nothing}
      ${p.sensors?.length ? this._entities(t("insp.sensors"), p.sensors) : nothing}
      ${none ? this._field(t("insp.power"), t("insp.no_power")) : nothing}
      ${d.control && p.devices?.length
        ? html`${this._field(t("insp.on_by"), t(`mode.${d.control.on}`))}${this._field(t("insp.off_by"), t(`mode.${d.control.off}`))}
          ${(d.control.on === "cec" || d.control.off === "cec") && d.control.fallback ? html`<p class="muted">${t("insp.fallback", { timeout: d.control.timeout })}</p>` : nothing}`
        : nothing}
      ${d.control
        ? d.inputs
          ? html`<div class="field"><span class="lbl">${t("insp.inputs")} ${d.inputs.entity ? t("insp.inputs_via", { entity: d.inputs.entity }) : t("insp.inputs_actions")}</span>
              <div class="inputs">${d.inputs.list.map((i) => html`<span>${i}</span>`)}</div></div>`
          : this._field(t("insp.inputs"), t("insp.no_inputs"))
        : nothing}
      ${d.volume
        ? html`<div class="field"><span class="lbl">${t("insp.volume")}${room.volume.where === "device" && room.volume.device === d.id ? html` <span class="tag room">${t("insp.room_volume")}</span>` : nothing}</span>
            <button class="ent" @click=${() => this._moreInfo(d.volume)}>${d.volume}</button>
            ${d.volume_migrated ? html`<p class="muted">${t("insp.volume_migrated", { entity: d.volume, device: n.name })}</p>` : nothing}</div>`
        : nothing}
      ${this._field(t("insp.on_routes"), routes.map((s) => this._sourceLabel(s)).join(", ") || "–")}
      ${d.settings && !none
        ? html`<div class="field"><span class="lbl">${t("insp.settings")}</span>${this._settingsList(
            Object.fromEntries(Object.entries(d.settings).filter(([k]) => p.sensors?.length || !STANDBY.includes(k)))
          )}</div>`
        : nothing}`;
  }

  _field(label, value) {
    return html`<div class="field"><span class="lbl">${label}</span><div>${value}</div></div>`;
  }

  _entities(label, ids) {
    return html`<div class="field"><span class="lbl">${label}</span>${ids.map(
      (id) => html`<button class="ent" @click=${() => this._moreInfo(id)}>${id}</button>`
    )}</div>`;
  }

  // settings by key: names and values (changed with the action set_setting)
  _settingsList(settings) {
    return html`<div class="settings">${Object.entries(settings).map(
      ([k, v]) => html`<div class="row" title=${k}><span>${this._t(`setting.${k}`)}</span><b>${this._value(k, v)}</b></div>`
    )}</div>`;
  }

  _value(key, v) {
    const t = this._t;
    if (v === null || v === undefined) return key === "night_run_down" ? t("value.as_by_day") : "–";
    if (typeof v === "boolean") return t(v ? "value.on" : "value.off");
    if (typeof v === "string") return t(`choice.${v}`);
    const num = this.hass?.locale ? Number(v).toLocaleString(this.hass.locale.language) : String(v);
    return UNITS[key] ? `${num} ${UNITS[key]}` : num;
  }

  _renderSettings(room) {
    const t = this._t;
    const name = (id) => this.hass.states[id]?.attributes.friendly_name || id;
    return html`
      <div class="card"><h3>${t("settings.room")}</h3>
        ${this._field(t("settings.area"), room.area || "–")}
        ${this._field(t("settings.volume"), this._roomVolume(room))}
        <div class="field"><span class="lbl">${t("settings.presence")}</span>
          ${room.occupancy.length ? room.occupancy.map((id) => html`<button class="ent" @click=${() => this._moreInfo(id)}>${name(id)}</button>`) : html`<div>${t("settings.none")}</div>`}
        </div>
      </div>
      <div class="card"><h3>${t("settings.behaviour")}</h3>
        ${room.settings ? html`<p class="muted">${t("settings.behaviour_hint")} ${t("settings.change_hint")}</p>${this._settingsList(room.settings)}` : html`<p class="muted">${t("settings.not_loaded")}</p>`}
      </div>`;
  }

  _roomVolume(room) {
    const t = this._t;
    if (room.volume.where !== "device") return t("settings.volume_players");
    const d = room.devices.find((x) => x.id === room.volume.device);
    if (!d?.volume) return "–";
    return html`${t("settings.volume_device", { device: d.name || t(`kind.${d.kind}`), entity: d.volume })}
      ${d.volume_migrated ? html`<p class="muted">${t("insp.volume_migrated", { entity: d.volume, device: d.name || t(`kind.${d.kind}`) })}</p>` : nothing}`;
  }

  _renderCentral() {
    const t = this._t;
    const c = this._config.central;
    if (!c) return html`<div class="card"><p class="muted">${t("central.missing")}</p></div>`;
    const follow = c.follow_player;
    return html`
      <div class="card"><h3>${t("central.follow")}</h3>
        <div class="field"><span class="lbl">${t("central.follow_player")}</span>
          ${follow ? html`<button class="ent" @click=${() => this._moreInfo(follow)}>${this.hass.states[follow]?.attributes.friendly_name || follow}</button>` : html`<div>${t("central.off")}</div>`}
        </div>
      </div>
      <div class="card"><h3>${t("central.settings")}</h3>
        ${c.settings ? html`<p class="muted">${t("settings.change_hint")}</p>${this._settingsList(c.settings)}` : html`<p class="muted">${t("settings.not_loaded")}</p>`}</div>`;
  }

  // ------------------------------------------------------------ interaction

  _select(e, what) {
    e.stopPropagation();
    if (this._justDragged) return;
    this._selected = what;
  }

  _onCanvasClick(e) {
    if (this._justPanned || e.target.id !== "canvas") return;
    this._selected = null;
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
    if (e.button !== 0 || !e.isPrimary || !this._room?.loaded) return;
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
  _onKey(e) {
    if (e.target !== e.currentTarget) return;
    const keys = { "+": () => this._zoomBy(1.2), "=": () => this._zoomBy(1.2), "-": () => this._zoomBy(1 / 1.2), 0: () => this._fit(), 1: () => this._zoomBy(1 / this._view().k) };
    const act = keys[e.key];
    if (act) {
      e.preventDefault();
      act();
    } else if (e.key === "Escape") this._selected = null;
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
    const w = b.width - 64, h = b.height - 44; // clear of the zoom buttons and the hint
    const k = Math.max(b.width < 600 ? 0.5 : 0, Math.min(1.2, w / (box.x1 - box.x0), h / (box.y1 - box.y0)));
    // wider than the screen (narrow, not smaller than readable): from the left, the rest by dragging
    const tx = (box.x1 - box.x0) * k > w ? -box.x0 * k : (w - (box.x1 - box.x0) * k) / 2 - box.x0 * k;
    this._setView(tx, (h - (box.y1 - box.y0) * k) / 2 - box.y0 * k, k);
  }

  updated() {
    if (this._page === "room" && this._tab === "wiring" && this._room && !this._view().fitted && this.renderRoot.querySelector(".canvas-wrap")) {
      this._view().fitted = true;
      requestAnimationFrame(() => this._fit());
    }
  }

  async _saveLayout(room) {
    if (!room) return;
    const layout = Object.fromEntries(this._tiles(room).filter((n) => n.placed).map((n) => [n.id, [n.x, n.y]]));
    room.layout = layout;
    try {
      await this.hass.callWS({ type: "better_media_player/layout/save", entry_id: room.entry_id, layout });
    } catch (err) {
      this._notice = this._t("save_failed", { error: err?.message || String(err) });
    }
  }

  async _rearrange(room) {
    for (const n of this._tiles(room)) n.placed = false;
    room.layout = {};
    this._nodes[room.entry_id] = arrange(nodesOf(room, this._t), room.cables, {});
    this._tick++;
    requestAnimationFrame(() => this._fit());
    try {
      await this.hass.callWS({ type: "better_media_player/layout/save", entry_id: room.entry_id, layout: {} });
    } catch (err) {
      this._notice = this._t("save_failed", { error: err?.message || String(err) });
    }
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
      background: var(--bmp-bg);
      color: var(--bmp-text);
      font-family: var(--ha-font-family-body, Roboto, "Noto Sans", system-ui, sans-serif);
      font-size: 14px;
    }
    * { box-sizing: border-box; }
    button { font: inherit; color: inherit; cursor: pointer; }
    .message { padding: 24px; color: var(--bmp-muted); }
    .app { display: grid; grid-template-rows: var(--header-height, 56px) auto 1fr; height: 100%; }
    .toolbar { display: flex; align-items: center; gap: 8px; padding: 0 16px; background: var(--app-header-background-color, var(--bmp-primary)); color: var(--app-header-text-color, #fff); }
    .toolbar h1 { font-size: 20px; font-weight: 400; margin: 0; }
    .notice { display: flex; align-items: center; gap: 12px; justify-content: space-between; padding: 8px 16px; background: var(--warning-color, #f9a825); color: #000; }
    .zoombar button:disabled { opacity: .4; cursor: default; }
    .viewonly { padding: 8px 16px; background: var(--bmp-card); border-bottom: 1px solid var(--bmp-line); color: var(--bmp-muted); font-size: 13px; }
    .main { display: grid; grid-template-columns: 220px 1fr; min-height: 0; }
    .main.details { grid-template-columns: 220px 1fr 320px; }
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
    .warn { color: var(--warning-color, #f9a825); }
    .stage { display: grid; grid-template-rows: auto auto 1fr auto; min-width: 0; min-height: 0; }
    .stage.settings { grid-template-rows: auto 1fr; }
    .tabs { display: flex; gap: 4px; padding: 0 16px; background: var(--bmp-card); border-bottom: 1px solid var(--bmp-line); }
    .tabs button { border: 0; background: none; padding: 12px 14px 10px; border-bottom: 3px solid transparent; color: var(--bmp-muted); font-weight: 500; }
    .tabs button[aria-selected="true"] { color: var(--bmp-primary); border-bottom-color: var(--bmp-primary); }
    .page { overflow: auto; padding: 20px 16px; display: grid; gap: 16px; align-content: start; align-items: start; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); }
    .card { background: var(--bmp-card); border: 1px solid var(--bmp-line); border-radius: var(--ha-card-border-radius, 12px); padding: 16px 18px; display: grid; gap: 12px; align-content: start; }
    .card h3 { margin: 0; font-size: 16px; font-weight: 500; }
    .settings { display: grid; gap: 2px; }
    .row { display: grid; grid-template-columns: 1fr auto; gap: 12px; align-items: center; padding: 5px 0; border-bottom: 1px solid var(--bmp-line); }
    .row:last-child { border-bottom: 0; }
    .row b { font-weight: 500; font-variant-numeric: tabular-nums; }
    .sources { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 12px 16px; border-bottom: 1px solid var(--bmp-line); background: var(--bmp-card); }
    .lbl { color: var(--bmp-muted); }
    .chip { border: 1px solid var(--bmp-line); background: var(--bmp-chip); border-radius: 16px; padding: 5px 12px; }
    .chip[aria-pressed="true"] { background: var(--bmp-primary); border-color: var(--bmp-primary); color: #fff; }
    .legend { margin-left: auto; display: flex; gap: 14px; color: var(--bmp-muted); font-size: 12px; align-items: center; flex-wrap: wrap; }
    .legend i { display: inline-block; width: 22px; height: 0; border-top: 3px solid; vertical-align: middle; margin-right: 6px; }
    .legend i.picture { border-color: var(--bmp-picture); }
    .legend i.sound { border-color: var(--bmp-sound); border-top-style: dashed; }
    .legend i.direct { border-color: var(--bmp-muted); border-top-style: dotted; border-top-width: 2px; }
    .canvas-wrap:focus { outline: none; }
    .canvas-wrap:focus-visible:not([data-pointer]) { outline: 2px solid var(--bmp-primary); outline-offset: -2px; }
    .canvas-wrap { position: relative; overflow: hidden; min-height: 0; background-image: radial-gradient(var(--bmp-line) 1px, transparent 1px); background-size: 20px 20px; }
    svg#canvas { display: block; width: 100%; height: 100%; user-select: none; -webkit-user-select: none; touch-action: none; cursor: grab; }
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
    .node.dim { opacity: .3; }
    .node.passive .box { stroke-dasharray: 5 4; }
    .node .name { fill: var(--bmp-text); font-size: 14px; font-weight: 500; }
    .node .sub { fill: var(--bmp-muted); font-size: 11.5px; font-family: var(--code-font-family, ui-monospace, monospace); }
    .node .icon { fill: none; stroke: var(--bmp-muted); stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; }
    .badge rect { fill: var(--bmp-cec); }
    .badge text { fill: #fff; font-size: 10.5px; font-weight: 500; }
    .port { fill: var(--bmp-card); stroke: var(--bmp-muted); stroke-width: 1.5; }
    .cable { fill: none; stroke-width: 3; }
    .cable.picture { stroke: var(--bmp-picture); }
    .cable.sound { stroke: var(--bmp-sound); stroke-dasharray: 7 5; }
    .cable.sel { stroke-width: 5; }
    .cable.dim { opacity: .15; }
    .cable.direct { stroke: var(--bmp-muted); stroke-width: 1.5; stroke-dasharray: 2 5; opacity: .7; }
    .cable-hit { fill: none; stroke: transparent; stroke-width: 14; cursor: pointer; }
    .clabel { cursor: pointer; }
    .clabel rect { fill: var(--bmp-card); stroke: var(--bmp-line); }
    .clabel text { font-size: 11.5px; font-family: var(--code-font-family, ui-monospace, monospace); fill: var(--bmp-text); }
    .clabel.cec rect { stroke: var(--bmp-cec); }
    .clabel.dim { opacity: .2; }
    .inspector h2 { font-size: 18px; font-weight: 500; margin: 0 0 2px; }
    .kind { color: var(--bmp-muted); margin-bottom: 16px; }
    .field { display: grid; gap: 6px; margin-bottom: 14px; }
    .field > .lbl { font-size: 12px; }
    .ent { display: block; text-align: left; font-family: var(--code-font-family, ui-monospace, monospace); font-size: 12.5px; background: var(--bmp-chip); border: 0; border-radius: 6px; padding: 6px 8px; overflow-wrap: anywhere; }
    .ent:hover { outline: 1px solid var(--bmp-primary); }
    .inputs { display: flex; flex-wrap: wrap; gap: 6px; }
    .inputs span { font-family: var(--code-font-family, ui-monospace, monospace); font-size: 12px; background: var(--bmp-chip); border-radius: 6px; padding: 3px 7px; }
    .muted { color: var(--bmp-muted); }
    .note { margin: 0 0 12px; padding: 10px 12px; border-radius: 8px; background: var(--bmp-chip); font-size: 13px; }
    @media (max-width: 1100px) { .main { grid-template-columns: 180px 1fr; } .main.details { grid-template-columns: 180px 1fr 280px; } }
    @media (max-width: 820px) {
      .main { display: block; overflow: auto; }
      aside { display: flex; gap: 8px; align-items: center; overflow-x: auto; padding: 8px 16px; border-right: 0; border-bottom: 1px solid var(--bmp-line); }
      .side-title { display: none; }
      .nav { flex: 0 0 auto; width: auto; flex-direction: row; padding: 6px 14px; border: 1px solid var(--bmp-line); border-radius: 16px; background: var(--bmp-chip); white-space: nowrap; }
      .nav small { display: none; }
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
