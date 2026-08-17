// Library — browse, open, edit and delete saved knowledge. Shell in shell.js.
const { headers, esc, toast, openDocument } = window.DC;

const list = document.getElementById("list");
const tagsEl = document.getElementById("tags");
let activeTag = null;

async function loadTags() {
  const resp = await fetch("/api/v1/tags", { headers: headers(false) });
  if (!resp.ok) return;
  const { tags } = await resp.json();
  tagsEl.innerHTML = "";
  tags.sort((a, b) => b.count - a.count).forEach(({ name, count }) => {
    const span = document.createElement("span");
    span.className = "badge";
    span.style.cursor = "pointer";
    span.textContent = `${name} (${count})`;
    if (name === activeTag) span.style.outline = "2px solid var(--accent)";
    span.onclick = () => { activeTag = activeTag === name ? null : name; loadDocs(); loadTags(); };
    tagsEl.appendChild(span);
  });
}

function docCard(d) {
  const card = document.createElement("div");
  card.className = "card";
  const origin = d.url || d.file_path || "";
  const link = d.url ? `<a href="${esc(d.url)}" target="_blank" rel="noopener"
                          style="color:var(--accent)">${esc(origin)}</a>` : esc(origin);
  card.innerHTML = `
    <div class="row">
      <span class="doc-title">${d.source_type === "mindmap" ? "MindMap: " : ""}${esc(d.title)}</span>
      <span class="badge plain">${esc(d.source_type)}</span>
      ${d.distilled ? "" : '<span class="badge warn" title="fallback excerpts — run dc redistill">undistilled</span>'}
      <div class="doc-actions">
        <button class="ghost open">Open</button>
        <button class="danger del">Delete</button></div>
    </div>
    <div class="muted">${esc(d.summary || "")}</div>
    <div class="faint" style="margin-top:6px">${d.tags.map(t => `<span class="badge">${esc(t)}</span>`).join("")}
      saved ${d.created_at.slice(0, 10)} · <span class="src">${link}</span></div>`;
  const open = () => openDocument(d.id, { onChange: () => { loadDocs(); loadTags(); } });
  card.querySelector(".doc-title").onclick = open;
  card.querySelector(".open").onclick = open;
  card.querySelector(".del").onclick = async () => {
    if (!confirm(`Delete "${d.title}" from your knowledge base?`)) return;
    await fetch(`/api/v1/documents/${d.id}`, { method: "DELETE", headers: headers(false) });
    toast("Document deleted");
    loadDocs(); loadTags();
  };
  return card;
}

async function loadDocs() {
  const params = new URLSearchParams({ limit: "200" });
  if (activeTag) params.set("tag", activeTag);
  const resp = await fetch("/api/v1/documents?" + params, { headers: headers(false) });
  if (resp.status === 401) { list.innerHTML = '<div class="card">Invalid token — set it from the sidebar.</div>'; return; }
  const { documents } = await resp.json();
  const needle = document.getElementById("filter").value.trim().toLowerCase();
  list.innerHTML = "";
  const shown = documents
    .filter(d => !needle || (d.title + " " + (d.summary || "")).toLowerCase().includes(needle));
  shown.forEach(d => list.appendChild(docCard(d)));
  document.getElementById("lib-count").textContent =
    `${shown.length}${activeTag ? " in " + activeTag : ""}`;
  if (!list.children.length) list.innerHTML = '<div class="card muted">Nothing here yet — save a page or run dc sync-notes.</div>';
}

document.getElementById("refresh").onclick = () => { loadDocs(); loadTags(); };
document.getElementById("filter").addEventListener("input", () => loadDocs());
document.getElementById("export").onclick = async () => {
  const resp = await fetch("/api/v1/export", { headers: headers(false) });
  const blob = await resp.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "dronacharya-export.zip";
  a.click();
};
window.DC.onToken = () => { loadDocs(); loadTags(); };

loadDocs(); loadTags();
