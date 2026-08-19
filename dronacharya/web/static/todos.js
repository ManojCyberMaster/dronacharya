// To-dos — tiny documents (source_type="todo") in the knowledge base:
// synced, searchable, exported/wiped like everything else.
const { headers, esc, toast, req } = window.DC;

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
  const r = await req("/api/v1/todos?" + new URLSearchParams(
    { include_done: showDone.checked ? "true" : "false" }), { quiet: true });
  if (!r.ok) {
    listEl.innerHTML = `<div class="card muted">${
      r.auth ? "Invalid token — set it from the sidebar."
             : "Could not load your to-dos — " + esc(r.error)}</div>`;
    return;
  }
  const todos = r.data.todos || [];
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
      const want = e.target.checked;
      const res = await req(`/api/v1/todos/${t.id}`, { method: "PATCH",
        body: JSON.stringify({ done: want }) });
      // on failure the box used to silently un-tick itself on reload, with no
      // hint that anything had gone wrong
      if (!res.ok) { e.target.checked = !want; return; }
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
          const res = await req(`/api/v1/todos/${t.id}`, { method: "PATCH",
            body: JSON.stringify({ text }) });
          if (!res.ok) {   // keep the typed text on screen so it isn't lost
            settled = false;
            input.focus();
            return;
          }
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
      const res = await req(`/api/v1/documents/${t.id}`, { method: "DELETE" });
      if (!res.ok) return;
      toast("To-do deleted");
      load();
    };
    listEl.appendChild(card);
  });
}

let adding = false;
async function add() {
  if (adding) return;
  const textEl = document.getElementById("td-text");
  const dueEl = document.getElementById("td-due");
  const text = textEl.value.trim();
  if (!text) return;
  const due = dueEl.value ? new Date(dueEl.value).toISOString() : null;
  adding = true;
  const r = await req("/api/v1/todos", { method: "POST",
    body: JSON.stringify({ text, due }) });
  adding = false;
  // clear the inputs only once the to-do actually exists — clearing first
  // meant a rejected or offline add threw the typed text away silently
  if (!r.ok) return;
  textEl.value = "";
  dueEl.value = "";
  load();
}
document.getElementById("td-add").onclick = add;
document.getElementById("td-text").addEventListener("keydown",
  (e) => { if (e.key === "Enter") add(); });
showDone.onchange = load;
window.DC.onToken = load;
load();
