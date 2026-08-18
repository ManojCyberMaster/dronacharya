// Shared app shell: sidebar, token, modals, toasts, document viewer/editor,
// tag suggestions. Every page includes this before its own script.
(function () {
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  function headers(json = true) {
    const h = json ? { "Content-Type": "application/json" } : {};
    const t = localStorage.getItem("dc_token");
    if (t) h["Authorization"] = "Bearer " + t;
    return h;
  }

  // ---------------------------------------------------------------- shell --
  const ICONS = {
    ask: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>',
    library: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>',
    tags: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>',
    mindmap: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="2.5"/><circle cx="4" cy="5" r="2"/><circle cx="20" cy="5" r="2"/><circle cx="4" cy="19" r="2"/><circle cx="20" cy="19" r="2"/><path d="M6 6l4 4M18 6l-4 4M6 18l4-4M18 18l-4-4"/></svg>',
    graph: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="5" cy="6" r="2.5"/><circle cx="19" cy="6" r="2.5"/><circle cx="12" cy="18" r="2.5"/><path d="M7.5 6h9M6.3 8.2l4.4 7.6M17.7 8.2l-4.4 7.6"/></svg>',
    todos: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="17" rx="3"/><path d="M8 12.5l2.6 2.6L16.5 9"/></svg>',
    key: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2l-2 2m-7.6 7.6a5.5 5.5 0 1 1-7.78 7.78 5.5 5.5 0 0 1 7.78-7.78zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/></svg>',
    collapse: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 17l-5-5 5-5M18 17l-5-5 5-5"/></svg>',
    expand: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 17l5-5-5-5M6 17l5-5-5-5"/></svg>',
  };
  const NAV = [
    ["ask", "/", "Ask"], ["library", "/library", "Library"],
    ["tags", "/tags", "Tags"], ["mindmap", "/mindmap", "Mind maps"],
    ["graph", "/graph", "Graph"], ["todos", "/todos", "To-dos"],
  ];

  if (localStorage.getItem("dc_side_min") === "1")
    document.body.classList.add("side-min");

  function buildShell() {
    const page = document.body.dataset.page || "ask";
    const aside = document.createElement("aside");
    aside.className = "side";
    aside.innerHTML = `
      <div class="brand"><span class="bow">🏹</span>
        <span class="name"><span class="grad">Drona</span>Charya</span></div>
      <nav>${NAV.map(([k, href, label]) =>
        `<a href="${href}" class="${k === page ? "active" : ""}" title="${label}">
           ${ICONS[k]}<span>${label}</span></a>`).join("")}
      </nav>
      <div class="foot">
        <button id="dc-tokenbtn" title="API token">${ICONS.key}
          <span>API token</span><span class="dot" id="dc-dot"></span></button>
        <button id="dc-collapse" title="Collapse sidebar">
          <span class="ico-min">${ICONS.collapse}</span>
          <span class="ico-max">${ICONS.expand}</span><span>Collapse</span></button>
      </div>`;
    document.body.prepend(aside);
    document.getElementById("dc-tokenbtn").onclick = tokenModal;
    document.getElementById("dc-collapse").onclick = () => {
      const min = document.body.classList.toggle("side-min");
      localStorage.setItem("dc_side_min", min ? "1" : "0");
      window.dispatchEvent(new Event("resize"));
    };
    updateTokenDot();
  }

  // the dot is a warning, not decoration: shown only while no token is set
  function updateTokenDot() {
    const dot = document.getElementById("dc-dot");
    if (dot) dot.style.display = localStorage.getItem("dc_token") ? "none" : "";
  }

  // ---------------------------------------------------------------- modal --
  function modal(title, bodyEl, opts = {}) {
    const overlay = document.createElement("div");
    overlay.className = "dc-overlay";
    const box = document.createElement("div");
    box.className = "dc-modal" + (opts.small ? " small" : "");
    const head = document.createElement("div");
    head.className = "dc-modal-head";
    head.innerHTML = `<div class="t"></div>`;
    head.querySelector(".t").append(title instanceof Node ? title : document.createTextNode(title));
    const close = document.createElement("button");
    close.className = "ghost icon"; close.textContent = "✕";
    close.onclick = () => dismiss();
    head.appendChild(close);
    const body = document.createElement("div");
    body.className = "dc-modal-body";
    if (typeof bodyEl === "string") body.innerHTML = bodyEl;
    else body.appendChild(bodyEl);
    box.append(head, body);
    overlay.appendChild(box);
    function dismiss() {
      overlay.remove();
      document.removeEventListener("keydown", onKey);
      if (opts.onClose) opts.onClose();
    }
    function onKey(e) { if (e.key === "Escape") dismiss(); }
    overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) dismiss(); });
    document.addEventListener("keydown", onKey);
    document.body.appendChild(overlay);
    return { close: dismiss, body, head };
  }

  function toast(msg, type = "ok") {
    let holder = document.getElementById("dc-toasts");
    if (!holder) {
      holder = document.createElement("div");
      holder.id = "dc-toasts";
      document.body.appendChild(holder);
    }
    const t = document.createElement("div");
    t.className = "dc-toast" + (type === "error" ? " error" : "");
    t.textContent = msg;
    holder.appendChild(t);
    setTimeout(() => t.remove(), 3600);
  }

  // styled replacement for window.prompt(): small modal, Enter submits
  function promptModal(title, opts = {}) {
    const wrap = document.createElement("div");
    wrap.innerHTML = `
      <div class="muted" id="dc-prompt-label" style="margin-bottom:8px; display:none"></div>
      <div style="position:relative">
        <input id="dc-prompt-in" type="text" autocomplete="off"></div>
      <div class="row" style="margin-top:12px; justify-content:flex-end">
        <button class="secondary" id="dc-prompt-cancel">Cancel</button>
        <button id="dc-prompt-ok">${esc(opts.okText || "OK")}</button></div>`;
    if (opts.label) {
      const l = wrap.querySelector("#dc-prompt-label");
      l.textContent = opts.label; l.style.display = "block";
    }
    const m = modal(title, wrap, { small: true });
    const input = wrap.querySelector("#dc-prompt-in");
    input.value = opts.value || "";
    input.placeholder = opts.placeholder || "";
    if (opts.suggestTags) attachTagSuggest(input, (t) => { input.value = t; });
    setTimeout(() => { input.focus(); input.select(); }, 30);
    const ok = () => {
      const v = input.value.trim();
      if (!v && !opts.allowEmpty) return;
      m.close();
      if (opts.onOk) opts.onOk(v);
    };
    wrap.querySelector("#dc-prompt-ok").onclick = ok;
    wrap.querySelector("#dc-prompt-cancel").onclick = () => m.close();
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") ok(); });
  }

  function tokenModal() {
    const wrap = document.createElement("div");
    wrap.innerHTML = `
      <div class="muted" style="margin-bottom:10px">Paste the bearer token from
        <code>config.toml</code> → <code>[server]</code>. Stored only in this browser.</div>
      <input id="dc-token-in" type="password" autocomplete="off" placeholder="API token">
      <div class="row" style="margin-top:12px; justify-content:flex-end">
        <button id="dc-token-save">Save</button></div>`;
    const m = modal("API token", wrap, { small: true });
    const input = wrap.querySelector("#dc-token-in");
    input.value = localStorage.getItem("dc_token") || "";
    input.focus();
    const save = () => {
      localStorage.setItem("dc_token", input.value.trim());
      m.close(); toast("Token saved");
      updateTokenDot();
      if (DC.onToken) DC.onToken();
    };
    wrap.querySelector("#dc-token-save").onclick = save;
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") save(); });
  }

  // ------------------------------------------------- tag suggestions -------
  // One tag namespace app-wide: every tag input suggests from /api/v1/tags.
  let tagCache = null;
  async function knownTags(force = false) {
    if (tagCache === null || force) {
      try {
        const r = await fetch("/api/v1/tags", { headers: headers(false) });
        tagCache = r.ok ? (await r.json()).tags.map(t => t.name) : [];
      } catch { tagCache = []; }
    }
    return tagCache;
  }

  // Dropdown under `input`; calls onPick(tag). Suggests after `minChars`.
  function attachTagSuggest(input, onPick, minChars = 3) {
    const holder = input.parentElement;
    if (getComputedStyle(holder).position === "static")
      holder.style.position = "relative";
    const dd = document.createElement("div");
    dd.className = "dc-suggest";
    holder.appendChild(dd);
    const hide = () => { dd.style.display = "none"; };
    input.addEventListener("input", async () => {
      const needle = input.value.trim().toLowerCase();
      if (needle.length < minChars) { hide(); return; }
      const tags = await knownTags();
      const matches = tags.filter(t => t.toLowerCase().includes(needle)).slice(0, 8);
      dd.innerHTML = "";
      matches.forEach(t => {
        const row = document.createElement("div");
        row.textContent = t;
        row.onmousedown = (e) => { e.preventDefault(); onPick(t); hide(); };
        dd.appendChild(row);
      });
      dd.style.display = matches.length ? "block" : "none";
    });
    input.addEventListener("blur", () => setTimeout(hide, 150));
    input.addEventListener("keydown", (e) => { if (e.key === "Escape") hide(); });
    return { hide };
  }

  // ------------------------------------------------- document view/editor --
  // Shows a document with its knowledge units; units are editable/deletable
  // (mind maps redirect to the editor). Used by Library and Tags pages.
  async function openDocument(id, opts = {}) {
    const resp = await fetch(`/api/v1/documents/${id}`, { headers: headers(false) });
    if (!resp.ok) { toast("Could not load the document", "error"); return; }
    const doc = await resp.json();
    const changed = () => opts.onChange && opts.onChange();
    // server-declared capabilities; fall back for pre-capabilities servers
    const caps = doc.capabilities
      || { editable_units: doc.source_type !== "mindmap",
           editor: doc.source_type === "mindmap" ? "mindmap" : null };
    const isMap = caps.editor === "mindmap";
    const ownedElsewhere = !caps.editable_units;

    const wrap = document.createElement("div");
    const origin = doc.url || doc.file_path || "";
    wrap.innerHTML = `
      <div class="row wrap" style="margin-bottom:6px">
        <span class="badge plain">${esc(doc.source_type)}</span>
        <span id="dv-tags"></span>
        <span class="doc-actions">
          <button class="danger" id="dv-del">Delete document</button></span>
      </div>
      ${origin ? `<div class="muted" style="margin-bottom:8px">${
        doc.url ? `<a href="${esc(doc.url)}" target="_blank" rel="noopener"
                     style="color:var(--accent)">${esc(origin)}</a>` : esc(origin)}</div>` : ""}
      ${doc.summary ? `<div class="muted" style="margin-bottom:12px">${esc(doc.summary)}</div>` : ""}
      <div id="dv-units"></div>
      <div class="faint" style="margin-top:8px">${doc.units.length} knowledge
        item${doc.units.length === 1 ? "" : "s"}${isMap ? "" :
        " — hover one to edit or remove it"}.</div>`;

    const m = modal((isMap ? "MindMap: " : "") + (doc.title || "(untitled)"), wrap);

    // tags editor (chips + add); mind-map tags are owned by the map itself
    const tagsEl = wrap.querySelector("#dv-tags");
    function renderTags() {
      tagsEl.innerHTML = "";
      doc.tags.forEach((t, i) => {
        const chip = document.createElement("span");
        chip.className = "chip";
        chip.innerHTML = isMap ? esc(t) : `${esc(t)} <b title="remove tag">×</b>`;
        if (!isMap) chip.querySelector("b").onclick = async () => {
          doc.tags.splice(i, 1);
          await saveTags();
        };
        tagsEl.appendChild(chip);
      });
      if (isMap) return;
      const add = document.createElement("button");
      add.className = "ghost"; add.style.fontSize = "12px";
      add.textContent = "+ tag";
      add.onclick = () => {           // inline input, right where the chip goes
        const holder = document.createElement("span");
        holder.style.cssText = "position:relative; display:inline-block";
        const input = document.createElement("input");
        input.type = "text";
        input.placeholder = "new tag…";
        input.style.cssText = "width:160px; padding:4px 10px; font-size:12.5px";
        holder.appendChild(input);
        add.replaceWith(holder);
        const commit = async (t) => {
          t = (t ?? input.value).trim();
          if (t && !doc.tags.includes(t)) { doc.tags.push(t); await saveTags(); }
          else renderTags();
        };
        attachTagSuggest(input, commit);
        input.focus();
        input.addEventListener("keydown", (e) => {
          if (e.key === "Enter") commit();
          if (e.key === "Escape") renderTags();
        });
        input.addEventListener("blur", () =>
          setTimeout(() => { if (holder.isConnected) commit(); }, 160));
      };
      tagsEl.appendChild(add);
    }
    async function saveTags() {
      await fetch(`/api/v1/documents/${id}`, {
        method: "PATCH", headers: headers(),
        body: JSON.stringify({ tags: doc.tags }) });
      knownTags(true);
      renderTags(); changed();
    }
    renderTags();

    // units
    const unitsEl = wrap.querySelector("#dv-units");
    const editable = !ownedElsewhere;
    if (!editable) {
      const page = caps.editor === "todo" ? "/todos" : "/" + caps.editor;
      const what = caps.editor === "todo" ? "the To-dos page" : "the mind-map editor";
      unitsEl.innerHTML = `<div class="muted" style="margin-bottom:10px">
        This knowledge is owned by its own editor — change it in
        <a href="${page}" style="color:var(--accent)">${what}</a>;
        every change re-enters the corpus automatically.</div>`;
    }
    async function putUnits() {
      const resp = await fetch(`/api/v1/documents/${id}/units`, {
        method: "PUT", headers: headers(),
        body: JSON.stringify({ units: doc.units }) });
      if (!resp.ok) {
        const detail = (await resp.json().catch(() => ({}))).detail;
        toast(detail || "Save failed", "error");
        return false;
      }
      toast("Saved — re-embedded into your knowledge base");
      changed();
      return true;
    }
    function renderUnits() {
      unitsEl.querySelectorAll(".unit").forEach(el => el.remove());
      doc.units.forEach((u, i) => {
        const el = document.createElement("div");
        el.className = "unit";
        el.innerHTML = `
          <div class="u-head">
            <span class="badge plain">${esc(u.kind || "note")}</span>
            <span class="u-path">${esc(u.heading_path || "")}</span>
            ${editable ? `<span class="u-tools">
              <button class="ghost icon" title="edit">✎</button>
              <button class="ghost icon" title="remove">✕</button></span>` : ""}
          </div>
          <div class="u-text">${esc(u.text)}</div>`;
        if (editable) {
          const [editBtn, delBtn] = el.querySelectorAll(".u-tools button");
          editBtn.onclick = () => {
            const area = document.createElement("textarea");
            area.value = u.text;
            const bar = document.createElement("div");
            bar.className = "row";
            bar.style.marginTop = "8px";
            bar.innerHTML = `<button>Save</button>
                             <button class="secondary">Cancel</button>`;
            el.querySelector(".u-text").replaceWith(area);
            el.appendChild(bar);
            area.focus();
            bar.children[0].onclick = async () => {
              const text = area.value.trim();
              if (!text) return;
              const prev = u.text;
              u.text = text;
              if (await putUnits()) renderUnits();
              else { u.text = prev; renderUnits(); }
            };
            bar.children[1].onclick = () => renderUnits();
          };
          delBtn.onclick = async () => {
            if (doc.units.length === 1) {
              toast("Last item — delete the whole document instead", "error");
              return;
            }
            if (!confirm("Remove this knowledge item?")) return;
            const removed = doc.units.splice(i, 1);
            if (await putUnits()) renderUnits();
            else { doc.units.splice(i, 0, removed[0]); renderUnits(); }
          };
        }
        unitsEl.appendChild(el);
      });
    }
    renderUnits();

    wrap.querySelector("#dv-del").onclick = async () => {
      if (!confirm(`Delete "${doc.title}" and all its knowledge?`)) return;
      await fetch(`/api/v1/documents/${id}`, { method: "DELETE",
                                               headers: headers(false) });
      m.close(); toast("Document deleted"); changed();
    };
  }

  window.DC = { headers, esc, modal, toast, openDocument, promptModal,
                knownTags, attachTagSuggest, onToken: null };
  buildShell();
})();
