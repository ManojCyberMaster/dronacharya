# Mind maps, tags & the knowledge graph (web UI)

All three are **web-only surfaces** over the same knowledge base — nothing new
to install, no build step, and every third-party library is vendored under a
permissive license (mind-elixir MIT, Cytoscape.js MIT — see NOTICE). The whole
web UI shares one product shell: a collapsible sidebar, dark/light themes, and
an API-token dialog (sidebar → *API token*).

## Mind maps — http://your-server:8317/mindmap

A full-featured XMind-style editor (mind-elixir engine). The toolbar holds the
frequent actions (new map, file menu, undo/redo, node ops, focus, zoom); the
**right rail** opens hovering panels over the map — click a tab to open it, it
stays until you pick another tab or click it again:

- **Maps** — your map list (open, delete, ＋ new). The header search finds
  maps by **name or their central topics**, autocompleting after 3 characters
  from a local cache.
- **Layout** — four arrangements: left, right, balanced, **hierarchical**
  (top-down tree).
- **Theme** — Drona, Midnight, Latte, Ocean, Sunset; saved per map.
- **Node style** — text color, background, bold/italic/underline, **box
  shape (rectangle, rounded, ellipse, or no box)**, emoji icons, hyperlink,
  and **tags — the same tags the whole app uses** (suggested from your
  existing ones). (Built in-house against mind-elixir's MIT API — no
  third-party style plugin, so it stays license-clean.)
- **Note** — every node can carry a rich-text note (bold/italic/underline/
  strike/lists — text only, no images). Add one from the toolbar note button,
  the right-click menu, or the rail; noted nodes show a note badge — one
  click opens it. The panel is corner-resizable and ⤢ expands it for long
  notes. **Notes are searchable knowledge.**
- **Link style** — every connection is a link: **click any line** (tree
  branch or custom link) to select and style it — label, color,
  solid/dashed/dotted, thin/normal/bold. Styling applies to **that link
  only**; child links follow their parent's style through the *inherit from
  parent link* checkbox (on by default — uncheck to give a link its own
  style). **Remove link** detaches the node so it stands alone (reattach
  any time; its knowledge stays). Custom links (for unrelated nodes:
  right-click → *Link*, click the target) add one-way/both-ways arrows.
  The toolbar's *standalone node* button adds a node with no connecting
  line at all.
- **Outline** — the whole map as an indented list with find-in-map
  (Enter jumps to the first hit).

Editing basics: Tab/Enter add child/sibling, F2 or double-click edits, drag
re-parents, Ctrl+Z/Y undo/redo, Ctrl+C/V copies branches. Navigation: the
**wheel scrolls** (Shift+wheel or a horizontal wheel scrolls sideways),
**Ctrl+wheel zooms at the pointer**, **Ctrl+= / Ctrl+- / Ctrl+0** zoom and
reset from the keyboard, **Alt+drag** (or Space+drag) pans. Rail panels
close when you click empty space. *Focus* drills into one branch as a
temporary map (Unfocus returns). File menu: rename, duplicate,
**map tags**, import JSON, export PNG / SVG / JSON / Markdown. Autosave ~1 s
after you stop editing.

**Every node goes into your knowledge corpus.** A map is stored as a normal
document (`source_type="mindmap"`, full map JSON in its metadata) and each
node becomes a knowledge unit whose text is the node's full path —
`Homelab > Proxmox > Docker VM` — plus its note text, so Ask, Search, the
Graph and the MCP server all see your map knowledge, and it syncs to every
device. **Node tags and the optional map-level tag land in the app-wide tag
namespace**: filtering or browsing by such a tag returns
`MindMap:<Name> > <node path>` for the exact tagged nodes. Deleting a map
removes its knowledge with the usual tombstone propagation. Mind-map
knowledge is edited *in the map* (the Library shows it read-only and links
here).

## Tags — http://your-server:8317/tags

A **semantic word map** of every tag you have:

- Related topics sit **near each other** — positions come from embedding the
  tag names with your KB's own model and projecting to 2D.
- **Color families are semantic clusters**; within a family, **darker means
  more knowledge items**, lighter means fewer. The size grows with the count
  too, and each pill shows its item count.
- **The biggest tags sit at the center** and smaller ones fan out in every
  direction; a tag's direction still comes from its semantics, so related
  topics share a sector — dense, no dead space.
- Same navigation as the mind map, inside its own frame: wheel scrolls
  (Shift = sideways), Ctrl+wheel zooms, Ctrl+= / Ctrl+- / Ctrl+0 zoom and
  reset, arrow keys pan, Alt+drag pans, corner buttons zoom/reset,
  double-click resets (the ? button lists them all). The page never scrolls.
- **Click a tag** to open its documents (mind-map entries show
  `MindMap:<Name> > <node path>`); click a document to view **every knowledge
  item in it — edit or remove items right there** (see below). The tag modal
  also offers **Rename tag** and **Delete tag** (delete removes only the tag,
  never the knowledge).

Only tags that documents actually carry are shown — a tag orphaned by
retagging or deletion disappears immediately. There is no "add tag" button
because a tag exists by being on something: add one to a document (Library →
open → *+ tag*), to a mind-map node (Style panel), or at save time. One
document can carry **any number of tags**.

Tag hygiene from the CLI: `dc tags list`, `dc tags rename OLD NEW`,
`dc tags remove TAG`, and `dc tags strip-prefix PREFIX` (e.g.
`dc tags strip-prefix seed` turns legacy `seed/wsl` into `wsl`). New seed-kit
installs already tag with the plain topic.

## Editing & deleting knowledge items

Open any document (Library → *Open*, or through a tag). Each knowledge unit
shows ✎ **edit** and ✕ **remove** on hover; edits are re-embedded and
re-indexed immediately, and the document version bumps so the change syncs
like any other. Tags and the document itself can be changed or deleted in the
same view. The API is `PUT /api/v1/documents/{id}/units` with the full new
unit list; removing the last unit is refused — delete the document instead.

## Knowledge graph — http://your-server:8317/graph

Type a query ("docker on proxmox with the DGX") and get the connected
neighborhood of your knowledge:

- **facts** (green) — the retrieved knowledge units
- **documents / mind maps** (blue) — solid edges tie facts to their sources
- **tags** (amber) — shared tags connect documents
- **dashed edges** — semantic relatedness between facts from *different*
  sources: each fact links to its nearest related fact (embedding cosine),
  with extra edges for very tight relations; thicker = closer

Click any node for its full text and source link. Computed live from what the
KB already stores — no LLM calls, works offline, nothing new persisted.

A full GraphRAG upgrade (entities extracted at save time, persistent entity
graph, multi-hop retrieval) is designed but not built — see
a future GraphRAG exploration (not yet in this repo).

## Browser extension: capture behind logins, to-do reminders

The extension (in `extension/`, load unpacked) captures the page **straight
from your browser tab** — so pages behind logins, paywalled docs you're
signed into, and internal tools all save reliably; the server never has to
fetch them. After a save, the popup shows the **distilled summary for
review**: edit it in place and *keep*, or *discard* the save entirely.
Nothing but that distilled knowledge is stored, as always.

The popup also has a **To-dos** section, and the web UI has a full
**To-dos page** (sidebar → To-dos): add items with an optional due time and
get a **browser notification** from the extension when they're due. To-dos
live in your knowledge base (`source_type="todo"`) — they sync to every
device, show up in search, and are exported/wiped with everything else. API:
`GET/POST /api/v1/todos`, `PATCH /api/v1/todos/{id}`, delete via the normal
documents endpoint — the same API a future mobile app will use.
