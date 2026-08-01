"""Correct draw order, resolved offline.

A single depth scalar per element cannot express occlusion in general. The
textbook failure is a large floor polygon and a box standing on its far half:
the floor's centroid is *nearer* than the box, so a centroid sort paints the
floor over the box and slices it in half.

Rather than accept that, we do what this whole project does -- pay for accuracy
once, at bake time. The player is unaffected: it still sorts one scalar per
frame. We just hand it depth values already in the right order, so the fix
costs nothing at runtime.

Method
------
For every pair that overlaps on screen, sample points inside the *shared*
region and compare each element's depth there. Whoever is further away at the
place where they actually overlap gets painted first. Those constraints form a
DAG which is topologically sorted, tie-broken by centroid depth.

Comparing at the overlap is what makes this antisymmetric: each pair yields at
most one constraint, so two elements can never require each other to come
first. That matters -- the obvious alternative, Newell's pairwise plane-side
tests, is *not* antisymmetric. Coplanar polygons (a subdivided floor) each test
as "in front of" the other, and adjacent faces of a box each test as "behind"
the other. Both produce 2-cycles between every neighbouring pair, which
saturates the graph with cycles and silently degrades back to centroid order.

Depth is recovered exactly by fitting a plane in screen space: for a planar
polygon under perspective, 1/depth is an affine function of (x, y); under
orthographic, depth itself is. So the fit is exact, not an approximation.
"""

from __future__ import annotations

import heapq

import numpy as np

FILL_KIND = 1

# Diagnostics from the most recent resolve() call. Cycle breaks are the number
# worth watching: they mean geometry that genuinely interpenetrates, and the
# elements involved can jump in paint order between frames.
LAST_STATS: dict = {}


# ---------------------------------------------------------------------------
# per-element screen-space depth field
# ---------------------------------------------------------------------------

def _fit_field(pts, depths, is_ortho):
    """Affine field over screen xy giving depth (ortho) or 1/depth (perspective)."""
    if len(pts) < 3:
        return None
    target = depths if is_ortho else 1.0 / np.maximum(depths, 1e-9)
    A = np.column_stack([pts[:, 0], pts[:, 1], np.ones(len(pts))])
    try:
        sol, *_ = np.linalg.lstsq(A, target, rcond=None)
    except np.linalg.LinAlgError:
        return None
    return sol


def _depths_at(item, qx, qy, is_ortho):
    """Depth of this element at each of many screen points, vectorised.

    Chaining turns short segments into long polylines, so the nearest-segment
    search runs over every query point and every segment at once -- doing it in
    a Python loop per point made the ordering pass tens of times slower.
    """
    qx = np.atleast_1d(np.asarray(qx, dtype=np.float64))
    qy = np.atleast_1d(np.asarray(qy, dtype=np.float64))
    if item["kind"] == FILL_KIND and item["field"] is not None:
        f = item["field"]
        v = f[0] * qx + f[1] * qy + f[2]
        return v if is_ortho else 1.0 / np.maximum(v, 1e-9)

    pts, d = item["pts"], item["depths"]
    A = pts[:-1]
    AB = pts[1:] - A
    denom = (AB * AB).sum(axis=1)
    Q = np.stack([qx, qy], axis=1)
    AQ = Q[:, None, :] - A[None, :, :]
    t = np.where(
        denom[None, :] > 1e-12,
        (AQ * AB[None, :, :]).sum(axis=2) / np.maximum(denom, 1e-12)[None, :],
        0.0,
    )
    np.clip(t, 0.0, 1.0, out=t)
    proj = A[None, :, :] + AB[None, :, :] * t[:, :, None]
    nearest = ((Q[:, None, :] - proj) ** 2).sum(axis=2).argmin(axis=1)
    rows = np.arange(len(qx))
    tk = t[rows, nearest]
    d0 = d[nearest]
    d1 = d[nearest + 1]
    if is_ortho:
        return d0 + (d1 - d0) * tk
    inv = (1.0 / np.maximum(d0, 1e-9)) * (1 - tk) + (1.0 / np.maximum(d1, 1e-9)) * tk
    return 1.0 / np.maximum(inv, 1e-12)


def _depth_at(item, x, y, is_ortho):
    """Single-point form, kept for the validator."""
    return float(_depths_at(item, [x], [y], is_ortho)[0])


def _walk(pts, fractions):
    """Points at the given fractions of a polyline's total length."""
    deltas = np.diff(pts, axis=0)
    lengths = np.linalg.norm(deltas, axis=1)
    total = lengths.sum()
    if total <= 1e-12:
        return np.repeat(pts[:1], len(fractions), axis=0)
    edges = np.concatenate([[0.0], np.cumsum(lengths)]) / total
    out = np.empty((len(fractions), 2))
    for i, f in enumerate(fractions):
        k = int(np.searchsorted(edges, f, side="right") - 1)
        k = max(0, min(k, len(pts) - 2))
        span = edges[k + 1] - edges[k]
        t = 0.0 if span <= 1e-12 else (f - edges[k]) / span
        out[i] = pts[k] + (pts[k + 1] - pts[k]) * t
    return out


def _inside(poly, qx, qy):
    """Even-odd point-in-polygon, vectorised over query points."""
    inside = np.zeros(len(qx), dtype=bool)
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        straddles = (yi > qy) != (yj > qy)
        dy = yj - yi
        xint = xi + (xj - xi) * (qy - yi) / (dy if abs(dy) > 1e-12 else 1e-12)
        inside ^= straddles & (qx < xint)
        j = i
    return inside


def _shared_samples(a, b, grid=3):
    """Screen points covered by both elements, or None."""
    a_fill = a["kind"] == FILL_KIND
    b_fill = b["kind"] == FILL_KIND

    if a_fill and b_fill:
        x0 = max(a["bbox"][0], b["bbox"][0])
        x1 = min(a["bbox"][1], b["bbox"][1])
        y0 = max(a["bbox"][2], b["bbox"][2])
        y1 = min(a["bbox"][3], b["bbox"][3])
        if x1 <= x0 or y1 <= y0:
            return None
        step_x = (x1 - x0) / (grid + 1)
        step_y = (y1 - y0) / (grid + 1)
        gx, gy = np.meshgrid(
            x0 + step_x * np.arange(1, grid + 1),
            y0 + step_y * np.arange(1, grid + 1),
        )
        qx, qy = gx.ravel(), gy.ravel()
        keep = _inside(a["pts"], qx, qy) & _inside(b["pts"], qx, qy)
        if not keep.any():
            return None
        return qx[keep], qy[keep]

    # one of them is a segment: walk along it and keep the part inside the fill
    seg, fill = (a, b) if not a_fill else (b, a)
    if not (a_fill or b_fill):
        return None
    # a chained polyline can be long, so scale the sampling with its length
    count = 5 if len(seg["pts"]) == 2 else min(16, 2 * len(seg["pts"]))
    walked = _walk(seg["pts"], np.linspace(0.04, 0.96, count))
    qx, qy = walked[:, 0], walked[:, 1]
    keep = _inside(fill["pts"], qx, qy)
    if not keep.any():
        return None
    return qx[keep], qy[keep]


# ---------------------------------------------------------------------------
# resolve
# ---------------------------------------------------------------------------

def resolve(items, is_ortho=False, tol=1e-4):
    """items -> depth values that sort into a correct painter's order.

    Each item needs: seed (float), kind, pts (N,2 screen), depths (N,).
    Returns floats aligned with `items` -- the original seed depths permuted
    onto the corrected ranking, so magnitudes stay smooth over time and the
    keyframe reducer still collapses them well.
    """
    n = len(items)
    if n < 2:
        return [it["seed"] for it in items]

    for it in items:
        pts = it["pts"]
        it["bbox"] = (pts[:, 0].min(), pts[:, 0].max(), pts[:, 1].min(), pts[:, 1].max())
        it["dmin"] = float(it["depths"].min())
        it["dmax"] = float(it["depths"].max())
        it["field"] = _fit_field(pts, it["depths"], is_ortho) if it["kind"] == FILL_KIND else None

    seed = np.array([it["seed"] for it in items], dtype=np.float64)
    bbox = np.array([it["bbox"] for it in items], dtype=np.float64)
    dmin = np.array([it["dmin"] for it in items])
    dmax = np.array([it["dmax"] for it in items])
    kinds = np.array([it["kind"] for it in items], dtype=np.int32)

    after = [set() for _ in range(n)]
    indeg = [0] * n

    def add_edge(behind, front):
        if behind != front and front not in after[behind]:
            after[behind].add(front)
            indeg[front] += 1

    # Only fills can meaningfully occlude -- strokes are hairlines, so
    # stroke-vs-stroke order is invisible and those pairs are skipped.
    for i in np.nonzero(kinds == FILL_KIND)[0]:
        i = int(i)
        overlap = (
            (bbox[:, 0] <= bbox[i, 1]) & (bbox[:, 1] >= bbox[i, 0])
            & (bbox[:, 2] <= bbox[i, 3]) & (bbox[:, 3] >= bbox[i, 2])
        )
        overlap[i] = False
        overlap[:i + 1] &= kinds[:i + 1] != FILL_KIND   # test each fill pair once
        for j in np.nonzero(overlap)[0]:
            j = int(j)
            # Disjoint depth intervals settle it outright, no sampling needed.
            # These pairs still need a real constraint: it is tempting to skip
            # them on the grounds that seed depth already orders them, but the
            # topological sort reorders globally, so an element held back by
            # its own constraints can be carried straight past an unconstrained
            # neighbour. Skipping here is what let the ground paint over the
            # mast legs.
            if dmax[j] < dmin[i]:
                add_edge(i, j)
                continue
            if dmax[i] < dmin[j]:
                add_edge(j, i)
                continue
            shared = _shared_samples(items[i], items[j])
            if shared is None:
                continue
            qx, qy = shared
            delta = float(np.mean(_depths_at(items[i], qx, qy, is_ortho)
                                  - _depths_at(items[j], qx, qy, is_ortho)))
            if abs(delta) <= tol:
                # Coplanar or touching: depth cannot decide, and leaving it to
                # the seed order makes the pair flicker as the seeds jitter
                # frame to frame (mast legs standing on the ground). Break it
                # deterministically -- lines belong on top of surfaces.
                if kinds[i] == FILL_KIND and kinds[j] != FILL_KIND:
                    add_edge(i, j)
                elif kinds[j] == FILL_KIND and kinds[i] != FILL_KIND:
                    add_edge(j, i)
                continue
            if delta > 0:
                add_edge(i, j)    # i is further away where they overlap
            else:
                add_edge(j, i)

    # Kahn's algorithm, tie-broken by seed depth so unconstrained elements keep
    # their natural far-to-near ordering.
    constraints = sum(len(s) for s in after)
    cycle_breaks = 0
    heap = [(-seed[i], i) for i in range(n) if indeg[i] == 0]
    heapq.heapify(heap)
    ranking = []
    blocked = set(i for i in range(n) if indeg[i] > 0)
    while len(ranking) < n:
        if not heap:
            cycle_breaks += 1
            # A genuine cycle (mutually interpenetrating geometry). Resolving it
            # properly means splitting polygons, which would break element
            # identity -- so break at the furthest element and carry on.
            victim = max(blocked, key=lambda k: seed[k])
            blocked.discard(victim)
            for src in range(n):
                after[src].discard(victim)
            indeg[victim] = 0
            heapq.heappush(heap, (-seed[victim], victim))
            continue
        _, i = heapq.heappop(heap)
        ranking.append(i)
        for j in after[i]:
            indeg[j] -= 1
            if indeg[j] == 0:
                blocked.discard(j)
                heapq.heappush(heap, (-seed[j], j))

    LAST_STATS.clear()
    LAST_STATS.update({"elements": n, "constraints": constraints, "cycle_breaks": cycle_breaks})

    # Emit the *rank*, not a depth magnitude. The player only ever sorts these,
    # so the absolute distance is information nobody reads -- and storing it
    # costs dearly: real depths are six-digit quantised integers that change
    # every frame, whereas a rank is a small integer that mostly does not
    # change at all, which delta-codes to runs of zeros.
    #
    # Ranks are also distinct by construction, which removes the tie problem
    # that absolute depths had (an edge inherits the depth of the face it
    # borders, so every edge around a face shared a value and the player broke
    # the tie arbitrarily).
    #
    # Larger value = painted earlier = further away, matching the player's
    # descending sort.
    out = [0.0] * n
    for rank, i in enumerate(ranking):
        out[i] = float(n - 1 - rank)
    return out
