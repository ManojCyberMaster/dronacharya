// Tags — semantic word map. Server supplies 2D positions (PCA over the KB's
// own embeddings) + clusters; here we color, size, de-overlap and render.
// The map pans (drag) and zooms (wheel / buttons) inside its own frame.
const { headers, esc, modal, toast, openDocument } = window.DC;

const wrap = document.getElementById("tagmap-wrap");
const stage = document.getElementById("tagmap");

// one hue per cluster; shade within a cluster encodes item count
const HUES = [160, 199, 262, 330, 25, 92, 55, 0];

function colorFor(cluster, count, maxCount) {
  const hue = HUES[cluster % HUES.length];
  const t = maxCount > 1 ? Math.log(count) / Math.log(maxCount) : 1; // 0..1
  const light = 72 - t * 34;             // few items → light, many → dark
  return { bg: `hsl(${hue} 62% ${light}%)`,
           fg: light < 55 ? "#fff" : "#1b2130" };
}

function sizeFor(count, maxCount) {
  const t = maxCount > 1 ? Math.sqrt(count / maxCount) : 1;
  return 12.5 + t * 19;                  // px font size
}

function relax(items, W, H) {
  // simple iterative de-overlap: push intersecting pills apart, stay in bounds
  for (let iter = 0; iter < 220; iter++) {
    let moved = false;
    for (let i = 0; i < items.length; i++) {
      for (let j = i + 1; j < items.length; j++) {
        const a = items[i], b = items[j];
        const dx = b.x - a.x, dy = b.y - a.y;
        const ox = (a.w + b.w) / 2 + 8 - Math.abs(dx);
        const oy = (a.h + b.h) / 2 + 6 - Math.abs(dy);
        if (ox > 0 && oy > 0) {
          moved = true;
          if (ox < oy) {
            const push = ox / 2 * (dx >= 0 ? 1 : -1);
            a.x -= push; b.x += push;
          } else {
            const push = oy / 2 * (dy >= 0 ? 1 : -1);
            a.y -= push; b.y += push;
          }
        }
      }
    }
    items.forEach(it => {
      it.x = Math.max(it.w / 2 + 6, Math.min(W - it.w / 2 - 6, it.x));
      it.y = Math.max(it.h / 2 + 6, Math.min(H - it.h / 2 - 6, it.y));
    });
    if (!moved) break;
  }
}

// ------------------------------------------------------------ pan & zoom --
// Same controls as the mind map: wheel pans (Shift = horizontal, or a mouse's
// horizontal wheel), Ctrl+wheel zooms at the pointer, Alt+drag pans.
const view = { s: 1, tx: 0, ty: 0 };
function applyView() {
  stage.style.transform = `translate(${view.tx}px, ${view.ty}px) scale(${view.s})`;
  document.getElementById("tz-val").textContent = Math.round(view.s * 100) + "%";
}
function zoomAt(factor, cx, cy) {
  const s2 = Math.min(3, Math.max(0.4, view.s * factor));
  const r = wrap.getBoundingClientRect();
  const px = cx - r.left, py = cy - r.top;      // pointer inside the frame
  view.tx = px - (px - view.tx) * (s2 / view.s);
  view.ty = py - (py - view.ty) * (s2 / view.s);
  view.s = s2;
  applyView();
}
function zoomCenter(factor) {
  const r = wrap.getBoundingClientRect();
  zoomAt(factor, r.left + r.width / 2, r.top + r.height / 2);
}
wrap.addEventListener("wheel", (e) => {
  e.preventDefault();
  if (e.ctrlKey || e.metaKey) { zoomAt(Math.exp(-e.deltaY * 0.0016), e.clientX, e.clientY); return; }
  if (e.shiftKey) view.tx -= (e.deltaY || e.deltaX);
  else { view.tx -= e.deltaX; view.ty -= e.deltaY; }
  applyView();
}, { passive: false });

let pan = null;               // Alt+drag pans; plain clicks stay clicks
let suppressClick = false;    // a drag must not open the tag modal on release
wrap.addEventListener("pointerdown", (e) => {
  if (e.button !== 0 || !e.altKey) return;
  if (e.target.closest("#tagmap-zoom")) return;
  pan = { x: e.clientX, y: e.clientY, moved: false };
  wrap.classList.add("panning");
  e.preventDefault();
});
window.addEventListener("pointermove", (e) => {
  if (!pan) return;
  pan.moved = true;
  view.tx += e.clientX - pan.x;
  view.ty += e.clientY - pan.y;
  pan.x = e.clientX; pan.y = e.clientY;
  applyView();
});
window.addEventListener("pointerup", () => {
  if (pan && pan.moved) {
    suppressClick = true;
    setTimeout(() => { suppressClick = false; }, 0);
  }
  pan = null; wrap.classList.remove("panning");
});
wrap.addEventListener("dblclick", (e) => {
  if (e.target.closest(".tagword") || e.target.closest("#tagmap-zoom")) return;
  view.s = 1; view.tx = 0; view.ty = 0; applyView();
});
document.getElementById("tz-in").onclick = () => zoomCenter(1.25);
document.getElementById("tz-out").onclick = () => zoomCenter(0.8);
document.getElementById("tz-fit").onclick = () => {
  view.s = 1; view.tx = 0; view.ty = 0; applyView();
};

// keyboard navigation: Ctrl+=/− zoom, Ctrl+0 reset, arrow keys pan
document.addEventListener("keydown", (e) => {
  if (e.target.closest && e.target.closest("input, textarea")) return;
  if (e.ctrlKey || e.metaKey) {
    if (e.key === "=" || e.key === "+") { e.preventDefault(); zoomCenter(1.25); }
    else if (e.key === "-" || e.key === "_") { e.preventDefault(); zoomCenter(0.8); }
    else if (e.key === "0") {
      e.preventDefault();
      view.s = 1; view.tx = 0; view.ty = 0; applyView();
    }
    return;
  }
  const PAN = 60;
  if (e.key === "ArrowUp") { view.ty += PAN; applyView(); e.preventDefault(); }
  else if (e.key === "ArrowDown") { view.ty -= PAN; applyView(); e.preventDefault(); }
  else if (e.key === "ArrowLeft") { view.tx += PAN; applyView(); e.preventDefault(); }
  else if (e.key === "ArrowRight") { view.tx -= PAN; applyView(); e.preventDefault(); }
});

document.getElementById("tm-help").onclick = () => {
  modal("Navigation", `
    <div class="kbd-grid">
      <span>mouse wheel</span><span>scroll up/down (<kbd>Shift</kbd>+wheel sideways)</span>
      <span><kbd>Ctrl</kbd>+wheel</span><span>zoom at the pointer</span>
      <span><kbd>Ctrl</kbd>+<kbd>=</kbd> / <kbd>Ctrl</kbd>+<kbd>-</kbd></span><span>zoom in / out</span>
      <span><kbd>Ctrl</kbd>+<kbd>0</kbd></span><span>reset the view</span>
      <span><kbd>Alt</kbd>+drag</span><span>pan the map</span>
      <span><kbd>←↑↓→</kbd></span><span>pan the map</span>
      <span>double-click</span><span>reset the view</span>
      <span>click a tag</span><span>browse &amp; edit its knowledge</span>
    </div>`, { small: true });
};

// --------------------------------------------------------------- tag modal --
async function tagModal(name, count) {
  const body = document.createElement("div");
  body.innerHTML = `<div class="muted">loading…</div>`;
  const head = document.createElement("span");
  head.textContent = `${name} — ${count} knowledge item${count === 1 ? "" : "s"}`;
  const m = modal(head, body);

  const tools = document.createElement("span");
  tools.className = "row";
  tools.style.marginLeft = "12px";
  tools.innerHTML = `
    <button class="secondary" style="font-size:12px" id="tg-ren">Rename tag</button>
    <button class="danger" style="font-size:12px" id="tg-del">Delete tag</button>`;
  m.head.insertBefore(tools, m.head.lastElementChild);
  tools.querySelector("#tg-ren").onclick = () => {
    window.DC.promptModal("Rename tag", {
      label: `Rename "${name}" everywhere to:`, value: name, okText: "Rename",
      onOk: async (to) => {
        if (to === name) return;
        const r = await fetch("/api/v1/tags/rename", {
          method: "POST", headers: headers(),
          body: JSON.stringify({ old: name, new: to }) });
        if (!r.ok) { toast("Rename failed", "error"); return; }
        const out = await r.json();
        toast(`Renamed on ${out.documents} document${out.documents === 1 ? "" : "s"}`);
        m.close(); load();
      },
    });
  };
  tools.querySelector("#tg-del").onclick = async () => {
    if (!confirm(`Remove tag "${name}" from all ${count} item${count === 1 ? "" : "s"}?\n` +
                 "The knowledge itself stays — only the tag goes away.")) return;
    const r = await fetch("/api/v1/tags/delete", {
      method: "POST", headers: headers(), body: JSON.stringify({ name }) });
    if (!r.ok) { toast("Delete failed", "error"); return; }
    toast(`Tag "${name}" removed`);
    m.close(); load();
  };

  const resp = await fetch("/api/v1/documents?" + new URLSearchParams(
    { tag: name, limit: "200" }), { headers: headers(false) });
  if (!resp.ok) { body.innerHTML = `<div class="muted">could not load</div>`; return; }
  const { documents } = await resp.json();
  body.innerHTML = "";
  documents.forEach(d => {
    const isMap = d.source_type === "mindmap";
    const nodePaths = isMap && d.tag_nodes && d.tag_nodes[name] || null;
    const row = document.createElement("div");
    row.className = "unit";
    row.style.cursor = "pointer";
    row.innerHTML = `
      <div class="u-head">
        <span class="badge plain">${esc(d.source_type)}</span>
        <span class="u-path">${esc((d.url || d.file_path || ""))}</span>
      </div>
      <div style="font-weight:600">${isMap ? "MindMap: " : ""}${esc(d.title)}</div>
      ${nodePaths ? nodePaths.map(p =>
        `<div class="muted">MindMap:${esc(p)}</div>`).join("") :
        `<div class="muted">${esc(d.summary || "")}</div>`}`;
    row.onclick = () => openDocument(d.id, { onChange: load });
    body.appendChild(row);
  });
  if (!documents.length)
    body.innerHTML = `<div class="muted">no documents carry this tag</div>`;
}

// -------------------------------------------------------------------- load --
async function load() {
  stage.innerHTML = `<div class="muted" style="padding:20px">computing the map…</div>`;
  let resp;
  try {
    resp = await fetch("/api/v1/tags/map", { headers: headers(false) });
  } catch { stage.innerHTML = `<div class="muted" style="padding:20px">server unreachable</div>`; return; }
  if (resp.status === 401) {
    stage.innerHTML = `<div class="muted" style="padding:20px">Invalid token — set it from the sidebar.</div>`;
    return;
  }
  const data = await resp.json();
  if (!data.tags.length) {
    stage.innerHTML = `<div class="muted" style="padding:20px">No tags yet —
      tags appear as you save pages, import bookmarks or install a seed kit.</div>`;
    return;
  }
  const W = wrap.clientWidth, H = wrap.clientHeight;
  const maxCount = Math.max(...data.tags.map(t => t.count));
  stage.innerHTML = "";
  // center-out placement: the biggest tags sit in the middle and smaller ones
  // fan out in every direction; each tag's direction comes from its semantic
  // position, so related topics still share a sector — and no dead space
  const order = [...data.tags].sort((a, b) => b.count - a.count);
  const cx = W / 2, cy = H / 2;
  const maxR = Math.min(W, H) / 2 - 60;
  const GOLDEN = Math.PI * (3 - Math.sqrt(5));
  const pos = new Map();
  order.forEach((t, i) => {
    const dx = t.x - 0.5, dy = t.y - 0.5;
    const angle = Math.hypot(dx, dy) > 0.03 ? Math.atan2(dy, dx) : i * GOLDEN;
    const r = i === 0 ? 0 : maxR * Math.sqrt(i / (order.length - 1 || 1));
    pos.set(t.name, { x: cx + Math.cos(angle) * r * 1.25,
                      y: cy + Math.sin(angle) * r * 0.85 });
  });
  const items = data.tags.map(t => {
    const el = document.createElement("div");
    el.className = "tagword";
    const { bg, fg } = colorFor(t.cluster, t.count, maxCount);
    el.style.background = bg;
    el.style.color = fg;
    el.style.fontSize = sizeFor(t.count, maxCount) + "px";
    el.innerHTML = `${esc(t.name)}<span class="n">${t.count}</span>`;
    el.title = `${t.name} — ${t.count} knowledge item${t.count === 1 ? "" : "s"}`;
    el.onclick = () => { if (!suppressClick) tagModal(t.name, t.count); };
    stage.appendChild(el);
    const p = pos.get(t.name);
    return { el, t,
             x: Math.max(60, Math.min(W - 60, p.x)),
             y: Math.max(40, Math.min(H - 40, p.y)),
             w: el.offsetWidth, h: el.offsetHeight };
  });
  relax(items, W, H);
  items.forEach(it => {
    it.el.style.left = it.x + "px";
    it.el.style.top = it.y + "px";
  });
  applyView();
}

document.getElementById("tm-refresh").onclick = () => {
  view.s = 1; view.tx = 0; view.ty = 0;
  load();
};
window.DC.onToken = load;
let resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer); resizeTimer = setTimeout(load, 300);
});
load();
