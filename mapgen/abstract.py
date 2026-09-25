"""Abstraction: high-res semantic raster -> coarse grid of game terrain kinds."""
import numpy as np
from scipy import ndimage

from . import raster as R

# --- game terrain kinds ----------------------------------------------------
KINDS = [
    "grass", "forest", "paddy", "field", "water", "road", "bridge", "rail", "rail_bridge", "crossing",
    "park", "parking", "yard", "plaza", "bare", "house", "shop", "factory", "public", "road_diag", "rail_diag",
]
K = {name: i for i, name in enumerate(KINDS)}
BUILDING_KINDS = {K["house"], K["shop"], K["factory"], K["public"]}
BLOCKING_KINDS = {K["water"], K["rail"], K["rail_bridge"], K["forest"], K["rail_diag"]} | BUILDING_KINDS
ROADLIKE = {K["road"], K["bridge"], K["crossing"], K["road_diag"]}

# land class -> ground kind when nothing structural sits on the cell
GROUND_OF_CLASS = {
    R.NONE: K["grass"], R.GRASS: K["grass"], R.FOREST: K["forest"], R.PADDY: K["paddy"],
    R.FIELD: K["field"], R.WATER: K["water"], R.PARK: K["park"], R.PARKING: K["parking"],
    R.LOT_RES: K["yard"], R.LOT_COM: K["plaza"], R.LOT_IND: K["plaza"], R.LOT_PUB: K["plaza"],
    R.BARE: K["bare"], R.ROAD: K["road"], R.RAIL: K["rail"], R.BLDG: K["house"], R.PAVED: K["plaza"],
}
BUILDING_OF_LOT = {R.LOT_RES: K["house"], R.LOT_COM: K["shop"], R.LOT_IND: K["factory"], R.LOT_PUB: K["public"]}
GROUND_CANDIDATES = [c for c in range(R.N_CLASSES) if c not in (R.ROAD, R.RAIL, R.BLDG)]


def _block_mean(mask, k):
    h, w = mask.shape
    return mask.reshape(h // k, k, w // k, k).mean(axis=(1, 3))


def _block_any(mask, k):
    h, w = mask.shape
    return mask.reshape(h // k, k, w // k, k).any(axis=(1, 3))


def abstract(rast, k, *, road_thresh=0.5, bldg_thresh=0.4, rail_thresh=0.3, water_thresh=0.5):
    """Downsample the rasterizer's layers into a (rows, cols) kind grid.

    Each cell covers k x k raster pixels. Thin features (roads, rails) use their
    centerlines so they stay connected; area features use coverage fractions.
    """
    cls = rast.array("cls")
    h, w = (cls.shape[0] // k) * k, (cls.shape[1] // k) * k
    cls = cls[:h, :w]
    land = rast.array("land")[:h, :w]
    frac = {c: _block_mean(cls == c, k) for c in range(R.N_CLASSES)}
    water_under = _block_mean(rast.array("water")[:h, :w] > 0, k)
    road_cl = _block_any(rast.array("road_cl")[:h, :w] > 0, k)
    rail_cl = _block_any(rast.array("rail_cl")[:h, :w] > 0, k)

    # 1. ground: majority of non-structural classes
    stack = np.stack([frac[c] for c in GROUND_CANDIDATES])
    ground_cls = np.array(GROUND_CANDIDATES)[stack.argmax(0)]
    lut = np.zeros(R.N_CLASSES, dtype=np.int32)
    for c, kind in GROUND_OF_CLASS.items():
        lut[c] = kind
    kinds = lut[ground_cls]
    kinds = _mode_smooth(kinds, protect=set())

    # 2. buildings, typed by the surrounding land-use lots
    is_bldg = frac[R.BLDG] >= bldg_thresh
    lot_frac = np.stack([ndimage.uniform_filter(_block_mean(land == c, k), 3) for c in R.LOT_CLASSES])
    lot_kind = np.array([BUILDING_OF_LOT[c] for c in R.LOT_CLASSES])[lot_frac.argmax(0)]
    lot_kind[lot_frac.max(0) == 0] = K["house"]
    kinds[is_bldg] = lot_kind[is_bldg]

    # 3. water / rail / road, in increasing priority
    is_water = (frac[R.WATER] >= water_thresh) & ~is_bldg
    kinds[is_water] = K["water"]
    is_rail = (frac[R.RAIL] >= rail_thresh) | rail_cl
    is_road = (frac[R.ROAD] >= road_thresh) | road_cl
    is_rail = _repair_diagonals(is_rail, avoid=is_bldg)
    is_road = _repair_diagonals(is_road, avoid=is_bldg | is_rail)
    is_road = _drop_small(is_road, 3)
    kinds[is_rail] = K["rail"]
    kinds[is_road] = K["road"]
    # Where a road runs *along* the railway (under a viaduct, beside the tracks) the rail wins;
    # only roads that cross it become level crossings.
    both = is_road & is_rail
    rail_h = _run(is_rail, 1) >= _run(is_rail, 0)
    road_only = is_road & ~is_rail
    perp = np.where(rail_h, _run(road_only | both, 0), _run(road_only | both, 1))
    along = np.where(rail_h, _run(road_only | both, 1), _run(road_only | both, 0))
    crossing = both & (perp > along)
    kinds[both & ~crossing] = K["rail"]
    kinds[crossing] = K["crossing"]
    is_road = is_road & ~(both & ~crossing)
    # bridges: road/rail over water, but only spans that actually touch open water
    # (streams culverted under a street would otherwise become long "bridges")
    over_water = (water_under >= 0.4) & (is_road | is_rail)
    lab, n = ndimage.label(over_water)
    if n:
        touches = ndimage.maximum(ndimage.binary_dilation(kinds == K["water"]), lab, range(1, n + 1))
        ok = np.zeros(n + 1, bool)
        ok[1:] = np.asarray(touches) > 0
        span = ok[lab]
        kinds[is_road & ~is_rail & span] = K["bridge"]
        kinds[is_rail & ~is_road & span] = K["rail_bridge"]
    return kinds


def _run(mask, axis, cap=8):
    """Length of the straight run of True cells through each cell along axis (capped)."""
    total = mask.astype(np.int32)
    for sign in (1, -1):
        alive = mask.copy()
        for d in range(1, cap):
            shifted = np.roll(mask, sign * d, axis=axis)
            if axis == 0:
                (shifted[:d] if sign > 0 else shifted[-d:])[...] = False
            else:
                (shifted[:, :d] if sign > 0 else shifted[:, -d:])[...] = False
            alive &= shifted
            total += alive
    return total


def _mode_smooth(kinds, protect, min_votes=5):
    """Replace a cell by the kind held by >= min_votes of its 8 neighbours (removes speckle)."""
    out = kinds.copy()
    pad = np.pad(kinds, 1, mode="edge")
    rows, cols = kinds.shape
    neigh = np.stack([pad[1 + dy:1 + dy + rows, 1 + dx:1 + dx + cols]
                      for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dy or dx])
    for kind in np.unique(kinds):
        votes = (neigh == kind).sum(0)
        m = (votes >= min_votes) & (kinds != kind)
        if protect:
            m &= ~np.isin(kinds, list(protect))
        out[m] = kind
    return out


def _repair_diagonals(mask, avoid):
    """Make diagonal-only links 4-connected so a walker can follow them."""
    m = mask.copy()
    a, b = m[:-1, :-1], m[1:, 1:]      # "\" diagonal
    c, d = m[:-1, 1:], m[1:, :-1]      # "/" diagonal
    need1 = a & b & ~c & ~d
    need2 = c & d & ~a & ~b
    for need, (r1, c1), (r2, c2) in ((need1, (0, 1), (1, 0)), (need2, (0, 0), (1, 1))):
        ys, xs = np.nonzero(need)
        for y, x in zip(ys, xs):
            # fill whichever of the two empty corners is not a building
            y1, x1, y2, x2 = y + r1, x + c1, y + r2, x + c2
            if not avoid[y1, x1]:
                m[y1, x1] = True
            else:
                m[y2, x2] = True
    return m


def _drop_small(mask, min_size):
    lab, n = ndimage.label(mask)
    if n == 0:
        return mask
    sizes = ndimage.sum(mask, lab, range(1, n + 1))
    keep = np.zeros(n + 1, bool)
    keep[1:] = sizes >= min_size
    return keep[lab]


def building_groups(kinds, house_block=(2, 3)):
    """Label building footprints so each gets its own roof; long house rows are split into small houses."""
    groups = np.zeros(kinds.shape, dtype=np.int32)
    next_id = 1
    rows, cols = np.indices(kinds.shape)
    for kind in BUILDING_KINDS:
        lab, n = ndimage.label(kinds == kind)
        if n == 0:
            continue
        if kind == K["house"]:
            bh, bw = house_block
            sub = lab.astype(np.int64) * 1_000_000 + (rows // bh) * 1000 + (cols // bw)
            sub[lab == 0] = 0
            uniq, inv = np.unique(sub, return_inverse=True)
            inv = inv.reshape(sub.shape)
            ids = np.where(lab > 0, inv + next_id - 1, 0)   # uniq[0] == 0 is background
            groups = np.where(lab > 0, ids, groups)
            next_id += len(uniq)
        else:
            groups = np.where(lab > 0, lab + next_id, groups)
            next_id += n + 1
    return groups
