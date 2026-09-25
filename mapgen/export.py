"""Write the generated map: PNGs, Tiled JSON (.tmj), a compact game JSON and a playable HTML viewer."""
import base64
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image

from .abstract import BLOCKING_KINDS, KINDS
from .tileset import ROWS, T

KIND_COLORS = {
    "grass": (104, 176, 72), "forest": (40, 104, 56), "paddy": (118, 178, 132), "field": (168, 120, 72),
    "water": (56, 120, 208), "road": (120, 120, 128), "bridge": (150, 132, 112), "rail": (80, 72, 72),
    "rail_bridge": (80, 72, 72), "crossing": (230, 200, 40), "park": (132, 206, 104), "parking": (160, 160, 168),
    "yard": (150, 204, 112), "plaza": (214, 204, 186), "bare": (196, 168, 120), "house": (192, 80, 64),
    "shop": (170, 150, 200), "factory": (110, 130, 150), "public": (224, 170, 90),
    "road_diag": (120, 120, 128), "rail_diag": (80, 72, 72),
}


def kinds_image(kinds, scale=1):
    pal = np.array([KIND_COLORS[k] for k in KINDS], np.uint8)
    img = Image.fromarray(pal[kinds])
    return img.resize((img.width * scale, img.height * scale), Image.NEAREST) if scale > 1 else img


def _tile_blocking(tile_index, blocking_rows):
    return tile_index // 16 in blocking_rows


def write_all(out, *, kinds, tiles, tileset, source_img, semantic_img, labels, meta, overlay=None):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    rows, cols = kinds.shape
    if overlay is None:
        overlay = np.full(kinds.shape, -1)

    source_img.save(out / "source_map.png")
    semantic_img.save(out / "semantic_map.png")
    kinds_image(kinds, 4).save(out / "abstract.png")
    tileset.save(out / "tileset.png")

    # which tileset rows block movement
    row_kind = {}
    for i, (name, _, _) in enumerate(ROWS):
        row_kind[i] = name.rstrip("0123456789")
    blocking_rows = {i for i, k in row_kind.items() if KINDS.index(k) in BLOCKING_KINDS}

    # --- Tiled map (.tmj), tileset embedded -------------------------------
    tile_props = []
    for idx in range(16 * len(ROWS)):
        props = [{"name": "kind", "type": "string", "value": row_kind[idx // 16]},
                 {"name": "collides", "type": "bool", "value": _tile_blocking(idx, blocking_rows)}]
        tile_props.append({"id": idx, "properties": props})
    objects = []
    for i, l in enumerate(labels):
        objects.append({"id": i + 1, "name": l["name"], "type": l["kind"], "point": True,
                        "x": l["tile"][0] * T, "y": l["tile"][1] * T, "width": 0, "height": 0,
                        "rotation": 0, "visible": True})
    tmj = {
        "type": "map", "version": "1.10", "tiledversion": "1.10.2", "orientation": "orthogonal",
        "renderorder": "right-down", "infinite": False, "width": cols, "height": rows,
        "tilewidth": T, "tileheight": T, "nextlayerid": 4, "nextobjectid": len(objects) + 1,
        "properties": [{"name": k, "type": "string", "value": str(v)} for k, v in meta.items()],
        "tilesets": [{"firstgid": 1, "name": "kimimachi", "image": "tileset.png", "imagewidth": tileset.width,
                      "imageheight": tileset.height, "tilewidth": T, "tileheight": T, "columns": 16,
                      "tilecount": 16 * len(ROWS), "margin": 0, "spacing": 0, "tiles": tile_props}],
        "layers": [
            {"id": 1, "name": "ground", "type": "tilelayer", "width": cols, "height": rows, "x": 0, "y": 0,
             "opacity": 1, "visible": True, "data": (tiles.ravel() + 1).tolist()},
            {"id": 3, "name": "overlay", "type": "tilelayer", "width": cols, "height": rows, "x": 0, "y": 0,
             "opacity": 1, "visible": True, "data": (overlay.ravel() + 1).tolist()},
            {"id": 2, "name": "labels", "type": "objectgroup", "x": 0, "y": 0, "opacity": 1, "visible": True,
             "draworder": "topdown", "objects": objects},
        ],
    }
    (out / "map.tmj").write_text(json.dumps(tmj, ensure_ascii=False))

    # --- compact game data ---------------------------------------------
    game = {
        "meta": meta, "width": cols, "height": rows, "tileSize": T, "kinds": KINDS,
        "blockingKinds": sorted(KINDS[k] for k in BLOCKING_KINDS),
        "kindGrid": kinds.ravel().tolist(), "tileGrid": tiles.ravel().tolist(),
        "overlayGrid": overlay.ravel().tolist(), "labels": labels,
    }
    (out / "map.json").write_text(json.dumps(game, ensure_ascii=False))

    # --- full render + viewer ------------------------------------------
    from .tileset import render_map
    full = render_map(tiles, tileset, overlay)
    full.save(out / "map.png")
    buf = io.BytesIO()
    tileset.save(buf, "PNG")
    viewer = (Path(__file__).parent / "viewer.html").read_text()
    viewer = viewer.replace("__TITLE__", meta["place"]).replace(
        "/*__MAPDATA__*/null", json.dumps({**game, "kindColors": [KIND_COLORS[k] for k in KINDS]}, ensure_ascii=False)
    ).replace("__TILESET__", "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode())
    (out / "index.html").write_text(viewer)
    write_gallery(out.parent)
    return out


def write_gallery(root):
    """out/index.html: links to every generated map."""
    from html import escape
    from urllib.parse import quote

    items = []
    for d in sorted(Path(root).iterdir(), key=lambda p: -p.stat().st_mtime):
        if not (d / "map.json").exists():
            continue
        meta = json.loads((d / "map.json").read_text())["meta"]
        q = quote(d.name)
        items.append(f'<a class="card" href="{q}/index.html"><img src="{q}/abstract.png" alt="">'
                     f'<b>{escape(meta["place"])}</b><small>{escape(meta["source"])} ・ 1マス{meta["tile_m"]}m</small></a>')
    (Path(root) / "index.html").write_text(GALLERY.replace("__ITEMS__", "\n".join(items)))


GALLERY = """<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>kimimachi マップ一覧</title>
<style>
:root{--bg:#16161d;--card:#23232e;--text:#f4f1e8;--muted:#b8b4a8;--accent:#f2c14e;--err:#ff7a6b}
body{margin:0;padding:24px 16px;background:var(--bg);color:var(--text);font-family:system-ui,sans-serif}
main{max-width:1100px;margin:0 auto}h1{font-size:20px;margin:0 0 16px}
form{display:flex;flex-wrap:wrap;gap:8px;align-items:end;padding:12px;background:var(--card);border:2px solid #000;border-radius:8px;margin-bottom:12px}
label{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--muted)}
input{font:inherit;font-size:15px;padding:8px 10px;border-radius:6px;border:1px solid #555;background:#15151c;color:var(--text)}
input[name=place]{width:min(320px,80vw)}input[name=size]{width:90px}input[name=title]{width:180px}
button{font:inherit;font-size:15px;padding:9px 16px;border-radius:6px;border:0;background:var(--accent);color:#1b1b1b;cursor:pointer}
button:disabled{opacity:.5;cursor:default}
#status{white-space:pre-wrap;font:12px/1.5 ui-monospace,monospace;color:var(--muted);background:#0f0f14;border-radius:6px;padding:8px 10px;margin:0 0 16px;display:none;max-height:180px;overflow:auto}
#status.err{color:var(--err)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:16px}
.card{display:flex;flex-direction:column;gap:4px;padding:10px;background:var(--card);border:2px solid #000;border-radius:8px;color:inherit;text-decoration:none}
.card:hover{outline:2px solid var(--accent)}.card img{width:100%;aspect-ratio:1;object-fit:cover;image-rendering:pixelated;border-radius:4px}
small{color:var(--muted)}
</style></head><body><main><h1>kimimachi マップ一覧</h1>
<form id="gen" hidden>
  <label>地名・駅名・住所<input name="place" required placeholder="例: 吉祥寺駅 / 京都市中京区 / 金沢駅"></label>
  <label>範囲 (m)<input name="size" type="number" value="1500" min="300" max="5000" step="100"></label>
  <label>マップ名 (任意)<input name="title" placeholder="地名と同じ"></label>
  <button>マップを作る</button>
</form>
<pre id="status"></pre>
<div class="grid">
__ITEMS__
</div></main>
<script>
const form = document.getElementById("gen"), status = document.getElementById("status");
fetch("/api/jobs/_").then(r => { if (r.status === 404) form.hidden = false; }).catch(() => {});
form.addEventListener("submit", async e => {
  e.preventDefault();
  const fd = new FormData(form), btn = form.querySelector("button");
  btn.disabled = true; status.style.display = "block"; status.className = ""; status.textContent = "受付中…";
  try {
    const r = await fetch("/api/generate", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ place: fd.get("place"), size: fd.get("size"), title: fd.get("title") }) });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || "失敗しました");
    for (;;) {
      await new Promise(res => setTimeout(res, 1500));
      const s = await (await fetch("/api/jobs/" + j.id)).json();
      status.textContent = (s.status === "queued" ? "順番待ち…\\n" : "") + s.log.join("\\n");
      status.scrollTop = status.scrollHeight;
      if (s.status === "done") { location.href = s.url; return; }
      if (s.status === "error") throw new Error("生成に失敗しました（上のログを確認してください）");
    }
  } catch (err) {
    status.className = "err"; status.textContent += "\\n" + err.message; btn.disabled = false;
  }
});
</script></body></html>"""
