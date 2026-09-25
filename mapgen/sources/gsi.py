"""国土地理院 ベクトルタイル (experimental_bvmap): buildings, rails, roads, water, labels. Nationwide."""
from ..net import fetch_many
from ..raster import BLDG, RAIL, ROAD, WATER, mvt_features

URL = "https://cyberjapandata.gsi.go.jp/xyz/experimental_bvmap/{z}/{x}/{y}.pbf"
Z = 16
ATTRIBUTION = "国土地理院ベクトルタイル"

# road centerline width rank -> meters
RNK_WIDTH_M = {0: 2.5, 1: 4.0, 2: 8.0, 3: 16.0, 4: 24.0}
SKIP_RAIL_STATES = {"トンネル", "地下", "建設中", "運休中"}


def load(frame):
    tiles = frame.tiles(Z)
    blobs = fetch_many([URL.format(z=z, x=x, y=y) for z, x, y in tiles])
    feats = []
    for (z, x, y), data in zip(tiles, blobs):
        tile_feats = list(mvt_features(data, z, x, y, frame))
        has_detail_rail = any(l == "railway" and p.get("orgGILvl") == "2500" for l, p, _, _ in tile_feats)
        for lname, props, kind, parts in tile_feats:
            # rails exist at two scales; prefer the detailed 1:2500 geometry when present
            if lname == "railway" and has_detail_rail and props.get("orgGILvl") != "2500":
                continue
            feats.append((lname, props, kind, parts))
    return feats


def draw_water(rast, feats):
    mpp = rast.frame.mpp
    for lname, props, kind, parts in feats:
        if lname == "waterarea" and kind == "polygon":
            for rings in parts:
                rast.polygon(rings, WATER)
                rast.polygon(rings, 1, "water")
        elif lname == "river" and kind == "line":
            for line in parts:
                rast.line(line, 3.0 / mpp, WATER)
                rast.line(line, 3.0 / mpp, 1, "water")


def road_lines(feats):
    """[(Nx2 px, width_m)] road centerlines."""
    out = []
    for lname, props, kind, parts in feats:
        # 2701 ordinary centerline, 2703 bridges/elevated sections, 2711 other; skip expressways (motorway=1)
        if lname == "road" and kind == "line" and props.get("ftCode") in (2701, 2703, 2711) \
                and props.get("motorway") != 1:
            w = RNK_WIDTH_M.get(props.get("rnkWidth"), 3.0)
            out.extend((line, w) for line in parts)
    return out


def rail_lines(feats):
    out = []
    for lname, props, kind, parts in feats:
        if lname != "railway" or kind != "line":
            continue
        # railState is text at 1:2500 and a code at 1:25000 (2 = tunnel, 3 = underground / subway)
        if props.get("railState") in SKIP_RAIL_STATES or props.get("railState") in (2, 3):
            continue
        out.extend(parts)
    return out


def draw_roads(rast, lines, fill_area, min_width_m=0.0, raw=False):
    """lines: [(pts, width_m)]. Always burns thin centerlines; with fill_area also paints the road surface."""
    mpp = rast.frame.mpp
    for line, width in lines:
        rast.line(line, 1, 1, "road_cl", raw=raw)
        if fill_area:
            rast.line(line, max(width, min_width_m) / mpp, ROAD, raw=raw)


def draw_rails(rast, lines, min_width_m=4.0, raw=False):
    mpp = rast.frame.mpp
    for line in lines:
        rast.line(line, max(4.0, min_width_m) / mpp, RAIL, raw=raw)
        rast.line(line, 1, 1, "rail_cl", raw=raw)


def draw_buildings(rast, feats):
    for lname, props, kind, parts in feats:
        if lname != "building":
            continue
        if kind == "polygon":
            for rings in parts:
                rast.polygon(rings, BLDG)
        else:  # outlines delivered as (closed) lines
            for line in parts:
                if len(line) >= 4 and abs(line[0] - line[-1]).max() < 1.0:
                    rast.polygon([line], BLDG)


def labels(feats, frame, transform=None):
    out = []
    for lname, props, kind, parts in feats:
        if lname != "label" or not props.get("knj"):
            continue
        pt = parts[0][:1]
        x, y = (transform(pt) if transform else pt)[0]
        if 0 <= x < frame.W and 0 <= y < frame.H:
            ctg = int(props.get("annoCtg", 0))
            kind_name = {4: "station", 8: "facility", 6: "facility", 3: "nature", 2: "area"}.get(ctg // 100, "other")
            if ctg == 800:
                kind_name = "district"
            if kind_name == "station" and not props["knj"].endswith("駅"):
                kind_name = "route"   # line / expressway names share the transport category
            out.append({"name": props["knj"], "kind": kind_name, "code": ctg, "px": [float(x), float(y)]})
    # the same label can appear in several tiles
    seen, uniq = set(), []
    for l in out:
        k = (l["name"], round(l["px"][0] / 20), round(l["px"][1] / 20))
        if k not in seen:
            seen.add(k)
            uniq.append(l)
    return uniq
