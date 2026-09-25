"""mapgen: 地名 -> ピクセルゲームマップ

  python -m mapgen "新潟県長岡市"
  python -m mapgen "長岡駅" --size 1500 --tile-m 6
  python -m mapgen "渋谷駅" --source google      # GOOGLE_MAPS_API_KEY が必要
"""
import argparse
import math
import re
import time

import numpy as np
from PIL import Image

from . import diagonal
from . import raster as R
from .abstract import KINDS, abstract, building_groups
from .export import write_all
from .geo import Frame, geocode
from .sources import gsi, plateau
from .schematic import Schematizer
from .tileset import assign_tiles, build_tileset, overlay_tiles

SUBPX = 8  # raster pixels per game tile


def main(argv=None):
    ap = argparse.ArgumentParser(description="地名から地図を取得してピクセルゲームマップに変換する")
    ap.add_argument("place", help="地名・住所・施設名 (例: 新潟県長岡市)")
    ap.add_argument("--source", choices=["plateau", "gsi", "google"], default="plateau",
                    help="plateau: PLATEAU土地利用+道路 (+地理院の建物/鉄道), gsi: 地理院のみ, google: Google Static Maps")
    ap.add_argument("--size", type=float, default=2000, help="切り出す幅 [m] (default 2000)")
    ap.add_argument("--height", type=float, help="切り出す高さ [m] (default = size)")
    ap.add_argument("--tile-m", type=float, default=8, help="1タイルが表す距離 [m] (default 8)")
    ap.add_argument("--lat", type=float, help="中心緯度 (地名検索結果を上書き)")
    ap.add_argument("--lon", type=float, help="中心経度")
    ap.add_argument("--title", help="マップ名 (default = 地名)")
    ap.add_argument("--out", help="出力ディレクトリ (default out/<マップ名>)")
    ap.add_argument("--layout", choices=["schematic", "real"], default="schematic",
                    help="schematic: 線路を垂直に、道路を水平/垂直に整えた「頭の中の地図」 (既定), real: 実際の形のまま")
    ap.add_argument("--rotate", choices=["auto", "rail", "none"], default="auto",
                    help="schematic の回転: auto=斜めの道が最も少なくなる角度 (既定), rail=線路を --rail-axis に合わせる, none=北が真上")
    ap.add_argument("--rail-axis", choices=["vertical", "horizontal", "auto"], default="auto",
                    help="--rotate rail のとき線路を合わせる向き (auto = 回転が小さい方)")
    ap.add_argument("--straighten", type=float, default=3.0,
                    help="schematic で1区間をまっすぐにするために許す横ずれ [タイル]。大きいほど大胆に直線化 (default 3)")
    ap.add_argument("--no-diagonal", dest="diagonal", action="store_false",
                    help="45°の斜めパーツを使わず、斜めの道も階段状に描く")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args(argv)

    t0 = time.time()
    place = geocode(a.place)
    place["query"] = a.place
    lat = a.lat if a.lat is not None else place["lat"]
    lon = a.lon if a.lon is not None else place["lon"]
    print(f"📍 {place['display_name']}  ({lat:.5f}, {lon:.5f})")

    frame = Frame(lat, lon, a.size, a.height or a.size, mpp=a.tile_m / SUBPX)
    print(f"🗺  {a.size:.0f}m x {(a.height or a.size):.0f}m, {a.tile_m}m/tile -> {frame.W // SUBPX} x {frame.H // SUBPX} tiles")
    schematic = a.layout == "schematic"
    if schematic and a.source == "google":
        raise SystemExit("--source google は --layout real のみ対応です")
    # schematic mode reads a larger area so that rotation/warping never exposes empty corners
    margin = 1.5 if schematic else 1.0
    src = Frame(lat, lon, a.size * margin + (300 if schematic else 0),
                (a.height or a.size) * margin + (300 if schematic else 0), mpp=frame.mpp)
    rast = R.Rasterizer(frame)
    attributions = []

    print("⬇  地理院ベクトルタイル (建物・鉄道・道路中心線・水域・注記)")
    gfeats = gsi.load(src)
    roads, rails = gsi.road_lines(gfeats), gsi.rail_lines(gfeats)
    source = a.source
    source_img = None

    tf = None
    diag_roads, diag_rails = [], []
    if schematic:
        print("📐 模式化 (斜めの道が最も少ない角度に回転し、道路を水平/垂直/45°に整列)")
        tf = Schematizer(src, frame, [l for l, _ in roads], [w for _, w in roads], rails,
                         tile_px=SUBPX, rail_axis=a.rail_axis, straighten=a.straighten, rotate=a.rotate)
        rast.transform = tf
        print(f"   回転 {math.degrees(tf.phi):+.1f}°, 道路 {len(tf.road_segments)} 区間, 線路 {len(tf.rail_segments)} 区間, 最大移動 {tf.max_disp * frame.mpp:.0f}m")
        seg_roads, seg_rails = [], []
        def is_diag(ab):
            return a.diagonal and diagonal.is_diagonal(ab[0], ab[1], SUBPX * 0.75)

        # 45° segments are placed later as diagonal tile pieces, the rest is rasterized
        for pa, pb, w in tf.road_segments:
            ab = tf.out(np.array([pa, pb]))
            diag_roads.append(ab) if is_diag(ab) else seg_roads.append((ab, w))
        for pa, pb, _ in tf.rail_segments:
            ab = tf.out(np.array([pa, pb]))
            diag_rails.append(ab) if is_diag(ab) else seg_rails.append(ab)
        print(f"   45°斜めパーツ: 道路 {len(diag_roads)} 区間, 線路 {len(diag_rails)} 区間")

    def draw_network(fill_min_w):
        if schematic:
            gsi.draw_roads(rast, seg_roads, fill_area=True, min_width_m=max(fill_min_w, a.tile_m * 0.5), raw=True)
            gsi.draw_rails(rast, seg_rails, min_width_m=a.tile_m * 0.6, raw=True)
        else:
            gsi.draw_rails(rast, rails, min_width_m=a.tile_m * 0.6)

    if source == "plateau":
        ds = plateau.find_datasets(src)
        if not ds:
            print("⚠  この地域のPLATEAU土地利用/道路データが見つからないため gsi ソースで生成します")
            source = "gsi"
        else:
            print(f"⬇  PLATEAU {ds['city']} {ds['years']}")
            gsi.draw_water(rast, gfeats)
            if not schematic:
                gsi.draw_roads(rast, roads, fill_area=True)  # outside PLATEAU coverage
            n_luse = plateau.draw_landuse(rast, ds, src, road_land=not schematic)
            n_tran = 0 if schematic else plateau.draw_roads(rast, ds, src)
            print(f"   土地利用 {n_luse} 面, 道路 {n_tran} 面")
            gsi.draw_buildings(rast, gfeats)
            draw_network(0)
            attributions += [plateau.ATTRIBUTION, gsi.ATTRIBUTION]

    if source == "gsi":
        gsi.draw_water(rast, gfeats)
        gsi.draw_buildings(rast, gfeats)
        if not schematic:
            gsi.draw_roads(rast, roads, fill_area=True, min_width_m=a.tile_m * 0.5)
        draw_network(a.tile_m * 0.5)
        attributions += [gsi.ATTRIBUTION]

    if source == "google":
        from .sources import google
        print("⬇  Google Static Maps")
        source_img, cls = google.render(frame)
        rast.layers["cls"].paste(Image.fromarray(cls))
        # rails/roads from the image have no centerlines; borrow them from GSI for connectivity
        gsi.draw_roads(rast, roads, fill_area=False)
        gsi.draw_rails(rast, rails, min_width_m=a.tile_m * 0.6)
        gsi.draw_buildings(rast, gfeats)
        attributions += [google.ATTRIBUTION, gsi.ATTRIBUTION]

    semantic_img = R.colorize(rast.array("cls"))
    if source_img is None:
        source_img = semantic_img

    print("🧩 抽象化")
    kinds = abstract(rast, SUBPX)
    kinds, base, fam, col = diagonal.apply(kinds, diag_roads, diag_rails, SUBPX)
    groups = building_groups(base)
    tiles = assign_tiles(base, groups, seed=a.seed, extra_road=fam == 1, extra_rail=fam == 2)
    overlay = overlay_tiles(fam, col)
    tileset = build_tileset(seed=a.seed)

    labels = []
    for l in gsi.labels(gfeats, frame, tf):
        tx, ty = l["px"][0] / SUBPX, l["px"][1] / SUBPX
        if tx < kinds.shape[1] and ty < kinds.shape[0]:
            labels.append({"name": l["name"], "kind": l["kind"], "code": l["code"], "tile": [round(tx, 2), round(ty, 2)]})

    lon0, lat0, lon1, lat1 = frame.bounds_lonlat()
    title = a.title or a.place
    meta = {"place": title, "display_name": place["display_name"], "source": source,
            "center": [round(lat, 6), round(lon, 6)], "bounds": [lon0, lat0, lon1, lat1],
            "tile_m": a.tile_m, "layout": a.layout, "attribution": " / ".join(attributions)}
    out = a.out or "out/" + re.sub(r'[\\/:*?"<>| ]+', "_", title)
    write_all(out, kinds=kinds, tiles=tiles, tileset=tileset, source_img=source_img,
              semantic_img=semantic_img, labels=labels, meta=meta, overlay=overlay)

    counts = np.bincount(kinds.ravel(), minlength=len(KINDS))
    summary = ", ".join(f"{KINDS[i]} {c / kinds.size:.0%}" for i, c in sorted(enumerate(counts), key=lambda x: -x[1]) if c)
    print(f"   {summary}")
    print(f"✅ {out}/  ({time.time() - t0:.1f}s)  index.html / map.png / map.tmj / map.json")


if __name__ == "__main__":
    main()
