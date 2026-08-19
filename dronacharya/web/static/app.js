// Ask page — streamed answers with cited sources. Shell (token, nav) in shell.js.
const { headers, esc, openDocument } = window.DC;

const thread = document.getElementById("thread");
const questionInput = document.getElementById("question");

function sourceLine(s, i) {
  const origin = s.url || s.file_path || "";
  const editor = s.capabilities ? s.capabilities.editor
    : (s.source_type === "mindmap" ? "mindmap" : null);   // pre-capabilities servers
  const a = document.createElement("a");
  a.href = "#";
  // opens the actual saved knowledge in the Library, not the raw external
  // page — the URL/path is shown as text for reference, not as the link
  a.addEventListener("click", (e) => { e.preventDefault(); openDocument(s.document_id); });
  const title = (editor === "mindmap" ? "MindMap: " : "") + s.title;
  a.textContent = `[${i + 1}] ${title}${s.heading_path ? " · " + s.heading_path : ""}${origin ? " — " + origin : ""}`;
  return a;
}

// ---- tag filter: funnel icon opens a hovering box; multiple tags supported ----
const activeTags = [];
const tagInput = document.getElementById("tagfilter");
const tagChips = document.getElementById("tagchips");
const filterBtn = document.getElementById("filterbtn");
const filterPop = document.getElementById("filterpop");
const filterCount = document.getElementById("filtercount");

function renderChips() {
  tagChips.innerHTML = "";
  activeTags.forEach((tag, i) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.innerHTML = `${esc(tag)} <b title="remove">×</b>`;
    chip.querySelector("b").onclick = () => { activeTags.splice(i, 1); renderChips(); };
    tagChips.appendChild(chip);
  });
  filterCount.textContent = activeTags.length;
  filterCount.style.display = activeTags.length ? "block" : "none";
  filterBtn.classList.toggle("on", activeTags.length > 0);
}

function addTag(tag) {
  tag = tag.trim();
  if (tag && !activeTags.includes(tag)) { activeTags.push(tag); renderChips(); }
  tagInput.value = "";
}

window.DC.attachTagSuggest(tagInput, addTag);
tagInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && tagInput.value.trim()) { e.preventDefault(); addTag(tagInput.value); }
});
filterBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  filterPop.classList.toggle("open");
  if (filterPop.classList.contains("open")) tagInput.focus();
});
document.addEventListener("click", (e) => {
  if (!filterPop.contains(e.target) && !filterBtn.contains(e.target))
    filterPop.classList.remove("open");
});

async function ask(mode) {
  const q = questionInput.value.trim();
  if (!q) return;
  questionInput.value = "";

  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `<div class="qline">Q: <b>${esc(q)}</b></div>` +
    (mode === "deeper" ? `<div class="warnbox">⚠ outside your knowledge base</div>` : "") +
    `<div class="answer"></div><div class="sources"></div><div class="faint footer" style="margin-top:8px"></div>`;
  thread.prepend(card);
  const answerEl = card.querySelector(".answer");
  const sourcesEl = card.querySelector(".sources");
  const footerEl = card.querySelector(".footer");

  let resp;
  try {
    resp = await fetch("/api/v1/query", {
      method: "POST", headers: headers(),
      body: JSON.stringify({ question: q, mode,
                             tags: activeTags.length ? activeTags : null }),
    });
  } catch (e) {
    answerEl.textContent = "Server unreachable. Is `dc serve` running?";
    return;
  }
  if (resp.status === 401) { answerEl.textContent = "Invalid token — set it from the sidebar (API token)."; return; }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let pendingSources = [];
  let gotDone = false;
  let noAnswer = false;   // status event fired: nothing was actually answered
  while (true) {
    let done, value;
    try { ({ done, value } = await reader.read()); }
    catch (e) { break; }                       // connection dropped mid-stream
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const raw = buffer.slice(0, idx); buffer = buffer.slice(idx + 2);
      const event = (raw.match(/^event: (.*)$/m) || [])[1];
      const data = (raw.match(/^data: (.*)$/m) || [])[1] || "";
      if (event === "sources") {
        pendingSources = JSON.parse(data);
      } else if (event === "token") {
        answerEl.textContent += JSON.parse(data);
      } else if (event === "status") {
        noAnswer = true;
        answerEl.textContent = data === "no_answer"
          ? "Your knowledge base doesn't cover this. Try “Deeper”."
          : "No LLM provider available — configure one in config.toml. Search still works in the Library.";
      } else if (event === "done") {
        const info = JSON.parse(data);
        gotDone = true;
        if (info.provider) footerEl.textContent = "answered by " + info.provider;
        if (info.error) footerEl.textContent += " · answer was cut short (" + info.error + ")";
        // No answer was generated at all — the retrieved candidates were
        // never read by the model, so listing them as "sources" would be a
        // lie (this was showing 8 unrelated docs alongside "doesn't cover
        // this" before the fix).
        if (noAnswer) { /* nothing to attribute */ }
        else {
          // only list sources the answer actually cited — the SERVER computes
          // the cited set (single source of truth for every client)
          const cited = new Set(info.cited || []);
          pendingSources.forEach((s, i) => {
            if (cited.has(i + 1) || (mode !== "deeper" && cited.size === 0))
              sourcesEl.appendChild(sourceLine(s, i));
          });
        }
      }
    }
  }
  if (!gotDone) {
    // stream died before `done`: never leave a partial answer unattributed
    footerEl.textContent = "connection lost mid-answer";
    if (!sourcesEl.childElementCount)
      pendingSources.forEach((s, i) => sourcesEl.appendChild(sourceLine(s, i)));
  }
}

async function findAll() {
  const q = questionInput.value.trim();
  if (!q) return;
  questionInput.value = "";

  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `<div class="qline">Find all: <b>${esc(q)}</b></div>` +
    `<div class="answer">Scanning your whole knowledge base…</div>` +
    `<div class="sources"></div><div class="faint footer" style="margin-top:8px"></div>`;
  thread.prepend(card);
  const answerEl = card.querySelector(".answer");
  const sourcesEl = card.querySelector(".sources");
  const footerEl = card.querySelector(".footer");

  let resp;
  try {
    resp = await fetch("/api/v1/find", {
      method: "POST", headers: headers(),
      body: JSON.stringify({ question: q }),
    });
  } catch (e) {
    answerEl.textContent = "Server unreachable. Is `dc serve` running?";
    return;
  }
  if (resp.status === 401) { answerEl.textContent = "Invalid token — set it from the sidebar (API token)."; return; }
  const info = await resp.json().catch(() => ({}));
  if (!resp.ok) { answerEl.textContent = info.error || "Could not run the scan."; return; }
  footerEl.textContent = `scanned ${info.scanned} unit(s) · answered by ${info.provider}`;
  if (!info.items || !info.items.length) {
    answerEl.textContent = "No matches found.";
    return;
  }
  answerEl.textContent = `Found ${info.items.length} match(es):`;
  info.items.forEach((item) => {
    const row = document.createElement("div");
    row.className = "finditem";
    const where = item.where ? ` — ${item.where}` : "";
    const src = item.source ? ` [${item.source}]` : "";
    const link = document.createElement("a");
    link.href = "#";
    link.textContent = item.document;
    link.addEventListener("click", (e) => { e.preventDefault(); openDocument(item.document_id); });
    row.appendChild(link);
    const rest = document.createElement("span");
    rest.textContent = where + src;
    row.appendChild(rest);
    const body = document.createElement("div");
    body.textContent = item.text;
    row.appendChild(body);
    sourcesEl.appendChild(row);
  });
}

document.getElementById("ask").addEventListener("click", () => ask("kb"));
document.getElementById("deeper").addEventListener("click", () => ask("deeper"));
document.getElementById("findall").addEventListener("click", () => findAll());
questionInput.addEventListener("keydown", (e) => { if (e.key === "Enter") ask("kb"); });
