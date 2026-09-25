"""45° diagonal roads / rails on the tile grid.

A 45° band cannot be drawn with whole tiles; it covers a chain of *centre* cells
plus a triangle of the two cells that flank every step. For a "\\" line through
cells (i, i), (i+1, i+1) ... the cell to the right of each centre holds the
band's lower-left triangle and the cell below holds its upper-right triangle.
"/" is the mirror image (left neighbour: lower-right, below: upper-left).

These pieces live on an overlay layer (transparent outside the band) so the
ground of the cell stays visible under the band's edges.
"""
import numpy as np

from .abstract import BUILDING_KINDS, K, ROADLIKE

# overlay piece ids (column in the overlay rows of the tileset)
BS_C, BS_R, BS_B, FS_C, FS_L, FS_B = range(6)   # "\" centre/right/below, "/" centre/left/below
NO_CURB = 6                                       # + offset: road variants without curbs (on asphalt)

GROUND_UNDER = {K["house"]: K["yard"], K["shop"]: K["plaza"], K["factory"]: K["plaza"], K["public"]: K["plaza"],
                K["forest"]: K["grass"], K["rail"]: K["bare"], K["rail_bridge"]: K["water"], K["crossing"]: K["road"]}


def is_diagonal(pa, pb, min_len, tol=0.08):
    dx, dy = pb[0] - pa[0], pb[1] - pa[1]
    ln = max(abs(dx), abs(dy))
    return ln >= min_len and abs(abs(dx) - abs(dy)) <= tol * ln


def _cells(pa, pb, cell_px):
    """Centre cells of a 45° segment, snapped to the tile grid, and its direction."""
    a = np.asarray(pa) / cell_px - 0.5
    b = np.asarray(pb) / cell_px - 0.5
    sx, sy = int(np.sign(b[0] - a[0])), int(np.sign(b[1] - a[1]))
    n = int(round((abs(b[0] - a[0]) + abs(b[1] - a[1])) / 2))
    c0, r0 = int(round(a[0])), int(round(a[1]))
    return [(r0 + t * sy, c0 + t * sx) for t in range(n + 1)], ("\\" if sx == sy else "/")


def apply(kinds, road_segs, rail_segs, cell_px):
    """Place diagonal pieces.

    Returns (kinds, base_kinds, overlay):
      kinds      game logic grid (diagonal cells become road_diag / rail_diag)
      base_kinds what to paint underneath (ground, asphalt, water)
      overlay    (row_name, column) per cell or None, as two arrays: family (0 none, 1 road, 2 rail) and column
    """
    rows, cols = kinds.shape
    base = kinds.copy()
    fam = np.zeros(kinds.shape, np.int8)
    col = np.zeros(kinds.shape, np.int16)
    prio = np.zeros(kinds.shape, np.int8)   # centre (2) beats corner (1)
    out_kinds = kinds.copy()

    def put(r, c, family, piece, p):
        if not (0 <= r < rows and 0 <= c < cols) or p < prio[r, c]:
            return
        k = kinds[r, c]
        on_road = k in ROADLIKE
        if family == 1 and on_road:
            fam[r, c], col[r, c] = 1, piece + NO_CURB      # joins an existing street: no curbs
            prio[r, c] = p
            return
        fam[r, c], col[r, c], prio[r, c] = family, piece, p
        base[r, c] = GROUND_UNDER.get(int(k), int(k))
        if int(k) in BUILDING_KINDS or k == K["forest"]:
            base[r, c] = GROUND_UNDER[int(k)]
        out_kinds[r, c] = K["road_diag"] if family == 1 else K["rail_diag"]

    for family, segs in ((2, rail_segs), (1, road_segs)):   # roads last: they win over rails
        for pa, pb in segs:
            cells, d = _cells(pa, pb, cell_px)
            for r, c in cells:
                if d == "\\":
                    put(r, c + 1, family, BS_R, 1)
                    put(r + 1, c, family, BS_B, 1)
                else:
                    put(r, c - 1, family, FS_L, 1)
                    put(r + 1, c, family, FS_B, 1)
            for r, c in cells:
                put(r, c, family, BS_C if d == "\\" else FS_C, 2)
    return out_kinds, base, fam, col
