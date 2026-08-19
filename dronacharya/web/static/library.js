// Library — browse, open, edit and delete saved knowledge. Shell in shell.js.
const { headers, esc, toast, openDocument, req } = window.DC;

const list = document.getElementById("list");
const tagsEl = document.getElementById("tags");
let activeTag = null;

async function loadTags() {
  const r = await req("/api/v1/tags", { quiet: true });
  if (!r.ok) return;
  const tags = r.data.tags || [];
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
      <span class="doc-title">${(d.capabilities?.editor || d.source_type) === "mindmap" ? "MindMap: " : ""}${esc(d.title)}</span>
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
    const r = await req(`/api/v1/documents/${d.id}`, { method: "DELETE" });
    if (!r.ok) return;          // never announce a delete the server refused
    toast("Document deleted");
    loadDocs(); loadTags();
  };
  return card;
}

const PAGE = 200;
let loaded = [];          // everything fetched so far, in server order
let moreAvailable = false;
let loadSeq = 0;          // guards against an older response landing last

async function loadDocs(append = false) {
  const seq = ++loadSeq;
  const params = new URLSearchParams({ limit: String(PAGE) });
  if (append) params.set("offset", String(loaded.length));
  if (activeTag) params.set("tag", activeTag);
  const r = await req("/api/v1/documents?" + params, { quiet: true });
  if (seq !== loadSeq) return;          // a newer request already answered
  if (!r.ok) {
    if (r.auth)
      list.innerHTML = '<div class="card">Invalid token — set it from the sidebar.</div>';
    else if (!append)
      list.innerHTML = `<div class="card">Could not load your library — ${esc(r.error)}</div>`;
    else toast(r.error, "error");
    return;
  }
  const documents = r.data.documents || [];
  moreAvailable = documents.length === PAGE;
  loaded = append ? loaded.concat(documents) : documents;
  render();
}

function render() {
  const needle = document.getElementById("filter").value.trim().toLowerCase();
  list.innerHTML = "";
  const shown = loaded
    .filter(d => !needle || (d.title + " " + (d.summary || "")).toLowerCase().includes(needle));
  shown.forEach(d => list.appendChild(docCard(d)));
  // say plainly that this is a window onto a larger set — reporting the
  // clipped number as the total hid every document past the first page
  document.getElementById("lib-count").textContent =
    `${shown.length}${moreAvailable ? "+" : ""}${activeTag ? " in " + activeTag : ""}`;
  if (!list.children.length)
    list.innerHTML = '<div class="card muted">Nothing here yet — save a page or run dc sync-notes.</div>';
  if (moreAvailable) {
    const more = document.createElement("button");
    more.className = "ghost";
    more.style.cssText = "margin:12px auto; display:block";
    more.textContent = `Load ${PAGE} more`;
    more.onclick = () => { more.disabled = true; loadDocs(true); };
    list.appendChild(more);
  }
}

document.getElementById("refresh").onclick = () => { loadDocs(); loadTags(); };
// filtering is over what is already loaded — no refetch, no flicker
document.getElementById("filter").addEventListener("input", () => render());
document.getElementById("export").onclick = async () => {
  let resp;
  try {
    resp = await fetch("/api/v1/export", { headers: headers(false) });
  } catch {
    toast("Export failed — server unreachable", "error"); return;
  }
  if (!resp.ok) {   // otherwise the JSON error body downloads AS the zip
    toast(resp.status === 401 ? "Not signed in — set your token from the sidebar"
                              : `Export failed (${resp.status})`, "error");
    return;
  }
  const blob = await resp.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "dronacharya-export.zip";
  a.click();
};
window.DC.onToken = () => { loadDocs(); loadTags(); };

loadDocs(); loadTags();


// ------------------------------ file upload -------------------------------
// AbstractSpoon .tdl, notes, PDFs, Office files — same parsers as `dc add`.
const uploadInput = document.getElementById("upload-input");
const dropzone = document.getElementById("dropzone");

async function uploadFiles(fileList) {
  const files = [...fileList];
  if (!files.length) return;
  toast(`Uploading ${files.length} file${files.length > 1 ? "s" : ""}…`);
  const form = new FormData();
  files.forEach(f => form.append("files", f));
  let resp;
  try {
    resp = await fetch("/api/v1/upload", {
      method: "POST", headers: headers(false), body: form });
  } catch {
    toast("Upload failed — server unreachable", "error");
    return;
  }
  if (!resp.ok) {
    toast(resp.status === 413 ? "File too large (25 MB limit)"
          : "Upload failed (" + resp.status + ")", "error");
    return;
  }
  const { results } = await resp.json();
  results.forEach(r => {
    const good = ["created", "updated", "unchanged"].includes(r.status);
    toast(`${r.file}: ${r.status}${r.message && !good ? " — " + r.message : ""}`,
          good ? "ok" : "error");
  });
  loadDocs(); loadTags();
}

document.getElementById("upload").onclick = () => uploadInput.click();
uploadInput.onchange = () => { uploadFiles(uploadInput.files); uploadInput.value = ""; };

let dragDepth = 0;
document.addEventListener("dragenter", (e) => {
  if ([...(e.dataTransfer?.types || [])].includes("Files")) {
    dragDepth++; dropzone.classList.add("show");
  }
});
document.addEventListener("dragleave", () => {
  if (--dragDepth <= 0) { dragDepth = 0; dropzone.classList.remove("show"); }
});
document.addEventListener("dragover", (e) => e.preventDefault());
document.addEventListener("drop", (e) => {
  e.preventDefault();
  dragDepth = 0; dropzone.classList.remove("show");
  if (e.dataTransfer?.files?.length) uploadFiles(e.dataTransfer.files);
});


document.getElementById("newnote").onclick = () =>
  window.DC.openNoteEditor({ onSaved: () => { loadDocs(); loadTags(); } });
