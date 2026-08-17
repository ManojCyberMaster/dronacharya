const $ = (id) => document.getElementById(id);
const DEFAULTS = { serverUrl: "http://127.0.0.1:8317", token: "", alwaysOverwrite: false };
let page = null;
let currentTabId = null;

async function settings() {
  return { ...DEFAULTS, ...(await chrome.storage.local.get(DEFAULTS)) };
}

function apiHeaders(cfg, json = true) {
  return {
    ...(json ? { "Content-Type": "application/json" } : {}),
    ...(cfg.token ? { Authorization: "Bearer " + cfg.token } : {}),
  };
}

async function init() {
  const cfg = await settings();
  $("serverUrl").value = cfg.serverUrl;
  $("token").value = cfg.token;
  $("alwaysOverwrite").checked = cfg.alwaysOverwrite;

  // status dot (unauthenticated endpoint)
  try {
    const r = await fetch(cfg.serverUrl.replace(/\/$/, "") + "/api/v1/status");
    if (r.ok) $("dot").classList.add("ok");
  } catch {}

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id || !/^https?:/.test(tab.url || "")) {
    $("pagetitle").textContent = "This page can't be saved.";
    $("save").disabled = true;
    return;
  }
  currentTabId = tab.id;
  page = await chrome.runtime.sendMessage({ type: "capture", tabId: tab.id });
  $("pagetitle").textContent = page.title;
  if (page.selection && page.selection.trim()) $("saveSel").style.display = "inline-block";
}

function tagsList() {
  return $("tags").value.split(",").map(t => t.trim()).filter(Boolean);
}

// ---- tag autocomplete: suggestions from your existing tags after 4 chars ----
let knownTags = null;
async function loadKnownTags() {
  if (knownTags !== null) return knownTags;
  try {
    const cfg = await settings();
    const resp = await fetch(cfg.serverUrl.replace(/\/$/, "") + "/api/v1/tags",
                             { headers: apiHeaders(cfg, false) });
    knownTags = resp.ok ? (await resp.json()).tags.map(t => t.name) : [];
  } catch { knownTags = []; }
  return knownTags;
}

$("tags").addEventListener("input", async () => {
  const parts = $("tags").value.split(",");
  const fragment = parts[parts.length - 1].trim().toLowerCase();
  const box = $("tagSuggest");
  if (fragment.length < 4) { box.style.display = "none"; return; }
  const tags = await loadKnownTags();
  const matches = tags.filter(t => t.toLowerCase().includes(fragment)).slice(0, 6);
  box.innerHTML = "";
  matches.forEach(t => {
    const row = document.createElement("div");
    row.textContent = t;
    row.style.cssText = "padding:5px 10px; cursor:pointer";
    row.onmouseenter = () => row.style.background = "#e3f0eb";
    row.onmouseleave = () => row.style.background = "";
    row.onclick = () => {
      parts[parts.length - 1] = " " + t;
      $("tags").value = parts.join(",").replace(/^ /, "");
      box.style.display = "none";
      $("tags").focus();
    };
    box.appendChild(row);
  });
  box.style.display = matches.length ? "block" : "none";
});
document.addEventListener("click", (e) => {
  if (e.target !== $("tags")) $("tagSuggest").style.display = "none";
});

// Saving hands off to the background worker, which shows the progress +
// review modal ON THE PAGE itself — so the popup just closes.
function startSaveFlow(selectionOnly) {
  if (currentTabId == null) return;
  chrome.runtime.sendMessage({
    type: "save-flow", tabId: currentTabId,
    options: { tags: tagsList(), note: $("note").value.trim(), selectionOnly },
  });
  window.close();
}

$("save").onclick = () => startSaveFlow(false);
$("saveSel").onclick = () => startSaveFlow(true);

$("saveSettings").onclick = async () => {
  const serverUrl = $("serverUrl").value.trim().replace(/\/$/, "") || DEFAULTS.serverUrl;
  // remote (non-localhost) servers need a runtime host permission grant
  if (!/^https?:\/\/(127\.0\.0\.1|localhost)([:/]|$)/.test(serverUrl)) {
    try {
      await chrome.permissions.request({ origins: [serverUrl + "/*"] });
    } catch {}
  }
  await chrome.storage.local.set({
    serverUrl, token: $("token").value.trim(),
    alwaysOverwrite: $("alwaysOverwrite").checked,
  });
  init();
};

// ------------------------------- to-dos --------------------------------
// Stored as tiny documents on your server → sync, search, export like all
// knowledge. A due time schedules a browser notification (background.js).
async function loadTodos() {
  const cfg = await settings();
  const base = cfg.serverUrl.replace(/\/$/, "");
  let todos = [];
  try {
    const r = await fetch(base + "/api/v1/todos", { headers: apiHeaders(cfg, false) });
    if (!r.ok) { $("todoList").textContent = "Server unreachable or bad token."; return; }
    todos = (await r.json()).todos;
  } catch { $("todoList").textContent = "Server unreachable."; return; }
  $("todoCount").textContent = todos.length ? `(${todos.length} open)` : "";
  const list = $("todoList");
  list.classList.remove("muted");
  list.innerHTML = todos.length ? "" : '<span class="muted">Nothing pending 🎉</span>';
  const now = Date.now();
  todos.forEach(t => {
    const row = document.createElement("div");
    row.className = "todo";
    const dueTs = t.due ? Date.parse(t.due) : null;
    row.innerHTML = `<input type="checkbox" title="mark done">
      <span class="txt"></span>
      ${dueTs ? `<span class="due ${dueTs < now ? "late" : ""}"></span>` : ""}
      <span class="x" title="delete">×</span>`;
    row.querySelector(".txt").textContent = t.text;
    if (dueTs) row.querySelector(".due").textContent =
      new Date(dueTs).toLocaleString([], { month: "short", day: "numeric",
                                           hour: "2-digit", minute: "2-digit" });
    row.querySelector("input").onchange = async () => {
      row.classList.add("done");
      await fetch(base + "/api/v1/todos/" + t.id, {
        method: "PATCH", headers: apiHeaders(cfg),
        body: JSON.stringify({ done: true }) });
      chrome.runtime.sendMessage({ type: "sync-todo-alarms" });
      setTimeout(loadTodos, 600);
    };
    row.querySelector(".x").onclick = async () => {
      await fetch(base + "/api/v1/documents/" + t.id, {
        method: "DELETE", headers: apiHeaders(cfg, false) });
      chrome.runtime.sendMessage({ type: "sync-todo-alarms" });
      loadTodos();
    };
    list.appendChild(row);
  });
}

async function addTodo() {
  const text = $("todoText").value.trim();
  if (!text) return;
  const cfg = await settings();
  const dueLocal = $("todoDue").value;    // datetime-local, empty if unset
  const due = dueLocal ? new Date(dueLocal).toISOString() : null;
  $("todoText").value = ""; $("todoDue").value = "";
  await fetch(cfg.serverUrl.replace(/\/$/, "") + "/api/v1/todos", {
    method: "POST", headers: apiHeaders(cfg),
    body: JSON.stringify({ text, due }) });
  chrome.runtime.sendMessage({ type: "sync-todo-alarms" });
  loadTodos();
}
$("todoAdd").onclick = addTodo;
$("todoText").addEventListener("keydown", (e) => { if (e.key === "Enter") addTodo(); });
loadTodos();

$("question").addEventListener("keydown", async (e) => {
  if (e.key !== "Enter") return;
  const q = $("question").value.trim();
  if (!q) return;
  const cfg = await settings();
  $("answer").textContent = "…";
  $("answerSources").textContent = "";
  const resp = await fetch(cfg.serverUrl.replace(/\/$/, "") + "/api/v1/query", {
    method: "POST", headers: apiHeaders(cfg),
    body: JSON.stringify({ question: q, mode: "kb", k: 4 }),
  });
  if (!resp.ok) { $("answer").textContent = "Query failed (HTTP " + resp.status + ")."; return; }
  $("answer").textContent = "";
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const raw = buffer.slice(0, idx); buffer = buffer.slice(idx + 2);
      const event = (raw.match(/^event: (.*)$/m) || [])[1];
      const data = (raw.match(/^data: (.*)$/m) || [])[1] || "";
      if (event === "token") $("answer").textContent += JSON.parse(data);
      else if (event === "status" && data === "no_answer")
        $("answer").textContent = "Not in your knowledge base.";
      else if (event === "status") $("answer").textContent = "No LLM provider configured.";
      else if (event === "sources") {
        const sources = JSON.parse(data);
        $("answerSources").textContent =
          sources.map((s, i) => `[${i + 1}] ${s.title}`).join("  ");
      }
    }
  }
});

init();
