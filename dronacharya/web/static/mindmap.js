// Mind maps — mind-elixir (MIT, vendored) + autosave into the knowledge base.
// Custom rail panels (layout/theme/style/note/link/outline) — no third-party
// plugins (license-clean). Node tags share the app-wide tag namespace; notes
// (per-node, text-only rich text) become searchable knowledge too.
const { headers, esc, modal, toast, attachTagSuggest, knownTags, errText } = window.DC;

const ME = window.MindElixir && (window.MindElixir.default || window.MindElixir);
const statusEl = document.getElementById("mm-status");
const listEl = document.getElementById("mm-list");
// node badge: filled yellow sticky note (the rail keeps its line-style icon)
const NOTE_SVG = '<svg viewBox="0 0 24 24" stroke-linejoin="round"><path d="M20 3H4a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h10l7-7V4a1 1 0 0 0-1-1z" fill="#fbbf24" stroke="#92610a" stroke-width="1.4"/><path d="M14 21v-6a1 1 0 0 1 1-1h6z" fill="#fde68a" stroke="#92610a" stroke-width="1.4"/></svg>';

let mind = null;          // mind-elixir instance
let currentId = null;     // document id of the open map
let saveTimer = null;
let dirty = false;
let selected = null;      // selected nodeObj (last of multi-select)
let currentArrowId = null;
let themeKey = "drona";
let direction = 2;        // ME.SIDE
let mapsCache = [];

function setStatus(text) { statusEl.textContent = text; }

// ------------------------------------------------------------------ themes
const GAPS = {
  "--node-gap-x": "30px", "--node-gap-y": "10px", "--main-gap-x": "65px",
  "--main-gap-y": "45px", "--root-radius": "30px", "--main-radius": "20px",
  "--topic-padding": "3px", "--map-padding": "50px 80px", "--main-border": "",
};
const THEMES = {
  latte: () => ME.THEME,
  dark: () => ME.DARK_THEME,
  drona: () => ({
    name: "Drona", type: "dark",
    palette: ["#34d399", "#22d3ee", "#818cf8", "#f472b6", "#fbbf24",
              "#a3e635", "#f87171", "#38bdf8", "#c084fc", "#2dd4bf"],
    cssVar: { ...GAPS,
      "--root-color": "#062d21", "--root-bgcolor": "#34d399",
      "--root-border-color": "rgba(0,0,0,0)",
      "--main-color": "#e7eaf2", "--main-bgcolor": "#171d2b",
      "--main-bgcolor-transparent": "rgba(23,29,43,.8)",
      "--color": "#8b93a7", "--bgcolor": "#121722",
      "--selected": "#22d3ee", "--accent-color": "#34d399",
      "--panel-color": "#e7eaf2", "--panel-bgcolor": "#171d2b",
      "--panel-border-color": "#222a3a" } }),
  ocean: () => ({
    name: "Ocean", type: "dark",
    palette: ["#38bdf8", "#818cf8", "#22d3ee", "#60a5fa", "#a5b4fc",
              "#67e8f9", "#93c5fd", "#7dd3fc", "#c7d2fe", "#5eead4"],
    cssVar: { ...GAPS,
      "--root-color": "#e0f2fe", "--root-bgcolor": "#0c4a6e",
      "--root-border-color": "rgba(0,0,0,0)",
      "--main-color": "#e0f2fe", "--main-bgcolor": "#0f2540",
      "--main-bgcolor-transparent": "rgba(15,37,64,.8)",
      "--color": "#7da7c9", "--bgcolor": "#0a1a2e",
      "--selected": "#38bdf8", "--accent-color": "#22d3ee",
      "--panel-color": "#e0f2fe", "--panel-bgcolor": "#0f2540",
      "--panel-border-color": "#1e3a5f" } }),
  sunset: () => ({
    name: "Sunset", type: "light",
    palette: ["#ea580c", "#db2777", "#d97706", "#dc2626", "#c026d3",
              "#e11d48", "#f59e0b", "#f97316", "#be185d", "#b45309"],
    cssVar: { ...GAPS,
      "--root-color": "#fff7ed", "--root-bgcolor": "#c2410c",
      "--root-border-color": "rgba(0,0,0,0)",
      "--main-color": "#431407", "--main-bgcolor": "#ffedd5",
      "--main-bgcolor-transparent": "rgba(255,237,213,.85)",
      "--color": "#9a3412", "--bgcolor": "#fff7ed",
      "--selected": "#db2777", "--accent-color": "#ea580c",
      "--panel-color": "#431407", "--panel-bgcolor": "#ffedd5",
      "--panel-border-color": "#fdba74" } }),
};
const THEME_NAMES = { drona: "Drona", dark: "Midnight", latte: "Latte",
                      ocean: "Ocean", sunset: "Sunset" };

// ------------------------------------------------------------- rail pops --
const POPS = ["maps", "layout", "theme", "style", "note", "link", "outline"];
let openPopKey = null;
function openPop(key) {           // toggling the same tab closes it
  openPopKey = openPopKey === key ? null : key;
  POPS.forEach(k => {
    document.getElementById("pop-" + k).classList.toggle("open", k === openPopKey);
    document.querySelector(`.mm-rail [data-pop="${k}"]`)
      .classList.toggle("active", k === openPopKey);
  });
  if (openPopKey === "style") updateStylePanel();
  if (openPopKey === "note") updateNotePanel();
  if (openPopKey === "link") updateLinkPanel();
  if (openPopKey === "outline") renderOutline();
  if (openPopKey === "layout") updateLayoutPanel();
  if (openPopKey === "theme") renderThemes();
}
document.querySelectorAll(".mm-rail [data-pop]").forEach(b => {
  b.onclick = () => openPop(b.dataset.pop);
});
function closePops() {
  if (openPopKey) openPop(openPopKey);      // toggling the open one closes it
}

// clicking empty space closes the open panel; clicks on nodes, lines,
// badges, the rail, the toolbar or the panel itself keep it open — and a
// drag (pan) is not a click
{
  let downAt = null;
  document.addEventListener("pointerdown", (e) => {
    downAt = { x: e.clientX, y: e.clientY };
  }, true);
  document.addEventListener("click", (e) => {
    if (!openPopKey) return;
    if (downAt && Math.hypot(e.clientX - downAt.x, e.clientY - downAt.y) > 5)
      return;                               // it was a drag, not a click
    const t = e.target;
    if (t.closest && t.closest(
      ".mm-pop, .mm-rail, .mm-toolbar, .mm-menu, me-tpc, me-epd," +
      " .dc-note-badge, .dc-branch-hit, .dc-branch-label, .topiclinks," +
      " .svg-label, .context-menu, .dc-overlay, .dc-suggest, #dc-toasts")) return;
    closePops();
  });
}

// ------------------------------------------------------------------ list
async function refreshList() {
  const r = await window.DC.req("/api/v1/mindmaps", { quiet: true });
  if (!r.ok) {
    // an unchecked failure emptied mapsCache and rendered "No mind maps yet",
    // which reads as "your maps are gone" rather than "could not load them"
    setStatus(r.auth ? "set your API token (sidebar)"
                     : "could not load your maps — " + r.error);
    return;
  }
  mapsCache = r.data.mindmaps || [];
  renderList();
}

function renderList() {
  listEl.innerHTML = "";
  mapsCache.forEach(m => {
    const row = document.createElement("div");
    row.className = "mapitem" + (m.id === currentId ? " active" : "");
    row.innerHTML = `<span>🗺</span>
      <span style="flex:1;min-width:0"><span class="t"></span><br>
        <span class="meta">${(m.updated_at || "").slice(0, 10)}</span></span>
      <span class="del" title="delete">×</span>`;
    row.querySelector(".t").textContent = m.title;
    row.onclick = (e) => { if (!e.target.classList.contains("del")) openMap(m.id); };
    row.querySelector(".del").onclick = async (e) => {
      e.stopPropagation();
      if (!confirm(`Delete mind map "${m.title}"? Its knowledge leaves the corpus too.`)) return;
      const res = await window.DC.req(`/api/v1/documents/${m.id}`, { method: "DELETE" });
      // never destroy the open editor for a delete the server refused
      if (!res.ok) return;
      if (m.id === currentId) { currentId = null; destroyMind(); setStatus("no map"); }
      toast("Mind map deleted");
      refreshList();
    };
    listEl.appendChild(row);
  });
  if (!listEl.children.length)
    listEl.innerHTML = `<div class="muted" style="padding:8px">
      No mind maps yet — click “＋ New map”.</div>`;
}

// header search: matches map names AND their central (first-level) topics,
// autocompletes from the cached list after 3 characters
const searchInput = document.getElementById("mm-search-maps");
{
  const holder = searchInput.parentElement;
  const dd = document.createElement("div");
  dd.className = "dc-suggest";
  holder.appendChild(dd);
  const hide = () => { dd.style.display = "none"; };
  const matches = (needle) => mapsCache.filter(m =>
    m.title.toLowerCase().includes(needle) ||
    (m.topics || []).some(t => t.toLowerCase().includes(needle)));
  searchInput.addEventListener("input", () => {
    const needle = searchInput.value.trim().toLowerCase();
    if (needle.length < 3) { hide(); return; }
    const hits = matches(needle).slice(0, 8);
    dd.innerHTML = "";
    hits.forEach(m => {
      const row = document.createElement("div");
      const topic = (m.topics || []).find(t => t.toLowerCase().includes(needle));
      row.innerHTML = `${esc(m.title)}${topic && !m.title.toLowerCase()
        .includes(needle) ? ` <span class="muted">› ${esc(topic)}</span>` : ""}`;
      row.onmousedown = (e) => { e.preventDefault(); hide();
                                searchInput.value = ""; openMap(m.id); };
      dd.appendChild(row);
    });
    dd.style.display = hits.length ? "block" : "none";
  });
  searchInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      const needle = searchInput.value.trim().toLowerCase();
      const hit = needle.length >= 1 && matches(needle)[0];
      if (hit) { hide(); searchInput.value = ""; openMap(hit.id); }
    }
    if (e.key === "Escape") hide();
  });
  searchInput.addEventListener("blur", () => setTimeout(hide, 150));
}

// ------------------------------------------------------------------ editor
function destroyMind() {
  document.getElementById("mm").innerHTML = "";
  mind = null; selected = null; currentArrowId = null;
  updateStylePanel(); updateNotePanel(); updateLinkPanel(); renderOutline();
}

function markDirty() {
  dirty = true;
  setStatus("editing…");
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveNow, 1200);   // autosave, debounced
}

function nodesAdjacent(fromId, toId) {
  // parent/child in the tree ⇒ already connected, no custom link allowed
  try {
    const a = mind.findEle(fromId).nodeObj;
    if (a.parent && a.parent.id === toId) return true;
    if ((a.children || []).some(c => c.id === toId)) return true;
  } catch { /* node not rendered */ }
  return false;
}

function initMind(data) {
  destroyMind();
  themeKey = data.dcTheme && THEMES[data.dcTheme] ? data.dcTheme : themeKey;
  direction = typeof data.direction === "number" ? data.direction : direction;
  mind = new ME({
    el: "#mm",
    direction,
    draggable: true,
    // engine default wheel = scroll pans, Shift+wheel horizontal, Ctrl zooms
    contextMenu: {
      focus: true, link: true,
      extend: [{ name: "Note", onclick: () => {
        // the engine hides its menu overlay only for built-in items — a
        // custom item must hide it itself or the invisible full-screen
        // overlay keeps swallowing every later click
        const overlay = mind.container.querySelector(".context-menu");
        if (overlay) overlay.hidden = true;
        selected = (mind.currentNode && mind.currentNode.nodeObj) || selected;
        if (openPopKey !== "note") openPop("note"); else updateNotePanel();
        setTimeout(() => noteEditor.focus(), 50);
      } }],
    },
    toolBar: false,            // our toolbar replaces the built-in widget
    keypress: true,
    allowUndo: true,
    scaleMax: 2.5,
    scaleMin: 0.3,
    theme: THEMES[themeKey](),
  });
  // decorations (note badges, branch labels) redraw whenever the engine
  // relayouts — linkDiv runs after every render, so we piggyback on it
  const origLinkDiv = mind.linkDiv ? mind.linkDiv.bind(mind) : null;
  if (origLinkDiv) mind.linkDiv = (...a) => {
    // badges FIRST: they change node sizes, and the engine computes line
    // geometry from the DOM — decorating after linkDiv left main branches
    // visibly disconnected (e.g. a note on the central node)
    decorateNotes();
    const r = origLinkDiv(...a);
    styleBranchPaths();
    drawBranchLabels();
    return r;
  };
  mind.init(data);
  mind.dcTag = String(data.dcTag || "");   // map-level tag(s), saved with the map
  mind.bus.addListener("operation", (op) => {
    if (op && op.name === "createArrow" &&
        nodesAdjacent(op.obj.from, op.obj.to)) {
      const g = (mind.arrowSvg || mind.container)
        .querySelector(`g[data-linkid="${op.obj.id}"]`);
      if (g) mind.removeArrow(g);
      toast("Those nodes are already connected in the tree — no custom link needed", "error");
      return;
    }
    markDirty(); renderOutline();
  });
  mind.bus.addListener("selectNodes", (nodes) => {
    selected = nodes && nodes.length ? nodes[nodes.length - 1] : null;
    if (selectedBranchId) clearBranchSelection();
    updateStylePanel(); updateNotePanel(); updateLinkPanel();
  });
  mind.bus.addListener("unselectNodes", () => {
    selected = null;
    updateStylePanel(); updateNotePanel(); updateLinkPanel();
  });
  mind.bus.addListener("selectArrow", (obj) => {
    currentArrowId = obj && obj.id || null;
    if (selectedBranchId) clearBranchSelection();
    if (currentArrowId && openPopKey !== "link") openPop("link");
    else updateLinkPanel();
  });
  mind.bus.addListener("unselectArrow", () => {
    currentArrowId = null; updateLinkPanel();
  });
  mind.bus.addListener("scale", updateZoom);
  updateZoom();
  renderOutline();
  updateLayoutPanel();
  renderThemes();
}

// Alt+drag pans the map (in addition to Space+drag and right-drag)
{
  const mmEl = document.getElementById("mm");
  let altPan = null;
  mmEl.addEventListener("pointerdown", (e) => {
    if (!e.altKey || e.button !== 0 || !mind) return;
    altPan = { x: e.clientX, y: e.clientY };
    e.preventDefault(); e.stopPropagation();
  }, true);
  window.addEventListener("pointermove", (e) => {
    if (!altPan || !mind) return;
    mind.move(e.clientX - altPan.x, e.clientY - altPan.y);
    altPan = { x: e.clientX, y: e.clientY };
  }, true);
  window.addEventListener("pointerup", () => { altPan = null; }, true);
}

// A save is bound to the map it started on. `currentId` is the map the EDITOR
// is showing and can change while a request is in flight, so the save must
// never write `currentId` back from its own response — that used to snap it to
// the previous map and every later edit was then PUT onto that map's document,
// silently overwriting it with the content of the one on screen.
let savePromise = null;   // the in-flight save, so callers can actually wait

function saveNow() {
  if (savePromise) {
    // already saving: chain, so `await saveNow()` waits for a settled state
    return savePromise.then(() => (dirty ? saveNow() : undefined));
  }
  if (!mind || !dirty) return Promise.resolve();
  savePromise = (async () => {
    dirty = false;
    const savingId = currentId;          // bound for the whole request
    const data = mind.getData();
    data.dcTheme = themeKey;
    data.direction = mind.direction;
    data.dcTag = mind.dcTag || "";
    setStatus("saving…");
    let resp;
    try {
      resp = await fetch(
        savingId ? `/api/v1/mindmaps/${savingId}` : "/api/v1/mindmaps",
        { method: savingId ? "PUT" : "POST", headers: headers(),
          body: JSON.stringify({ data }) });
    } catch (e) {
      dirty = true; setStatus("save failed — offline?"); return;
    }
    if (!resp.ok) {
      setStatus(errText(await resp.json().catch(() => ({})))
                || "save failed — check token");
      dirty = true; return;
    }
    const out = await resp.json().catch(() => null);
    if (!out) { dirty = true; setStatus("save failed — bad response"); return; }
    if (savingId === null && currentId === null) {
      currentId = out.id;                // adopt the new id only if still ours
    }
    if (currentId !== savingId && !(savingId === null && currentId === out.id)) {
      // the user switched maps mid-save: that save is complete and correct for
      // its own map, but nothing about it applies to what is on screen now
      refreshList();
      return;
    }
    setStatus("saved ✓ (in your knowledge base)");
    knownTags(true);   // node tags may have changed the app-wide tag list
    if (savingId === null) refreshList();
    else {
      const m = mapsCache.find(x => x.id === currentId);
      if (m && m.title !== out.title) { m.title = out.title; renderList(); }
    }
  })().finally(() => { savePromise = null; });
  return savePromise;
}

async function openMap(id) {
  clearTimeout(saveTimer);
  await saveNow();                       // now genuinely waits
  let resp;
  try {
    resp = await fetch(`/api/v1/mindmaps/${id}`, { headers: headers() });
  } catch (e) {
    toast("Could not open that map — offline?", "error"); return;
  }
  if (!resp.ok) {
    toast(errText(await resp.json().catch(() => ({}))) || "Could not open that map",
          "error");
    return;
  }
  const m = await resp.json();
  currentId = id;
  dirty = false;                         // the incoming map is clean
  initMind(m.data && m.data.nodeData ? m.data : ME.new(m.title || "Untitled"));
  setStatus("saved ✓");
  renderList();
}

// ----------------------------------------------------------------- toolbar
async function newMap() {
  clearTimeout(saveTimer);
  await saveNow();                       // flush the current map FIRST
  window.DC.promptModal("New mind map", {
    label: "Central topic of the new map", value: "New idea",
    okText: "Create",
    onOk: async (topic) => {
      // flush again: the prompt was open, and anything still in flight belongs
      // to the OLD map. Only then detach, or the new map's first save would be
      // a PUT onto the previous map's document.
      clearTimeout(saveTimer);
      await saveNow();
      currentId = null;
      initMind(ME.new(topic));
      dirty = true;
      saveNow();
    },
  });
}
document.getElementById("mm-new").onclick = newMap;
document.getElementById("mm-new2").onclick = newMap;

function requireNode() {
  if (!mind) return null;
  const el = mind.currentNode;
  if (!el) { toast("Select a node first"); return null; }
  return el;
}

document.getElementById("mm-undo").onclick = () => mind && mind.undo && mind.undo();
document.getElementById("mm-redo").onclick = () => mind && mind.redo && mind.redo();
document.getElementById("mm-add-child").onclick = () => {
  if (requireNode()) mind.addChild();
};
document.getElementById("mm-add-sib").onclick = () => {
  if (requireNode()) mind.insertSibling("after");
};
document.getElementById("mm-del-node").onclick = () => {
  if (requireNode()) mind.removeNodes(mind.currentNodes);
};
document.getElementById("mm-add-free").onclick = () => {
  if (!mind) return;
  const root = mind.nodeData || mind.getData().nodeData;
  let rootEl;
  try { rootEl = mind.findEle(root.id); } catch { return; }
  const id = "free-" + Date.now().toString(36) +
             Math.random().toString(36).slice(2, 8);
  mind.addChild(rootEl, { id, topic: "New node", children: [],
                          dcBranch: { hidden: true } });
  toast("Standalone node added — double-click it to rename");
};
document.getElementById("mm-focus").onclick = () => {
  const el = requireNode();
  if (el) try { mind.focusNode(el); } catch {}
};
document.getElementById("mm-unfocus").onclick = () => {
  if (mind) try { mind.cancelFocus(); } catch {}
};

// -------------------------------------------------------------- layout ----
const LAYOUT_FN = { 0: "initLeft", 1: "initRight", 2: "initSide", 3: "initDown" };
function updateLayoutPanel() {
  document.querySelectorAll("#pop-layout [data-dir]").forEach(b => {
    b.classList.toggle("sel", mind && Number(b.dataset.dir) === mind.direction);
  });
}
document.querySelectorAll("#pop-layout [data-dir]").forEach(b => {
  b.onclick = () => {
    if (!mind) return;
    direction = Number(b.dataset.dir);
    mind[LAYOUT_FN[direction]]();
    updateLayoutPanel();
    markDirty();
  };
});

// --------------------------------------------------------------- themes ---
function renderThemes() {
  const holder = document.getElementById("mm-themes");
  holder.innerHTML = "";
  Object.keys(THEME_NAMES).forEach(key => {
    const t = THEMES[key]();
    const b = document.createElement("button");
    b.className = key === themeKey ? "sel" : "";
    b.innerHTML = `${esc(THEME_NAMES[key])}<span class="dots">${
      t.palette.slice(0, 5).map(c => `<i style="background:${c}"></i>`).join("")}</span>`;
    b.onclick = () => {
      if (!mind) return;
      themeKey = key;
      mind.changeTheme(THEMES[key]());
      renderThemes();
      markDirty();
    };
    holder.appendChild(b);
  });
}
renderThemes();

// zoom
function updateZoom() {
  const z = mind ? Math.round((mind.scaleVal || 1) * 100) : 100;
  document.getElementById("mm-zoom").textContent = z + "%";
}
document.getElementById("mm-zoom-in").onclick = () => {
  if (mind) { mind.scale(Math.min(2.5, (mind.scaleVal || 1) + 0.15)); updateZoom(); }
};
document.getElementById("mm-zoom-out").onclick = () => {
  if (mind) { mind.scale(Math.max(0.3, (mind.scaleVal || 1) - 0.15)); updateZoom(); }
};
document.getElementById("mm-fit").onclick = () => {
  if (mind) { mind.scale(1); mind.toCenter(); updateZoom(); }
};

// ------------------------------------------------------------- file menu --
function download(name, blob) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
}
function mapName() {
  return (mind && mind.getData().nodeData.topic || "mindmap").slice(0, 60);
}
function toMarkdown(node, depth = 0) {
  const link = node.hyperLink ? ` ([link](${node.hyperLink}))` : "";
  const tags = node.tags && node.tags.length ? ` _[${node.tags.join(", ")}]_` : "";
  let line = depth === 0 ? `# ${node.topic}${link}${tags}\n\n`
    : `${"  ".repeat(depth - 1)}- ${node.topic}${link}${tags}\n`;
  (node.children || []).forEach(c => { line += toMarkdown(c, depth + 1); });
  return line;
}

// dropdown menus
document.querySelectorAll(".mm-menu").forEach(menu => {
  menu.querySelector(":scope > button").onclick = (e) => {
    e.stopPropagation();
    document.querySelectorAll(".mm-menu.open").forEach(m2 => {
      if (m2 !== menu) m2.classList.remove("open");
    });
    menu.classList.toggle("open");
  };
});
document.addEventListener("click", () =>
  document.querySelectorAll(".mm-menu.open").forEach(m => m.classList.remove("open")));

function mapTagsModal() {
  if (!mind) return;
  const data = mind.getData();
  const wrap = document.createElement("div");
  wrap.innerHTML = `
    <div class="muted" style="margin-bottom:8px">Optional tag(s) for the whole
      map — same tags as everywhere else in the app. Comma-separated.</div>
    <div style="position:relative">
      <input id="mm-maptag-in" type="text" placeholder="e.g. homelab, Research/RAG"></div>
    <div class="row" style="margin-top:12px; justify-content:flex-end">
      <button id="mm-maptag-save">Save</button></div>`;
  const m = modal("Map tags", wrap, { small: true });
  const input = wrap.querySelector("#mm-maptag-in");
  input.value = String(mind.dcTag || data.dcTag || "");
  attachTagSuggest(input, (t) => {
    const parts = input.value.split(",").map(s => s.trim()).filter(Boolean);
    parts.pop();                       // replace the fragment being typed
    parts.push(t);
    input.value = parts.join(", ");
  });
  input.focus();
  wrap.querySelector("#mm-maptag-save").onclick = () => {
    mind.dcTag = input.value;          // saveNow writes it into the map JSON
    m.close(); markDirty();
    toast("Map tags saved");
  };
}

document.getElementById("mm-file-menu").querySelectorAll("[data-act]").forEach(b => {
  b.onclick = async () => {
    const act = b.dataset.act;
    if (!mind && act !== "import") return;
    if (act === "rename") {
      const root = mind.getData().nodeData;
      window.DC.promptModal("Rename map", {
        label: "Central topic", value: root.topic, okText: "Rename",
        onOk: (t) => {
          root.topic = t;
          const data = mind.getData(); data.nodeData = root;
          initMind(data); dirty = true; saveNow();
        },
      });
    } else if (act === "duplicate") {
      clearTimeout(saveTimer); await saveNow();
      const data = mind.getData();
      data.nodeData.topic += " (copy)";
      const res = await window.DC.req("/api/v1/mindmaps", {
        method: "POST", body: JSON.stringify({ data }) });
      if (!res.ok) return;          // a failed duplicate was entirely silent
      toast("Duplicated"); refreshList();
    } else if (act === "maptags") {
      mapTagsModal();
    } else if (act === "import") {
      const input = document.createElement("input");
      input.type = "file"; input.accept = ".json,application/json";
      input.onchange = async () => {
        try {
          const data = JSON.parse(await input.files[0].text());
          if (!data.nodeData || !data.nodeData.topic) throw new Error("no nodeData");
          const res = await window.DC.req("/api/v1/mindmaps", {
            method: "POST", body: JSON.stringify({ data }) });
          if (!res.ok) return;
          const out = res.data;
          toast("Imported into your knowledge base");
          await refreshList(); openMap(out.id);
        } catch (e) { toast("Import failed: " + e.message, "error"); }
      };
      input.click();
    } else if (act === "export-json") {
      download(mapName() + ".json", new Blob(
        [JSON.stringify(mind.getData(), null, 2)], { type: "application/json" }));
    } else if (act === "export-md") {
      download(mapName() + ".md", new Blob(
        [toMarkdown(mind.getData().nodeData)], { type: "text/markdown" }));
    } else if (act === "export-svg") {
      try {
        const blob = mind.exportSvg();
        download(mapName() + ".svg", blob instanceof Promise ? await blob : blob);
      } catch (e) { toast("SVG export failed", "error"); }
    } else if (act === "export-png") {
      try {
        const blob = await mind.exportPng();
        if (blob) download(mapName() + ".png", blob);
        else toast("PNG export unavailable — try SVG", "error");
      } catch (e) { toast("PNG export failed — try SVG", "error"); }
    }
  };
});

// --------------------------------------------------------- style panel ----
const COLORS = ["#e7eaf2", "#34d399", "#22d3ee", "#818cf8", "#f472b6",
                "#fbbf24", "#f87171", "#1b2130"];
const BGS = ["#171d2b", "#0d3a2e", "#0c3b46", "#28275a", "#4a1d3a",
             "#4a3607", "#4a1616", "#e7eaf2"];
const EMOJIS = ["⭐", "❗", "❓", "✅", "❌", "🔥", "💡", "📌", "🎯", "⚠️", "🚀", "❤️"];

function swatchRow(el, colors, apply) {
  el.innerHTML = "";
  const none = document.createElement("span");
  none.className = "swatch none"; none.title = "clear";
  none.onclick = () => apply(undefined);
  el.appendChild(none);
  colors.forEach(c => {
    const s = document.createElement("span");
    s.className = "swatch"; s.style.background = c;
    s.onclick = () => apply(c);
    el.appendChild(s);
  });
}

function reshape(patch) {
  if (!mind || !selected) return;
  let el;
  try { el = mind.findEle(selected.id); } catch { return; }
  mind.reshapeNode(el, patch);
}
function patchStyle(prop, value) {
  // "" (not deletion) clears a property: the engine merges the OLD style into
  // every patch, so a deleted key would silently come back — the un-bold bug
  const style = { ...(selected && selected.style || {}) };
  style[prop] = value === undefined ? "" : value;
  reshape({ style });
}

swatchRow(document.getElementById("mm-color"), COLORS, c => patchStyle("color", c));
swatchRow(document.getElementById("mm-bg"), BGS, c => patchStyle("background", c));
function toggleStyle(id, prop, onVal) {
  document.getElementById(id).onclick = () => {
    if (!selected) return;
    const cur = selected.style && selected.style[prop];
    patchStyle(prop, cur === onVal ? "" : onVal);
    updateStylePanel();
  };
}
toggleStyle("mm-bold", "fontWeight", "bold");
toggleStyle("mm-italic", "fontStyle", "italic");
toggleStyle("mm-underline", "textDecoration", "underline");

// node box shape; clicking the active one restores the theme default
const BOXES = {
  rect: { borderRadius: "3px", border: "2px solid currentColor",
          padding: "4px 14px" },
  round: { borderRadius: "16px", border: "2px solid currentColor",
           padding: "4px 14px" },
  ellipse: { borderRadius: "50%", border: "2px solid currentColor",
             padding: "8px 20px" },
  none: { borderRadius: "0", border: "none", padding: "" },
};
document.querySelectorAll("#mm-box [data-box]").forEach(b => {
  b.onclick = () => {
    if (!selected) return;
    const kind = b.dataset.box;
    const style = { ...(selected.style || {}) };
    if (style.background === "transparent") style.background = "";
    if ((selected.dcBox || "") === kind) {       // toggle off → theme default
      style.borderRadius = ""; style.border = ""; style.padding = "";
      reshape({ dcBox: "", style });
    } else {
      Object.assign(style, BOXES[kind]);
      if (kind === "none") style.background = "transparent";
      reshape({ dcBox: kind, style });
    }
    updateStylePanel();
  };
});

document.getElementById("mm-icons").innerHTML = "";
EMOJIS.forEach(e => {
  const b = document.createElement("button");
  b.textContent = e;
  b.onclick = () => {
    if (!selected) return;
    const icons = [...(selected.icons || [])];
    const i = icons.indexOf(e);
    if (i >= 0) icons.splice(i, 1); else icons.push(e);
    reshape({ icons });
  };
  document.getElementById("mm-icons").appendChild(b);
});

// node tags: chips + suggest from the app-wide tag list
const nodeTagInput = document.getElementById("mm-node-tags");
function renderNodeTagChips() {
  const holder = document.getElementById("mm-tag-chips");
  holder.innerHTML = "";
  ((selected && selected.tags) || []).forEach((t, i) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.innerHTML = `${esc(t)} <b title="remove">×</b>`;
    chip.querySelector("b").onclick = () => {
      const tags = [...(selected.tags || [])];
      tags.splice(i, 1);
      reshape({ tags });
      renderNodeTagChips();
    };
    holder.appendChild(chip);
  });
}
function addNodeTag(tag) {
  tag = tag.trim();
  if (!selected || !tag) return;
  const tags = [...(selected.tags || [])];
  if (!tags.includes(tag)) { tags.push(tag); reshape({ tags }); }
  nodeTagInput.value = "";
  renderNodeTagChips();
}
attachTagSuggest(nodeTagInput, addNodeTag);
nodeTagInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && nodeTagInput.value.trim()) {
    e.preventDefault(); addNodeTag(nodeTagInput.value);
  }
});
document.getElementById("mm-node-link").addEventListener("change", (e) => {
  if (!selected) return;
  const url = e.target.value.trim();
  reshape({ hyperLink: url || undefined });
});

function updateStylePanel() {
  const has = !!selected;
  document.getElementById("mm-style-hint").style.display = has ? "none" : "block";
  const body = document.getElementById("mm-style-body");
  body.style.opacity = has ? "1" : ".4";
  body.style.pointerEvents = has ? "auto" : "none";
  if (has) {
    document.getElementById("mm-node-link").value = selected.hyperLink || "";
    const st = selected.style || {};
    document.getElementById("mm-bold").classList.toggle("sel", st.fontWeight === "bold");
    document.getElementById("mm-italic").classList.toggle("sel", st.fontStyle === "italic");
    document.getElementById("mm-underline").classList.toggle("sel",
      st.textDecoration === "underline");
    document.querySelectorAll("#mm-box [data-box]").forEach(b =>
      b.classList.toggle("sel", (selected.dcBox || "") === b.dataset.box));
    renderNodeTagChips();
  }
}

// ---------------------------------------------------------- node notes ----
// nodeObj.dcNote (sanitized HTML, text-only). Nodes with a note carry the
// 🗒 icon; clicking the icon opens this panel.
const DROP_TAGS = new Set(["SCRIPT", "STYLE", "IMG", "IFRAME", "VIDEO", "AUDIO",
                           "OBJECT", "EMBED", "LINK", "META", "svg", "SVG"]);
const KEEP_TAGS = new Set(["B", "I", "U", "S", "STRIKE", "EM", "STRONG", "P",
                           "DIV", "BR", "UL", "OL", "LI", "H1", "H2", "H3",
                           "SPAN", "BLOCKQUOTE"]);
function sanitizeNote(html) {
  const tpl = document.createElement("template");
  tpl.innerHTML = html;
  (function walk(root) {
    [...root.children].forEach(c => {
      if (DROP_TAGS.has(c.tagName)) { c.remove(); return; }
      walk(c);
      if (!KEEP_TAGS.has(c.tagName)) { c.replaceWith(...c.childNodes); return; }
      [...c.attributes].forEach(a => c.removeAttribute(a.name));
    });
  })(tpl.content);
  return tpl.innerHTML;
}

const noteEditor = document.getElementById("mm-note-editor");
let noteNodeId = null;      // node the editor is currently bound to
let noteTimer = null;

function commitNote() {
  if (!mind || !noteNodeId) return;
  let el;
  try { el = mind.findEle(noteNodeId); } catch { return; }
  const obj = el.nodeObj;
  const html = sanitizeNote(noteEditor.innerHTML);
  const textEmpty = !noteEditor.textContent.trim();
  if (textEmpty) {
    if (!obj.dcNote) return;
    mind.reshapeNode(el, { dcNote: "" });      // fires operation → autosave
    delete obj.dcNote;
  } else {
    if (obj.dcNote === html) return;
    mind.reshapeNode(el, { dcNote: html });    // badge redraws via linkDiv hook
  }
}

// note badge: the rail's note icon rendered on every node that has a note;
// a single click opens the note panel for that node
function decorateNotes() {
  if (!mind || !mind.nodes) return;
  mind.nodes.querySelectorAll("me-tpc").forEach(el => {
    const has = el.nodeObj && el.nodeObj.dcNote;
    let badge = el.querySelector(".dc-note-badge");
    if (has && !badge) {
      badge = document.createElement("span");
      badge.className = "dc-note-badge";
      badge.title = "Open note";
      badge.innerHTML = NOTE_SVG;
      el.appendChild(badge);
    } else if (!has && badge) { badge.remove(); badge = null; }
    if (badge) {
      // ~75% of the node's height, measured from the TEXT span — measuring
      // the node itself would include the badge and feedback-loop it huge
      const textEl = el.querySelector(".text");
      const base = (textEl ? textEl.offsetHeight : 18) + 6;   // + padding
      const h = Math.max(14, Math.min(38, Math.round(base * 0.75)));
      badge.style.width = badge.style.height = h + "px";
    }
  });
}

// Per-node branch styling (dash / width / detach) + click-to-select. The
// engine draws exactly one path per visible node — main branches into
// this.lines in wrapper order, sub-branches into each main wrapper's
// subLines svg in pre-order — so we can walk the same order, restyle each
// path for its node, and lay an invisible fat hit-path over it so the LINE
// itself is clickable (same as custom links).
let selectedBranchId = null;   // node whose incoming branch is selected
const branchPathByNode = new Map();   // node id → its incoming branch <path>
function branchStyleOf(obj) { return (obj && obj.dcBranch) || {}; }
// a link styles ONLY itself unless children opt in via "inherit from parent
// link" (the default) — then they take the parent link's effective style
function effectiveBranch(own, parentEff) {
  if (own.inherit !== false) return parentEff;   // may be null = theme default
  return { color: own.color || null, dash: own.dash || null,
           width: own.width || null };
}
function applyBranchStyle(path, tpc, eff) {
  if (!path || !tpc || !tpc.nodeObj) return;
  const obj = tpc.nodeObj;
  branchPathByNode.set(obj.id, path);
  const st = branchStyleOf(obj);
  if (st.hidden) { path.style.display = "none"; return; }
  if (eff) {
    if (eff.color) path.setAttribute("stroke", eff.color);
    if (eff.dash && eff.dash !== "0")
      path.setAttribute("stroke-dasharray", eff.dash);
    if (eff.width) path.setAttribute("stroke-width", eff.width);
  }
  if (obj.id === selectedBranchId) path.classList.add("dc-branch-sel");
  const hit = path.cloneNode(false);           // clickable hit area
  hit.removeAttribute("stroke-dasharray");
  hit.removeAttribute("class");
  hit.setAttribute("stroke", "transparent");
  hit.setAttribute("stroke-width", "13");
  hit.setAttribute("fill", "none");
  hit.classList.add("dc-branch-hit");
  hit.dataset.nodeid = obj.id;
  path.parentNode.appendChild(hit);
}
function styleBranchPaths() {
  if (!mind || !mind.nodes) return;
  // clean previous hit paths / highlights (needed when called outside the
  // linkDiv redraw, e.g. on selection changes) so index matching stays true
  branchPathByNode.clear();
  [mind.lines, mind.nodes].filter(Boolean).forEach(s => {
    s.querySelectorAll("path.dc-branch-hit").forEach(p => p.remove());
    s.querySelectorAll(".dc-branch-sel").forEach(p =>
      p.classList.remove("dc-branch-sel"));
  });
  const mains = mind.nodes.querySelectorAll("me-main > me-wrapper");
  const mainPaths = mind.lines ? mind.lines.querySelectorAll("path") : [];
  mains.forEach((w, i) => {
    const mainTpc = w.querySelector("me-tpc");
    const mainEff = mainTpc && mainTpc.nodeObj
      ? effectiveBranch(branchStyleOf(mainTpc.nodeObj), null) : null;
    applyBranchStyle(mainPaths[i], mainTpc, mainEff);
    const svg = w.querySelector(":scope > svg.subLines");
    if (!svg) return;
    const paths = svg.querySelectorAll("path");
    let idx = 0;
    (function walk(wrapper, parentEff) {
      const childrenEl = wrapper.children[1];              // me-children
      if (!childrenEl) return;
      [...childrenEl.children].forEach(cw => {
        const parentEl = cw.firstChild;                    // me-parent
        const tpc = parentEl && parentEl.firstChild;       // me-tpc
        const eff = tpc && tpc.nodeObj
          ? effectiveBranch(branchStyleOf(tpc.nodeObj), parentEff) : parentEff;
        applyBranchStyle(paths[idx++], tpc, eff);
        const epd = parentEl && parentEl.children[1];      // expander
        if (!epd || !epd.expanded) return;                 // mirror the engine
        walk(cw, eff);
      });
    })(w, mainEff);
  });
}

function selectBranch(nodeId) {
  try { mind.clearSelection(); } catch {}    // clears nodes + arrows first
  selectedBranchId = nodeId;
  styleBranchPaths();                        // repaint the selection highlight
  if (openPopKey !== "link") openPop("link"); else updateLinkPanel();
}
function clearBranchSelection() {
  if (!selectedBranchId) return;
  selectedBranchId = null;
  if (mind) styleBranchPaths();
  updateLinkPanel();
}
// capture-phase so the engine's pan/box-select never swallows the click
document.getElementById("mm").addEventListener("pointerdown", (e) => {
  const t = e.target;
  if (t && t.classList && t.classList.contains("dc-branch-hit")) {
    e.stopPropagation(); e.preventDefault();
    selectBranch(t.dataset.nodeid);
    return;
  }
  clearBranchSelection();   // clicking anywhere else deselects the line
}, true);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") clearBranchSelection();
});

// branch labels: callout-style — the label can be dragged anywhere, an
// anchor line stays pinned to the link's midpoint; redrawn on every
// relayout via the linkDiv hook. Full text is always in the tooltip.
const SVG_NS = "http://www.w3.org/2000/svg";
function drawBranchLabels() {
  if (!mind || !mind.nodes) return;
  let holder = mind.nodes.querySelector(".dc-branch-labels");
  if (!holder) {
    holder = document.createElement("div");
    holder.className = "dc-branch-labels";
    mind.nodes.appendChild(holder);
  }
  holder.innerHTML = "";
  const anchors = document.createElementNS(SVG_NS, "svg");
  anchors.setAttribute("class", "dc-anchors");
  holder.appendChild(anchors);
  const canvas = mind.nodes.getBoundingClientRect();
  const scale = mind.scaleVal || 1;
  // custom-link (arrow) labels: full text as tooltip too
  mind.container.querySelectorAll(".svg-label").forEach(l => {
    l.title = l.textContent;
  });
  mind.nodes.querySelectorAll("me-tpc").forEach(el => {
    const obj = el.nodeObj;
    if (!obj || !obj.dcBranchLabel || !obj.parent) return;
    if (branchStyleOf(obj).hidden) return;   // no line → no label
    let pEl;
    try { pEl = mind.findEle(obj.parent.id); } catch { return; }
    const a = el.getBoundingClientRect(), b = pEl.getBoundingClientRect();
    if (!a.width || !b.width) return;
    const my = ((a.top + a.bottom) + (b.top + b.bottom)) / 4;
    const lab = document.createElement("span");
    lab.className = "dc-branch-label";
    lab.textContent = obj.dcBranchLabel;
    lab.title = obj.dcBranchLabel;           // long text fully readable
    const color = branchStyleOf(obj).color || obj.branchColor;
    if (color) lab.style.color = color;
    // default spot: centered in the gap between the nodes' facing edges,
    // never wider than that span (fixed cap as a backstop)
    const gapStart = Math.min(a.right, b.right);
    const gapEnd = Math.max(a.left, b.left);
    let mx, maxW;
    if (gapEnd - gapStart > 40) {                      // normal: real gap
      mx = (gapStart + gapEnd) / 2;
      maxW = (gapEnd - gapStart) / scale - 6;
    } else {                                           // edges overlap →
      mx = ((a.left + a.right) + (b.left + b.right)) / 4;   // centers midpoint
      maxW = (Math.max(a.right, b.right)
              - Math.min(a.left, b.left)) / scale - 6;      // union span
    }
    lab.style.maxWidth = Math.max(34, Math.min(maxW, 220)) + "px";
    // anchor point: ON the actual curve — the halfway point of the branch
    // path itself (the straight-line midpoint of a bezier hangs in space)
    let cx, cy;
    const path = branchPathByNode.get(obj.id);
    if (path && path.getTotalLength) {
      try {
        const pt = path.getPointAtLength(path.getTotalLength() / 2);
        const svgR = path.ownerSVGElement.getBoundingClientRect();
        cx = (svgR.left - canvas.left) / scale + pt.x;
        cy = (svgR.top - canvas.top) / scale + pt.y;
      } catch { /* fall through to the straight-line midpoint */ }
    }
    if (cx === undefined) {
      cx = (mx - canvas.left) / scale;
      cy = (my - canvas.top) / scale;
    }
    const off = obj.dcBranchLabelPos || { dx: 0, dy: 0 };
    const lx = cx + off.dx, ly = cy + off.dy;
    if (off.dx || off.dy) {                  // visible callout line
      const line = document.createElementNS(SVG_NS, "line");
      line.setAttribute("x1", cx); line.setAttribute("y1", cy);
      line.setAttribute("x2", lx); line.setAttribute("y2", ly);
      line.setAttribute("stroke", color || "#8b93a7");
      line.setAttribute("stroke-width", "1");
      anchors.appendChild(line);
      const dot = document.createElementNS(SVG_NS, "circle");
      dot.setAttribute("cx", cx); dot.setAttribute("cy", cy);
      dot.setAttribute("r", "2");
      dot.setAttribute("fill", color || "#8b93a7");
      anchors.appendChild(dot);
    }
    lab.style.left = lx + "px";
    lab.style.top = ly + "px";
    // drag to move (anchor stays); plain click selects the link;
    // double-click snaps the label back onto the line
    lab.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      e.stopPropagation(); e.preventDefault();
      const start = { x: e.clientX, y: e.clientY,
                      dx: off.dx || 0, dy: off.dy || 0 };
      let moved = false;
      const move = (ev) => {
        const s = mind.scaleVal || 1;
        const ddx = (ev.clientX - start.x) / s, ddy = (ev.clientY - start.y) / s;
        if (!moved && Math.hypot(ddx, ddy) < 3) return;
        moved = true;
        obj.dcBranchLabelPos = { dx: Math.round(start.dx + ddx),
                                 dy: Math.round(start.dy + ddy) };
        drawBranchLabels();                  // live callout redraw
      };
      const up = () => {
        window.removeEventListener("pointermove", move);
        if (moved) markDirty(); else selectBranch(obj.id);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up, { once: true });
    });
    lab.addEventListener("dblclick", (e) => {
      e.stopPropagation();
      delete obj.dcBranchLabelPos;
      drawBranchLabels(); markDirty();
    });
    holder.appendChild(lab);
  });
}
noteEditor.addEventListener("input", () => {
  clearTimeout(noteTimer);
  noteTimer = setTimeout(commitNote, 600);
});
noteEditor.addEventListener("blur", () => { clearTimeout(noteTimer); commitNote(); });
noteEditor.addEventListener("paste", (e) => {        // text-only paste
  e.preventDefault();
  const text = (e.clipboardData || window.clipboardData).getData("text/plain");
  document.execCommand("insertText", false, text);
});
document.querySelectorAll("#pop-note .wtb [data-cmd]").forEach(b => {
  b.onmousedown = (e) => e.preventDefault();         // keep editor focus
  b.onclick = () => { noteEditor.focus(); document.execCommand(b.dataset.cmd); };
});

function updateNotePanel() {
  // flush pending edits for the previous node before rebinding
  if (noteNodeId && (!selected || selected.id !== noteNodeId)) {
    clearTimeout(noteTimer); commitNote();
  }
  const has = !!selected;
  document.getElementById("mm-note-hint").style.display = has ? "none" : "block";
  document.getElementById("mm-note-body").style.display = has ? "block" : "none";
  document.getElementById("mm-note-topic").textContent =
    has ? String(selected.topic).slice(0, 40) : "";
  if (has && selected.id !== noteNodeId) {
    noteNodeId = selected.id;
    noteEditor.innerHTML = sanitizeNote(selected.dcNote || "");
  } else if (!has) {
    noteNodeId = null;
    noteEditor.innerHTML = "";
  }
}

// a single click on a node's note badge opens its note panel
document.getElementById("mm").addEventListener("click", (e) => {
  const badge = e.target.closest && e.target.closest(".dc-note-badge");
  if (!badge || !mind) return;
  e.stopPropagation();
  const tpc = badge.closest("me-tpc");
  if (!tpc || !tpc.nodeObj) return;
  try { mind.selectNode(mind.findEle(tpc.nodeObj.id)); } catch {}
  selected = tpc.nodeObj;                    // don't rely on bus timing
  if (openPopKey !== "note") openPop("note"); else updateNotePanel();
}, true);

// toolbar note button: open (or start) the note of the selected node
document.getElementById("mm-note-btn").onclick = () => {
  if (!requireNode()) return;
  selected = mind.currentNode.nodeObj;
  if (openPopKey !== "note") openPop("note"); else updateNotePanel();
  setTimeout(() => noteEditor.focus(), 50);
};

// notes panel can grow: ⤢ toggles a wide, near-full-height mode (the panel
// is also resizable by its bottom-right handle)
document.getElementById("mm-note-wide").onclick = () => {
  document.getElementById("pop-note").classList.toggle("wide");
};
document.getElementById("mm-note-del").onclick = () => {
  if (!noteNodeId) return;
  clearTimeout(noteTimer);
  noteEditor.innerHTML = "";
  commitNote();               // empty → dcNote removed, badge disappears
  toast("Note deleted");
};

// ---------------------------------------------------------- link panel ----
const LINK_COLORS = ["#e3a93c", "#34d399", "#22d3ee", "#818cf8", "#f472b6",
                     "#f87171", "#8b93a7"];
function currentArrow() {
  return mind && currentArrowId
    ? (mind.arrows || []).find(a => a.id === currentArrowId) : null;
}
function patchArrow(patch) {
  const obj = currentArrow();
  if (!obj) return;
  mind.reshapeArrow(obj, patch);        // fires operation → autosave
  updateLinkPanel();
}
function patchArrowStyle(prop, value) {
  const obj = currentArrow();
  if (!obj) return;
  patchArrow({ style: { ...(obj.style || {}), [prop]: value } });
}
swatchRow(document.getElementById("mm-link-color"), LINK_COLORS, c => {
  if (c === undefined) c = "";
  const obj = currentArrow();
  if (!obj) return;
  patchArrow({ style: { ...(obj.style || {}), stroke: c, labelColor: c } });
});
document.querySelectorAll("#mm-link-dash [data-dash]").forEach(b => {
  b.onclick = () => patchArrowStyle("strokeDasharray", b.dataset.dash);
});
document.querySelectorAll("#mm-link-width [data-width]").forEach(b => {
  b.onclick = () => patchArrowStyle("strokeWidth", b.dataset.width);
});
document.querySelectorAll("#mm-link-dir [data-bidir]").forEach(b => {
  b.onclick = () => {
    const obj = currentArrow();
    if (!obj) return;
    obj.bidirectional = b.dataset.bidir === "1";
    // arrowheads need a re-render; refresh keeps data, loses selection
    const id = obj.id;
    const data = mind.getData();
    mind.refresh(data);
    currentArrowId = id;
    markDirty(); updateLinkPanel();
  };
});
document.getElementById("mm-link-label").addEventListener("change", (e) => {
  patchArrow({ label: e.target.value.trim() || "Link" });
});
document.getElementById("mm-link-del").onclick = () => {
  const obj = currentArrow();
  if (!obj) return;
  const g = (mind.arrowSvg || mind.container)
    .querySelector(`g[data-linkid="${obj.id}"]`);
  if (g) mind.removeArrow(g);
  currentArrowId = null;
  updateLinkPanel();
};

// ---- tree-branch styling: click the LINE itself to select it (branches
// ---- and custom links are the same idea — every connection is stylable)
function branchNode() {
  // the node whose incoming branch is selected; a DETACHED node also exposes
  // its (hidden) branch when the node is selected — there's no line to click
  if (!mind) return null;
  if (selectedBranchId) {
    try { return mind.findEle(selectedBranchId).nodeObj; } catch { return null; }
  }
  if (selected && selected.parent && branchStyleOf(selected).hidden)
    return selected;
  return null;
}
function reshapeBranchNode(patch) {
  const obj = branchNode();
  if (!obj) return;
  let el;
  try { el = mind.findEle(obj.id); } catch { return; }
  mind.reshapeNode(el, patch);
}
function patchBranch(patch, extra = {}) {
  const obj = branchNode();
  if (!obj) return;
  reshapeBranchNode({ dcBranch: { ...branchStyleOf(obj), ...patch }, ...extra });
  updateLinkPanel();
}
swatchRow(document.getElementById("mm-branch-color"), COLORS, c => {
  // per-link color lives in dcBranch.color; also clear any legacy
  // branchColor, whose engine-native behavior cascades to all children
  patchBranch({ color: c === undefined ? "" : c }, { branchColor: "" });
});
document.getElementById("mm-branch-inherit").addEventListener("change", (e) => {
  patchBranch({ inherit: e.target.checked });
});
document.getElementById("mm-branch-label").addEventListener("change", (e) => {
  reshapeBranchNode({ dcBranchLabel: e.target.value.trim() });
});
document.querySelectorAll("#mm-branch-dash [data-dash]").forEach(b => {
  b.onclick = () => patchBranch({ dash: b.dataset.dash });
});
document.querySelectorAll("#mm-branch-width [data-width]").forEach(b => {
  b.onclick = () => patchBranch({ width: b.dataset.width });
});
document.getElementById("mm-branch-detach").onclick = () => {
  const obj = branchNode();
  if (!obj) return;
  const hidden = !branchStyleOf(obj).hidden;
  patchBranch({ hidden });
  if (hidden) selectedBranchId = null;       // the line is gone now
  updateLinkPanel();
  toast(hidden ? "Detached — the node stands alone (its knowledge stays); " +
                 "select the node to reattach"
               : "Reattached to its parent");
};

function updateLinkPanel() {
  const obj = currentArrow();
  const branch = obj ? null : branchNode();
  document.getElementById("mm-link-hint").style.display =
    obj || branch ? "none" : "block";
  document.getElementById("mm-link-body").style.display = obj ? "block" : "none";
  document.getElementById("mm-branch-body").style.display =
    branch ? "block" : "none";
  if (branch) {
    document.getElementById("mm-branch-topic").textContent =
      String(branch.topic).slice(0, 30);
    document.getElementById("mm-branch-label").value = branch.dcBranchLabel || "";
    const st = branchStyleOf(branch);
    const inheritOn = st.inherit !== false;
    document.getElementById("mm-branch-inherit").checked = inheritOn;
    document.getElementById("mm-branch-style-groups")
      .classList.toggle("inherit", inheritOn);
    document.querySelectorAll("#mm-branch-dash [data-dash]").forEach(b =>
      b.classList.toggle("sel", !inheritOn && (st.dash || "0") === b.dataset.dash));
    document.querySelectorAll("#mm-branch-width [data-width]").forEach(b =>
      b.classList.toggle("sel",
        !inheritOn && String(st.width || "2") === b.dataset.width));
    document.getElementById("mm-branch-detach").textContent =
      st.hidden ? "Reattach to parent" : "Remove link (detach from parent)";
    return;
  }
  if (!obj) return;
  document.getElementById("mm-link-label").value = obj.label || "";
  const st = obj.style || {};
  document.querySelectorAll("#mm-link-dash [data-dash]").forEach(b =>
    b.classList.toggle("sel", (st.strokeDasharray || "8,2") === b.dataset.dash ||
      (b.dataset.dash === "8,4" && !st.strokeDasharray)));
  document.querySelectorAll("#mm-link-width [data-width]").forEach(b =>
    b.classList.toggle("sel", String(st.strokeWidth || "2") === b.dataset.width));
  document.querySelectorAll("#mm-link-dir [data-bidir]").forEach(b =>
    b.classList.toggle("sel", (obj.bidirectional ? "1" : "0") === b.dataset.bidir));
}

// ------------------------------------------------------------- outline ----
function renderOutline() {
  const listOut = document.getElementById("mm-outline-list");
  if (!listOut) return;
  listOut.innerHTML = "";
  if (!mind) return;
  const needle = document.getElementById("mm-search-nodes").value.trim().toLowerCase();
  const walk = (node, depth) => {
    const item = document.createElement("div");
    item.className = "o-item" +
      (needle && node.topic.toLowerCase().includes(needle) ? " hit" : "");
    item.style.paddingLeft = 8 + depth * 14 + "px";
    item.textContent = node.topic;
    item.title = node.topic;
    item.onclick = () => {
      try {
        const el = mind.findEle(node.id);
        mind.selectNode(el);
        el.scrollIntoView({ block: "center", inline: "center", behavior: "smooth" });
      } catch { toast("Node is inside a collapsed branch"); }
    };
    listOut.appendChild(item);
    (node.children || []).forEach(c => walk(c, depth + 1));
  };
  walk(mind.getData().nodeData, 0);
}
document.getElementById("mm-search-nodes").addEventListener("input", renderOutline);
document.getElementById("mm-search-nodes").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    const hit = document.querySelector("#mm-outline-list .o-item.hit");
    if (hit) hit.click();
  }
});

// ---------------------------------------------------------------- help ----
document.getElementById("mm-help").onclick = () => {
  modal("Shortcuts & tips", `
    <div class="kbd-grid">
      <span><kbd>Tab</kbd></span><span>add a child node</span>
      <span><kbd>Enter</kbd></span><span>add a sibling node</span>
      <span><kbd>F2</kbd> / double-click</span><span>edit the node text</span>
      <span><kbd>Del</kbd></span><span>remove node</span>
      <span><kbd>Ctrl+Z</kbd> / <kbd>Ctrl+Y</kbd></span><span>undo / redo</span>
      <span><kbd>Ctrl+C</kbd> / <kbd>Ctrl+V</kbd></span><span>copy / paste a branch</span>
      <span><kbd>Ctrl+S</kbd></span><span>save now (autosaves anyway)</span>
      <span>mouse wheel</span><span>scroll up/down (<kbd>Shift</kbd>+wheel sideways)</span>
      <span><kbd>Ctrl</kbd>+wheel</span><span>zoom at the pointer</span>
      <span><kbd>Ctrl</kbd>+<kbd>=</kbd> / <kbd>Ctrl</kbd>+<kbd>-</kbd></span><span>zoom in / out</span>
      <span><kbd>Ctrl</kbd>+<kbd>0</kbd></span><span>reset zoom &amp; center</span>
      <span><kbd>Space</kbd>+drag</span><span>pan the map</span>
      <span><kbd>Alt</kbd>+drag</span><span>pan the map</span>
      <span>drag a node</span><span>move / re-parent it</span>
      <span>right-click</span><span>note, custom links, focus, more</span>
      <span>right rail</span><span>layout, theme, node style, notes, link style, outline</span>
    </div>
    <div class="muted" style="margin-top:12px">Every node (and its note and
      tags) lands in your knowledge base as searchable knowledge — Ask, Search,
      Tags, Graph and sync all see it.</div>`, { small: true });
};

document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "s") {
    e.preventDefault();
    clearTimeout(saveTimer); saveNow();
    return;
  }
  // navigation: Ctrl+= / Ctrl+- zoom, Ctrl+0 reset (overrides browser zoom)
  if (!mind || !(e.ctrlKey || e.metaKey)) return;
  if (e.target.closest && e.target.closest("input, textarea, [contenteditable=true]")) return;
  if (e.key === "=" || e.key === "+") {
    e.preventDefault();
    mind.scale(Math.min(2.5, (mind.scaleVal || 1) + 0.15)); updateZoom();
  } else if (e.key === "-" || e.key === "_") {
    e.preventDefault();
    mind.scale(Math.max(0.3, (mind.scaleVal || 1) - 0.15)); updateZoom();
  } else if (e.key === "0") {
    e.preventDefault();
    mind.scale(1); mind.toCenter(); updateZoom();
  }
});

window.addEventListener("beforeunload", (e) => {
  if (!dirty || !mind) return;
  // A normal fetch is aborted when the page goes away, so the edits were lost
  // even though the browser had just warned they might be. keepalive lets the
  // request outlive the document; the dialog then only covers a failed send.
  const data = mind.getData();
  data.dcTheme = themeKey;
  data.direction = mind.direction;
  data.dcTag = mind.dcTag || "";
  try {
    fetch(currentId ? `/api/v1/mindmaps/${currentId}` : "/api/v1/mindmaps",
          { method: currentId ? "PUT" : "POST", headers: headers(),
            body: JSON.stringify({ data }), keepalive: true });
  } catch (err) { /* nothing more we can do on the way out */ }
  e.preventDefault();
});

// ------------------------------------------------------------------ boot
if (!ME) {
  setStatus("mind-elixir failed to load");
} else {
  refreshList().then(() => {
    if (mapsCache.length) openMap(mapsCache[0].id);
    else openPop("maps");
  });
}
window.DC.onToken = refreshList;
