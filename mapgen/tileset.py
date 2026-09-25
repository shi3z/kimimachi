"""Procedurally painted 16x16 pixel-art tileset and autotile assignment.

Layout: one row per tile family, 16 columns.
  * autotiled families: column = 4-bit mask of same-family neighbours (N=1, E=2, S=4, W=8)
  * textured families : column = random variant
"""
import numpy as np
from PIL import Image

from .abstract import BUILDING_KINDS, K, ROADLIKE, _run

T = 16
N, E, S, W = 1, 2, 4, 8


class Tile:
    def __init__(self, bg):
        self.a = np.zeros((T, T, 4), np.uint8)
        self.a[:, :] = (*bg, 255)

    def px(self, x, y, c):
        if 0 <= x < T and 0 <= y < T:
            self.a[y, x] = (*c, 255)

    def rect(self, x0, y0, x1, y1, c):  # inclusive-exclusive
        self.a[max(0, y0):max(0, y1), max(0, x0):max(0, x1)] = (*c, 255)

    def speckle(self, rng, colors, n):
        for _ in range(n):
            self.px(int(rng.integers(T)), int(rng.integers(T)), colors[int(rng.integers(len(colors)))])

    def image(self):
        return Image.fromarray(self.a, "RGBA")


def shade(c, f):
    return tuple(int(max(0, min(255, v * f))) for v in c)


# ---------------------------------------------------------------- palettes
GRASS = (104, 176, 72)
GRASS_D = (84, 150, 60)
GRASS_L = (136, 200, 96)
WATER = (56, 120, 208)
WATER_L = (104, 160, 232)
WATER_D = (40, 96, 176)
SHORE = (216, 232, 248)
ASPHALT = (116, 116, 124)
CURB = (196, 196, 188)
RAILC = (210, 210, 220)
SLEEPER = (112, 80, 56)
GRAVEL = (146, 138, 128)
WALL = (236, 228, 208)
WINDOW = (112, 168, 216)
OUTLINE = (52, 44, 48)


# ---------------------------------------------------------------- painters
def p_grass(v, rng, base=GRASS, dark=GRASS_D, light=GRASS_L):
    t = Tile(base)
    t.speckle(rng, [dark, light], 14)
    if v % 4 == 0:  # tuft
        x, y = int(rng.integers(2, 13)), int(rng.integers(3, 13))
        for dx, dy in ((0, 0), (1, -1), (2, 0)):
            t.px(x + dx, y + dy, dark)
    if v == 5:  # tiny flower
        x, y = int(rng.integers(2, 13)), int(rng.integers(2, 13))
        t.px(x, y, (248, 240, 200))
    return t


def p_forest(mask, rng):
    t = p_grass(1, rng)
    dark, mid, light = (32, 88, 48), (48, 116, 60), (84, 152, 76)
    # canopy fills the tile, pulled back where the forest ends
    x0 = 0 if mask & W else 2
    x1 = 16 if mask & E else 14
    y0 = 0 if mask & N else 2
    y1 = 16 if mask & S else 13
    t.rect(x0, y0, x1, y1, mid)
    for _ in range(9):  # leaf clumps
        cx, cy = int(rng.integers(x0, x1)), int(rng.integers(y0, y1))
        t.rect(cx - 1, cy - 1, cx + 2, cy + 1, light)
        t.px(cx, cy + 1, dark)
    t.speckle(rng, [dark], 10)
    if not mask & S:  # trunks / shadow at the forest edge
        t.rect(x0, y1, x1, y1 + 1, dark)
        for x in range(x0 + 2, x1 - 1, 5):
            t.rect(x, y1, x + 2, min(16, y1 + 3), (96, 64, 40))
    if not mask & N:
        t.rect(x0 + 1, y0, x1 - 1, y0 + 1, light)
    return t


def p_paddy(v, rng):
    t = Tile((118, 178, 132))
    t.speckle(rng, [(136, 196, 170), (100, 160, 150)], 10)
    for y in range(2, 16, 4):
        for x in range(1 + (y // 4) % 2, 16, 3):
            t.px(x, y, (60, 132, 60))
            t.px(x, y - 1, (84, 156, 72))
    if v % 3 == 0:
        t.rect(0, 15, 16, 16, (150, 130, 90))  # levee
    return t


def p_field(v, rng):
    t = Tile((168, 120, 72))
    for y in range(1, 16, 3):
        t.rect(0, y, 16, y + 1, (136, 92, 56))
    t.speckle(rng, [(188, 140, 90)], 8)
    if v % 2:
        for y in range(0, 16, 3):
            for x in range(1, 16, 3):
                t.px(x, y, (96, 164, 64))
    return t


def p_water(mask, rng, base=WATER):
    t = Tile(base)
    for _ in range(4):
        x, y = int(rng.integers(1, 12)), int(rng.integers(2, 14))
        t.rect(x, y, x + 3, y + 1, WATER_L)
    t.speckle(rng, [WATER_D], 6)
    # shoreline where neighbours are dry
    if not mask & N:
        t.rect(0, 0, 16, 2, SHORE); t.rect(0, 2, 16, 3, WATER_L)
    if not mask & S:
        t.rect(0, 14, 16, 16, SHORE); t.rect(0, 13, 16, 14, WATER_D)
    if not mask & W:
        t.rect(0, 0, 2, 16, SHORE); t.rect(2, 0, 3, 16, WATER_L)
    if not mask & E:
        t.rect(14, 0, 16, 16, SHORE); t.rect(13, 0, 14, 16, WATER_D)
    return t


def p_road(mask, rng, base=ASPHALT):
    t = Tile(base)
    t.speckle(rng, [shade(base, 1.12), shade(base, 0.9)], 12)
    # curbs where the road ends
    for bit, rect in ((N, (0, 0, 16, 1)), (S, (0, 15, 16, 16)), (W, (0, 0, 1, 16)), (E, (15, 0, 16, 16))):
        if not mask & bit:
            t.rect(*rect, CURB)
    # centre dashes on straight 2-way segments
    if mask in (E | W,):
        t.rect(3, 7, 7, 8, (232, 220, 150)); t.rect(11, 7, 15, 8, (232, 220, 150))
    elif mask in (N | S,):
        t.rect(7, 3, 8, 7, (232, 220, 150)); t.rect(7, 11, 8, 15, (232, 220, 150))
    return t


def p_bridge(mask, rng):
    t = p_road(mask | N | S | E | W, rng, base=(150, 132, 112))
    rail = (84, 60, 44)
    for bit, rect in ((N, (0, 0, 16, 2)), (S, (0, 14, 16, 16)), (W, (0, 0, 2, 16)), (E, (14, 0, 16, 16))):
        if not mask & bit:
            t.rect(*rect, rail)
    if not mask & N or not mask & S:
        for x in range(1, 16, 4):
            if not mask & N:
                t.px(x, 2, OUTLINE)
            if not mask & S:
                t.px(x, 13, OUTLINE)
    return t


def _tracks(t, mask):
    """Draw rails from the tile centre towards each connected side."""
    if mask == 0:
        mask = E | W
    horiz = mask & (E | W)
    vert = mask & (N | S)
    if horiz:
        xa = 0 if mask & W else 5
        xb = 16 if mask & E else 11
        for x in range(xa + 1, xb, 3):
            t.rect(x, 3, x + 2, 13, SLEEPER)
        t.rect(xa, 5, xb, 6, RAILC); t.rect(xa, 10, xb, 11, RAILC)
    if vert:
        ya = 0 if mask & N else 5
        yb = 16 if mask & S else 11
        for y in range(ya + 1, yb, 3):
            t.rect(3, y, 13, y + 2, SLEEPER)
        t.rect(5, ya, 6, yb, RAILC); t.rect(10, ya, 11, yb, RAILC)


def p_rail(mask, rng):
    t = Tile(GRAVEL)
    t.speckle(rng, [(170, 162, 150), (120, 112, 104)], 30)
    _tracks(t, mask)
    return t


def p_rail_bridge(mask, rng):
    t = p_water(0b1111, rng)
    if mask & (E | W) or mask == 0:
        t.rect(0, 3, 16, 13, (96, 84, 76))
    if mask & (N | S):
        t.rect(3, 0, 13, 16, (96, 84, 76))
    _tracks(t, mask)
    return t


def p_crossing(v, rng):
    t = p_road(N | S | E | W, rng)
    stripe = [(240, 200, 40), (40, 40, 40)]
    if v == 0:  # rails run east-west, road north-south
        for x in range(16):
            t.px(x, 0, stripe[(x // 2) % 2]); t.px(x, 15, stripe[(x // 2) % 2])
        t.rect(0, 5, 16, 6, RAILC); t.rect(0, 10, 16, 11, RAILC)
    else:
        for y in range(16):
            t.px(0, y, stripe[(y // 2) % 2]); t.px(15, y, stripe[(y // 2) % 2])
        t.rect(5, 0, 6, 16, RAILC); t.rect(10, 0, 11, 16, RAILC)
    return t


def p_park(v, rng):
    t = p_grass(v, rng, base=(120, 196, 96), dark=(96, 170, 80), light=(152, 216, 120))
    for _ in range(2 if v % 3 else 0):
        t.px(int(rng.integers(1, 15)), int(rng.integers(1, 15)), [(248, 224, 80), (240, 128, 168), (255, 255, 255)][int(rng.integers(3))])
    if v == 7:  # small tree
        t.rect(5, 3, 11, 9, (56, 128, 64)); t.rect(6, 4, 9, 6, (96, 168, 88)); t.rect(7, 9, 9, 12, (104, 72, 48))
    return t


def p_parking(v, rng):
    t = Tile((150, 150, 158))
    t.speckle(rng, [(160, 160, 168), (138, 138, 146)], 10)
    t.rect(0, 0, 1, 7, (236, 236, 236)); t.rect(8, 0, 9, 7, (236, 236, 236))
    if v % 5 == 1:  # parked car
        c = [(200, 60, 60), (60, 100, 200), (230, 230, 230), (40, 40, 48)][v % 4]
        t.rect(2, 1, 7, 9, c); t.rect(3, 3, 6, 5, (150, 200, 230))
    return t


def p_yard(v, rng):
    t = p_grass(v, rng, base=(140, 196, 104), dark=(116, 172, 88), light=(168, 212, 128))
    if v % 4 == 1:  # hedge
        t.rect(0, 12, 16, 15, (64, 136, 72)); t.rect(0, 12, 16, 13, (96, 168, 88))
    if v % 4 == 2:  # stepping stones
        for x in (3, 8, 12):
            t.rect(x, 7, x + 2, 9, (200, 196, 180))
    return t


def p_plaza(v, rng):
    t = Tile((212, 202, 184))
    for i in range(0, 16, 4):
        t.rect(0, i, 16, i + 1, (190, 180, 164)); t.rect(i, 0, i + 1, 16, (190, 180, 164))
    t.speckle(rng, [(224, 216, 200)], 5)
    return t


def p_bare(v, rng):
    t = Tile((196, 168, 120))
    t.speckle(rng, [(176, 148, 104), (214, 190, 146), (150, 130, 100)], 22)
    return t


def make_building(roof, wall=WALL, style="gable"):
    roof_d, roof_l = shade(roof, 0.78), shade(roof, 1.18)

    def paint(mask, rng):
        t = Tile(roof)
        wall_h = 0 if mask & S else 6
        top = 16 - wall_h
        if style == "gable":      # tiled roof rows
            for y in range(2, top, 3):
                t.rect(0, y, 16, y + 1, roof_d)
            if not mask & N:
                t.rect(0, 0, 16, 1, roof_l)
        elif style == "flat":     # concrete roof with parapet and AC units
            t.speckle(rng, [shade(roof, 0.94), shade(roof, 1.05)], 10)
            if rng.random() < 0.35 and top > 8:
                x, y = int(rng.integers(3, 10)), int(rng.integers(3, top - 5))
                t.rect(x, y, x + 4, y + 3, (168, 172, 180)); t.rect(x + 1, y + 1, x + 3, y + 2, (120, 124, 132))
            for bit, rect in ((N, (0, 0, 16, 2)), (W, (0, 0, 2, top)), (E, (14, 0, 16, top))):
                if not mask & bit:
                    t.rect(*rect, roof_l)
        elif style == "sawtooth":  # factory
            for x in range(0, 16, 4):
                t.rect(x, 0, x + 1, top, roof_d); t.rect(x + 1, 0, x + 2, top, roof_l)
        if wall_h:
            t.rect(0, top, 16, 16, wall)
            t.rect(0, top, 16, top + 1, shade(roof, 0.55))
            t.rect(0, 15, 16, 16, shade(wall, 0.7))
            if style == "sawtooth":
                t.rect(3, top + 2, 13, 15, (150, 156, 164))
                for y in range(top + 3, 15, 2):
                    t.rect(3, y, 13, y + 1, (126, 132, 140))
            elif rng.random() < 0.3:
                t.rect(6, top + 2, 10, 15, (132, 92, 64)); t.px(9, top + 7, (232, 200, 96))
            else:
                t.rect(3, top + 2, 6, top + 4, WINDOW); t.rect(10, top + 2, 13, top + 4, WINDOW)
        if not mask & W:
            t.rect(0, 0, 1, 16, OUTLINE)
        if not mask & E:
            t.rect(15, 0, 16, 16, OUTLINE)
        if not mask & N:
            t.rect(0, 0, 16, 1, OUTLINE)
        return t

    return paint


def _diag_piece(col, rng, paint_px):
    """Overlay piece of a 45° band (see diagonal.py). col: 0-5 pieces, +6 = variant flag."""
    t = Tile((0, 0, 0))
    t.a[:, :, 3] = 0
    piece, variant = col % 6, col // 6
    if col >= 12:
        return t
    mirror = piece >= 3
    k = {0: 0, 1: 16, 2: -16}[piece % 3]          # d offset of centre / right(left) / below cell
    along = {0: 0, 1: 16, 2: 16}[piece % 3]
    for y in range(T):
        for x in range(T):
            d = x - y + k                          # signed distance across the "\" band (x-y units)
            a = x + y + along                      # position along the band
            c = paint_px(d, a, variant, rng)
            if c is not None:
                xx = T - 1 - x if mirror else x
                t.a[y, xx] = (*c, 255)
    return t


def _road_px(d, a, no_curb, rng, w=11):
    if abs(d) > w:
        return None
    if abs(d) == w and not no_curb:
        return CURB
    if d == 0 and (a // 4) % 2 == 0 and not no_curb:
        return (232, 220, 150)
    r = rng.random()
    return shade(ASPHALT, 1.12) if r < 0.06 else shade(ASPHALT, 0.9) if r < 0.12 else ASPHALT


def _rail_px(d, a, _, rng, w=9):
    if abs(d) > w:
        return None
    if abs(d) == 4:
        return RAILC
    if abs(d) <= 7 and a % 5 == 0:
        return SLEEPER
    r = rng.random()
    return (170, 162, 150) if r < 0.15 else (120, 112, 104) if r < 0.3 else GRAVEL


def p_road_diag(col, rng):
    return _diag_piece(col, rng, _road_px)


def p_rail_diag(col, rng):
    return _diag_piece(col, rng, _rail_px)


HOUSE_ROOFS = [(192, 72, 64), (72, 104, 176), (80, 136, 96), (152, 100, 68)]

# (family name, painter, autotiled?)
ROWS = [
    ("grass", p_grass, False),
    ("forest", p_forest, True),
    ("paddy", p_paddy, False),
    ("field", p_field, False),
    ("water", p_water, True),
    ("road", p_road, True),
    ("bridge", p_bridge, True),
    ("rail", p_rail, True),
    ("rail_bridge", p_rail_bridge, True),
    ("crossing", p_crossing, False),
    ("park", p_park, False),
    ("parking", p_parking, False),
    ("yard", p_yard, False),
    ("plaza", p_plaza, False),
    ("bare", p_bare, False),
    *[(f"house{i}", make_building(c), True) for i, c in enumerate(HOUSE_ROOFS)],
    ("shop0", make_building((150, 164, 184), wall=(212, 222, 236), style="flat"), True),
    ("shop1", make_building((184, 170, 160), wall=(236, 226, 214), style="flat"), True),
    ("factory", make_building((128, 148, 168), wall=(196, 200, 204), style="sawtooth"), True),
    ("public", make_building((216, 176, 112), wall=(244, 240, 228), style="flat"), True),
    # overlay rows (transparent outside the band)
    ("road_diag", p_road_diag, False),
    ("rail_diag", p_rail_diag, False),
]
ROW = {name: i for i, (name, _, _) in enumerate(ROWS)}


def build_tileset(seed=7):
    img = Image.new("RGBA", (T * 16, T * len(ROWS)))
    for r, (name, painter, _) in enumerate(ROWS):
        for col in range(16):
            rng = np.random.default_rng(seed * 1000 + r * 16 + col)
            img.paste(painter(col, rng).image(), (col * T, r * T))
    return img


def _neigh_mask(same):
    """same: bool grid -> 4-bit mask of same neighbours (map edge counts as same)."""
    p = np.pad(same, 1, constant_values=True)
    return (p[:-2, 1:-1] * N + p[1:-1, 2:] * E + p[2:, 1:-1] * S + p[1:-1, :-2] * W).astype(np.int32)


def assign_tiles(kinds, groups, seed=7, extra_road=None, extra_rail=None):
    """Return a grid of tile indices (row*16 + col) into the tileset."""
    rng = np.random.default_rng(seed)
    variant = rng.integers(0, 16, kinds.shape)
    tiles = np.zeros(kinds.shape, np.int32)
    kind_names = {v: k for k, v in K.items()}

    def isin(names):
        return np.isin(kinds, [K[n] for n in names])

    families = {
        "forest": isin(["forest"]),
        "water": isin(["water", "bridge", "rail_bridge"]),
        "road": np.isin(kinds, list(ROADLIKE)) | (extra_road if extra_road is not None else False),
        "rail": isin(["rail", "rail_bridge", "crossing"]) | (extra_rail if extra_rail is not None else False),
    }
    masks = {name: _neigh_mask(m) for name, m in families.items()}
    # buildings: neighbours are "same" only inside the same roof group
    g = np.pad(groups, 1, constant_values=-1)
    gmask = (((g[:-2, 1:-1] == groups) * N) + ((g[1:-1, 2:] == groups) * E)
             + ((g[2:, 1:-1] == groups) * S) + ((g[1:-1, :-2] == groups) * W))
    rail_ew = masks["rail"] & (E | W)
    # Where tracks run in parallel (station yards) neighbour masks become junction soup;
    # draw straight track along the longer run direction instead.
    rail = families["rail"]
    run_h, run_v = _run(rail, 1), _run(rail, 0)
    straight = np.where(run_h >= run_v, E | W, N | S)
    rm = masks["rail"]
    nbits = (rm & 1) + (rm >> 1 & 1) + (rm >> 2 & 1) + (rm >> 3 & 1)
    wide = (np.minimum(run_h, run_v) >= 2) | (nbits >= 3)
    masks["rail"] = np.where(wide, straight, rm)
    rail_ew = np.where(wide, straight & (E | W), rail_ew)

    for kid in np.unique(kinds):
        name = kind_names[int(kid)]
        sel = kinds == kid
        if name == "forest":
            tiles[sel] = ROW["forest"] * 16 + masks["forest"][sel]
        elif name == "water":
            tiles[sel] = ROW["water"] * 16 + masks["water"][sel]
        elif name in ("road", "bridge"):
            tiles[sel] = ROW[name] * 16 + masks["road"][sel]
        elif name in ("rail", "rail_bridge"):
            tiles[sel] = ROW[name] * 16 + masks["rail"][sel]
        elif name == "crossing":
            tiles[sel] = ROW["crossing"] * 16 + np.where(rail_ew[sel] > 0, 0, 1)
        elif int(kid) in BUILDING_KINDS:
            h = (groups[sel].astype(np.int64) * 2654435761) % 2**32
            if name == "house":
                row = ROW["house0"] + h % len(HOUSE_ROOFS)
            elif name == "shop":
                row = ROW["shop0"] + h % 2
            else:
                row = ROW[name]
            tiles[sel] = row * 16 + gmask[sel]
        else:
            tiles[sel] = ROW[name] * 16 + variant[sel]
    return tiles


def overlay_tiles(fam, col):
    """Overlay tile indices (-1 = empty) from diagonal.apply output."""
    rows = np.array([-1, ROW["road_diag"], ROW["rail_diag"]])
    return np.where(fam > 0, rows[fam] * 16 + col, -1)


def render_map(tile_grid, tileset, overlay=None):
    rows, cols = tile_grid.shape
    out = Image.new("RGBA", (cols * T, rows * T))
    cache = {}

    def crop(t):
        if t not in cache:
            cache[t] = tileset.crop(((t % 16) * T, (t // 16) * T, (t % 16 + 1) * T, (t // 16 + 1) * T))
        return cache[t]

    for (r, c), t in np.ndenumerate(tile_grid):
        out.paste(crop(int(t)), (c * T, r * T))
    if overlay is not None:
        for (r, c), t in np.ndenumerate(overlay):
            if t >= 0:
                piece = crop(int(t))
                out.paste(piece, (c * T, r * T), piece)
    return out
