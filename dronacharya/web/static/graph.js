// Connected-facts graph — cytoscape (MIT, vendored) over /api/v1/graph.
const { headers, esc, toast } = window.DC;

const info = document.getElementById("graph-info");
let cy = null;

function nodeColor(type) {
  return type === "unit" ? "#34d399" : type === "document" ? "#5b9cf5" : "#e3a93c";
}

function render(graph) {
  const elements = [];
  graph.nodes.forEach(n => elements.push({ data: { ...n } }));
  graph.edges.forEach((e, i) => elements.push({
    data: { id: "e" + i, source: e.source, target: e.target,
            kind: e.kind, weight: e.weight } }));

  if (cy) cy.destroy();
  cy = cytoscape({
    container: document.getElementById("cy"),
    elements,
    style: [
      { selector: "node", style: {
          "background-color": ele => nodeColor(ele.data("type")),
          "label": "data(label)",
          "font-size": ele => ele.data("type") === "unit" ? 9 : 11,
          "text-wrap": "wrap", "text-max-width": 160,
          "text-valign": "bottom", "text-margin-y": 5,
          "color": getComputedStyle(document.body).color,
          "text-outline-width": 2,
          "text-outline-color": getComputedStyle(document.body).backgroundColor,
          "width": ele => ele.data("type") === "document" ? 34 : ele.data("type") === "tag" ? 22 : 26,
          "height": ele => ele.data("type") === "document" ? 34 : ele.data("type") === "tag" ? 22 : 26,
          "shape": ele => ele.data("type") === "document" ? "round-rectangle"
                        : ele.data("type") === "tag" ? "diamond" : "ellipse",
      }},
      { selector: "edge", style: {
          "curve-style": "bezier",
          "line-color": "#8a93a755",
          "width": ele => ele.data("kind") === "related" ? 1 + 4 * (ele.data("weight") - 0.5) : 1.4,
          "line-style": ele => ele.data("kind") === "related" ? "dashed" : "solid",
      }},
      { selector: "node:selected", style: { "border-width": 3, "border-color": "#f87171" } },
    ],
    layout: { name: "cose", animate: false, nodeRepulsion: 9000, idealEdgeLength: 90 },
    wheelSensitivity: 0.2,
  });

  cy.on("tap", "node", evt => {
    const d = evt.target.data();
    info.style.display = "block";
    const link = d.url ? `<div style="margin-top:6px"><a href="${esc(d.url)}" target="_blank"
      rel="noopener" style="color:var(--accent);overflow-wrap:anywhere">${esc(d.url)}</a></div>` : "";
    const text = d.full_text ? `<div style="margin-top:6px;font-size:13px">${esc(d.full_text)}</div>` : "";
    info.innerHTML = `<button class="ghost icon" style="float:right">✕</button>
      <b>${esc(d.label)}</b>
      <div class="muted">${esc(d.type)}${d.score ? " · score " + d.score : ""}</div>` + text + link;
    info.querySelector("button").onclick = () => info.style.display = "none";
  });
  cy.on("tap", evt => { if (evt.target === cy) info.style.display = "none"; });
}

async function go() {
  const q = document.getElementById("gq").value.trim();
  if (!q) return;
  let resp;
  try {
    resp = await fetch("/api/v1/graph", { method: "POST", headers: headers(),
                                          body: JSON.stringify({ query: q }) });
  } catch { toast("Server unreachable", "error"); return; }
  if (resp.status === 401) { toast("Set your API token from the sidebar first", "error"); return; }
  const graph = await resp.json();
  if (!graph.nodes || !graph.nodes.length) {
    document.getElementById("cy").innerHTML =
      `<div class="muted" style="padding:30px">No connected knowledge found for that query.</div>`;
    if (cy) { cy.destroy(); cy = null; }
    return;
  }
  render(graph);
}

document.getElementById("go").onclick = go;
document.getElementById("gq").addEventListener("keydown", e => { if (e.key === "Enter") go(); });
