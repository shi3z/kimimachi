"""Schematization: turn the real street network into a "mental map".

People remember a town as a simplified diagram: the railway is a straight
vertical line, the streets leaving the station run straight across, corners
are right angles. This module

  1. rotates the area to the angle at which the fewest streets are diagonal
     (or, on request, so the railway lines up with a screen axis),
  2. builds a topological graph of roads and rails, simplifies every chain
     between junctions (Douglas-Peucker) and forces each remaining segment to
     be horizontal / vertical (or 45°) when that is a small correction,
  3. derives a smooth displacement field from how the network moved and uses
     it to warp everything else (land use, buildings, water, labels), so a
     building stays on the same side of the same street.
"""
import math

import numpy as np
from scipy.ndimage import gaussian_filter, map_coordinates
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree


# ------------------------------------------------------------------ rotation
def dominant_angle(lines, sym):
    """Length-weighted mean direction of line segments, with `sym`-fold symmetry (2 = lines, 4 = grid)."""
    acc = 0j
    for pts in lines:
        d = np.diff(pts, axis=0)
        ln = np.hypot(d[:, 0], d[:, 1])
        acc += (ln * np.exp(1j * sym * np.arctan2(d[:, 1], d[:, 0]))).sum()
    if abs(acc) == 0:
        return None
    return np.angle(acc) / sym


def min_diagonal_rotation(road_lines, rail_lines, rail_weight=3.0, step_deg=0.5):
    """Rotation (radians, within ±45°) after which the least road length is diagonal.

    A segment counts as diagonal when it is more than 22.5° away from both axes
    (it would be drawn with 45° tiles). Rails count `rail_weight` times so a
    railway also prefers to end up straight. Ties are broken by how far segments
    are from the axes on average.
    """
    vecs, wts = [], []
    for lines, w in ((road_lines, 1.0), (rail_lines, rail_weight)):
        for pts in lines:
            d = np.diff(np.asarray(pts, float), axis=0)
            if len(d):
                vecs.append(d)
                wts.append(np.full(len(d), w))
    if not vecs:
        return 0.0
    d = np.concatenate(vecs)
    w = np.concatenate(wts) * np.hypot(d[:, 0], d[:, 1])
    theta = np.degrees(np.arctan2(d[:, 1], d[:, 0]))
    best, best_cost = 0.0, None
    for phi in np.arange(-45.0, 45.0, step_deg):
        a = np.mod(theta + phi, 90.0)
        dev = np.minimum(a, 90.0 - a)                      # 0..45° from the nearest axis
        cost = (w * (dev > 22.5)).sum() + 0.1 * (w * dev / 45.0).sum()
        if best_cost is None or cost < best_cost:
            best, best_cost = phi, cost
    return math.radians(best)


def choose_rotation(rail_lines, road_lines, rail_axis="vertical", mode="auto"):
    """mode auto: fewest diagonal roads; rail: railway along rail_axis; none: keep north up."""
    if mode == "none":
        return 0.0
    if mode == "auto":
        return min_diagonal_rotation(road_lines, rail_lines)
    return _rail_rotation(rail_lines, road_lines, rail_axis)


def _rail_rotation(rail_lines, road_lines, rail_axis="vertical"):
    """Rotation (radians) that makes the railway vertical/horizontal, or aligns the street grid."""
    th = dominant_angle(rail_lines, 2) if rail_lines else None
    if th is not None:
        target = {"vertical": math.pi / 2, "horizontal": 0.0}.get(rail_axis)
        if target is None:  # auto: nearest axis
            target = math.pi / 2 if abs(math.sin(th)) > abs(math.cos(th)) else 0.0
        phi = target - th
    else:
        th4 = dominant_angle(road_lines, 4)
        phi = -th4 if th4 is not None else 0.0
    # equivalent rotation with the smallest magnitude (lines have 180° symmetry)
    return (phi + math.pi / 2) % math.pi - math.pi / 2


# ------------------------------------------------------------------ graph
def _dp(pts, tol):
    """Douglas-Peucker: indices of kept points."""
    if len(pts) <= 2:
        return list(range(len(pts)))
    a, b = pts[0], pts[-1]
    ab = b - a
    n = np.hypot(*ab)
    if n == 0:
        dist = np.hypot(*(pts - a).T)
    else:
        dist = np.abs(ab[0] * (pts[:, 1] - a[1]) - ab[1] * (pts[:, 0] - a[0])) / n
    i = int(dist.argmax())
    if dist[i] <= tol:
        return [0, len(pts) - 1]
    left = _dp(pts[: i + 1], tol)
    right = _dp(pts[i:], tol)
    return left + [j + i for j in right[1:]]


class Network:
    """Undirected graph of polylines with merged vertices, split into junction-to-junction chains."""

    def __init__(self, lines, attrs, merge_tol):
        allpts = np.concatenate(lines) if lines else np.zeros((0, 2))
        # merge vertices closer than merge_tol (tile seams, shared junctions)
        tree = cKDTree(allpts)
        pairs = tree.query_pairs(merge_tol, output_type="ndarray")
        n = len(allpts)
        g = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(n, n)) if len(pairs) else coo_matrix((n, n))
        _, lab = connected_components(g, directed=False)
        cnt = np.bincount(lab)
        self.pos = np.stack([np.bincount(lab, allpts[:, 0]) / cnt, np.bincount(lab, allpts[:, 1]) / cnt], 1)

        self.adj = {}
        self.edge_attr = {}
        off = 0
        for pts, attr in zip(lines, attrs):
            ids = lab[off: off + len(pts)]
            off += len(pts)
            for a, b in zip(ids[:-1], ids[1:]):
                if a == b:
                    continue
                self.adj.setdefault(a, set()).add(b)
                self.adj.setdefault(b, set()).add(a)
                key = (min(a, b), max(a, b))
                self.edge_attr[key] = max(self.edge_attr.get(key, 0), attr)
        self.chains = self._chains()

    def _chains(self):
        chains, seen = [], set()
        ends = [v for v, nb in self.adj.items() if len(nb) != 2]

        def walk(start, nxt):
            chain = [start, nxt]
            while len(self.adj[chain[-1]]) == 2 and chain[-1] != start:
                a, b = self.adj[chain[-1]]
                step = b if a == chain[-2] else a
                chain.append(step)
            return chain

        for v in ends:
            for nb in self.adj[v]:
                key = (min(v, nb), max(v, nb))
                if key in seen:
                    continue
                c = walk(v, nb)
                seen.update((min(a, b), max(a, b)) for a, b in zip(c[:-1], c[1:]))
                chains.append(c)
        # pure loops (every vertex degree 2)
        for v, nb in self.adj.items():
            for u in nb:
                key = (min(v, u), max(v, u))
                if key not in seen:
                    c = walk(v, u)
                    seen.update((min(a, b), max(a, b)) for a, b in zip(c[:-1], c[1:]))
                    chains.append(c)
        return chains


def orthogonalize(net, simplify_tol, max_dev, diag_deg=30.0, stiffness=200.0):
    """Returns (segments, controls).

    Each simplified segment gets a direction constraint: horizontal / vertical when
    within `diag_deg` of an axis, otherwise 45° diagonal -- but only if that needs
    less than `max_dev` px of sideways movement; other segments stay free. Positions are solved by
    least squares (constraints weighted by `stiffness` vs. staying where they were),
    so straightening never drags a street hundreds of meters away; H/V runs are
    then snapped exactly.

    segments: list of (p_new_a, p_new_b, attr)
    controls: (old_points, new_points) for every original vertex, used to build the warp
    """
    from scipy.sparse import csr_matrix, identity
    from scipy.sparse.linalg import spsolve

    pos = net.pos
    kept_chains = []
    kept_nodes = set()
    for c in net.chains:
        idx = _dp(pos[c], simplify_tol)
        kept_chains.append((c, idx))
        kept_nodes.update(c[i] for i in idx)
    nodes = np.array(sorted(kept_nodes))
    nid = {v: i for i, v in enumerate(nodes)}
    m = len(nodes)
    p0 = pos[nodes]

    tan_d = math.tan(math.radians(diag_deg))
    rows, cols, vals = [], [], []
    h_edges, v_edges = [], []
    r = 0
    for c, idx in kept_chains:
        for i, j in zip(idx[:-1], idx[1:]):
            a, b = nid[c[i]], nid[c[j]]
            if a == b:
                continue
            dx, dy = pos[c[j]] - pos[c[i]]
            if abs(dy) <= tan_d * abs(dx):          # horizontal: y_a = y_b
                if abs(dy) > max_dev:
                    continue
                h_edges.append((a, b))
                terms = [(m + a, 1), (m + b, -1)]
            elif abs(dx) <= tan_d * abs(dy):        # vertical: x_a = x_b
                if abs(dx) > max_dev:
                    continue
                v_edges.append((a, b))
                terms = [(a, 1), (b, -1)]
            else:                                   # diagonal: |dx| = |dy| with matching signs
                if abs(abs(dx) - abs(dy)) > max_dev:
                    continue
                sg = 1 if dx * dy > 0 else -1
                terms = [(a, 1), (b, -1), (m + a, -sg), (m + b, sg)]
            for col, v in terms:
                rows.append(r); cols.append(col); vals.append(v)
            r += 1
    A = csr_matrix((vals, (rows, cols)), shape=(r, 2 * m))
    x0 = np.concatenate([p0[:, 0], p0[:, 1]])
    lhs = (identity(2 * m) + stiffness * (A.T @ A)).tocsc()
    sol = spsolve(lhs, x0)
    new = np.stack([sol[:m], sol[m:]], 1)

    # exact snapping of H / V runs (the solve leaves ~1/stiffness residual)
    def snap(edges, coord):
        e = np.array(edges, dtype=np.int64).reshape(-1, 2)
        g = coo_matrix((np.ones(len(e)), (e[:, 0], e[:, 1])), shape=(m, m))
        _, lab = connected_components(g, directed=False)
        new[:, coord] = (np.bincount(lab, new[:, coord]) / np.bincount(lab))[lab]

    snap(v_edges, 0)
    snap(h_edges, 1)

    segments, old_pts, new_pts = [], [], []
    for c, idx in kept_chains:
        width = max(net.edge_attr[(min(a, b), max(a, b))] for a, b in zip(c[:-1], c[1:]))
        for i, j in zip(idx[:-1], idx[1:]):
            pa, pb = new[nid[c[i]]], new[nid[c[j]]]
            pts = octilinear(pa, pb)
            segments.extend((p, q, width) for p, q in zip(pts[:-1], pts[1:]))
            # every original vertex between i..j slides proportionally along the new segment
            sub = pos[c[i: j + 1]]
            s = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(sub, axis=0).T))])
            t = s / s[-1] if s[-1] > 0 else np.zeros_like(s)
            old_pts.append(sub)
            new_pts.append(pa + t[:, None] * (pb - pa))
    if not old_pts:
        return segments, (np.zeros((0, 2)), np.zeros((0, 2)))
    return segments, (np.concatenate(old_pts), np.concatenate(new_pts))


def octilinear(pa, pb, tol=0.08):
    """Split a segment into horizontal/vertical + 45° pieces with the same endpoints.

    A segment at an arbitrary angle becomes  straight half -> 45° part -> straight half,
    so every road can be drawn with axis or 45° tiles without moving its junctions.
    """
    d = pb - pa
    ax, ay = abs(d[0]), abs(d[1])
    big, small = max(ax, ay), min(ax, ay)
    if big == 0 or small <= tol * big or big - small <= tol * big:
        return [pa, pb]
    sx, sy = np.sign(d[0]), np.sign(d[1])
    diag = np.array([sx * small, sy * small])
    axis = np.array([sx * (ax - small), 0.0]) if ax > ay else np.array([0.0, sy * (ay - small)])
    p1 = pa + axis / 2
    p2 = p1 + diag
    return [pa, p1, p2, pb]


# ------------------------------------------------------------------ transform
class Schematizer:
    """Maps source-frame px -> output-frame px (rotation + network-driven warp)."""

    def __init__(self, src_frame, out_frame, road_lines, road_widths, rail_lines,
                 tile_px, rail_axis="vertical", smooth_tiles=2.0, straighten=3.0, rotate="auto"):
        self.c = np.array([src_frame.W / 2, src_frame.H / 2])
        self.offset = np.array([(src_frame.W - out_frame.W) / 2, (src_frame.H - out_frame.H) / 2])
        self.phi = choose_rotation(rail_lines, road_lines, rail_axis, rotate)
        cs, sn = math.cos(self.phi), math.sin(self.phi)
        self.R = np.array([[cs, -sn], [sn, cs]])

        rr = [self.rotate(p) for p in road_lines]
        rl = [self.rotate(p) for p in rail_lines]
        merge = tile_px * 0.25
        simplify = tile_px * 1.0
        road_net = Network(rr, road_widths, merge) if rr else None
        rail_net = Network(rl, [0] * len(rl), merge) if rl else None
        empty = ([], (np.zeros((0, 2)),) * 2)
        self.road_segments, rc = orthogonalize(road_net, simplify, max_dev=tile_px * straighten) if road_net else empty
        # rails are simplified and straightened much harder: a line should read as one straight stroke
        self.rail_segments, lc = orthogonalize(rail_net, simplify * 4, max_dev=tile_px * straighten * 8 / 3) if rail_net else empty

        old = np.concatenate([rc[0], lc[0]])
        disp = np.concatenate([rc[1], lc[1]]) - old
        self.max_disp = float(np.hypot(*disp.T).max()) if len(disp) else 0.0
        # Smooth displacement field by normalized convolution on a coarse grid:
        # nearby buildings move together with their street instead of being torn apart.
        self.cell = cell = tile_px * 1.0
        gw, gh = int(src_frame.W / cell) + 2, int(src_frame.H / cell) + 2
        gx = np.clip((old[:, 0] / cell).astype(int), 0, gw - 1)
        gy = np.clip((old[:, 1] / cell).astype(int), 0, gh - 1)
        flat = gy * gw + gx
        wsum = np.bincount(flat, minlength=gw * gh).reshape(gh, gw).astype(float)
        dxs = np.bincount(flat, disp[:, 0], gw * gh).reshape(gh, gw)
        dys = np.bincount(flat, disp[:, 1], gw * gh).reshape(gh, gw)
        sig = smooth_tiles
        w = gaussian_filter(wsum, sig)
        eps = 1e-3 * w.max() if w.size else 1.0
        self.fx = gaussian_filter(dxs, sig) / (w + eps)
        self.fy = gaussian_filter(dys, sig) / (w + eps)

    def rotate(self, pts):
        return (np.asarray(pts, float) - self.c) @ self.R.T + self.c

    def warp(self, q):
        # bilinear lookup in the displacement grid (cell centers at (i + .5) * cell)
        u = q / self.cell - 0.5
        coords = [u[:, 1], u[:, 0]]
        dx = map_coordinates(self.fx, coords, order=1, mode="nearest")
        dy = map_coordinates(self.fy, coords, order=1, mode="nearest")
        return q + np.stack([dx, dy], 1)

    def __call__(self, pts):
        """Transform an Nx2 array of source-frame px into output-frame px."""
        return self.warp(self.rotate(pts)) - self.offset

    def out(self, p):
        """A point already in rotated/schematic space -> output frame."""
        return np.asarray(p, float) - self.offset
