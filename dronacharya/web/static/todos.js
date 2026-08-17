// To-dos — tiny documents (source_type="todo") in the knowledge base:
// synced, searchable, exported/wiped like everything else.
const { headers, esc, toast } = window.DC;

const listEl = document.getElementById("td-list");
const showDone = document.getElementById("td-show-done");

function dueLabel(due) {
  if (!due) return "";
  const ts = Date.parse(due);
  if (Number.isNaN(ts)) return "";
  const late = ts < Date.now();
  const txt = new Date(ts).toLocaleString([], { month: "short", day: "numeric",
                                                hour: "2-digit", minute: "2-digit" });
  return `<span class="badge ${late ? "warn" : "plain"}" title="${late ? "overdue" : "reminder"}">
            ⏰ ${esc(txt)}</span>`;
}

async function load() {
  let resp;
  try {
    resp = await fetch("/api/v1/todos?" + new URLSearchParams(
      { include_done: showDone.checked ? "true" : "false" }),
      { headers: headers(false) });
  } catch { listEl.innerHTML = '<div class="card muted">Server unreachable.</div>'; return; }
  if (resp.status === 401) {
    listEl.innerHTML = '<div class="card muted">Invalid token — set it from the sidebar.</div>';
    return;
  }
  const { todos } = await resp.json();
  listEl.innerHTML = todos.length ? "" :
    '<div class="card muted">Nothing pending 🎉 — add one above.</div>';
  todos.forEach(t => {
    const card = document.createElement("div");
    card.className = "card td-item" + (t.done ? " done" : "");
    card.innerHTML = `
      <div class="row">
        <input type="checkbox" ${t.done ? "checked" : ""} title="${t.done ? "mark open" : "mark done"}"
               style="width:auto; transform:scale(1.15)">
        <span class="td-text" style="flex:1; min-width:0"></span>
        ${dueLabel(t.due)}
        <span class="doc-actions">
          <button class="ghost icon" title="edit">✎</button>
          <button class="danger" title="delete">✕</button></span>
      </div>`;
    card.querySelector(".td-text").textContent = t.text;
    if (t.done) card.querySelector(".td-text").style.textDecoration = "line-through";
    card.querySelector("input[type=checkbox]").onchange = async (e) => {
      await fetch(`/api/v1/todos/${t.id}`, { method: "PATCH", headers: headers(),
        body: JSON.stringify({ done: e.target.checked }) });
      load();
    };
    card.querySelector(".ghost").onclick = () => {   // inline edit, in place
      const span = card.querySelector(".td-text");
      const input = document.createElement("input");
      input.type = "text";
      input.value = t.text;
      input.style.cssText = "flex:1; min-width:0";
      span.replaceWith(input);
      input.focus(); input.select();
      let settled = false;
      const commit = async () => {
        if (settled) return;
        settled = true;
        const text = input.value.trim();
        if (text && text !== t.text) {
          await fetch(`/api/v1/todos/${t.id}`, { method: "PATCH",
            headers: headers(), body: JSON.stringify({ text }) });
        }
        load();
      };
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") commit();
        if (e.key === "Escape") { settled = true; load(); }
      });
      input.addEventListener("blur", commit);
    };
    card.querySelector(".danger").onclick = async () => {
      await fetch(`/api/v1/documents/${t.id}`, { method: "DELETE",
                                                 headers: headers(false) });
      toast("To-do deleted");
      load();
    };
    listEl.appendChild(card);
  });
}

async function add() {
  const text = document.getElementById("td-text").value.trim();
  if (!text) return;
  const dueLocal = document.getElementById("td-due").value;
  const due = dueLocal ? new Date(dueLocal).toISOString() : null;
  document.getElementById("td-text").value = "";
  document.getElementById("td-due").value = "";
  const r = await fetch("/api/v1/todos", { method: "POST", headers: headers(),
    body: JSON.stringify({ text, due }) });
  if (!r.ok) { toast("Could not add — check the token", "error"); return; }
  load();
}
document.getElementById("td-add").onclick = add;
document.getElementById("td-text").addEventListener("keydown",
  (e) => { if (e.key === "Enter") add(); });
showDone.onchange = load;
window.DC.onToken = load;
load();
