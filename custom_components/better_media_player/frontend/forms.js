// The panel's fields: entities (picked with Home Assistant's own entity selector), numbers, choices, on/off.

import { html, nothing } from "./vendor/lit.js";

let listId = 0;

// Home Assistant's selector (search by name, icons, states, areas). It is part of the
// frontend; should it not be there (yet), plain inputs stand in until it is.
let waiting = false;
function rich(p) {
  if (customElements.get("ha-selector")) return true;
  if (!waiting) {
    waiting = true;
    customElements.whenDefined("ha-selector").then(() => p.requestUpdate());
  }
  return false;
}

function picked(onChange, multiple) {
  return (e) => {
    e.stopPropagation();
    const v = e.detail.value;
    onChange(multiple ? (v || []).filter(Boolean) : v || null);
  };
}

function selector(p, { label, value, domains, onChange, hint, multiple, exclude }) {
  return html`<div class="field">
    <ha-selector .hass=${p.hass} .label=${label} .helper=${hint} .required=${false}
      .selector=${{ entity: { multiple, filter: { domain: domains }, ...(exclude?.length ? { exclude_entities: exclude } : {}) } }} .value=${value ?? (multiple ? [] : "")}
      @value-changed=${picked(onChange, multiple)}></ha-selector>
    ${unknown(p, multiple ? value || [] : value ? [value] : [])}
  </div>`;
}

// an entity no longer there (removed, renamed): named, so it is not missed
function unknown(p, ids) {
  const gone = ids.filter((e) => !p.hass.states[e]);
  return gone.length ? html`<small class="warn">${p._t("form.unknown_entity", { entities: gone.join(", ") })}</small>` : nothing;
}

// the entities of these domains, for an <input list>
function options(hass, domains, exclude = []) {
  return Object.keys(hass.states)
    .filter((id) => domains.includes(id.split(".")[0]) && !exclude.includes(id))
    .sort()
    .map((id) => html`<option value=${id}>${hass.states[id].attributes.friendly_name || ""}</option>`);
}

export function friendly(hass, id) {
  return hass.states[id]?.attributes.friendly_name || id;
}

// one entity (or none); onChange(value | null)
// one entity; exclude: entities not offered (the chosen one always is)
export function entityField(p, { label, value, domains, onChange, hint, exclude = [] }) {
  exclude = exclude.filter((e) => e !== value);
  if (rich(p)) return selector(p, { label, value, domains, onChange, hint, multiple: false, exclude });
  const lid = `bmp-list-${++listId}`;
  return html`<div class="field">
    <label class="lbl" for=${lid + "-in"}>${label}</label>
    <div class="pick">
      <input id=${lid + "-in"} list=${lid} .value=${value || ""} placeholder=${p._t("form.choose_entity")} spellcheck="false"
        @change=${(e) => onChange(e.target.value.trim() || null)}>
      ${value ? html`<button class="x" title=${p._t("form.clear")} aria-label=${p._t("form.clear")} @click=${() => onChange(null)}>×</button>` : nothing}
      <datalist id=${lid}>${options(p.hass, domains, exclude)}</datalist>
    </div>
    ${value && p.hass.states[value] ? html`<small class="muted">${friendly(p.hass, value)}</small>` : unknown(p, value ? [value] : [])}
    ${hint ? html`<small class="muted">${hint}</small>` : nothing}
  </div>`;
}

// several entities; onChange(list)
export function entitiesField(p, { label, value, domains, onChange, hint }) {
  if (rich(p)) return selector(p, { label, value, domains, onChange, hint, multiple: true });
  const lid = `bmp-list-${++listId}`;
  const list = value || [];
  return html`<div class="field">
    <span class="lbl">${label}</span>
    ${list.map((e) => html`<span class="chipent"><span><b>${friendly(p.hass, e)}</b><small>${e}</small></span>
      <button class="x" title=${p._t("form.remove")} aria-label=${`${p._t("form.remove")} ${e}`} @click=${() => onChange(list.filter((x) => x !== e))}>×</button></span>`)}
    <div class="pick">
      <input list=${lid} placeholder=${p._t("form.add_entity")} spellcheck="false" aria-label=${label}
        @change=${(e) => { const v = e.target.value.trim(); e.target.value = ""; if (v && !list.includes(v)) onChange([...list, v]); }}>
      <datalist id=${lid}>${options(p.hass, domains)}</datalist>
    </div>
    ${hint ? html`<small class="muted">${hint}</small>` : nothing}
  </div>`;
}

// an area (or none); onChange(area_id | null)
export function areaField(p, { label, value, onChange }) {
  if (rich(p)) {
    return html`<div class="field"><ha-selector .hass=${p.hass} .label=${label} .required=${false} .selector=${{ area: {} }}
      .value=${value || ""} @value-changed=${picked(onChange, false)}></ha-selector></div>`;
  }
  const areas = Object.values(p.hass.areas || {}).sort((a, b) => a.name.localeCompare(b.name));
  return html`<div class="field"><label class="lbl" for="r-area">${label}</label>
    <select id="r-area" @change=${(e) => onChange(e.target.value || null)}>
      <option value="" ?selected=${!value}>–</option>
      ${areas.map((a) => html`<option value=${a.area_id} ?selected=${a.area_id === value}>${a.name}</option>`)}
    </select></div>`;
}

export function textField(p, { label, value, onChange, hint, mono = false, id }) {
  return html`<div class="field">
    <label class="lbl" for=${id}>${label}</label>
    <input id=${id} class=${mono ? "mono" : ""} .value=${value ?? ""} @change=${(e) => onChange(e.target.value)}>
    ${hint ? html`<small class="muted">${hint}</small>` : nothing}
  </div>`;
}

// segmented choice: [[value, label], …]
export function segField(label, value, choices, onChange) {
  return html`<div class="field"><span class="lbl">${label}</span>
    <div class="seg" role="group" aria-label=${label}>${choices.map(([v, l]) => html`<button aria-pressed=${String(v === value)} @click=${() => onChange(v)}>${l}</button>`)}</div></div>`;
}

export function checkField(label, value, onChange) {
  return html`<label class="check"><input type="checkbox" .checked=${!!value} @change=${(e) => onChange(e.target.checked)}><span>${label}</span></label>`;
}

// a setting as a row: number (with its unit), choice or on/off, by its spec
export function settingRow(p, key, value, onChange, { nullable = false, units = {} } = {}) {
  const t = p._t;
  const spec = p._config.specs[key] || { type: "number", min: 0, max: 999 };
  const id = `set-${key}-${++listId}`;
  let input;
  if (spec.type === "bool") {
    input = html`<input id=${id} type="checkbox" class="toggle" .checked=${!!value} @change=${(e) => onChange(e.target.checked)}>`;
  } else if (spec.type === "choice") {
    input = html`<select id=${id} @change=${(e) => onChange(e.target.value)}>${spec.choices.map((c) => html`<option value=${c} ?selected=${c === value}>${t(`choice.${c}`)}</option>`)}</select>`;
  } else {
    input = html`<span class="num"><input id=${id} type="number" min=${spec.min} max=${spec.max} step="any" .value=${value ?? ""}
        placeholder=${nullable ? t("value.as_by_day") : ""}
        @change=${(e) => {
          const raw = e.target.value;
          if (raw === "" && nullable) return onChange(null);
          const v = Number(raw);
          if (raw === "" || Number.isNaN(v)) { e.target.value = value ?? ""; return; }
          onChange(Math.min(spec.max, Math.max(spec.min, v)));
        }}>${units[key] ? html`<span class="unit">${units[key]}</span>` : nothing}</span>`;
  }
  return html`<div class="row"><label for=${id}>${t(`setting.${key}`)}</label>${input}</div>`;
}
