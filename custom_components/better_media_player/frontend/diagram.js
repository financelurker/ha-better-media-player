// The room as a drawing: its sources and devices as tiles, its cables as lines.
// Pure functions: what goes where (automatic arrangement), cable shapes, label spots.

export const W = 196;
export const H = 64;
export const COLW = 330;
export const X0 = 30;
export const ROWY0 = 64;
export const ROWGAP = 140;

export const PASSIVE = ["mixer", "splitter"];
export const SINKS = ["speakers", "subwoofer", "amp"];

// the room's tiles: sources (players) first, then the devices
export function nodesOf(room, t) {
  const sources = room.sources.map((s) => ({
    id: s.id,
    kind: "player",
    name: s.name || s.player || t(`source.${s.id}`),
    sub: s.player || "",
    source: s,
  }));
  const devices = room.devices.map((d) => {
    const p = d.power || {};
    const first = (p.plugs || [])[0] || (p.devices || [])[0] || (p.actions ? p.actions[0] : null);
    return { id: d.id, kind: d.kind, name: d.name || t(`kind.${d.kind}`), sub: first || "", device: d };
  });
  return [...sources, ...devices];
}

// columns by depth: the players, then each device one further than what feeds it
function depthOf(nodes, cables, id, seen = new Set()) {
  const n = nodes.find((x) => x.id === id);
  if (!n) return 0;
  if (n.kind === "player") return 0;
  if (seen.has(id)) return 1;
  seen.add(id);
  const feeds = cables.filter((c) => c.to === id).map((c) => depthOf(nodes, cables, c.from, new Set(seen)) + 1);
  return Math.max(cables.length ? 1 : 2, ...feeds); // without any cables: a gap for the direct lines
}

// automatic placement (only for tiles the user has not moved): columns by depth;
// in each column a tile sits near the average height of what feeds it (fewer
// crossing cables), pushed apart so nothing overlaps
export function arrange(nodes, cables, saved = {}) {
  const depth = Object.fromEntries(nodes.map((n) => [n.id, depthOf(nodes, cables, n.id)]));
  const maxDepth = Math.max(0, ...Object.values(depth));
  const ys = {};
  for (const n of nodes) {
    const at = saved[n.id];
    if (at) {
      n.x = at[0];
      n.y = at[1];
      n.placed = true;
      ys[n.id] = n.y;
    } else n.placed = false;
  }
  for (let c = 0; c <= maxDepth; c++) {
    const col = nodes.filter((n) => !n.placed && depth[n.id] === c);
    const want = col
      .map((n, i) => {
        const feeds = cables.filter((k) => k.to === n.id && ys[k.from] != null).map((k) => ys[k.from]);
        return { n, i, y: feeds.length ? feeds.reduce((a, b) => a + b, 0) / feeds.length : ROWY0 + i * ROWGAP };
      })
      .sort((a, b) => a.y - b.y || a.i - b.i);
    let next = ROWY0;
    for (const w of want) {
      const y = Math.max(w.y, next);
      w.n.x = X0 + c * COLW;
      w.n.y = Math.round(y / 2) * 2;
      ys[w.n.id] = w.n.y;
      next = w.n.y + ROWGAP;
    }
  }
  return nodes;
}

export function curve(x1, y1, x2, y2) {
  const dx = Math.max(60, (x2 - x1) / 2);
  return `M${x1} ${y1} C${x1 + dx} ${y1} ${x2 - dx} ${y2} ${x2} ${y2}`;
}

// a free spot on the cable for its label: the middle, else further along either way
export function labelSpot(x1, y1, x2, y2, w, h, taken) {
  const dx = Math.max(60, (x2 - x1) / 2);
  const P = [[x1, y1], [x1 + dx, y1], [x2 - dx, y2], [x2, y2]];
  const at = (t) => [0, 1].map((k) => (1 - t) ** 3 * P[0][k] + 3 * (1 - t) ** 2 * t * P[1][k] + 3 * (1 - t) * t ** 2 * P[2][k] + t ** 3 * P[3][k]);
  const free = ([x, y]) => !taken.some(([a, b, c, d]) => x - w / 2 < c && x + w / 2 > a && y - h / 2 < d && y + h / 2 > b);
  for (const t of [0.5, 0.4, 0.6, 0.3, 0.7, 0.2, 0.8]) {
    const pt = at(t);
    if (free(pt)) return pt;
  }
  const pt = at(0.5);
  return [pt[0], pt[1] - 24]; // no room on the cable: just above it
}

export function bounds(nodes, pad = 40) {
  if (!nodes.length) return null;
  return {
    x0: Math.min(...nodes.map((n) => n.x)) - pad,
    y0: Math.min(...nodes.map((n) => n.y)) - pad - 20,
    x1: Math.max(...nodes.map((n) => n.x + W)) + pad,
    y1: Math.max(...nodes.map((n) => n.y + H)) + pad,
  };
}

const ICONS = {
  player: "M3 4h18v16H3zM10 9l5 3-5 3z",
  display: "M2 3h20v13H2zM8 20h8M12 16v4",
  receiver: "M2 6h20v12H2zM6 12h.01M10 10h8M10 14h8",
  speakers: "M6 2h12v20H6zM12 16m-3 0a3 3 0 1 0 6 0a3 3 0 1 0-6 0M12 7h.01",
  subwoofer: "M3 3h18v18H3zM12 12m-5 0a5 5 0 1 0 10 0a5 5 0 1 0-10 0",
  switch: "M3 8h18v8H3zM7 12h.01M11 12h.01M15 12h.01M7 16v3M17 16v3",
  amp: "M2 7h20v10H2zM6 12m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0M12 10h7M12 14h7",
  mixer: "M3 6h6l6 6h6M3 18h6l6-6M17 9l4 3-4 3",
  splitter: "M3 12h6l6-6h6M9 12l6 6h6M17 3l4 3-4 3M17 15l4 3-4 3",
};
export function iconPath(kind) {
  return ICONS[kind] || ICONS.player;
}

// tiles added since the last arrangement: below whatever they would overlap
export function unstack(nodes, fresh) {
  const overlaps = (a, b) => a !== b && Math.abs(a.x - b.x) < W + 20 && Math.abs(a.y - b.y) < H + 20;
  for (const n of nodes.filter((x) => fresh.has(x.id))) {
    while (nodes.some((o) => overlaps(n, o))) n.y += ROWGAP / 2;
  }
  return nodes;
}
