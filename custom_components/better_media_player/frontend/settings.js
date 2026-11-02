// "Room & behaviour" of a room and the "General" page (central entry), editable.

import { html, nothing } from "./vendor/lit.js";
import { DOMAINS } from "./editor.js";
import { areaField, entitiesField, entityField, segField, settingRow, textField } from "./forms.js";

// the room's settings in groups; follow me's only in a room with presence sensors
const GROUPS = [
  ["night", ["night_max_volume", "night_video_idle_timeout", "night_fade_in"]],
  ["presets", ["preset_quiet", "preset_normal", "preset_loud", "preset_party", "preset_fade", "power_on_preset"]],
  ["times", ["video_idle_timeout", "start_timeout"]],
  ["announce", ["announce_video", "announce_video_lower"]],
  ["sleep", ["sleep_fade", "snooze_minutes"]],
  ["follow", ["presence_run_down", "volume_offset", "prewarm", "wake_detect_left"]],
];

export function renderSettings(p, d) {
  const t = p._t;
  const ch = (fn) => p._change(d, fn);
  const withVolume = d.devices.filter((x) => x.volume);
  return html`
    <div class="card"><h3>${t("settings.room")}</h3>
      ${textField(p, { id: "r-name", label: t("settings.name"), value: d.name, onChange: (v) => ch(() => { d.name = v.trim() || d.name; }) })}
      ${areaField(p, { label: t("settings.area"), value: d.area_id, onChange: (v) => ch(() => { d.area_id = v; }) })}
      <hr><button class="btn danger" @click=${() => p._askDelete(d)}>${t("settings.delete")}</button>
    </div>
    <div class="card"><h3>${t("settings.volume")}</h3>
      <p class="muted">${t("settings.volume_hint")}</p>
      ${segField(t("settings.volume_where"), d.volume.where, [["players", t("settings.volume_players")], ["device", t("settings.volume_on_device")]], (v) => ch(() => {
        d.volume = v === "device" && withVolume.length ? { where: "device", device: withVolume[0].id } : { where: "players" };
      }))}
      ${d.volume.where === "device" ? html`<div class="field"><label class="lbl" for="r-voldev">${t("settings.volume_device_label")}</label>
        <select id="r-voldev" @change=${(e) => ch(() => { d.volume = { where: "device", device: e.target.value }; })}>
          ${withVolume.map((x) => html`<option value=${x.id} ?selected=${x.id === d.volume.device}>${x.name || t(`kind.${x.kind}`)} · ${x.volume}</option>`)}
        </select></div>` : nothing}
      ${!withVolume.length ? html`<p class="muted hint">${t("settings.volume_none")}</p>` : nothing}
    </div>
    <div class="card"><h3>${t("settings.follow")}</h3>
      <p class="muted">${t("settings.follow_hint")}</p>
      ${entitiesField(p, { label: t("settings.presence"), value: d.occupancy, domains: DOMAINS.presence, onChange: (v) => ch(() => { d.occupancy = v; }) })}
    </div>
    <div class="card wide"><h3>${t("settings.behaviour")}</h3>
      ${d.settings ? html`<p class="muted">${t("settings.behaviour_hint")}</p>
        ${GROUPS.filter(([g]) => g !== "follow" || d.occupancy.length).map(([g, keys]) => html`<h4>${t(`settings.group_${g}`)}</h4><div class="settings">
          ${keys.map((k) => settingRow(p, k, d.settings[k], (v) => ch(() => { d.settings[k] = v; }), { units: p._units }))}</div>`)}`
        : html`<p class="muted">${t("settings.not_loaded")}</p>`}
    </div>`;
}

export function renderCentral(p) {
  const t = p._t;
  const c = p._central;
  if (!c) return html`<div class="card"><p class="muted">${t("central.missing")}</p></div>`;
  const ch = (fn) => { fn(); p._centralChanged(); };
  return html`
    <div class="card"><h3>${t("central.follow")}</h3>
      ${entityField(p, { label: t("central.follow_player"), value: c.follow_player, domains: DOMAINS.player, hint: t("central.follow_hint"),
          onChange: (v) => ch(() => { c.follow_player = v; }) })}
      ${(c.problems || []).map((x) => html`<div class="problems"><div class="err">${p._problemText(x)}</div></div>`)}
    </div>
    <div class="card"><h3>${t("central.settings")}</h3>
      ${c.settings ? html`<div class="settings">${Object.keys(c.settings).map((k) => settingRow(p, k, c.settings[k], (v) => ch(() => { c.settings[k] = v; }), { units: p._units }))}</div>`
        : html`<p class="muted">${t("settings.not_loaded")}</p>`}
    </div>`;
}
