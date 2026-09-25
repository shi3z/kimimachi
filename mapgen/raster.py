"""Semantic classes and a rasterizer that burns vector features into class images."""
import numpy as np
from PIL import Image, ImageDraw

# --- semantic classes of the intermediate "abstract map image" -------------
NONE, GRASS, FOREST, PADDY, FIELD, WATER, ROAD, RAIL, PARK, PARKING = range(10)
LOT_RES, LOT_COM, LOT_IND, LOT_PUB, BARE, BLDG, PAVED = range(10, 17)

CLASS_NAMES = [
    "none", "grass", "forest", "paddy", "field", "water", "road", "rail", "park", "parking",
    "lot_res", "lot_com", "lot_ind", "lot_pub", "bare", "building", "paved",
]
CLASS_COLORS = [
    (236, 236, 226), (170, 208, 140), (74, 132, 76), (160, 214, 190), (214, 196, 140), (96, 160, 230),
    (255, 255, 255), (90, 90, 100), (130, 200, 110), (200, 200, 205), (246, 222, 200), (248, 196, 196),
    (206, 196, 226), (200, 222, 246), (222, 212, 190), (150, 120, 110), (226, 226, 220),
]
N_CLASSES = len(CLASS_NAMES)
LOT_CLASSES = (LOT_RES, LOT_COM, LOT_IND, LOT_PUB)


def colorize(cls):
    pal = np.array(CLASS_COLORS, dtype=np.uint8)
    return Image.fromarray(pal[cls])


class Rasterizer:
    """Draws features onto several aligned layers.

    cls    : the semantic map (later draws win)
    land   : lot type from land use, kept separately so buildings can be typed
    water  : every water polygon, even if something was drawn over it (bridges)
    road_cl: thin road centerlines (guarantees connectivity after downsampling)
    rail_cl: thin rail centerlines
    """

    def __init__(self, frame, transform=None):
        self.frame = frame
        self.transform = transform  # maps source px -> this frame's px (schematic warp)
        size = (frame.W, frame.H)
        self.layers = {name: Image.new("L", size, 0) for name in ("cls", "land", "water", "road_cl", "rail_cl")}
        self.draw = {name: ImageDraw.Draw(img) for name, img in self.layers.items()}

    def _tf(self, pts, raw):
        return pts if raw or self.transform is None else self.transform(np.asarray(pts, float))

    def polygon(self, rings, value, layer="cls", raw=False):
        """rings: list of Nx2 arrays in px; first is the exterior. Holes are left to later draws."""
        ext = self._tf(rings[0], raw)
        if len(ext) >= 3:
            self.draw[layer].polygon([tuple(p) for p in ext], fill=value)

    def line(self, pts, width_px, value, layer="cls", raw=False):
        if len(pts) < 2:
            return
        pts = self._tf(pts, raw)
        w = max(1, int(round(width_px)))
        pts = [tuple(p) for p in pts]
        self.draw[layer].line(pts, fill=value, width=w, joint="curve" if w > 2 else None)
        if w > 3:  # round caps so segments join cleanly
            r = w / 2
            for x, y in (pts[0], pts[-1]):
                self.draw[layer].ellipse((x - r, y - r, x + r, y + r), fill=value)

    def array(self, layer):
        return np.asarray(self.layers[layer])


# --- Mapbox Vector Tile helpers -------------------------------------------
def mvt_features(data, z, tx, ty, frame, layers=None):
    """Yield (layer_name, properties, geom_type, parts) with coordinates in frame px.

    parts: for polygons a list of polygons (each a list of rings), for lines a list of lines.
    """
    import mapbox_vector_tile

    if not data:
        return
    tile = mapbox_vector_tile.decode(data, default_options={"y_coord_down": True})
    n = 2**z
    for lname, layer in tile.items():
        if layers and lname not in layers:
            continue
        ext = layer.get("extent", 4096)

        def conv(coords, ext=ext):
            a = np.asarray(coords, dtype=np.float64)
            if a.ndim != 2 or len(a) == 0:
                return None
            mx = (tx + a[:, 0] / ext) / n
            my = (ty + a[:, 1] / ext) / n
            px, py = frame.merc_to_px(mx, my)
            return np.stack([px, py], axis=1)

        for f in layer["features"]:
            g = f["geometry"]
            t, c = g["type"], g["coordinates"]
            if t == "Polygon":
                polys = [c]
            elif t == "MultiPolygon":
                polys = c
            elif t == "LineString":
                polys = [[c]]
            elif t == "MultiLineString":
                polys = [[l] for l in c]
            elif t in ("Point", "MultiPoint"):
                pts = conv([c] if t == "Point" else c)
                if pts is not None:
                    yield lname, f["properties"], "point", [pts]
                continue
            else:
                continue
            parts = []
            for poly in polys:
                rings = [r for r in (conv(ring) for ring in poly) if r is not None]
                if rings:
                    parts.append(rings)
            if not parts:
                continue
            kind = "polygon" if "Polygon" in t else "line"
            if kind == "line":
                parts = [p[0] for p in parts]
            yield lname, f["properties"], kind, parts


def ring_area(r):
    x, y = r[:, 0], r[:, 1]
    return 0.5 * abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))
