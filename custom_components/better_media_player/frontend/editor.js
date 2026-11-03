// A room's draft in the panel: what can be changed, how it is changed, and what
// is sent to save it (better_media_player/room/save, room/create). Plain data, no UI.

export const SOURCE_KINDS = ["video", "audio"]; // what a player is for (its needs); any number of each
export const DEVICE_KINDS = ["display", "receiver", "amp", "speakers", "subwoofer", "switch", "mixer", "splitter"];
export const PASSIVE = ["mixer", "splitter"];
export const SOUND_KINDS = ["receiver", "amp", "speakers", "subwoofer"];
export const ID_PATTERN = /^[a-z][a-z0-9_]{0,31}$/;
const RESERVED = ["video", "audio", "video_host", "audio_host"];
export const needsOf = (kind) => (kind === "video" ? ["picture", "sound"] : ["sound"]);
// ids a device cannot have: the reserved ones, the players' and their plugs'
const taken = (d) => [...RESERVED, ...d.sources.flatMap((s) => [s.id, `${s.id}_host`])];

// the domains each field takes (as the integration checks them)
export const DOMAINS = {
  player: ["media_player"],
  plug: ["switch", "light", "input_boolean"],
  device: ["media_player", "remote"],
  action: ["script", "button", "input_button", "switch", "scene", "automation"],
  sensor: ["sensor"],
  volume: ["media_player", "number", "input_number"],
  inputs: ["media_player", "remote"],
  presence: ["binary_sensor"],
};

const clone = (x) => JSON.parse(JSON.stringify(x));

// the editable parts of a room as config/get gives it
export function draftOf(room) {
  return {
    entry_id: room.entry_id,
    isNew: !!room.isNew,
    version: room.version,
    name: room.name,
    area_id: room.area_id ?? null,
    loaded: room.loaded,
    sources: clone(room.sources),
    devices: clone(room.devices),
    cables: clone(room.cables),
    volume: clone(room.volume),
    occupancy: clone(room.occupancy),
    start_source: room.start_source ?? null,
    music_source: room.music_source ?? null,
    settings: room.settings ? clone(room.settings) : null,
    layout: clone(room.layout || {}),
    newIds: [...(room.newIds || [])], // devices not saved yet: their id can still change
  };
}

export function newRoom(name, settings) {
  return {
    entry_id: `new-${Date.now()}`,
    isNew: true,
    version: "",
    name,
    area_id: null,
    loaded: true,
    sources: [],
    devices: [],
    cables: [],
    volume: { where: "players" },
    occupancy: [],
    start_source: null,
    music_source: null,
    settings: clone(settings),
    layout: {},
    newIds: [],
    routes: {},
    problems: [{ error: true, code: "no_player" }],
  };
}

// the same as what was loaded (nothing to save)?
export function same(a, b) {
  const pick = (d) => JSON.stringify([d.name, d.area_id ?? null, d.sources, d.devices, d.cables, d.volume, d.occupancy, d.start_source ?? null, d.music_source ?? null, d.settings]);
  return pick(a) === pick(b);
}

// the settings changed against base (what was loaded): only those are saved, so a
// value an automation changed meanwhile is not put back
export function changedSettings(d, base) {
  const diff = (now, before) => Object.fromEntries(Object.entries(now || {}).filter(([k, v]) => !before || JSON.stringify(v) !== JSON.stringify(before[k])));
  const out = { room: diff(d.settings, base?.settings), devices: {}, sources: {} };
  for (const x of d.devices) {
    const was = base?.devices.find((y) => y.id === x.id && !d.newIds.includes(x.id));
    const changed = diff(x.settings, was?.settings);
    if (Object.keys(changed).length) out.devices[x.id] = changed;
  }
  for (const s of d.sources) {
    if (!s.host || !s.settings) continue;
    const was = base?.sources.find((y) => y.id === s.id && y.host);
    const changed = diff(s.settings, was?.settings);
    if (Object.keys(changed).length) out.sources[s.id] = changed;
  }
  return out;
}

// what the integration takes: setup and settings
export function payload(d, base = null) {
  const sources = d.sources.map((s) => ({
    id: s.id, kind: s.kind, player: s.player, host: s.host || null, onoff: s.onoff || "auto",
    on_action: s.on_action || null, off_action: s.off_action || null,
  }));
  const devices = d.devices.map((x) => {
    const out = {
      id: x.id, kind: x.kind, name: x.name || null, power: x.power ? clone(x.power) : null,
      control: x.control ? clone(x.control) : null,
      inputs: x.inputs ? { entity: x.inputs.entity || null, actions: { ...(x.inputs.actions || {}) }, pause: x.inputs.pause || 0 } : null,
      volume: x.volume || null,
    };
    if (x.volume_migrated) out.volume_migrated = true;
    return out;
  });
  const settings = changedSettings(d, base);
  return {
    name: d.name,
    area_id: d.area_id || null,
    setup: {
      sources, devices, cables: clone(d.cables), volume: clone(d.volume), occupancy: [...d.occupancy],
      start_source: d.start_source || null, music_source: d.music_source || null,
    },
    settings: d.loaded ? settings : {},
  };
}

// --------------------------------------------------------------- players

// a new player of a kind: its id is the kind's (video, audio) while free, else kind_2, …
export function addSource(d, kind, player) {
  let id = kind, k = 2;
  const used = (x) => d.sources.some((s) => s.id === x || `${s.id}_host` === x) || d.devices.some((v) => v.id === x || v.id === `${x}_host`);
  while (used(id)) id = `${kind}_${k++}`;
  const src = {
    id, kind, player, host: null, onoff: "auto", on_action: null, off_action: null,
    needs: needsOf(kind), settings: null,
  };
  d.sources.push(src);
  d.sources.sort((a, b) => SOURCE_KINDS.indexOf(a.kind) - SOURCE_KINDS.indexOf(b.kind)); // (stable)
  d.newIds.push(id);
  return src;
}

// what it is for; the players with the ids video and audio keep theirs (automations mean that)
export function setKind(d, src, kind) {
  if (SOURCE_KINDS.includes(src.id)) return;
  src.kind = kind;
  src.needs = needsOf(kind);
  if (kind !== "audio" && d.music_source === src.id) d.music_source = null;
}

export function sourceIdProblem(d, src) {
  if (!ID_PATTERN.test(src.id) || src.id.endsWith("_host")) return "bad_id";
  if (RESERVED.includes(src.id) && src.id !== src.kind) return "reserved_id";
  if (d.sources.filter((x) => x.id === src.id).length > 1 || d.devices.some((x) => x.id === src.id || x.id === `${src.id}_host`)) return "dup_id";
  return null;
}

export function setHost(d, src, plug, defaults) {
  src.host = plug || null;
  src.settings = src.host ? src.settings || { ...defaults.sources } : null;
}

export function removeSource(d, key) {
  d.sources = d.sources.filter((s) => s.id !== key);
  d.cables = d.cables.filter((c) => c.from !== key && c.to !== key);
  if (d.start_source === key) d.start_source = null;
  if (d.music_source === key) d.music_source = null;
  d.newIds = d.newIds.filter((x) => x !== key);
}

// --------------------------------------------------------------- devices

export function proposeId(d, kind) {
  let id = kind, k = 2;
  while (d.devices.some((x) => x.id === id) || taken(d).includes(id)) id = `${kind}_${k++}`;
  return id;
}

export function idProblem(d, dev) {
  if (!ID_PATTERN.test(dev.id)) return "bad_id";
  if (taken(d).includes(dev.id)) return "reserved_id";
  if (d.devices.filter((x) => x.id === dev.id).length > 1) return "dup_id";
  return null;
}

// a new device of a kind; passive speakers: speakers without power of their own
export function addDevice(d, kind, defaults, { passive = false } = {}) {
  const id = proposeId(d, kind);
  const switched = !PASSIVE.includes(kind) && !passive;
  const dev = {
    id, kind, name: null,
    power: switched ? { plugs: [], devices: [], actions: null, sensors: [] } : null,
    control: switched ? { on: "bmp", off: "bmp", fallback: false, timeout: 15 } : null,
    inputs: null, volume: null,
    settings: switched ? { ...defaults.devices[kind] } : null,
  };
  d.devices.push(dev);
  d.newIds.push(id);
  return dev;
}

// a new device's or player's id (fixed once saved)
export function renameId(d, dev, id) {
  const old = dev.id;
  if (id === old) return true;
  const isSource = d.sources.includes(dev);
  const others = [...d.devices, ...d.sources].filter((x) => x !== dev).map((x) => x.id);
  const hosts = d.sources.filter((s) => s !== dev).map((s) => `${s.id}_host`);
  if (others.includes(id) || hosts.includes(id) || (RESERVED.includes(id) && !(isSource && id === dev.kind))) return false;
  if (isSource && d.devices.some((x) => x.id === `${id}_host`)) return false;
  dev.id = id;
  if (isSource) {
    if (d.start_source === old) d.start_source = id;
    if (d.music_source === old) d.music_source = id;
  }
  for (const c of d.cables) {
    if (c.from === old) c.from = id;
    if (c.to === old) c.to = id;
  }
  if (d.volume.device === old) d.volume.device = id;
  d.newIds = d.newIds.map((x) => (x === old ? id : x));
  if (d.layout[old]) {
    d.layout[id] = d.layout[old];
    delete d.layout[old];
  }
  return true;
}

export function removeDevice(d, id) {
  d.devices = d.devices.filter((x) => x.id !== id);
  d.cables = d.cables.filter((c) => c.from !== id && c.to !== id);
  if (d.volume.device === id) d.volume = { where: "players" };
  d.newIds = d.newIds.filter((x) => x !== id);
}

// passive (driven by what feeds it) or switched (on a plug, by itself, by actions)
export function setPassive(d, dev, passive, defaults) {
  if (passive) {
    dev.power = null;
    dev.control = null;
    dev.settings = null;
  } else {
    dev.power = { plugs: [], devices: [], actions: null, sensors: [] };
    dev.control = dev.control || { on: "bmp", off: "bmp", fallback: false, timeout: 15 };
    dev.settings = dev.settings || { ...defaults.devices[dev.kind] };
  }
}

export function setVolume(d, dev, entity) {
  dev.volume = entity || null;
  delete dev.volume_migrated; // chosen now: no longer the migration's guess
  if (!dev.volume && d.volume.device === dev.id) d.volume = { where: "players" };
}

// --------------------------------------------------------------- cables

// what a new cable carries: what the target is for, else what arrives
export function defaultSignals(d, from, to) {
  const target = d.devices.find((x) => x.id === to);
  if (target?.kind === "display") return ["picture"];
  if (target && SOUND_KINDS.includes(target.kind)) return ["sound"];
  const src = d.sources.find((s) => s.id === from);
  if (src) return needsOf(src.kind);
  const arrives = new Set(d.cables.filter((c) => c.to === from).flatMap((c) => c.signals));
  return arrives.size ? [...arrives].sort() : ["sound"];
}

export function canConnect(d, from, to) {
  if (from === to || d.sources.some((s) => s.id === to)) return false;
  return !d.cables.some((c) => c.from === from && c.to === to);
}

export function addCable(d, from, to) {
  d.cables.push({ from, to, signals: defaultSignals(d, from, to), input: null, mode: "bmp" });
  return d.cables.length - 1;
}

// the dotted lines (players feeding devices no cable reaches) as cables, so the room keeps
// working as it does and the cabling can be changed from there; returns how many were added
export function directToCables(d) {
  let added = 0;
  for (const s of d.sources) {
    for (const to of d.routes?.[s.id]?.direct || []) {
      const signals = defaultSignals(d, s.id, to);
      const c = d.cables.find((x) => x.from === s.id && x.to === to);
      if (c) c.signals = [...new Set([...c.signals, ...signals])].sort();
      else { d.cables.push({ from: s.id, to, signals, input: null, mode: "bmp" }); added++; }
    }
  }
  return added;
}

// a passive device put into a cable (the cable is split in two)
export function insertInto(d, index, kind, defaults) {
  const c = d.cables[index];
  const dev = addDevice(d, kind, defaults);
  d.cables.splice(index, 1,
    { from: c.from, to: dev.id, signals: [...c.signals], input: null, mode: "bmp" },
    { from: dev.id, to: c.to, signals: [...c.signals], input: c.input, mode: c.mode });
  return dev;
}
