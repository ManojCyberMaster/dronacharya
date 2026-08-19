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
    // favicon for every page, injected once here instead of per-HTML-file
    if (!document.querySelector('link[rel="icon"]')) {
      const icon = document.createElement("link");
      icon.rel = "icon";
      icon.type = "image/png";
      icon.href = "/static/favicon.png";
      document.head.appendChild(icon);
    }
    const page = document.body.dataset.page || "ask";
    const aside = document.createElement("aside");
    aside.className = "side";
    aside.innerHTML = `
      <div class="brand"><img class="logo" src="/static/logo.png" alt=""
          onerror="this.outerHTML='<span class=&quot;bow&quot;>🏹</span>'">
        <span class="name indic"><span class="grad">Drona</span>Charya</span></div>
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
    box.className = "dc-modal" + (opts.small ? " small" : "") + (opts.wide ? " wide" : "")
      + (opts.tall ? " tall" : "");
    const head = document.createElement("div");
    head.className = "dc-modal-head";
    head.innerHTML = `<div class="t"></div>`;
    head.querySelector(".t").append(title instanceof Node ? title : document.createTextNode(title));
    const body = document.createElement("div");
    body.className = "dc-modal-body";
    if (typeof bodyEl === "string") body.innerHTML = bodyEl;
    else body.appendChild(bodyEl);
    if (opts.detachable) {
      const detach = document.createElement("button");
      detach.className = "ghost icon"; detach.textContent = "⤢";
      detach.title = "Open in its own window";
      detach.onclick = () => detachModal(box, body, title, dismiss);
      head.appendChild(detach);
    }
    const close = document.createElement("button");
    close.className = "ghost icon"; close.textContent = "✕";
    close.onclick = () => dismiss();
    head.appendChild(close);
    box.append(head, body);
    overlay.appendChild(box);
    function dismiss() {
      overlay.remove();
      box.remove();   // no-op unless detached (floating panels live outside overlay)
      document.removeEventListener("keydown", onKey);
      if (opts.onClose) opts.onClose();
    }
    function onKey(e) { if (e.key === "Escape") dismiss(); }
    overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) dismiss(); });
    document.addEventListener("keydown", onKey);
    document.body.appendChild(overlay);
    return { close: dismiss, body, head };
  }

  // "Detach": prefer a real OS-level window (Document Picture-in-Picture —
  // Chromium 116+, stays on top even across tabs/apps); fall back to a
  // draggable, resizable panel inside the page on browsers without it
  // (Firefox/Safari). Either way the modal's own backdrop goes away — the
  // point is to keep reading/editing while using the rest of the app.
  async function detachModal(box, body, title, dismiss) {
    const rect = box.getBoundingClientRect();
    if (window.documentPictureInPicture) {
      try {
        const pipWin = await window.documentPictureInPicture.requestWindow({
          width: Math.round(rect.width), height: Math.round(rect.height),
        });
        // PiP windows start with an empty document — carry the app's
        // stylesheets over so the content doesn't render unstyled.
        [...document.styleSheets].forEach((ss) => {
          try {
            if (ss.href) {
              const link = document.createElement("link");
              link.rel = "stylesheet"; link.href = ss.href;
              pipWin.document.head.appendChild(link);
            } else {
              const style = document.createElement("style");
              style.textContent = [...ss.cssRules].map((r) => r.cssText).join("\n");
              pipWin.document.head.appendChild(style);
            }
          } catch (e) { /* cross-origin stylesheet — can't read its rules, skip */ }
        });
        pipWin.document.title = typeof title === "string" ? title
          : (title.textContent || "DronaCharya");
        pipWin.document.body.style.cssText =
          "margin:0;background:var(--bg);color:var(--ink)";
        pipWin.document.adoptNode(body);
        pipWin.document.body.appendChild(body);
        (box.parentElement || box).remove();   // drop the dimmed backdrop + box; content now lives in the pip window
        pipWin.addEventListener("pagehide", () => dismiss(), { once: true });
        return;
      } catch (e) {
        // Document PiP requires a secure context (HTTPS or localhost) — the
        // most common reason this fails is DronaCharya being reached over
        // plain http:// on the LAN. Say so instead of silently switching
        // modes with no explanation.
        toast(window.isSecureContext ? "Picture-in-Picture unavailable — opened as a floating panel instead."
              : "Picture-in-Picture needs HTTPS (this page is http://) — opened as a floating panel instead.");
      }
    } else if (!window.isSecureContext) {
      toast("Picture-in-Picture needs HTTPS (this page is http://) — opened as a floating panel instead.");
    }
    floatPanel(box, body, rect);
  }

  function floatPanel(box, body, rect) {
    const overlay = box.parentElement;
    document.body.appendChild(box);   // re-parent past the dimmed overlay...
    if (overlay) overlay.remove();    // ...and drop it, so the page stays interactive
    box.classList.add("floating");
    Object.assign(box.style, {
      position: "fixed", margin: "0", left: rect.left + "px", top: rect.top + "px",
      width: rect.width + "px", height: rect.height + "px",
    });
    const headEl = box.querySelector(".dc-modal-head");
    headEl.style.cursor = "move";
    let dragging = false, dx = 0, dy = 0;
    headEl.addEventListener("mousedown", (e) => {
      if (e.target.closest("button")) return;
      dragging = true;
      dx = e.clientX - box.offsetLeft; dy = e.clientY - box.offsetTop;
    });
    document.addEventListener("mousemove", (e) => {
      if (!dragging) return;
      box.style.left = (e.clientX - dx) + "px";
      box.style.top = (e.clientY - dy) + "px";
    });
    document.addEventListener("mouseup", () => { dragging = false; });
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

  // XlsxParser stores each row as tab-separated text (first row = header) —
  // as plain escaped text those columns never line up. Detect that shape
  // and render a real table instead; anything else displays as before.
  function textAsGridRows(text) {
    const lines = (text || "").split("\n").filter(l => l.trim() !== "");
    const rows = lines.map(l => l.split("\t"));
    const looksLikeTable = rows.length >= 2 && rows[0].length > 1
      && rows.every(r => r.length === rows[0].length);
    return looksLikeTable ? rows : null;
  }

  function renderUnitTextHtml(text) {
    const rows = textAsGridRows(text);
    if (!rows) return `<div class="u-text">${esc(text)}</div>`;
    const [header, ...body] = rows;
    const thead = `<tr>${header.map(c => `<th>${esc(c)}</th>`).join("")}</tr>`;
    const tbody = body.map(r => `<tr>${r.map(c => `<td>${esc(c)}</td>`).join("")}</tr>`).join("");
    return `<div class="u-table-wrap"><table class="u-table">`
      + `<thead>${thead}</thead><tbody>${tbody}</tbody></table></div>`;
  }

  // Editable spreadsheet grid for table-shaped units (XLSX sections): every
  // cell is a real <input> (native tab/arrow navigation, no HTML-injection
  // risk since we only ever read .value); the first row is a real header
  // (its own labeled/editable row, separate from data), rows/columns can
  // be added or removed. .getGridValue() serializes back to the SAME
  // tab-separated shape XlsxParser stores, so search/FTS keeps working.
  function buildEditableGrid(text) {
    const [headerRow, ...dataRows] = textAsGridRows(text) || [[text || ""]];
    const wrap = document.createElement("div");
    wrap.className = "u-grid-wrap";
    const table = document.createElement("table");
    table.className = "u-grid";
    const thead = document.createElement("thead");
    const theadRow = document.createElement("tr");
    const tbody = document.createElement("tbody");

    const cellInput = (value) => {
      const input = document.createElement("input");
      input.type = "text";
      input.value = value;
      return input;
    };
    const colCount = () => Math.max(theadRow.children.length - 1, 0);
    const removeColumn = (ci) => {
      theadRow.deleteCell(ci);
      [...tbody.rows].forEach((tr) => tr.deleteCell(ci));
    };
    const addHeaderCell = (value) => {
      const th = document.createElement("th");
      th.appendChild(cellInput(value));
      const del = document.createElement("button");
      del.className = "ghost icon u-grid-colctl"; del.type = "button";
      del.textContent = "✕"; del.title = "remove column";
      del.onclick = () => removeColumn([...theadRow.children].indexOf(th));
      th.appendChild(del);
      theadRow.insertBefore(th, theadRow.lastElementChild);
    };
    const addDataRow = (values) => {
      const tr = document.createElement("tr");
      values.forEach((v) => {
        const td = document.createElement("td");
        td.appendChild(cellInput(v));
        tr.appendChild(td);
      });
      const ctl = document.createElement("td");
      ctl.className = "u-grid-rowctl";
      const del = document.createElement("button");
      del.className = "ghost icon"; del.type = "button";
      del.textContent = "✕"; del.title = "remove row";
      del.onclick = () => tr.remove();
      ctl.appendChild(del);
      tr.appendChild(ctl);
      tbody.appendChild(tr);
      return tr;
    };
    const addColumn = () => {
      addHeaderCell("");
      [...tbody.rows].forEach((tr) => {
        const td = document.createElement("td");
        td.appendChild(cellInput(""));
        tr.insertBefore(td, tr.lastElementChild);
      });
    };

    theadRow.appendChild(document.createElement("th"));   // spacer above the row-delete column
    headerRow.forEach((v) => addHeaderCell(v));
    thead.appendChild(theadRow);
    dataRows.forEach((r) => addDataRow(r));

    table.append(thead, tbody);
    const controls = document.createElement("div");
    controls.className = "row u-grid-controls";
    const addRowBtn = document.createElement("button");
    addRowBtn.className = "secondary"; addRowBtn.type = "button";
    addRowBtn.textContent = "+ Row";
    addRowBtn.onclick = () => addDataRow(Array(colCount()).fill(""));
    const addColBtn = document.createElement("button");
    addColBtn.className = "secondary"; addColBtn.type = "button";
    addColBtn.textContent = "+ Column";
    addColBtn.onclick = addColumn;
    controls.append(addRowBtn, addColBtn);
    wrap.append(table, controls);

    wrap.getGridValue = () => {
      const header = [...theadRow.querySelectorAll("input")].map((i) => i.value).join("\t");
      const data = [...tbody.rows].map((tr) =>
        [...tr.querySelectorAll("input")].map((i) => i.value).join("\t"));
      return [header, ...data].filter((line) => line.trim() !== "").join("\n");
    };
    return wrap;
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
          ${doc.file_path ? `<button class="ghost" id="dv-convert" title="${esc(
             "Sections are separate fields right now. This merges them into a single "
             + "document you can freely rewrite, reorder, or reformat — instead of "
             + "fixing one field at a time.")}">Edit as one document</button>` : ""}
          <button class="danger" id="dv-del">Delete document</button></span>
      </div>
      ${origin ? `<div class="muted" style="margin-bottom:8px">${
        doc.url ? `<a href="${esc(doc.url)}" target="_blank" rel="noopener"
                     style="color:var(--accent)">${esc(origin)}</a>` : esc(origin)}</div>` : ""}
      ${doc.summary ? (textAsGridRows(doc.summary)
          // a table-shaped summary (XLSX) shouldn't render as raw tab
          // soup, and shouldn't sit dimmed under .muted either — data
          // needs full contrast, not just a preview blurb does.
          ? `<div style="margin-bottom:12px">${renderUnitTextHtml(doc.summary)}</div>`
          : `<div class="muted" style="margin-bottom:12px">${esc(doc.summary)}</div>`) : ""}
      <div id="dv-units"></div>
      <div class="faint" style="margin-top:8px">${doc.units.length} knowledge
        item${doc.units.length === 1 ? "" : "s"}${isMap ? "" :
        " — hover one to edit or remove it"}.</div>`;

    const m = modal((isMap ? "MindMap: " : "") + (doc.title || "(untitled)"), wrap,
                    { wide: true, detachable: true });

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
    if (!editable && caps.editor === "note") {
      unitsEl.innerHTML = "";
      const btn = document.createElement("button");
      btn.textContent = "Edit note";
      btn.style.marginBottom = "10px";
      btn.onclick = () => {
        m.close();
        window.DC.openNoteEditor({ doc, onSaved: changed });
      };
      unitsEl.appendChild(btn);
    } else if (!editable) {
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
          ${renderUnitTextHtml(u.text)}`;
        if (editable) {
          const [editBtn, delBtn] = el.querySelectorAll(".u-tools button");
          editBtn.onclick = () => {
            const textBlock = el.querySelector(".u-text, .u-table-wrap");
            const originalHeight = textBlock.getBoundingClientRect().height;
            const isGrid = textAsGridRows(u.text) !== null;
            let editEl;
            if (isGrid) {
              editEl = buildEditableGrid(u.text);
            } else {
              editEl = document.createElement("textarea");
              editEl.className = "u-editarea";
              editEl.value = u.text;
              editEl.style.height = Math.max(originalHeight, 120) + "px";
            }
            const bar = document.createElement("div");
            bar.className = "row";
            bar.style.marginTop = "8px";
            bar.innerHTML = `<button>Save</button>
                             <button class="secondary">Cancel</button>`;
            textBlock.replaceWith(editEl);
            el.appendChild(bar);
            if (!isGrid) editEl.focus();
            bar.children[0].onclick = async () => {
              // .trim() only makes sense for free text — on grid data it
              // silently eats a legitimately empty LAST cell (trailing tab
              // on the final row), since trim only touches the very ends
              // of the whole string, not each line.
              const text = isGrid ? editEl.getGridValue() : editEl.value.trim();
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
    // note/todo/mindmap already rendered their own message/button above —
    // dumping every raw unit into #dv-units too made "Edit note" sit on
    // top of a full section list instead of replacing it.
    if (editable) renderUnits();

    wrap.querySelector("#dv-del").onclick = async () => {
      if (!confirm(`Delete "${doc.title}" and all its knowledge?`)) return;
      await fetch(`/api/v1/documents/${id}`, { method: "DELETE",
                                               headers: headers(false) });
      m.close(); toast("Document deleted"); changed();
    };
    const convertBtn = wrap.querySelector("#dv-convert");
    if (convertBtn) convertBtn.onclick = async () => {
      if (!confirm(`Edit "${doc.title}" as one document? Its sections merge into a `
                   + "single free-form note — re-uploading the original file will "
                   + "no longer update it."))
        return;
      const r = await fetch(`/api/v1/documents/${id}/convert-to-note`,
                            { method: "POST", headers: headers(false) });
      if (!r.ok) {
        toast((await r.json().catch(() => ({}))).detail || "could not convert", "error");
        return;
      }
      const updated = await r.json();
      m.close();
      openDocument(updated.id, opts);   // reopen — now shows the "Edit note" button
    };
  }

  function openNoteEditor(opts = {}) {
    // opts: { doc?, onSaved? } — doc present = edit mode. Format can still
    // be switched during edit (mdToHtml/htmlToMd below carry the content
    // across) — the radios always show, just pre-checked to the note's
    // current format.
    const doc = opts.doc || null;
    const fmt0 = doc ? (doc.note_format || "markdown") : "markdown";
    const wrap = document.createElement("div");
    wrap.className = "nt-wrap";
    wrap.innerHTML = `
      <input id="nt-title" type="text" placeholder="title (optional — first heading otherwise)"
             style="width:100%; box-sizing:border-box; margin-bottom:8px">
      <div class="row" style="margin-bottom:8px">
        <label style="display:inline-flex;gap:5px;align-items:center">
          <input type="radio" name="nt-fmt" value="markdown"
                 ${fmt0 === "markdown" ? "checked" : ""}> Markdown</label>
        <label style="display:inline-flex;gap:5px;align-items:center">
          <input type="radio" name="nt-fmt" value="rich"
                 ${fmt0 === "rich" ? "checked" : ""}> Rich text</label></div>
      <div id="nt-richbar" class="wtb" style="display:none">
        <button data-cmd="bold" title="Bold"><b>B</b></button>
        <button data-cmd="italic" title="Italic"><i>I</i></button>
        <button data-cmd="underline" title="Underline"><u>U</u></button>
        <button data-cmd="strikeThrough" title="Strike"><s>S</s></button>
        <button data-cmd="insertUnorderedList" title="Bullet list">•≡</button>
        <button data-cmd="insertOrderedList" title="Numbered list">1≡</button>
        <button data-cmd="removeFormat" title="Clear formatting">⌫ᵃ</button>
      </div>
      <textarea id="nt-md" placeholder="# Heading\n\nYour note — headings become sections…"
        class="nt-editarea"
        style="width:100%; box-sizing:border-box; resize:vertical;
               font-family:ui-monospace,monospace; display:none"></textarea>
      <div id="nt-rich" contenteditable="true" class="nt-editarea" style="display:none;
        border:1px solid var(--border); border-radius:8px; padding:10px;
        background:var(--panel); overflow:auto; resize:vertical"></div>
      <input id="nt-tags" type="text" placeholder="tags, comma separated (optional)"
             style="width:100%; box-sizing:border-box; margin-top:8px">
      <div class="row" style="margin-top:12px; justify-content:flex-end">
        <button id="nt-save">Save note</button>
      </div>`;
    const m = modal(doc ? "Edit note" : "New note", wrap, { wide: true, tall: true, detachable: true });
    const q = (sel) => wrap.querySelector(sel);
    let fmt = fmt0;
    const applyFmt = () => {
      q("#nt-md").style.display = fmt === "markdown" ? "block" : "none";
      q("#nt-rich").style.display = fmt === "rich" ? "block" : "none";
      q("#nt-richbar").style.display = fmt === "rich" ? "flex" : "none";
    };
  const mdToHtml = (md) => {
      const esc = (t) => t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      const inline = (t) => esc(t)
        .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
        .replace(/\*([^*]+)\*/g, "<i>$1</i>");
      const out = [];
      let list = null;   // "ul" | "ol" | null
      const closeList = () => { if (list) { out.push(`</${list}>`); list = null; } };
      md.split("\n").forEach((line) => {
        const h = line.match(/^(#{1,3})\s+(.*)$/);
        const ul = line.match(/^\s*[-*]\s+(.*)$/);
        const ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
        if (h) { closeList(); out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); }
        else if (ul) { if (list !== "ul") { closeList(); out.push("<ul>"); list = "ul"; }
                       out.push(`<li>${inline(ul[1])}</li>`); }
        else if (ol) { if (list !== "ol") { closeList(); out.push("<ol>"); list = "ol"; }
                       out.push(`<li>${inline(ol[1])}</li>`); }
        else if (!line.trim()) { closeList(); out.push("<div><br></div>"); }
        else { closeList(); out.push(`<div>${inline(line)}</div>`); }
      });
      closeList();
      return out.join("");
    };
    const htmlToMd = (html) => {
      const tpl = document.createElement("template");
      tpl.innerHTML = html;
      const walk = (node, ctx) => {
        let out = "";
        node.childNodes.forEach((c) => {
          if (c.nodeType === 3) { out += c.textContent; return; }
          const tag = (c.tagName || "").toLowerCase();
          if (tag === "br") { out += "\n"; return; }
          const inner = walk(c, tag === "ol" || tag === "ul" ? tag : ctx);
          if (/^h[1-3]$/.test(tag)) out += "\n" + "#".repeat(+tag[1]) + " " + inner.trim() + "\n";
          else if (tag === "b" || tag === "strong") out += inner.trim() ? `**${inner}**` : inner;
          else if (tag === "i" || tag === "em") out += inner.trim() ? `*${inner}*` : inner;
          else if (tag === "li") out += "\n" + (ctx === "ol" ? "1. " : "- ") + inner.trim();
          else if (tag === "ul" || tag === "ol") out += inner + "\n";
          else if (tag === "div" || tag === "p" || tag === "blockquote") out += "\n" + inner;
          else out += inner;
        });
        return out;
      };
      return walk(tpl.content, null).replace(/\n{3,}/g, "\n\n").trim();
    };

    wrap.querySelectorAll('input[name="nt-fmt"]').forEach(r =>
      r.onchange = () => {
        // CARRY the note across the switch — never discard typed content
        if (r.value === "rich" && fmt === "markdown")
          q("#nt-rich").innerHTML = mdToHtml(q("#nt-md").value);
        else if (r.value === "markdown" && fmt === "rich")
          q("#nt-md").value = htmlToMd(q("#nt-rich").innerHTML);
        fmt = r.value; applyFmt();
      });
    q("#nt-richbar").querySelectorAll("[data-cmd]").forEach(b => {
      b.onmousedown = (e) => e.preventDefault();   // keep editor selection
      b.onclick = () => document.execCommand(b.dataset.cmd);
    });
    if (doc) {
      if (doc.note_title_explicit) q("#nt-title").value = doc.title || "";
      else q("#nt-title").placeholder = `title (currently derived: ${doc.title})`;
      q("#nt-tags").value = (doc.tags || []).join(", ");
      if (fmt === "rich") q("#nt-rich").innerHTML = doc.note_source || "";
      else q("#nt-md").value = doc.note_source || "";
    }
    applyFmt();
    q("#nt-save").onclick = async () => {
      const content = fmt === "rich" ? q("#nt-rich").innerHTML
                                     : q("#nt-md").value;
      if (!(fmt === "rich" ? q("#nt-rich").textContent : content).trim()) {
        toast("The note is empty", "error"); return;
      }
      const tags = q("#nt-tags").value.split(",").map(t => t.trim())
        .filter(Boolean);
      const resp = await fetch(doc ? `/api/v1/notes/${doc.id}` : "/api/v1/notes", {
        method: doc ? "PUT" : "POST", headers: headers(),
        body: JSON.stringify({ title: q("#nt-title").value.trim(),
                               content, format: fmt, tags }),
      });
      if (!resp.ok) {
        toast((await resp.json().catch(() => ({}))).detail || "save failed",
              "error");
        return;
      }
      toast("Note saved — searchable now");
      knownTags(true);
      m.close();
      if (opts.onSaved) opts.onSaved();
    };
    (fmt === "rich" ? q("#nt-rich") : q("#nt-md")).focus();
  }

  window.DC = { headers, esc, modal, toast, openDocument, promptModal,
                openNoteEditor,
                knownTags, attachTagSuggest, onToken: null };
  buildShell();
})();
