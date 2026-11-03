// The details on the right: a player, a device or a cable of the room's draft, editable.

import { html, nothing } from "./vendor/lit.js";
import { DOMAINS, PASSIVE, SOURCE_KINDS, idProblem, insertInto, removeDevice, removeSource, renameId, setHost, setKind, setPassive, setVolume, sourceIdProblem } from "./editor.js";
import { checkField, entitiesField, entityField, segField, settingRow, textField } from "./forms.js";

const STANDBY = ["standby", "standby_w", "standby_max"]; // only with power sensors

// the inputs a device offers: its media player's source list, then its input actions
export function inputsOf(hass, dev) {
  const i = dev.inputs;
  if (!i) return [];
  const listed = i.entity ? hass.states[i.entity]?.attributes.source_list || [] : [];
  return [...listed, ...Object.keys(i.actions || {}).filter((k) => !listed.includes(k))];
}

export function renderInspector(p, d) {
  const sel = p._selected;
  if (sel.type === "cable") return cable(p, d, sel.index);
  const src = d.sources.find((s) => s.id === sel.id);
  if (src) return source(p, d, src);
  const dev = d.devices.find((x) => x.id === sel.id);
  return dev ? device(p, d, dev) : nothing;
}

// nothing picked: the room at a glance; without cables, how it works now and taking that over
export function renderOverview(p, d) {
  const t = p._t;
  const direct = !d.cables.length && d.devices.length && d.sources.some((s) => d.routes?.[s.id]?.direct?.length);
  const moved = d.devices.find((x) => x.volume_migrated);
  return html`
    ${direct ? html`<div class="takeover">
      <b>${t("over.title")}</b>
      <p>${t("over.text")}</p>
      <p>${t("over.passive")}</p>
      ${moved ? html`<p>${t("over.volume", { entity: moved.volume, device: moved.name || t(`kind.${moved.kind}`) })}</p>` : nothing}
      <button class="btn primary small" @click=${() => p._takeOver(d)}>${t("over.button")}</button>
      <small class="muted">${t("over.after")}</small>
    </div>` : nothing}
    <h2>${d.name}</h2>
    <div class="kind">${t("nav.room_sub", p._counts(d))}</div>
    <p class="muted">${t("insp.click")}</p>
    <p class="muted">${t("over.cable")}</p>
    <p class="muted">${t("over.move")}</p>
    <button class="btn small" @click=${() => { p._dialog = { type: "add" }; }}>+ ${t("add.button")}</button>`;
}

function problemsOf(p, d, id) {
  const own = (d.problems || []).filter((x) => x.device === id || x.source === id);
  return own.length ? html`<div class="problems">${own.map((x) => html`<div class=${x.error ? "err" : ""}>${p._problemText(x)}</div>`)}</div>` : nothing;
}

// ------------------------------------------------------------------ a player

function source(p, d, s) {
  const t = p._t;
  const ch = (fn) => p._change(d, fn);
  const offers = s.offers_onoff;
  const how = s.onoff || "auto";
  const autoText = offers === false
    ? (s.on_action || s.off_action ? t("onoff.auto_actions") : t("onoff.auto_nothing"))
    : t("onoff.auto_player");
  const hint = { auto: autoText, player: offers === false ? t("onoff.player_cannot") : t("onoff.player"), actions: t("onoff.actions"), none: t("onoff.none") }[how];
  const actions = how === "actions" || (how === "auto" && offers === false);
  const isNew = d.newIds.includes(s.id);
  const problem = sourceIdProblem(d, s);
  const system = systemOf(p, s);
  return html`<h2>${p._sourceName(s)}</h2><div class="kind">${t("kind.player")} · ${t(`source.${s.kind}`)}</div>
    ${problemsOf(p, d, s.id)}
    ${entityField(p, { label: t("insp.entity"), value: s.player, domains: DOMAINS.player, onChange: (v) => v && ch(() => { s.player = v; s.name = p.hass.states[v]?.attributes.friendly_name || null; s.offers_onoff = p._offersOnoff(v); }) })}
    ${isNew
      ? textField(p, { id: "f-sid", label: t("insp.id"), value: s.id, mono: true, hint: problem ? t(`problem.${problem}`, { device: s.id }) : t("insp.source_id_new", { id: s.id }),
          onChange: (v) => ch(() => { const old = s.id; if (!renameId(d, s, v.trim())) p._notice = t("insp.id_taken", { id: v.trim() }); else if (p._selected?.id === old) p._selected = { type: "node", id: s.id }; }) })
      : html`<div class="field"><span class="lbl">${t("insp.id")}</span><code class="ent">${s.id}</code><small class="muted">${t("insp.source_id_fixed")}</small></div>`}
    ${SOURCE_KINDS.includes(s.id)
      ? html`<div class="field"><span class="lbl">${t("insp.needs")}</span><div>${t(`insp.needs_${s.kind}`)}</div><small class="muted">${t("insp.needs_fixed", { id: s.id })}</small></div>`
      : html`${segField(t("insp.needs"), s.kind, SOURCE_KINDS.map((k) => [k, t(`insp.needs_${k}`)]), (v) => ch(() => setKind(d, s, v)))}
        <p class="muted hint">${t(`insp.needs_hint_${s.kind}`)}</p>`}
    ${s.player ? html`<div class="field"><span class="lbl">${t("insp.system")}</span><div>${t(`system.${system}`)}</div><small class="muted">${t(`system.${system}_hint`)}</small></div>` : nothing}
    ${segField(t("onoff.label"), how, [["auto", t("onoff.opt_auto")], ["player", t("onoff.opt_player")], ["actions", t("onoff.opt_actions")], ["none", t("onoff.opt_none")]], (v) => ch(() => { s.onoff = v; }))}
    <p class="muted hint">${hint}</p>
    ${actions ? html`
      ${entityField(p, { label: t("insp.on_action"), value: s.on_action, domains: DOMAINS.action, onChange: (v) => ch(() => { s.on_action = v; }) })}
      ${entityField(p, { label: t("insp.off_action"), value: s.off_action, domains: DOMAINS.action, onChange: (v) => ch(() => { s.off_action = v; }) })}` : nothing}
    ${entityField(p, { label: t("insp.host"), value: s.host, domains: DOMAINS.plug, hint: t("insp.host_hint"), onChange: (v) => ch(() => setHost(d, s, v, p._config.defaults)) })}
    ${s.host && s.settings ? html`<div class="settings">
        ${["off_wait", "on_wait"].map((k) => settingRow(p, k, s.settings[k], (v) => ch(() => { s.settings[k] = v; }), { units: p._units }))}</div>` : nothing}
    <hr><button class="btn danger" @click=${() => ch(() => { removeSource(d, s.id); p._selected = null; })}>${t("insp.remove_player")}</button>`;
}

// what the player's system brings (as the room uses it): Kodi for video, Music Assistant for music
function systemOf(p, s) {
  const platform = p.hass.entities?.[s.player]?.platform;
  if (s.kind === "video" && (platform === "kodi" || platform === "better_kodi")) return platform;
  if (s.kind === "audio" && platform === "music_assistant") return platform;
  return "other";
}

// ------------------------------------------------------------------ a device

function device(p, d, dev) {
  const t = p._t;
  const ch = (fn) => p._change(d, fn);
  const isNew = d.newIds.includes(dev.id);
  const passiveKind = PASSIVE.includes(dev.kind);
  const pw = dev.power;
  const routes = d.sources.filter((s) => d.routes?.[s.id]?.devices.includes(dev.id));
  const roomVolume = d.volume.where === "device" && d.volume.device === dev.id;
  const name = dev.name || t(`kind.${dev.kind}`);
  const inputs = inputsOf(p.hass, dev);
  return html`<h2>${name}</h2><div class="kind">${t(`kind.${dev.kind}`)}</div>
    ${problemsOf(p, d, dev.id)}
    ${textField(p, { id: "f-name", label: t("insp.name"), value: dev.name || "", onChange: (v) => ch(() => { dev.name = v.trim() || null; }) })}
    ${isNew
      ? textField(p, { id: "f-id", label: t("insp.id"), value: dev.id, mono: true, hint: idProblem(d, dev) ? t(`problem.${idProblem(d, dev)}`, { device: dev.id }) : t("insp.id_new", { id: dev.id }),
          onChange: (v) => ch(() => { if (!renameId(d, dev, v.trim())) p._notice = t("insp.id_taken", { id: v.trim() }); }) })
      : html`<div class="field"><span class="lbl">${t("insp.id")}</span><code class="ent">${dev.id}</code><small class="muted">${t("insp.id_fixed")}</small></div>`}
    ${passiveKind ? html`<p class="muted hint">${t(`insp.passive_${dev.kind}`)}</p>` : html`
      ${segField(t("insp.power"), pw ? "switched" : "passive", [["switched", t("insp.switched")], ["passive", t("insp.passive")]], (v) => ch(() => setPassive(d, dev, v === "passive", p._config.defaults)))}
      ${pw ? html`
        ${entitiesField(p, { label: t("insp.plugs"), value: pw.plugs, domains: DOMAINS.plug, onChange: (v) => ch(() => { pw.plugs = v; }) })}
        ${entitiesField(p, { label: t("insp.devices"), value: pw.devices, domains: DOMAINS.device, hint: t("insp.devices_hint"), onChange: (v) => ch(() => { pw.devices = v; }) })}
        ${checkField(t("insp.own_actions"), !!pw.actions, (on) => ch(() => { pw.actions = on ? [null, null] : null; }))}
        ${pw.actions ? html`
          ${entityField(p, { label: t("insp.on_action"), value: pw.actions[0], domains: DOMAINS.action, onChange: (v) => ch(() => { pw.actions[0] = v; }) })}
          ${entityField(p, { label: t("insp.off_action"), value: pw.actions[1], domains: DOMAINS.action, onChange: (v) => ch(() => { pw.actions[1] = v; }) })}` : nothing}
        ${entitiesField(p, { label: t("insp.sensors"), value: pw.sensors, domains: DOMAINS.sensor, hint: t("insp.sensors_hint"), onChange: (v) => ch(() => { pw.sensors = v; }) })}
        ${pw.devices.length && dev.control ? html`
          ${segField(t("insp.on_by"), dev.control.on, [["bmp", t("mode.bmp")], ["cec", t("mode.cec")], ["never", t("mode.never")]], (v) => ch(() => { dev.control.on = v; }))}
          ${segField(t("insp.off_by"), dev.control.off, [["bmp", t("mode.bmp")], ["cec", t("mode.cec")], ["never", t("mode.never")]], (v) => ch(() => { dev.control.off = v; }))}
          ${dev.control.on === "cec" || dev.control.off === "cec" ? html`
            ${checkField(t("insp.fallback", { timeout: dev.control.timeout }), dev.control.fallback, (v) => ch(() => { dev.control.fallback = v; }))}
            ${numberRow(t("insp.cec_timeout"), dev.control.timeout, 1, 120, "s", (v) => ch(() => { dev.control.timeout = v; }))}` : nothing}` : nothing}
      ` : html`<p class="muted hint">${t("insp.passive_hint")}</p>`}
      <hr>
      ${entityField(p, { label: t("insp.inputs_entity"), value: dev.inputs?.entity, domains: DOMAINS.inputs, hint: t("insp.inputs_hint"),
          onChange: (v) => ch(() => { dev.inputs = v || Object.keys(dev.inputs?.actions || {}).length ? { entity: v, actions: dev.inputs?.actions || {}, pause: dev.inputs?.pause || 0 } : null; }) })}
      ${inputActions(p, d, dev)}
      ${dev.inputs ? numberRow(t("insp.input_pause"), dev.inputs.pause || 0, 0, 30, "s", (v) => ch(() => { dev.inputs.pause = v; })) : nothing}
      ${inputs.length ? html`<div class="field"><span class="lbl">${t("insp.inputs")}</span><div class="inputs">${inputs.map((i) => html`<span>${i}</span>`)}</div></div>` : nothing}
      <hr>
      ${entityField(p, { label: t("insp.volume"), value: dev.volume, domains: DOMAINS.volume, hint: t("insp.volume_hint"), onChange: (v) => ch(() => setVolume(d, dev, v)) })}
      ${dev.volume ? (roomVolume
        ? html`<p class="hint"><span class="tag room">${t("insp.room_volume")}</span> ${t("insp.is_room_volume")}</p>`
        : html`<button class="btn small" @click=${() => ch(() => { d.volume = { where: "device", device: dev.id }; })}>${t("insp.use_as_room_volume")}</button>`) : nothing}
      ${dev.volume_migrated ? html`<p class="muted hint">${t("insp.volume_migrated", { entity: dev.volume, device: name })}</p>` : nothing}
    `}
    <div class="field"><span class="lbl">${t("insp.on_routes")}</span><div>${routes.map((s) => p._sourceLabel(s)).join(", ") || "–"}</div></div>
    ${dev.settings ? html`<div class="field"><span class="lbl">${t("insp.settings")}</span><div class="settings">
      ${Object.keys(dev.settings).filter((k) => pw?.sensors?.length || !STANDBY.includes(k)).filter((k) => dev.settings.standby || !["standby_w", "standby_max"].includes(k))
        .map((k) => settingRow(p, k, dev.settings[k], (v) => ch(() => { dev.settings[k] = v; }), { units: p._units }))}
    </div></div>` : nothing}
    <hr><button class="btn danger" @click=${() => ch(() => { removeDevice(d, dev.id); p._selected = null; })}>${t("insp.remove_device")}</button>`;
}

function numberRow(label, value, min, max, unit, onChange) {
  return html`<div class="row"><span>${label}</span><span class="num"><input type="number" min=${min} max=${max} step="any" .value=${value}
    aria-label=${label} @change=${(e) => { const v = Number(e.target.value); if (e.target.value === "" || Number.isNaN(v)) { e.target.value = value; return; } onChange(Math.min(max, Math.max(min, v))); }}>
    <span class="unit">${unit}</span></span></div>`;
}

// input name -> action (a script per input, e.g. for an IR remote)
function inputActions(p, d, dev) {
  const t = p._t;
  const ch = (fn) => p._change(d, fn);
  const actions = dev.inputs?.actions || {};
  const set = (next) => ch(() => {
    const has = dev.inputs?.entity || Object.keys(next).length;
    dev.inputs = has ? { entity: dev.inputs?.entity || null, actions: next, pause: dev.inputs?.pause || 0 } : null;
  });
  return html`<div class="field"><span class="lbl">${t("insp.input_actions")}</span>
    ${Object.entries(actions).map(([name, action]) => html`<div class="pair"><code>${name}</code><span>${action}</span>
      <button class="x" aria-label=${t("form.remove")} @click=${() => { const n = { ...actions }; delete n[name]; set(n); }}>×</button></div>`)}
    <form class="pair add" @submit=${(e) => {
      e.preventDefault();
      const f = e.target, name = f.elements.name.value.trim(), action = f.elements.action.value.trim();
      if (name && action) { set({ ...actions, [name]: action }); f.reset(); }
    }}>
      <input name="name" placeholder=${t("insp.input_name")} aria-label=${t("insp.input_name")}>
      <input name="action" list="bmp-actions" placeholder=${t("insp.input_action")} aria-label=${t("insp.input_action")} spellcheck="false">
      <datalist id="bmp-actions">${Object.keys(p.hass.states).filter((id) => DOMAINS.action.includes(id.split(".")[0])).sort().map((id) => html`<option value=${id}></option>`)}</datalist>
      <button class="btn small">${t("form.add")}</button>
    </form>
    <small class="muted">${t("insp.input_actions_hint")}</small>
  </div>`;
}

// ------------------------------------------------------------------ a cable

function cable(p, d, index) {
  const t = p._t;
  const c = d.cables[index];
  if (!c) return nothing;
  const ch = (fn) => p._change(d, fn);
  const end = (id) => d.sources.find((s) => s.id === id) ? p._sourceName(d.sources.find((s) => s.id === id)) : (d.devices.find((x) => x.id === id)?.name || t(`kind.${d.devices.find((x) => x.id === id)?.kind}`));
  const target = d.devices.find((x) => x.id === c.to);
  const offered = target ? inputsOf(p.hass, target) : [];
  const toggle = (sig) => ch(() => {
    const has = c.signals.includes(sig);
    if (has && c.signals.length === 1) return;
    c.signals = has ? c.signals.filter((x) => x !== sig) : [...c.signals, sig].sort();
  });
  return html`<h2>${end(c.from)} → ${end(c.to)}</h2><div class="kind">${t("insp.cable")}</div>
    ${problemsOf(p, d, c.to)}
    <div class="field"><span class="lbl">${t("insp.carries")}</span><div class="seg">
      ${["picture", "sound"].map((sig) => html`<button aria-pressed=${String(c.signals.includes(sig))} @click=${() => toggle(sig)}>${t(`legend.${sig}`)}</button>`)}</div></div>
    ${target && !PASSIVE.includes(target.kind) ? html`
      <div class="field"><label class="lbl" for="f-input">${t("insp.input_on", { device: end(c.to) })}</label>
        <input id="f-input" list="bmp-inputs" .value=${c.input || ""} placeholder=${t("insp.no_input")} @change=${(e) => ch(() => { c.input = e.target.value.trim() || null; })}>
        <datalist id="bmp-inputs">${offered.map((i) => html`<option value=${i}></option>`)}</datalist>
        <small class="muted">${target.inputs ? t("insp.input_hint") : t("insp.input_none_hint")}</small></div>
      ${c.input ? segField(t("insp.chosen_by"), c.mode, [["bmp", t("mode.bmp")], ["cec", t("mode.cec")], ["never", t("mode.never")]], (v) => ch(() => { c.mode = v; })) : nothing}
      ${c.input ? html`<p class="muted hint">${t(`insp.mode_${c.mode}`)}</p>` : nothing}` : nothing}
    <div class="field"><span class="lbl">${t("insp.insert")}</span><div class="btnrow">
      ${PASSIVE.map((k) => html`<button class="btn small" @click=${() => ch(() => { const dev = insertInto(d, index, k, p._config.defaults); p._selected = { type: "node", id: dev.id }; })}>${t(`kind.${k}`)}</button>`)}
    </div><small class="muted">${t("insp.insert_hint")}</small></div>
    <hr><button class="btn danger" @click=${() => ch(() => { d.cables.splice(index, 1); p._selected = null; })}>${t("insp.remove_cable")}</button>`;
}

