// The live state: what the room does right now (from better_media_player/room/live
// and /rooms/live), as a line under each tile, a sentence below the drawing, a
// line per room in the list and a box in the details. Times come from Home
// Assistant (epoch seconds); p._serverNow() is its clock as the browser sees it.

import { html, nothing } from "./vendor/lit.js";

// the time of day in Home Assistant's language and time zone
function clockTime(p, at) {
  const opts = { hour: "2-digit", minute: "2-digit" };
  try {
    return new Date(at * 1000).toLocaleTimeString(p.hass?.locale?.language || p.hass?.language, { ...opts, timeZone: p.hass?.config?.time_zone });
  } catch {
    return new Date(at * 1000).toLocaleTimeString(undefined, opts);
  }
}

// seconds left as "7:42" (coarse: whole minutes); an hour or more: the time of day
function left(p, at, coarse = false) {
  const s = Math.max(0, Math.round(at - p._serverNow()));
  if (s >= 3600) return { at: clockTime(p, at) };
  if (coarse) return { min: Math.max(1, Math.ceil(s / 60)) };
  return { in: `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}` };
}

// "off in 7:42" / "off in 8 min" (coarse) / "off at 23:40"
export function offText(p, at, coarse = false) {
  const l = left(p, at, coarse);
  return l.at ? p._t("live.off_at", { time: l.at }) : l.min ? p._t("live.off_in_min", { n: l.min }) : p._t("live.off_in", { time: l.in });
}

// "in 7:42" / "in 8 min" / "at 23:40" (inside a sentence)
function when(p, at, coarse = false) {
  const l = left(p, at, coarse);
  return l.at ? p._t("live.at", { time: l.at }) : l.min ? p._t("live.in_min", { n: l.min }) : p._t("live.in", { time: l.in });
}

const playerWord = (p, s) => (s.state === "paused" ? p._t("live.paused") : p._t("live.stopped"));

// the source's name ("Video", "Music"); in an English line it reads as a word ("plays video")
const kindOf = (room, key) => room.sources.find((s) => s.id === key)?.kind ?? (key === "video" ? "video" : "audio");
const srcName = (p, room, key) => p._t(`source.${kindOf(room, key)}`);
const srcWord = (p, room, key) => (String(p._lang || "en").startsWith("en") ? srcName(p, room, key).toLowerCase() : srcName(p, room, key));

const devName = (p, room, id) => {
  const d = room.devices.find((x) => x.id === id);
  return d ? d.name || p._t(`kind.${d.kind}`) : id;
};

// a start that failed: "<player> did not start" / "… did not become available" (from the room)
function failure(p, error) {
  const m = /^(\S+) did not (start|become available)$/.exec(error || "");
  if (!m) return error;
  const name = p.hass?.states?.[m[1]]?.attributes?.friendly_name || m[1];
  return p._t(m[2] === "start" ? "live.e_not_started" : "live.e_unavailable", { player: name });
}

// a CEC switch-on still awaited (left > 0) or over (left 0: BMP's fallback follows, or not)
const cecOn = (n) => n.cec.find((c) => c.on);

// the route in use: what plays, holds or starts (its tiles and cables are shown, the rest dimmed)
export function liveRoute(room, live) {
  if (!live?.loaded) return null;
  const list = Object.entries(live.sources);
  const pick = list.find(([, s]) => s.playing) || list.find(([, s]) => s.starting) || list.find(([, s]) => s.in_use || s.going_off);
  if (!pick) return null;
  const [key, s] = pick;
  const r = room.routes?.[key];
  const on = room.sources.find((x) => x.id === key)?.runs_on; // (the player it runs on is part of it)
  return r ? { key, flowing: s.playing, cables: new Set(r.cables), tiles: new Set([key, ...(on ? [on] : []), ...r.devices]), direct: new Set(r.direct) } : null;
}

// the line under a tile: {cls, text} (cls: on, play, wait, down, off, err, manual)
export function tileStatus(p, room, live, id) {
  const t = p._t;
  if (!live?.loaded) return null;
  const src = live.sources[id];
  if (src) {
    const host = live.nodes[`${id}_host`];
    if (live.error && live.error.startsWith(`${src.player} `)) return { cls: "err", text: t("live.not_started") };
    if (src.playing) return { cls: "play", text: t("live.playing") };
    if (src.serving?.length && src.on) {
      const guest = room.sources.find((x) => x.id === src.serving.find((k) => live.sources[k]?.playing) || src.serving[0]);
      return { cls: "on", text: t("live.serving", { player: guest ? p._sourceName(guest) : src.serving[0] }) };
    }
    if (src.starting) return { cls: "wait", text: t("live.starting") };
    if (src.going_off) return { cls: "wait", text: t("live.going_off") };
    if (src.in_use && src.since != null) {
      const word = playerWord(p, src);
      return { cls: "down", text: src.hold_end ? `${word} · ${offText(p, src.hold_end)}` : word };
    }
    // a player on its own hardware: the plug says more than the player
    if (host && !host.on) return { cls: "off", text: t("live.off") };
    if (host && (!src.on || host.off_at)) return { cls: "down", text: host.off_at ? t("live.plug", { text: offText(p, host.off_at) }) : t("live.plug_on") };
    if (!src.available) return { cls: "off", text: t("live.unavailable") };
    if (src.on) return { cls: "on", text: t("live.on") };
    return { cls: "off", text: t("live.off") };
  }
  const n = live.nodes[id];
  if (!n) return null; // passive: nothing switched
  const inp = n.input;
  const cec = cecOn(n);
  if (n.night_off && !n.on) return { cls: "off", text: t("live.night_off") };
  if (n.held.length && !cec) return { cls: "manual", text: t("live.held") };
  if (cec) return cec.left > 0 ? { cls: "wait", text: t("live.cec", { left: Math.ceil(cec.left) }) } : { cls: "wait", text: t("live.switching_on") };
  if (n.starting) return { cls: "wait", text: t("live.switching_on") };
  if (!n.on) return { cls: "off", text: t("live.off") };
  if (n.standby) return { cls: "wait", text: n.standby.watts != null ? t("live.standby_w", { w: Math.round(n.standby.watts) }) : t("live.standby") };
  if (n.waits_player) return { cls: "wait", text: t("live.waits_player") };
  if (n.devices_off) return { cls: "wait", text: t("live.going_off") };
  if (inp?.state === "manual") return { cls: "manual", text: t("live.by_hand", { input: inp.current ?? "?" }) };
  if (inp?.state === "failed") return { cls: "manual", text: t("live.input_failed", { input: inp.wanted }) };
  if (n.off_at) return { cls: "down", text: offText(p, n.off_at) };
  if (live.manual) return { cls: "manual", text: t("live.on_by_hand") };
  if (!n.needed && live.keep_on) return { cls: "on", text: t("live.kept_on") };
  if (inp && inp.state !== "done" && inp.mode !== "never") return { cls: "on", text: t("live.on_choosing", { input: inp.wanted }) };
  const shown = inp?.current ?? (inp?.state === "done" ? inp.wanted : null);
  return { cls: "on", text: shown ? t("live.on_input", { input: shown }) : t("live.on") };
}

// everything on the source's route is on, with its input
function allThere(room, live, key) {
  const r = room.routes?.[key];
  return (r?.devices || []).every((id) => {
    const n = live.nodes[id];
    return !n || (n.on && !n.held.length && !cecOn(n) && (!n.input || n.input.mode === "never" || n.input.state === "done"));
  });
}

// the sentence below the drawing; acts: the room's buttons ("keep_on", "release", "all_off");
// coarse: whole minutes (what a screen reader is told)
export function roomSentence(p, room, live, coarse = false) {
  const t = p._t;
  if (!live) return null;
  if (!live.loaded) return { cls: "note", text: t("live.not_loaded") };
  const src = Object.entries(live.sources);
  const name = (key) => p._sourceName(room.sources.find((s) => s.id === key) || { id: key });
  if (live.starting) {
    const key = live.start_source ?? src.find(([, s]) => s.starting)?.[0] ?? src.find(([, s]) => s.playing)?.[0];
    const waiting = Object.entries(live.nodes).find(([, n]) => cecOn(n)?.left > 0);
    if (waiting) {
      const c = cecOn(waiting[1]);
      return { cls: "note", text: t(c.fallback ? "live.s_starting_cec" : "live.s_starting_cec_only", { source: srcName(p, room, key ?? "video"), device: devName(p, room, waiting[0]), left: Math.ceil(c.left) }) };
    }
    return { cls: "note", text: key ? t("live.s_starting", { source: srcName(p, room, key) }) : t("live.s_starting_auto") };
  }
  if (live.error) return { cls: "bad", text: t("live.s_failed", { error: failure(p, live.error) }) };
  if (live.manual) return { cls: "note", text: t("live.s_manual") };
  if (!live.auto_power) return { cls: "note", text: t("live.s_no_auto") };
  const playing = src.find(([, s]) => s.playing);
  if (playing) {
    const byHand = Object.entries(live.nodes).find(([, n]) => n.input?.state === "manual");
    if (byHand) return { cls: "note", text: t("live.s_by_hand", { device: devName(p, room, byHand[0]), input: byHand[1].input.current ?? "?" }) };
    const acts = live.keep_on ? ["release", "all_off"] : ["keep_on", "all_off"];
    return { cls: "", text: t(allThere(room, live, playing[0]) ? "live.s_playing_all" : "live.s_playing", { source: srcName(p, room, playing[0]), player: name(playing[0]) }), acts };
  }
  const goingOff = src.find(([, s]) => s.going_off);
  if (goingOff) return { cls: "", text: t("live.s_going_off", { source: srcName(p, room, goingOff[0]), player: name(goingOff[0]), min: Math.round(live.idle_timeout / 60) }) };
  const nodes = Object.values(live.nodes);
  const anyOn = nodes.some((n) => n.on) || src.some(([, s]) => s.on);
  if (live.keep_on && anyOn) return { cls: "", text: t("live.s_kept_on"), acts: ["release", "all_off"] };
  const holding = src.find(([, s]) => s.in_use && s.since != null);
  if (holding) {
    const [key, s] = holding;
    const vars = { source: srcName(p, room, key), word: playerWord(p, s), since: clockTime(p, s.since), player: name(key) };
    const picture = (room.routes?.[key]?.picture || []).length > 0;
    return {
      cls: "",
      text: s.hold_end
        ? t(picture ? "live.s_holding_picture" : "live.s_holding", { ...vars, off: when(p, s.hold_end, coarse) })
        : t("live.s_holding_ever", vars),
      acts: ["keep_on", "all_off"],
    };
  }
  // on for something else than a player (follow me, a wake, after a restart)
  const why = [...new Set(nodes.filter((n) => n.on && n.needed).flatMap((n) => n.why.map((w) => w.split(":")[0])))];
  const r = { wake: t("live.r_wake"), announce: t("live.r_announce"), prewarm: t("live.r_prewarm"), restart: t("live.r_restart"),
    shutdown: t("live.r_shutdown"), plays: t("live.r_plays"), hold: t("live.r_hold") };
  if (why.length) return { cls: "", text: t("live.s_ready", { why: why.map((w) => r[w] ?? w).join(", ") }), acts: ["all_off"] };
  const going = nodes.filter((n) => n.on && !n.needed);
  if (going.length) {
    const at = Math.max(0, ...going.map((n) => n.off_at || 0));
    return { cls: "", text: at ? t("live.s_going_off_at", { off: when(p, at, coarse) }) : t("live.s_going_off_some"), acts: ["all_off"] };
  }
  return { cls: "", text: t("live.s_off") };
}

// the line of a room in the list (from rooms/live)
export function roomLine(p, room, line) {
  const t = p._t;
  if (!line) return null;
  if (line.error) return { cls: "err", text: t("live.l_failed") };
  if (line.starting) return { cls: "wait", text: t("live.l_starting", { source: srcWord(p, room, line.starting) }) };
  if (line.playing) return { cls: "play", text: t("live.l_playing", { source: srcWord(p, room, line.playing) }) };
  if (line.status === "kept_on") return { cls: "on", text: t("live.kept_on") };
  if (line.holding) {
    const text = t("live.l_paused", { source: srcName(p, room, line.holding) });
    return { cls: "down", text: line.off_at ? `${text} · ${offText(p, line.off_at, true)}` : text };
  }
  if (line.status === "manual") return { cls: "manual", text: t("live.l_manual") };
  if (line.status === "stopping") return { cls: "wait", text: t("live.standby") };
  if (line.status === "disabled") return { cls: "off", text: t("live.l_disabled") };
  if (line.status === "off") return { cls: "off", text: t("live.off") };
  if (line.off_at) return { cls: "down", text: offText(p, line.off_at, true) };
  return { cls: "on", text: t("live.on") };
}

// why a device is on (the details)
function reason(p, room, why) {
  const t = p._t;
  const [what, key] = why.split(":");
  const vars = key ? { source: srcName(p, room, key), player: p._sourceName(room.sources.find((s) => s.id === key) || { id: key }) } : {};
  return {
    plays: t("live.why_plays", vars), hold: t("live.why_hold", vars), wake: t("live.why_wake"), announce: t("live.why_announce"),
    prewarm: t("live.why_prewarm"), shutdown: t("live.why_shutdown", vars), restart: t("live.why_restart", vars),
  }[what] ?? why;
}

// the details of the picked tile: its state, why, its entities
export function liveBox(p, room, live, id) {
  const t = p._t;
  const st = tileStatus(p, room, live, id);
  if (!st) return nothing;
  const src = live.sources[id];
  const n = live.nodes[id] || live.nodes[`${id}_host`];
  const lines = [];
  if (src) lines.push(`${src.player}: ${src.state ?? "–"}`);
  if (n) {
    for (const w of n.why) lines.push(reason(p, room, w));
    if (!n.why.length && n.on) lines.push(live.keep_on ? t("live.why_kept") : t("live.why_none"));
    if (n.input) {
      const by = n.input.mode === "cec" ? "CEC" : n.input.mode === "never" ? t("live.by_nobody") : "BMP";
      lines.push(t("live.input_line", { current: n.input.current ?? "–", wanted: n.input.wanted, by }));
    }
    for (const [e, s] of Object.entries(n.entities)) lines.push(`${e}: ${s.state ?? "–"}`);
    for (const [e, w] of Object.entries(n.power)) if (w != null) lines.push(`${e}: ${w} W`);
  }
  return html`<div class="livebox"><span class="lv ${st.cls}"><i></i><b>${t("live.now", { text: st.text })}</b></span>
    ${lines.map((l) => html`<small>${l}</small>`)}</div>`;
}
