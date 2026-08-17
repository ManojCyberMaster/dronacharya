// DronaCharya background worker.
// Capture happens ONLY on explicit user gesture (context menu / popup) via
// chrome.scripting on the active tab — no content scripts run on every page,
// no data leaves the browser except to the user's own configured server.

const DEFAULTS = { serverUrl: "http://127.0.0.1:8317", token: "", alwaysOverwrite: false };

async function settings() {
  return { ...DEFAULTS, ...(await chrome.storage.local.get(DEFAULTS)) };
}

function apiHeaders(cfg, json = true) {
  return {
    ...(json ? { "Content-Type": "application/json" } : {}),
    ...(cfg.token ? { Authorization: "Bearer " + cfg.token } : {}),
  };
}

function capturePage() {
  // runs in the page context
  return {
    url: location.href,
    title: document.title,
    html: document.documentElement.outerHTML,
    selection: String(getSelection ? getSelection() : ""),
  };
}

async function captureTab(tabId) {
  const [{ result }] = await chrome.scripting.executeScript({
    target: { tabId },
    func: capturePage,
  });
  return result;
}

async function savePage(page, { overwrite = false, tags = [], note = "" } = {}) {
  const cfg = await settings();
  const resp = await fetch(cfg.serverUrl.replace(/\/$/, "") + "/api/v1/save-html", {
    method: "POST",
    headers: apiHeaders(cfg),
    body: JSON.stringify({
      url: page.url, title: page.title, html: page.html,
      tags, note: note || null,
      overwrite: overwrite || cfg.alwaysOverwrite,
    }),
  });
  return { status: resp.status, body: await resp.json().catch(() => ({})) };
}

// ---------------------------- in-page overlay ----------------------------
// Injected into the tab's isolated world on save: shows "Distilling
// knowledge…" with a progress bar, then the distilled summary + knowledge
// units for review, right on top of the page (the popup closes on save).
// It only listens for dc-overlay messages and reports user actions back.

function dcOverlayMain() {
  if (window.__dcOverlay) { window.__dcOverlay.reset(); return; }

  const host = document.createElement("div");
  host.style.cssText = "position:fixed;inset:0;z-index:2147483647;";
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `
    <style>
      * { box-sizing: border-box; }
      .veil { position: fixed; inset: 0; background: rgba(20,26,24,.48); }
      .card { position: fixed; top: 10vh; left: 50%; transform: translateX(-50%);
              width: min(600px, calc(100vw - 32px)); max-height: 78vh;
              display: flex; flex-direction: column;
              background: #f7f6f3; color: #1a1a1a; border-radius: 12px;
              box-shadow: 0 18px 50px rgba(0,0,0,.35);
              font: 13.5px/1.5 system-ui, sans-serif; }
      .head { display: flex; align-items: center; gap: 9px;
              padding: 13px 16px 10px; border-bottom: 1px solid #e2e0da; }
      .head b { font-size: 14.5px; }
      .spin { width: 16px; height: 16px; border: 2.5px solid #cfe4da;
              border-top-color: #0b6e4f; border-radius: 50%;
              animation: dcspin .8s linear infinite; }
      @keyframes dcspin { to { transform: rotate(360deg); } }
      .body { padding: 12px 16px; overflow: auto; }
      .bar { height: 6px; background: #e2e0da; border-radius: 3px; margin-top: 12px; }
      .bar > div { height: 100%; width: 0; background: #0b6e4f; border-radius: 3px;
                   transition: width .5s; }
      .muted { color: #6b6b6b; font-size: 12.5px; }
      textarea { width: 100%; min-height: 76px; resize: vertical; margin-top: 4px;
                 padding: 7px 9px; border: 1px solid #d8d6cf; border-radius: 7px;
                 font: inherit; background: #fff; }
      .units { margin-top: 10px; }
      .unit { display: flex; gap: 8px; align-items: flex-start;
              background: #fff; border: 1px solid #e2e0da; border-radius: 8px;
              padding: 7px 9px; margin-top: 6px; }
      .unit .k { flex: none; font-size: 10.5px; font-weight: 700;
                 text-transform: uppercase; letter-spacing: .04em;
                 color: #0b6e4f; background: #e7f2ec; border-radius: 4px;
                 padding: 1px 6px; margin-top: 2px; }
      .unit .k.howto { color: #7a4b00; background: #f6ecd9; }
      .unit .k.concept { color: #3c4a9e; background: #e8eaf7; }
      .unit .t { flex: 1; min-width: 0; overflow-wrap: anywhere; }
      .unit .hp { display: block; font-size: 11px; color: #8a887f; }
      .unit .tx { display: block; border-radius: 4px; padding: 1px 3px; margin: -1px -3px;
                  cursor: text; }
      .unit .tx:hover { background: #f3f8f5; }
      .unit .tx:focus { outline: 2px solid #0b6e4f33; background: #f3f8f5; }
      .unit .x { flex: none; cursor: pointer; border: 0; background: none;
                 color: #b3261e; font: 700 15px/1 system-ui; opacity: .5;
                 padding: 2px 4px; margin: 0; }
      .unit .x:hover { opacity: 1; }
      .foot { display: flex; gap: 8px; justify-content: flex-end;
              padding: 10px 16px 13px; border-top: 1px solid #e2e0da; }
      button.act { border: 0; border-radius: 7px; padding: 8px 14px; font: inherit;
                   cursor: pointer; background: #0b6e4f; color: #fff; }
      button.ghostb { background: transparent; color: #0b6e4f; border: 1px solid #0b6e4f; }
      button.danger { background: transparent; color: #b3261e; border: 1px solid #b3261e; }
      pre.diff { white-space: pre-wrap; background: #fff7e0; border: 1px solid #e0c56e;
                 border-radius: 7px; padding: 8px; font: 12px/1.5 ui-monospace, monospace; }
      .big { font-size: 15px; }
    </style>
    <div class="veil"></div>
    <div class="card">
      <div class="head"></div>
      <div class="body"></div>
      <div class="foot"></div>
    </div>`;
  const el = (sel) => root.querySelector(sel);
  const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;");
  document.documentElement.appendChild(host);

  let units = [];           // live (not-removed) units in review
  const send = (action, extra) =>
    chrome.runtime.sendMessage({ type: "dc-action", action, ...extra });
  const close = () => { host.remove(); window.__dcOverlay = null; };
  el(".veil").addEventListener("click", () => { send("cancel"); close(); });

  function button(cls, label, onclick) {
    const b = document.createElement("button");
    b.className = "act" + (cls ? " " + cls : "");
    b.textContent = label;
    b.onclick = onclick;
    return b;
  }

  function render(msg) {
    const head = el(".head"), body = el(".body"), foot = el(".foot");
    foot.innerHTML = "";
    if (msg.phase === "progress") {
      head.innerHTML = `<span class="spin"></span><b>${esc(msg.label || "Distilling knowledge…")}</b>`;
      body.innerHTML = `<div class="muted">${esc(msg.detail ||
        "Your server is reading the page and extracting the knowledge worth keeping.")}</div>
        <div class="bar"><div style="width:${Math.max(2, msg.pct | 0)}%"></div></div>`;
    } else if (msg.phase === "review") {
      units = msg.units.slice();
      head.innerHTML = `<b>Distilled knowledge</b>
        <span class="muted">— review before it stays in your KB</span>`;
      body.innerHTML = `<div class="muted">Summary (editable):</div>
        <textarea class="sum"></textarea>
        <div class="units"><div class="muted">${units.length} knowledge
        item${units.length === 1 ? "" : "s"} extracted — click to edit, × to remove:</div></div>`;
      el(".sum").value = msg.summary || "";
      const list = el(".units");
      units.forEach((u) => {
        const row = document.createElement("div");
        row.className = "unit";
        row.innerHTML = `<span class="k ${esc(u.kind)}">${esc(u.kind)}</span>
          <span class="t">${u.heading_path ? `<span class="hp">${esc(u.heading_path)}</span>` : ""}<span
            class="tx" contenteditable="plaintext-only" title="click to edit">${esc(u.text)}</span></span>
          <button class="x" title="remove this item">×</button>`;
        row.querySelector(".x").onclick = () => {
          if (units.length <= 1) return;   // a document needs at least one unit
          units = units.filter((x) => x !== u);
          row.remove();
        };
        row.querySelector(".tx").addEventListener("input", (e) => {
          u.text = e.target.textContent;
        });
        list.appendChild(row);
      });
      foot.appendChild(button("danger", "Discard save", () => {
        send("discard");
        render({ phase: "progress", label: "Discarding…", pct: 60 });
      }));
      foot.appendChild(button("", "Keep in my knowledge base", () => {
        const kept = units.map((u) => ({ ...u, text: u.text.trim() }))
          .filter((u) => u.text);
        send("keep", { summary: el(".sum").value.trim(),
                       units: kept.length ? kept : units });
        render({ phase: "progress", label: "Saving…", pct: 80 });
      }));
    } else if (msg.phase === "consent") {
      head.innerHTML = `<b>Page changed since you saved it</b>`;
      body.innerHTML = `<pre class="diff">Saved: ${esc(msg.old || "—")}\nNow:   ${esc(msg.now || "—")}</pre>
        <div class="muted">Update replaces the saved knowledge with a fresh distillation.</div>`;
      foot.appendChild(button("ghostb", "Keep the old version", () => { send("cancel"); close(); }));
      foot.appendChild(button("", "Update saved knowledge", () => {
        send("overwrite");
        render({ phase: "progress", label: "Distilling knowledge…", pct: 4 });
      }));
    } else if (msg.phase === "done") {
      head.innerHTML = `<b class="big">${esc(msg.label || "✓ Done")}</b>`;
      body.innerHTML = msg.detail ? `<div class="muted">${esc(msg.detail)}</div>` : "";
      setTimeout(close, msg.detail ? 3500 : 1600);
    } else if (msg.phase === "error") {
      head.innerHTML = `<b>Could not save</b>`;
      body.innerHTML = `<div>${esc(msg.detail || "Unknown error.")}</div>`;
      foot.appendChild(button("ghostb", "Close", () => { send("cancel"); close(); }));
    } else if (msg.phase === "close") {
      close();
    }
  }

  chrome.runtime.onMessage.addListener((msg) => {
    if (msg && msg.type === "dc-overlay") render(msg);
  });
  window.__dcOverlay = {
    reset: () => render({ phase: "progress", label: "Distilling knowledge…", pct: 2 }),
  };
  window.__dcOverlay.reset();
}

async function showOverlay(tabId, msg) {
  try { await chrome.tabs.sendMessage(tabId, { type: "dc-overlay", ...msg }); } catch {}
}

// ------------------------------ save flow --------------------------------
// One flow per tab: capture → overlay progress → save → poll until the
// server's distillation lands → in-page review (summary + units).

const flows = new Map();   // tabId -> { page, options, docId }

async function runSaveFlow(tabId, options = {}) {
  let page;
  try {
    page = await captureTab(tabId);
    await chrome.scripting.executeScript({ target: { tabId }, func: dcOverlayMain });
  } catch {
    badge(tabId, "✗", "#b3261e");   // page we can't script (chrome:// etc.)
    return;
  }
  if (options.selectionOnly && page.selection && page.selection.trim()) {
    const escaped = page.selection.replace(/&/g, "&amp;").replace(/</g, "&lt;");
    page = {
      ...page,
      html: `<html><head><title>${page.title} (selection)</title></head><body><article><p>${
        escaped.split(/\n\s*\n/).join("</p><p>")}</p></article></body></html>`,
    };
  }
  flows.set(tabId, { page, options });
  await startSave(tabId, false);
}

async function startSave(tabId, overwrite) {
  const flow = flows.get(tabId);
  if (!flow) return;
  await showOverlay(tabId, { phase: "progress", pct: 4 });
  let status, body;
  try {
    ({ status, body } = await savePage(flow.page, { ...flow.options, overwrite }));
  } catch {
    return showOverlay(tabId, { phase: "error",
      detail: "Your DronaCharya server is unreachable — check the extension settings." });
  }
  if (status === 202) return pollForReview(tabId);
  if (body.status === "unchanged")
    return showOverlay(tabId, { phase: "done", label: "✓ Already in your knowledge base",
      detail: body.message || "" });
  if (status === 409)
    return showOverlay(tabId, { phase: "consent",
      old: (body.old_summary || "").slice(0, 220),
      now: (body.new_preview || "").slice(0, 220) });
  if (status === 401)
    return showOverlay(tabId, { phase: "error",
      detail: "Invalid token — open the extension popup and fix it in Settings." });
  return showOverlay(tabId, { phase: "error",
    detail: body.message || ("HTTP " + status) });
}

async function pollForReview(tabId) {
  const flow = flows.get(tabId);
  if (!flow) return;
  const cfg = await settings();
  const base = cfg.serverUrl.replace(/\/$/, "");
  const MAX = 120;                                    // up to ~4 min
  for (let i = 0; i < MAX; i++) {
    await new Promise((r) => setTimeout(r, 2000));
    await showOverlay(tabId, { phase: "progress",
      pct: Math.min(95, 5 + i * 2),
      detail: i < 45 ? undefined
        : "Still distilling — a long page, or other saves are queued ahead." });
    try {
      const r = await fetch(base + "/api/v1/documents/lookup?" +
        new URLSearchParams({ url: flow.page.url }), { headers: apiHeaders(cfg, false) });
      if (r.status === 502) {                         // background save died
        const body = await r.json().catch(() => ({}));
        return showOverlay(tabId, { phase: "error",
          detail: "The server failed to save this page: " +
                  (body.error || "unknown error") + " — try saving again." });
      }
      if (!r.ok) continue;
      const doc = await r.json();
      if (!doc.distilled && i < MAX - 1) continue;
      const full = await (await fetch(base + "/api/v1/documents/" + doc.id,
        { headers: apiHeaders(cfg, false) })).json();
      flow.docId = doc.id;
      flow.summary = full.summary || "";
      flow.unitTexts = (full.units || []).map((u) => u.text);
      return showOverlay(tabId, { phase: "review",
        summary: flow.summary, units: full.units || [] });
    } catch { /* server briefly busy */ }
  }
  return showOverlay(tabId, { phase: "done", label: "✓ Saved",
    detail: "Distillation is still running — review it later in the Library." });
}

async function handleReviewAction(tabId, msg) {
  const flow = flows.get(tabId);
  if (!flow) return;
  const cfg = await settings();
  const base = cfg.serverUrl.replace(/\/$/, "");
  if (msg.action === "overwrite") return startSave(tabId, true);
  if (msg.action === "cancel") { flows.delete(tabId); return; }
  if (msg.action === "discard") {
    if (flow.docId)
      await fetch(base + "/api/v1/documents/" + flow.docId,
        { method: "DELETE", headers: apiHeaders(cfg, false) }).catch(() => {});
    flows.delete(tabId);
    return showOverlay(tabId, { phase: "done", label: "Save discarded — nothing kept" });
  }
  if (msg.action === "keep") {
    try {
      if (flow.docId && msg.summary && msg.summary !== flow.summary)
        await fetch(base + "/api/v1/documents/" + flow.docId, {
          method: "PATCH", headers: apiHeaders(cfg),
          body: JSON.stringify({ summary: msg.summary }),
        });
      const unitsChanged = Array.isArray(msg.units) && msg.units.length
        && (msg.units.length !== (flow.unitTexts || []).length
            || msg.units.some((u, i) => u.text !== flow.unitTexts[i]));
      if (flow.docId && unitsChanged)
        await fetch(base + "/api/v1/documents/" + flow.docId + "/units", {
          method: "PUT", headers: apiHeaders(cfg),
          body: JSON.stringify({ units: msg.units }),
        });
    } catch {}
    flows.delete(tabId);
    return showOverlay(tabId, { phase: "done", label: "✓ In your knowledge base" });
  }
}

function badge(tabId, text, color) {
  chrome.action.setBadgeText({ tabId, text });
  chrome.action.setBadgeBackgroundColor({ tabId, color });
  setTimeout(() => chrome.action.setBadgeText({ tabId, text: "" }), 4000);
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "dc-save",
    title: "Save page to DronaCharya",
    contexts: ["page", "selection"],
  });
  chrome.alarms.create("dc-todo-refresh", { periodInMinutes: 30 });
  syncTodoAlarms();
});
chrome.runtime.onStartup.addListener(syncTodoAlarms);

// ------------------------------- to-do reminders -------------------------
// Every open to-do with a future due time gets a chrome alarm; when it fires
// we show a browser notification. Re-synced periodically so reminders added
// on other devices (via sync) fire here too.
const TODO_PREFIX = "dc-todo:";

async function fetchTodos() {
  const cfg = await settings();
  const r = await fetch(cfg.serverUrl.replace(/\/$/, "") + "/api/v1/todos", {
    headers: cfg.token ? { Authorization: "Bearer " + cfg.token } : {},
  });
  if (!r.ok) return [];
  return (await r.json()).todos || [];
}

async function syncTodoAlarms() {
  let todos = [];
  try { todos = await fetchTodos(); } catch { return; }
  const wanted = new Map();     // alarm name -> {when, text}
  const now = Date.now();
  todos.forEach(t => {
    const when = t.due ? Date.parse(t.due) : NaN;
    if (!Number.isNaN(when) && when > now)
      wanted.set(TODO_PREFIX + t.id, { when, text: t.text });
  });
  const existing = await chrome.alarms.getAll();
  for (const a of existing) {
    if (a.name.startsWith(TODO_PREFIX) && !wanted.has(a.name))
      await chrome.alarms.clear(a.name);
  }
  for (const [name, { when, text }] of wanted) {
    chrome.alarms.create(name, { when });
    await chrome.storage.local.set({ ["txt-" + name]: text });
  }
}

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === "dc-todo-refresh") { syncTodoAlarms(); return; }
  if (!alarm.name.startsWith(TODO_PREFIX)) return;
  const stored = await chrome.storage.local.get("txt-" + alarm.name);
  const text = stored["txt-" + alarm.name] || "You have a to-do due.";
  chrome.notifications.create(alarm.name, {
    type: "basic",
    iconUrl: "icon128.png",
    title: "DronaCharya reminder",
    message: text,
    priority: 2,
  });
  chrome.storage.local.remove("txt-" + alarm.name);
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== "dc-save" || !tab?.id) return;
  runSaveFlow(tab.id, {});
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    if (msg.type === "capture") {
      sendResponse(await captureTab(msg.tabId));
    } else if (msg.type === "save-flow") {
      runSaveFlow(msg.tabId, msg.options || {});
      sendResponse({ ok: true });
    } else if (msg.type === "dc-action" && sender.tab?.id != null) {
      await handleReviewAction(sender.tab.id, msg);
      sendResponse({ ok: true });
    } else if (msg.type === "sync-todo-alarms") {
      await syncTodoAlarms();
      sendResponse({ ok: true });
    }
  })();
  return true; // async response
});
