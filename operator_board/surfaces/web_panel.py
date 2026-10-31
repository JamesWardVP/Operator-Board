"""A control surface in any web browser: phone, tablet or laptop on the stage network.

Served by the local HTTP API (api_server.py) at /panel. It shows the current surface layout as a
grid of buttons, follows page changes and active states, and pressing a button runs its action,
exactly like a Stream Deck key. Nothing to install on the phone.
"""
from __future__ import annotations

from typing import Any, Dict

from operator_board.surfaces.controller import SurfaceController


def surface_state(controller: SurfaceController) -> Dict[str, Any]:
    """The current page as the web panel draws it."""
    layout = controller.layout
    page = controller.current_page
    keys = []
    for index in range(layout.key_count):
        binding = controller.binding(index)
        assigned = bool(binding and binding.action)
        active = controller.is_active(index) if assigned else False
        keys.append({"label": (binding.label or binding.action) if assigned else "",
                     "color": (binding.active_color if active else binding.color) if assigned else "#1b1e23",
                     "active": active, "assigned": assigned})
    return {"name": layout.name, "columns": layout.columns, "rows": layout.rows,
            "page": page.name if page else "", "page_index": controller.page, "pages": len(layout.pages),
            "keys": keys}


PANEL_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="theme-color" content="#121417">
<title>Operator Board</title>
<style>
  :root { color-scheme: dark; }
  html, body { margin: 0; height: 100%; background: #121417; color: #e9ecef;
               font-family: -apple-system, "Segoe UI", Roboto, sans-serif; -webkit-user-select: none; user-select: none; }
  body { display: flex; flex-direction: column; padding: max(10px, env(safe-area-inset-top)) 10px
         max(10px, env(safe-area-inset-bottom)); box-sizing: border-box; }
  header { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
  header h1 { font-size: 15px; letter-spacing: 2px; color: #00d5ff; margin: 0; flex: 1; }
  header button { background: #23272e; color: #e9ecef; border: 1px solid #30353d; border-radius: 8px;
                  font-size: 15px; padding: 8px 14px; }
  #page { font-size: 13px; color: #8b949e; min-width: 4em; text-align: center; }
  #grid { flex: 1; display: grid; gap: 8px; }
  .key { border: 2px solid #30353d; border-radius: 12px; color: #fff; font-weight: 700; font-size: 15px;
         display: flex; align-items: center; justify-content: center; text-align: center; padding: 6px;
         overflow: hidden; overflow-wrap: normal; word-break: normal; hyphens: auto; line-height: 1.15;
         touch-action: manipulation; }
  .key.active { border-color: #fff; }
  .key.empty { opacity: .35; }
  .key:active { transform: scale(.96); }
  #status { font-size: 12px; color: #8b949e; margin-top: 8px; min-height: 1.2em; text-align: center; }
  #status.bad { color: #ff6b6b; }
</style></head>
<body>
<header><h1>OPERATOR BOARD</h1><button id="prev">&#9664;</button><span id="page"></span><button id="next">&#9654;</button></header>
<div id="grid"></div>
<div id="status">Connecting...</div>
<script>
const token = new URLSearchParams(location.search).get("token") || "";
const q = token ? ("?token=" + encodeURIComponent(token)) : "";
const grid = document.getElementById("grid"), status = document.getElementById("status");
let last = "";
function say(text, bad) { status.textContent = text; status.className = bad ? "bad" : ""; }
async function call(path) {
  const r = await fetch(path + q, {method: "POST"});
  const data = await r.json().catch(() => ({}));
  say(data.message || (r.ok ? "" : "Failed"), !r.ok);
  refresh();
}
function draw(s) {
  const sig = JSON.stringify(s);
  if (sig === last) return;
  last = sig;
  document.getElementById("page").textContent = s.page + (s.pages > 1 ? " (" + (s.page_index + 1) + "/" + s.pages + ")" : "");
  grid.style.gridTemplateColumns = "repeat(" + s.columns + ", 1fr)";
  grid.style.gridTemplateRows = "repeat(" + s.rows + ", 1fr)";
  grid.innerHTML = "";
  // Text sized to the button width, so whole words fit on narrow phone columns.
  const longest = Math.max(4, ...s.keys.map(k => Math.max(0, ...(k.label || "").split(/\s+/).map(w => w.length))));
  const width = (grid.clientWidth || window.innerWidth) / s.columns - 16;
  const size = Math.max(10, Math.min(18, Math.floor(width / (longest * 0.68))));
  s.keys.forEach((k, i) => {
    const b = document.createElement("div");
    b.className = "key" + (k.active ? " active" : "") + (k.assigned ? "" : " empty");
    b.style.background = k.color;
    b.textContent = k.label;
    b.style.fontSize = size + "px";
    if (k.assigned) b.addEventListener("click", () => call("/api/surface/press/" + i));
    grid.appendChild(b);
  });
}
async function refresh() {
  try {
    const r = await fetch("/api/surface" + q);
    if (r.status === 401) { say("Wrong or missing token: open the address shown in Operator Board", true); return; }
    draw(await r.json());
    if (status.textContent.startsWith("Connecting") || status.textContent.startsWith("Lost")) say("");
  } catch (e) { say("Lost connection to Operator Board, retrying...", true); }
}
document.getElementById("prev").onclick = () => call("/api/surface/page/prev");
document.getElementById("next").onclick = () => call("/api/surface/page/next");
window.addEventListener("resize", () => { last = ""; refresh(); });
refresh(); setInterval(refresh, 1000);
</script>
</body></html>
"""
