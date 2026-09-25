"""Project PLATEAU: land use (luse) and road (tran) vector tiles from the PLATEAU data catalog."""
import json

from ..net import fetch_json, fetch_many
from ..raster import (BARE, FIELD, FOREST, GRASS, LOT_COM, LOT_IND, LOT_PUB, LOT_RES, PADDY, PARK,
                      PARKING, PAVED, ROAD, WATER, mvt_features, ring_area)

CATALOG_URL = "https://api.plateau.reearth.io/datacatalog/plateau-datasets"
Z = 16
ATTRIBUTION = "3D都市モデル（Project PLATEAU）（国土交通省）"

# luse:class_code -> semantic class. None = draw into the road/water layers instead.
LUSE_CLASS = {
    "201": PADDY, "202": FIELD, "203": FOREST, "204": WATER, "205": GRASS, "206": GRASS,
    "211": LOT_RES, "212": LOT_COM, "213": LOT_IND, "214": LOT_PUB, "215": ROAD, "216": LOT_PUB,
    "217": PARK, "218": LOT_PUB, "219": LOT_IND, "220": BARE, "221": BARE, "222": PARKING,
    "223": BARE, "224": BARE, "231": GRASS,
}


REVGEO_URL = "https://mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress"


def municipalities(frame, n=5):
    """Municipality codes (e.g. '13101') found on an n x n grid of points over the frame (GSI reverse geocoder)."""
    from ..geo import merc_to_lonlat

    codes = []
    for j in range(n):
        for i in range(n):
            lon, lat = merc_to_lonlat(frame.x0 + (i + 0.5) / n * (frame.x1 - frame.x0),
                                      frame.y0 + (j + 0.5) / n * (frame.y1 - frame.y0))
            res = fetch_json(REVGEO_URL, params={"lat": f"{lat:.5f}", "lon": f"{lon:.5f}"})
            code = (res or {}).get("results", {}).get("muniCd")
            if code and code not in codes:
                codes.append(code)
    return codes


def find_datasets(frame):
    """PLATEAU luse/tran MVT URLs for every municipality the frame touches (latest year each).

    Returns {'luse': [...], 'tran': [...], 'city': '台東区, 千代田区', 'years': {...}} or None.
    """
    codes = municipalities(frame)
    cat = fetch_json(CATALOG_URL)
    published = {str(d.get("ward_code") or d.get("city_code") or "") for d in cat["datasets"]
                 if d.get("format") == "MVT" and d["type"] == "土地利用モデル"}
    # wards of designated cities (e.g. 川崎市多摩区 14135) are published under the city code (14130);
    # only fall back to it when the ward itself has no data (Tokyo's wards are published individually)
    codes = [c if c in published else c[:4] + "0" for c in codes]
    best = {}
    for d in cat["datasets"]:
        if d.get("format") != "MVT" or d["type"] not in ("土地利用モデル", "交通（道路）モデル"):
            continue
        code = str(d.get("ward_code") or d.get("city_code") or "")
        if code not in codes:
            continue
        key = ("luse" if d["type"] == "土地利用モデル" else "tran", code)
        # several road datasets can exist per city (LOD variants); keep the first of the latest year
        if key not in best or int(d.get("year", 0)) > int(best[key].get("year", 0)):
            best[key] = d
    if not best:
        return None
    names, credits = [], []
    for (_, code), d in best.items():
        name = d.get("ward") or d.get("city")
        if name not in names:
            names.append(name)
            credits.append(f"{name}（{d.get('year')}年度）")
    return {
        "city": ", ".join(names),
        # PLATEAU citation style: 3D都市モデル（Project PLATEAU）長岡市（2024年度）
        "attribution": f"3D都市モデル（Project PLATEAU）{'、'.join(credits)}（国土交通省）",
        "luse": [d["url"] for (k, _), d in best.items() if k == "luse"],
        "tran": [d["url"] for (k, _), d in best.items() if k == "tran"],
        "years": sorted({int(d.get("year", 0)) for d in best.values()}),
    }


def _load(url, frame):
    tiles = frame.tiles(Z)
    blobs = fetch_many([url.replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y)) for z, x, y in tiles])
    feats = []
    for (z, x, y), data in zip(tiles, blobs):
        feats.extend(mvt_features(data, z, x, y, frame))
    return feats


def luse_code(props):
    code = props.get("luse_class_code")
    if code is None and "attributes" in props:
        try:
            code = json.loads(props["attributes"]).get("luse:class_code")
        except ValueError:
            code = None
    return str(code) if code is not None else None


def draw_landuse(rast, ds, frame, road_land=True):
    """Returns number of land-use polygons drawn. road_land=False skips road-land polygons."""
    polys = []
    feats = [f for url in ds["luse"] for f in _load(url, frame)]
    for _, props, kind, parts in feats:
        if kind != "polygon":
            continue
        cls = LUSE_CLASS.get(luse_code(props) or "", GRASS)
        for rings in parts:
            polys.append((ring_area(rings[0]), cls, rings))
    # Big polygons first so that polygons sitting inside their holes overwrite them.
    polys.sort(key=lambda p: -p[0])
    for _, cls, rings in polys:
        if cls == ROAD and not road_land:
            cls = PAVED   # road land beyond the drawn (schematic) carriageway: sidewalks / plazas
        rast.polygon(rings, cls)
        if cls in (LOT_RES, LOT_COM, LOT_IND, LOT_PUB):
            rast.polygon(rings, cls, "land")
        if cls == WATER:
            rast.polygon(rings, 1, "water")
    return len(polys)


def draw_roads(rast, ds, frame):
    n = 0
    feats = [f for url in ds["tran"] for f in _load(url, frame)]
    for _, props, kind, parts in feats:
        if kind != "polygon":
            continue
        for rings in parts:
            rast.polygon(rings, ROAD)
            n += 1
    return n
