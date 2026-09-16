# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
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

import math

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


def _surface_prep(tris):
    """Per-triangle constants for `_surface_read`, computed once per item."""
    tx, ty, td = tris
    ax, ay = tx[:, 0], ty[:, 0]
    e1x, e1y = tx[:, 1] - ax, ty[:, 1] - ay
    e2x, e2y = tx[:, 2] - ax, ty[:, 2] - ay
    area = e1x * e2y - e1y * e2x
    keep = np.abs(area) > 1e-12            # an edge-on face covers no pixel
    inv = np.zeros_like(area)
    inv[keep] = 1.0 / area[keep]
    return {
        "x0": tx.min(axis=1)[keep], "x1": tx.max(axis=1)[keep],
        "y0": ty.min(axis=1)[keep], "y1": ty.max(axis=1)[keep],
        "ax": ax[keep], "ay": ay[keep],
        "e1x": e1x[keep], "e1y": e1y[keep], "e2x": e2x[keep], "e2y": e2y[keep],
        "inv": inv[keep], "d": td[keep],
    }


def _surface_read(item, qx, qy, is_ortho):
    """Depth of a merged part's front surface at each query point; NaN off it.

    Read off the triangle the point lies on, interpolated the way the camera
    interpolates -- linearly in depth for an orthographic camera, in inverse
    depth for a perspective one. Where front faces overlap on screen the nearest
    wins, because that is the one painted.
    """
    surf = item.get("surface")
    if surf is None:
        surf = item["surface"] = _surface_prep(item["tris"])
    out = np.full(len(qx), np.nan)
    if not len(surf["inv"]):
        return out
    # Bound the temporary query-by-triangle mask, not the geometry or samples.
    # One array operation per point was still the scalar path even when the
    # caller supplied a batch. Keep only bounding-box candidates for the more
    # expensive barycentric read, then reduce all hits to the nearest surface.
    chunk = max(1, 1_048_576 // len(surf["inv"]))
    for start in range(0, len(qx), chunk):
        x = qx[start:start + chunk, None]
        y = qy[start:start + chunk, None]
        rows, cand = np.nonzero((surf["x0"] <= x) & (x <= surf["x1"])
                                & (surf["y0"] <= y) & (y <= surf["y1"]))
        if not len(cand):
            continue
        vx = x[rows, 0] - surf["ax"][cand]
        vy = y[rows, 0] - surf["ay"][cand]
        inv = surf["inv"][cand]
        w1 = (vx * surf["e2y"][cand] - vy * surf["e2x"][cand]) * inv
        w2 = (surf["e1x"][cand] * vy - surf["e1y"][cand] * vx) * inv
        w0 = 1.0 - w1 - w2
        eps = -1e-9
        inside = (w0 >= eps) & (w1 >= eps) & (w2 >= eps)
        if not inside.any():
            continue
        d = surf["d"][cand[inside]]
        w = np.stack([w0[inside], w1[inside], w2[inside]], axis=1)
        if is_ortho:
            vals = (w * d).sum(axis=1)
        else:
            vals = 1.0 / np.maximum((w / np.maximum(d, 1e-9)).sum(axis=1), 1e-12)
        nearest = np.full(len(x), np.inf)
        np.minimum.at(nearest, rows[inside], vals)
        nearest[nearest == np.inf] = np.nan
        out[start:start + chunk] = nearest
    return out


def _depths_at(item, qx, qy, is_ortho):
    """Depth of this element at each of many screen points, vectorised.

    Chaining turns short segments into long polylines, so the nearest-segment
    search runs over every query point and every segment at once -- doing it in
    a Python loop per point made the ordering pass tens of times slower.
    """
    qx = np.atleast_1d(np.asarray(qx, dtype=np.float64))
    qy = np.atleast_1d(np.asarray(qy, dtype=np.float64))
    probe = item.get("probe")
    if probe is not None:
        # Real samples off the surface, not a polynomial through them. A
        # merged fill is a whole part, and a part can be a bowl: the middle
        # curves away while the rim curves toward the camera, so no plane
        # describes it. The Voyager dish's back shell missed its own depth by
        # up to 1.35 units under an affine fit, on a shell 0.07 thick -- which
        # is why the beams sitting inside it sorted behind it. A quadratic
        # halved that and made other parts worse, so fit nothing: read the
        # depth where the two elements actually overlap.
        #
        # Taking the nearest sample makes this a step function of the query
        # point: as the overlap between two parts drifts, the sample that wins
        # changes and the depth jumps the whole gap. Parts whose depths run
        # close trade places on that step, which is 17 more fill-vs-fill
        # reversals that do not hold, all on merged elements. Invisible in a
        # scene that draws every fill one colour -- but `pick()` returns
        # whichever is on top, and that is what a part explorer runs on.
        #
        # Weighting every sample by inverse fourth-power distance removes the
        # step: nothing enters or leaves a set, so the field is continuous. It
        # was built and measured, and it is better at both things it was asked
        # about --
        #
        #     ray cast          538 wrong of 24,000 -> 480   (2.24% -> 2.00%)
        #     fill/fill reversals that do not hold  341 -> 336
        #
        # -- and it takes the sixty-frame bake from 12.5 minutes to 55. The
        # graph is not what got slower: constraints 3,035,580 -> 3,033,740,
        # cycle breaks 1783 -> 1571, drops 507,597 -> 483,366, all slightly
        # *better*. It is this lookup, which turns out to dominate the ordering
        # pass. Eleven array operations on a few hundred samples instead of six,
        # several million times a frame.
        #
        # Reverted on that. Continuity is worth having and it is not worth four
        # times the bake for five reversals nobody can see. Anything cheaper has
        # to be continuous too: k nearest samples still steps when the k-th
        # changes, so it buys nothing.
        #
        # None of that was the real fault, which is that a sample is a vertex.
        # On a low-poly body the vertices are the corners of long faces, so the
        # nearest one to the middle of a face is half the face away, and the
        # depth read there belongs to a corner, which on a part reaching away
        # from the camera can be most of its depth off. So read the surface
        # itself -- the
        # triangle the point lies on, which is exact, and continuous across the
        # edge two faces share. The nearest sample stays for points off the
        # surface, which a centre between two separate patches can be.
        px, py, pd = probe
        out = (_surface_read(item, qx, qy, is_ortho) if item.get("tris") is not None
               else np.full(len(qx), np.nan))
        miss = ~np.isfinite(out)
        if miss.any():
            mx, my = qx[miss], qy[miss]
            fallback = np.empty(len(mx))
            chunk = max(1, 1_048_576 // len(px))
            for start in range(0, len(mx), chunk):
                d2 = ((mx[start:start + chunk, None] - px[None, :]) ** 2
                      + (my[start:start + chunk, None] - py[None, :]) ** 2)
                fallback[start:start + chunk] = pd[d2.argmin(axis=1)]
            out[miss] = fallback
        return out
    if item["kind"] == FILL_KIND and item["field"] is not None:
        f = item["field"]
        v = f[0] * qx + f[1] * qy + f[2]
        return v if is_ortho else 1.0 / np.maximum(v, 1e-9)

    pts, d = item["pts"], item["depths"]
    # Batched long-chain queries need the same memory bound as surface reads.
    # Using the point count also covers the extra segment of a closed chain.
    chunk = max(1, 1_048_576 // len(pts))
    if len(qx) > chunk:
        return np.concatenate([
            _depths_at(item, qx[start:start + chunk], qy[start:start + chunk], is_ortho)
            for start in range(0, len(qx), chunk)])
    if item.get("closed") and len(pts) > 2:
        pts = np.vstack([pts, pts[:1]])
        d = np.concatenate([d, d[:1]])
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


def _poly_edges(pts):
    """Edge arrays plus bounding box, in the form _inside() consumes."""
    x1 = np.ascontiguousarray(pts[:, 0], dtype=np.float64)
    y1 = np.ascontiguousarray(pts[:, 1], dtype=np.float64)
    x2 = np.empty_like(x1)
    y2 = np.empty_like(y1)
    x2[:-1] = x1[1:]; x2[-1] = x1[0]
    y2[:-1] = y1[1:]; y2[-1] = y1[0]
    return x1, y1, x2, y2, x1.min(), x1.max(), y1.min(), y1.max()


def _inside(poly, qx, qy):
    """Even-odd point-in-polygon, vectorised over points *and* edges.

    This is the hottest function in the whole bake -- it was 59% of the runtime
    when it looped over edges in Python, because each edge cost a handful of
    numpy calls whose overhead dwarfed the arithmetic. Doing every point
    against every edge in one shot turns dozens of tiny calls into a few.
    """
    qx = np.atleast_1d(np.asarray(qx, dtype=np.float64))
    qy = np.atleast_1d(np.asarray(qy, dtype=np.float64))
    if qx.size == 0:
        return np.zeros(0, dtype=bool)

    # A polygon is tested against many different polylines, so derive its edge
    # arrays once and keep them on the item. Rolling them per call was 178k
    # np.roll invocations and three seconds on its own.
    if isinstance(poly, dict):
        edges = poly.get("_edges")
        if edges is None:
            edges = _poly_edges(poly["pts"])
            poly["_edges"] = edges
    else:
        edges = _poly_edges(poly)
    x1, y1, x2, y2, bx0, bx1, by0, by1 = edges

    # cheap reject: anything outside the polygon's box cannot be inside it
    within = (qx >= bx0) & (qx <= bx1) & (qy >= by0) & (qy <= by1)
    if not within.any():
        return np.zeros(qx.size, dtype=bool)

    px = qx[within, None]
    py = qy[within, None]
    straddles = (y1[None, :] > py) != (y2[None, :] > py)
    dy = (y2 - y1)[None, :]
    xint = x1[None, :] + (x2 - x1)[None, :] * (py - y1[None, :]) / np.where(
        np.abs(dy) > 1e-12, dy, 1e-12
    )
    hits = (straddles & (px < xint)).sum(axis=1) & 1

    out = np.zeros(qx.size, dtype=bool)
    out[within] = hits.astype(bool)
    return out


def _inside_rows(poly, xs, ys):
    """`_inside` for rows of points that share a y: xs (R, C), ys (R,) -> (R, C).

    Where an edge crosses a row depends only on the row's y, so it is computed
    once per row rather than once per point -- the same formula on the same
    numbers, so every point gets exactly the verdict `_inside` would give it.
    """
    edges = poly.get("_edges")
    if edges is None:
        edges = poly["_edges"] = _poly_edges(poly["pts"])
    x1, y1, x2, y2, bx0, bx1, by0, by1 = edges
    py = ys[:, None]
    straddles = (y1[None, :] > py) != (y2[None, :] > py)
    dy = (y2 - y1)[None, :]
    xint = x1[None, :] + (x2 - x1)[None, :] * (py - y1[None, :]) / np.where(
        np.abs(dy) > 1e-12, dy, 1e-12
    )
    # A non-straddling edge can never count, so park its crossing where no
    # point is to its left; the strict comparison below then never counts it.
    xint = np.where(straddles, xint, -np.inf)
    hits = (xs[:, :, None] < xint[:, None, :]).sum(axis=2) & 1
    within = ((xs >= bx0) & (xs <= bx1)) & ((ys >= by0) & (ys <= by1))[:, None]
    return hits.astype(bool) & within


def _scc(nodes, after):
    """Tarjan's strongly connected components over a subgraph. Iterative.

    Used to find the edges that are *actually* on a cycle. When the sort
    deadlocks, most of the blocked elements are not in the cycle at all -- they
    are simply downstream of it, waiting on something that is. Breaking their
    constraints resolves nothing and throws away good evidence.

    Recursion is not an option here: components run to hundreds of elements on
    a real scene and Python's stack limit is well below that.
    """
    index, low, comp = {}, {}, {}
    stack, on_stack = [], set()
    counter = ncomp = 0
    for root in nodes:
        if root in index:
            continue
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        work = [(root, iter([v for v in after[root] if v in nodes]))]
        while work:
            v, it = work[-1]
            descended = False
            for w in it:
                if w not in index:
                    index[w] = low[w] = counter
                    counter += 1
                    stack.append(w)
                    on_stack.add(w)
                    work.append((w, iter([u for u in after[w] if u in nodes])))
                    descended = True
                    break
                if w in on_stack and index[w] < low[v]:
                    low[v] = index[w]
            if descended:
                continue
            work.pop()
            if work and low[v] < low[work[-1][0]]:
                low[work[-1][0]] = low[v]
            if low[v] == index[v]:
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp[w] = ncomp
                    if w == v:
                        break
                ncomp += 1
    return comp


def _bbox_of(item):
    box = item.get("bbox")
    if box is not None:
        return box
    pts = item["pts"]
    return (pts[:, 0].min(), pts[:, 0].max(), pts[:, 1].min(), pts[:, 1].max())


def _segments(item):
    """Point pairs the player actually strokes, closing segment included.

    A chain that comes back on itself is emitted closed, and the player calls
    closePath(), so it draws one segment more than the point list spells out.
    Walking `range(len(pts) - 1)` misses it, and that segment is not a detail:
    on the Voyager probe the bus rim's closing segment runs 126px straight
    across the feed-horn shaft, so the pair was never sampled, never
    constrained, and the rim was painted over a shaft a full unit in front of
    it. Nothing caught it, because every checker walked the points the same way.
    """
    pts = item["pts"]
    spans = [(pts[k], pts[k + 1]) for k in range(len(pts) - 1)]
    if item.get("closed") and len(pts) > 2:
        spans.append((pts[-1], pts[0]))
    return spans


def _overlap_centre(a, b, is_ortho, cells=24):
    """The centre of what two elements share, as one point, or None.

    Two fills share an area and its centre is the area's centroid. A stroke
    shares a length with a fill -- the part of the polyline inside it -- and
    its centre is the midpoint of that length. Two strokes share nothing worth
    ordering, and never reach here.

    One point, deliberately, where this used to take the mean of up to
    twenty-four. The sample set was chosen by a grid recomputed every frame
    from the bounding-box intersection, shrunk against a budget, with two
    fallbacks for when the grid missed the region entirely. Which points are
    in that set changes discretely as the overlap moves, so the mean steps
    when the geometry does not, and a pair whose depths run close changes
    places on the step: 8409 order swaps over one orbit of the probe, seen as
    parts jumping in front of each other and strokes crossing over their own
    fills.

    A centre moves continuously because the region does, and it is the same
    point every frame for the same geometry. That is worth more here than a
    better average, because the ordering only needs the sign.

    One point over the *whole* region, and it does not have to be a point either
    element covers: two shapes can meet in several places, and the mean of those
    places is a fourth place between them. A bar laid across a bracket with three
    webs is decided from the gap between two of them.

    Deciding per patch instead was built and measured, 2026-08-28: split the
    shared cells into connected components, sample each at its own centre, and
    let the pair go the way most of the shared *area* wants. It is the better
    rule on paper -- every sample lands on a pixel both elements cover, and the
    verdict is the one that paints fewest pixels wrong for that pair -- and it
    reduces to exactly this function when the pair meets in one place, which is
    seven pairs in eight.

    It bought nothing. Ray-cast wrong pixels 538 -> 580 of 24,000, which is
    inside the noise on that count; occlusion violations 10,474 -> 10,422; and
    the bake 5% longer. Not for want of cases: 12.5% of overlapping pairs on the
    probe meet in more than one patch, 9.9% have no patch holding 90% of the
    overlap, and 7.5% are close enough for the vote to decide them. The union
    centroid simply lands in the dominant patch nearly every time it matters,
    and where it does not, the pair is one that crosses -- so both answers are
    wrong somewhere and picking the cheaper one locally does not survive the
    rest of the graph.

    What was not separated, and would have to be for a second attempt: sampling
    per patch and voting by area are two changes, and they were measured
    together.
    """
    a_fill = a["kind"] == FILL_KIND
    b_fill = b["kind"] == FILL_KIND

    if a_fill and b_fill:
        x0 = max(a["bbox"][0], b["bbox"][0])
        x1 = min(a["bbox"][1], b["bbox"][1])
        y0 = max(a["bbox"][2], b["bbox"][2])
        y1 = min(a["bbox"][3], b["bbox"][3])
        if x1 <= x0 or y1 <= y0:
            return None
        # Cells span the shared box rather than a fixed number of pixels, so a
        # sliver is sampled finely across its thin direction instead of being
        # stepped over -- which is what the old grid needed its fallbacks for.
        gx, gy = np.meshgrid(
            x0 + ((x1 - x0) / (cells + 1)) * np.arange(1, cells + 1),
            y0 + ((y1 - y0) / (cells + 1)) * np.arange(1, cells + 1),
        )
        qx, qy = gx.ravel(), gy.ravel()
        both = _inside(a, qx, qy) & _inside(b, qx, qy)
        if not both.any():
            return None
        return float(qx[both].mean()), float(qy[both].mean())

    fill, line = (a, b) if a_fill else (b, a)
    if not (a_fill or b_fill):
        return None

    # Exact, not sampled: `_inside_spans` gives the stretches of each segment
    # that lie within the polygon, so the shared length and its centre come
    # out of arithmetic rather than out of how densely the line was walked.
    return _line_centre(line, _line_spans(fill, [line])[0])


def _line_centre(line, spans_of):
    """Midpoint of the length of `line` that `spans_of` puts inside a fill."""
    total = 0.0
    sx = sy = 0.0
    for k, (p, q) in enumerate(_segments(line)):
        if k not in spans_of:
            continue
        ax, ay = float(p[0]), float(p[1])
        bx, by = float(q[0]), float(q[1])
        seg = math.hypot(bx - ax, by - ay)
        if seg <= 0.0:
            continue
        for t0, t1 in spans_of[k]:
            if t1 <= t0:
                continue
            span = (t1 - t0) * seg
            mid = 0.5 * (t0 + t1)
            total += span
            sx += (ax + (bx - ax) * mid) * span
            sy += (ay + (by - ay) * mid) * span
    if total <= 0.0:
        return None
    return sx / total, sy / total


def _line_length(line, spans_of):
    """Length of `line` that `spans_of` puts inside a fill, in pixels."""
    total = 0.0
    for k, (p, q) in enumerate(_segments(line)):
        if k not in spans_of:
            continue
        seg = math.hypot(float(q[0]) - float(p[0]), float(q[1]) - float(p[1]))
        total += sum(max(0.0, t1 - t0) for t0, t1 in spans_of[k]) * seg
    return total


def _fill_fill_centres(items, bbox, pairs, cells=24, areas=None):
    """`_overlap_centre` for many fill pairs at once; the same answers, bit for bit.

    Per pair, the grid and the two inside tests were a handful of numpy calls on
    576 points, and on Apollo-Soyuz that ran 1.2 million times -- 84% of the
    ordering pass and most of the bake was call overhead, not arithmetic. So the
    grid points of every pair a fill takes part in go through one inside test
    for that fill. Each point's test is the same arithmetic on the same numbers
    whichever batch it lands in, and each centre is still the mean of the same
    points in the same order, so nothing downstream can tell the difference.
    """
    m = len(pairs)
    out = [None] * m
    if not m:
        return out
    ia = np.fromiter((p[0] for p in pairs), dtype=np.intp, count=m)
    ib = np.fromiter((p[1] for p in pairs), dtype=np.intp, count=m)
    x0 = np.maximum(bbox[ia, 0], bbox[ib, 0])
    x1 = np.minimum(bbox[ia, 1], bbox[ib, 1])
    y0 = np.maximum(bbox[ia, 2], bbox[ib, 2])
    y1 = np.minimum(bbox[ia, 3], bbox[ib, 3])
    live = np.nonzero((x1 > x0) & (y1 > y0))[0]
    if not len(live):
        return out
    k = np.arange(1, cells + 1)
    # Written exactly as the per-pair version computes them: a step, times the
    # cell numbers, plus the origin.
    xs = x0[live, None] + ((x1[live] - x0[live]) / (cells + 1))[:, None] * k[None, :]
    ys = y0[live, None] + ((y1[live] - y0[live]) / (cells + 1))[:, None] * k[None, :]
    count = cells * cells
    # meshgrid(xs, ys).ravel() runs x across each row of y.
    both = np.ones((len(live), count), dtype=bool)
    for side in (ia[live], ib[live]):
        order = np.argsort(side, kind="stable")
        owners = side[order]
        cuts = np.flatnonzero(np.diff(owners)) + 1
        for group in np.split(order, cuts):
            item = items[int(side[group[0]])]
            if not both[group].any():
                continue
            # Bound the points-by-edges temporary, as the surface read does.
            step = max(1, 8_000_000 // (count * max(1, len(item["pts"]))))
            for s in range(0, len(group), step):
                rows = group[s:s + step]
                rows = rows[both[rows].any(axis=1)]
                if not len(rows):
                    continue
                gx = np.broadcast_to(xs[rows][:, None, :],
                                     (len(rows), cells, cells)).reshape(-1, cells)
                both[rows] &= _inside_rows(item, gx, ys[rows].ravel()).reshape(
                    len(rows), count)
    for r in np.flatnonzero(both.any(axis=1)):
        qx = np.tile(xs[r], cells)
        qy = np.repeat(ys[r], cells)
        hit = both[r]
        p = int(live[r])
        out[p] = (float(qx[hit].mean()), float(qy[hit].mean()))
        if areas is not None:
            # The shared area on screen: the fraction of the grid both cover,
            # of the box the grid spans.
            areas[p] = (float(hit.sum()) / count
                        * float(x1[p] - x0[p]) * float(y1[p] - y0[p]))
    return out


def _shared_samples(a, b, grid=4, spacing=4.0, max_per_segment=32, max_total=24):
    """Screen points covered by both elements, or None.

    Sampling density has to follow *pixels*, not point count. Spreading a fixed
    number of samples along a polyline means a long chain gets samples tens of
    pixels apart, which steps clean over any small occluder: a 666px mast leg
    sampled 8 times never lands on a 41px dish, no constraint is generated, and
    the chain falls back to its nearest-member depth and paints over it.

    So only the segments whose own box meets the fill are sampled, and those are
    sampled at a few pixels apart. That is denser where it matters and cheaper
    everywhere else.
    """
    a_fill = a["kind"] == FILL_KIND
    b_fill = b["kind"] == FILL_KIND

    if a_fill and b_fill:
        x0 = max(a["bbox"][0], b["bbox"][0])
        x1 = min(a["bbox"][1], b["bbox"][1])
        y0 = max(a["bbox"][2], b["bbox"][2])
        y1 = min(a["bbox"][3], b["bbox"][3])
        if x1 <= x0 or y1 <= y0:
            return None

        # Density follows pixels here too. A fixed grid over the *bounding box*
        # intersection is only as good as the overlap fills that box, and two
        # large polygons crossing at an angle share a thin diagonal sliver a
        # long way from filling it -- on the reference butterfly the wings
        # overlap by under 1% of their own area, and a 4x4 grid landed 1 point
        # in it at best and none at all on four frames of seventeen. A missed
        # pair emits no constraint, so the order silently falls back to the
        # centroid seed, which is exactly what this module exists to avoid.
        step = max(spacing, 1e-6)
        nx = int(min(max(round((x1 - x0) / step), grid), 48))
        ny = int(min(max(round((y1 - y0) / step), grid), 48))
        budget = 576
        if nx * ny > budget:
            shrink = (budget / float(nx * ny)) ** 0.5
            nx = max(grid, int(nx * shrink))
            ny = max(grid, int(ny * shrink))
        gx, gy = np.meshgrid(
            x0 + ((x1 - x0) / (nx + 1)) * np.arange(1, nx + 1),
            y0 + ((y1 - y0) / (ny + 1)) * np.arange(1, ny + 1),
        )
        qx, qy = gx.ravel(), gy.ravel()
        keep = _inside(a, qx, qy) & _inside(b, qx, qy)
        if keep.any():
            return qx[keep], qy[keep]

        # Still nothing: the shared region is thinner than the sample step.
        # Walk both outlines instead. A sliver is bounded by an edge of one
        # polygon or the other, so following the edges finds it when a grid
        # spanning open space cannot.
        for owner, other in ((a, b), (b, a)):
            pts = owner["pts"]
            xs, ys = [], []
            for k in range(len(pts)):
                ax, ay = pts[k]
                bx, by = pts[(k + 1) % len(pts)]
                if (max(ax, bx) < x0 or min(ax, bx) > x1
                        or max(ay, by) < y0 or min(ay, by) > y1):
                    continue
                count = int(min(max(np.hypot(bx - ax, by - ay) / step, 2),
                                max_per_segment)) + 1
                t = np.linspace(0.0, 1.0, count)
                xs.append(ax + (bx - ax) * t)
                ys.append(ay + (by - ay) * t)
            if not xs:
                continue
            ex = np.concatenate(xs)
            ey = np.concatenate(ys)
            # Tested against the *other* polygon only: these points lie on this
            # one's boundary, where an inside test is a coin flip on rounding,
            # and a boundary point is in the shared region by closure anyway.
            keep = _inside(other, ex, ey)
            if keep.any():
                ex, ey = ex[keep], ey[keep]
                if len(ex) > max_total:
                    pick = np.linspace(0, len(ex) - 1, max_total).astype(np.int32)
                    ex, ey = ex[pick], ey[pick]
                return ex, ey
        return None

    # one of them is a polyline: sample the parts of it that reach the fill
    if not (a_fill or b_fill):
        return None
    seg, fill = (a, b) if not a_fill else (b, a)
    pts = seg["pts"]
    fx0, fx1, fy0, fy1 = _bbox_of(fill)

    xs, ys = [], []
    for a, b in _segments(seg):
        ax, ay = a
        bx, by = b
        if (max(ax, bx) < fx0 or min(ax, bx) > fx1
                or max(ay, by) < fy0 or min(ay, by) > fy1):
            continue                      # this segment never reaches the fill
        length = np.hypot(bx - ax, by - ay)
        count = int(min(max(length / spacing, 2), max_per_segment)) + 1
        t = np.linspace(0.0, 1.0, count)
        xs.append(ax + (bx - ax) * t)
        ys.append(ay + (by - ay) * t)
    if not xs:
        return None

    qx = np.concatenate(xs)
    qy = np.concatenate(ys)
    if len(qx) > max_total:
        # Thin out evenly. The samples already sit only on the segments that
        # reach the fill, so even a couple of dozen are far denser over the
        # overlap than spreading the same number along the whole polyline --
        # and the cost per pair stays bounded.
        pick = np.linspace(0, len(qx) - 1, max_total).astype(np.int32)
        qx, qy = qx[pick], qy[pick]
    keep = _inside(fill, qx, qy)
    if keep.any():
        return qx[keep], qy[keep]

    # Nothing landed inside. That does not mean they miss each other -- it can
    # equally mean the fill is narrower than the step we walked the line with.
    # An edge-on face is a sliver: feed-horn/f0 on the Voyager probe is 3.8px
    # wide, and a 125px chain segment crossing it, sampled every 4px, put not
    # one point on it. No samples, no constraint, and a bus edge is painted over
    # a shaft standing 0.95 units in front of it.
    #
    # So clip the line to the polygon instead of hoping to hit it. This is the
    # same fallback the fill-fill branch above already has, for the same reason.
    cx, cy = [], []
    for a, b in _segments(seg):
        ax, ay = float(a[0]), float(a[1])
        bx, by = float(b[0]), float(b[1])
        for t0, t1 in _inside_spans(a, b, fill["pts"]):
            f = np.array([0.25, 0.5, 0.75])
            t = t0 + (t1 - t0) * f
            cx.append(ax + (bx - ax) * t)
            cy.append(ay + (by - ay) * t)
        if len(cx) * 3 >= max_total:
            break
    if not cx:
        return None
    return np.concatenate(cx), np.concatenate(cy)


def _line_spans(fill, lines):
    """`_inside_spans` for every segment of every line against one fill, at once.

    One {segment index: [(t0, t1), ...]} per line, holding only segments with a
    span inside. The per-segment version walked the polygon's edges in Python
    and made one inside test per candidate span; per pair it was still dozens
    of small calls, and a fill meets hundreds of lines. Every quantity here is
    the same expression on the same doubles, the cuts are sorted the same way
    and each midpoint gets the same inside test, so the spans are identical.
    """
    out = [{} for _ in lines]
    owner, a, b = [], [], []
    for n, line in enumerate(lines):
        for p, q in _segments(line):
            owner.append(n)
            a.append(p)
            b.append(q)
    if not owner:
        return out
    owner = np.asarray(owner)
    start = np.searchsorted(owner, np.arange(len(lines)))
    a = np.array(a, dtype=np.float64)
    b = np.array(b, dtype=np.float64)
    poly = np.asarray(fill["pts"], dtype=np.float64)
    pxi, pyi = poly[:, 0], poly[:, 1]
    pxj, pyj = np.roll(pxi, 1), np.roll(pyi, 1)   # edge i runs from vertex i-1
    ex, ey = (pxi - pxj)[None, :], (pyi - pyj)[None, :]
    # Bound the segments-by-edges temporaries.
    step = max(1, 1_048_576 // len(poly))
    for s0 in range(0, len(a), step):
        ax, ay = a[s0:s0 + step, 0:1], a[s0:s0 + step, 1:2]
        dx, dy = b[s0:s0 + step, 0:1] - ax, b[s0:s0 + step, 1:2] - ay
        live = (dx * dx + dy * dy)[:, 0] >= 1e-18
        with np.errstate(divide="ignore", invalid="ignore"):
            den = dx * ey - dy * ex
            wx, wy = pxj[None, :] - ax, pyj[None, :] - ay
            t = (wx * ey - wy * ex) / den
            u = (wx * dy - wy * dx) / den
        cut = (np.abs(den) > 1e-12) & (u >= 0.0) & (u <= 1.0) & (t > 0.0) & (t < 1.0)
        cuts = np.concatenate([np.zeros((len(ax), 1)), np.ones((len(ax), 1)),
                               np.where(cut, t, np.nan)], axis=1)
        cuts.sort(axis=1)                              # NaN sorts last
        lo, hi = cuts[:, :-1], cuts[:, 1:]
        with np.errstate(invalid="ignore"):
            keep = ~(hi - lo < 1e-9) & np.isfinite(hi) & live[:, None]
        rows, cols = np.nonzero(keep)
        if not len(rows):
            continue
        m = 0.5 * (lo[rows, cols] + hi[rows, cols])
        inside = _inside(fill, ax[rows, 0] + dx[rows, 0] * m,
                         ay[rows, 0] + dy[rows, 0] * m)
        for r, c in zip(rows[inside].tolist(), cols[inside].tolist()):
            n = int(owner[s0 + r])
            out[n].setdefault(s0 + r - int(start[n]), []).append(
                (float(lo[r, c]), float(hi[r, c])))
    return out


def _inside_spans(a, b, poly):
    """Parameter ranges of the segment a->b that lie inside `poly`.

    Exact where sampling is a lottery: a segment crossing a shape enters and
    leaves it at points we can solve for, however thin the shape is.
    """
    ax, ay = float(a[0]), float(a[1])
    dx, dy = float(b[0]) - ax, float(b[1]) - ay
    if dx * dx + dy * dy < 1e-18:
        return []
    cuts = [0.0, 1.0]
    n = len(poly)
    j = n - 1
    for i in range(n):
        ex = float(poly[i][0]) - float(poly[j][0])
        ey = float(poly[i][1]) - float(poly[j][1])
        den = dx * ey - dy * ex
        if abs(den) > 1e-12:
            wx = float(poly[j][0]) - ax
            wy = float(poly[j][1]) - ay
            t = (wx * ey - wy * ex) / den
            u = (wx * dy - wy * dx) / den
            if 0.0 <= u <= 1.0 and 0.0 < t < 1.0:
                cuts.append(t)
        j = i
    cuts.sort()
    spans = []
    for lo, hi in zip(cuts, cuts[1:]):
        if hi - lo < 1e-9:
            continue
        m = 0.5 * (lo + hi)
        if _inside(poly, np.array([ax + dx * m]), np.array([ay + dy * m]))[0]:
            spans.append((lo, hi))
    return spans


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
        probe = it.get("probe")
        if probe is not None:
            # The span has to come from the samples too. Taken off the outline
            # alone it misses the inside of a bowl entirely -- the rim is not
            # the deepest part of it -- and the disjoint-interval shortcut
            # below would then settle pairs it has no business settling.
            it["dmin"] = float(probe[2].min())
            it["dmax"] = float(probe[2].max())
            it["field"] = None
        else:
            it["dmin"] = float(it["depths"].min())
            it["dmax"] = float(it["depths"].max())
            it["field"] = (_fit_field(pts, it["depths"], is_ortho)
                           if it["kind"] == FILL_KIND else None)

    seed = np.array([it["seed"] for it in items], dtype=np.float64)
    bbox = np.array([it["bbox"] for it in items], dtype=np.float64)
    dmin = np.array([it["dmin"] for it in items])
    dmax = np.array([it["dmax"] for it in items])
    kinds = np.array([it["kind"] for it in items], dtype=np.int32)

    after = [set() for _ in range(n)]
    before = [set() for _ in range(n)]
    indeg = [0] * n
    weight = {}

    def add_edge(behind, front, strength=0.0):
        if behind == front:
            return
        if front not in after[behind]:
            after[behind].add(front)
            before[front].add(behind)
            indeg[front] += 1
        # how far apart the two really were where they overlap: a constraint
        # derived from a 0.002-unit gap on a 30-unit scene is barely evidence,
        # and is the first thing to give up when a cycle has to be broken
        key = (behind, front)
        weight[key] = max(weight.get(key, 0.0), strength)

    # Surfaces that touch or interpenetrate produce contradictory constraints,
    # so treat "coplanar" as relative to the scene's own depth scale rather
    # than an absolute epsilon.
    span = float(dmax.max() - dmin.min()) if n else 0.0
    tol = max(tol, span * 1e-4)

    # Strokes are in this graph as ordinary nodes, and that is where the
    # deadlock comes from. Measured at the first break of a frame: the biggest
    # strongly connected component held 1474 elements, of which 358 were fills
    # and 1116 were strokes, and 83% of its internal edges ran fill<->line.
    # Restrict it to fills and fill->fill edges and the biggest component is
    # 205. A hairline cannot genuinely decide the order of two fills, so a cycle
    # running fill -> line -> fill is an artefact of sharing one graph -- and it
    # is seven eighths of what the cycle breaker pays for.
    #
    # The 205 that remain are real: geometry that interpenetrates, where no
    # single scalar per element is right whatever is done here.
    #
    # Calling that an artefact was wrong, and ranking the fills alone to prove
    # it is the measurement that shows why. Every stroke's constraints run to
    # fills, so a total order on fills gives each stroke an interval to sit in
    # and the deadlock stops being 1474 elements. Built, and it does exactly
    # that -- cycle breaks 48 -> 17 over two frames, constraints discarded
    # 5746 -> 1931, and *fewer* fill-to-fill edges spent, 1195 -> 450.
    #
    # The ray cast nearly doubles: 2.24% of sampled overlap pixels wrong -> 4.13%,
    # with face fills alone going 143 -> 418. Keeping more fill-to-fill evidence
    # and ordering fills worse is only possible if the evidence was never in
    # those edges to begin with, and it was not:
    #
    #     fillA ->(asserted) strokeX ->(measured) fillB
    #
    # The stroke lies *on* fillA, so "fillB covers strokeX where they overlap"
    # is a depth fact about fillA against fillB at that place, carried through
    # the stroke. Kahn was using those chains transitively, and they are 83% of
    # the edges. Take the strokes out and the fills are ordered on the 17% that
    # remain, with seed depth filling the gap. A hairline cannot *decide* the
    # order of two fills; it can and does *transmit* it.
    #
    # Only fills can meaningfully occlude, so stroke-vs-stroke pairs are skipped.
    #
    # "Invisible" is what that used to claim and it is not quite true. Two
    # strokes of different colours that cross do show which is on top, and
    # `stroke_source = MATERIAL` gives every edge the colour of the face it
    # borders, so a scene can have as many stroke colours as it has materials.
    # Skipping those pairs leaves their order to wherever the topological sort
    # happens to put them, and it changes between frames.
    #
    # Measured rather than argued, because the Voyager cannot show it: its 38
    # materials are untouched Principled defaults, so every fill is
    # NEUTRAL_GREY and every stroke darkens to the same colour. Recoloured with
    # 237 distinct hues -- far more variety than a real model, where colour
    # follows the part -- 7131 strokes over a sixty-frame orbit gave 1285
    # stroke-vs-stroke reversals that do not hold, of which:
    #
    #     1057  are between strokes of the same colour
    #      189  are between strokes that never actually cross
    #       39  are visible, and they total 217 px2 for the whole clip
    #
    # Under four square pixels a frame, on a canvas of two million. Most
    # reversals are same-colour because the strokes that overlap belong to the
    # same part, and a part is one colour.
    #
    # Constraining them would mean testing every stroke against every other --
    # the largest population in the scene, squared -- and adding those edges to
    # a graph whose density is already what cycle breaking spends its budget on.
    # The nine-stick scene built to provoke this found one visible reversal, at
    # 5.3 px2. If it is ever worth doing, do it for pairs that both cross *and*
    # differ in colour, not for the whole class.
    # Collect the same overlap centres first. Reading one point per pair made
    # numpy dispatch dominate the front-triangle read; each item's queries can
    # instead share one depth lookup. Keep disjoint pairs in this sequence too,
    # so batching changes neither graph insertion order nor cycle breaking.
    # The overlap centres of fill pairs are measured together, afterwards, for
    # the same reason (see _fill_fill_centres); `found` keeps every candidate
    # in the sequence it was met so the graph is still built in that order.
    found = []
    fill_pairs = []
    lines = []
    certain = []        # (fill, other, behind, front) settled by disjoint ranges
    line_share = {}     # slot in `found` -> pixels the line covers inside the fill
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
            # Weight these by the actual gap. They are the *most* certain
            # constraints in the graph -- the depth ranges do not even touch --
            # so leaving them at the default strength of zero made the cycle
            # breaker treat them as worthless and discard them first.
            if dmax[j] < dmin[i]:
                found.append((i, j, float(dmin[i] - dmax[j]), None))
                certain.append((i, j, i, j))
                continue
            if dmax[i] < dmin[j]:
                found.append((j, i, float(dmin[j] - dmax[i]), None))
                certain.append((i, j, j, i))
                continue
            if kinds[j] == FILL_KIND:
                found.append((i, j, None, len(fill_pairs)))
                fill_pairs.append((i, j))
            else:
                lines.append((len(found), j))
                found.append(None)
        # Every line this fill meets, clipped against it together.
        if lines:
            spans = _line_spans(items[i], [items[j] for _, j in lines])
            for (slot, j), spans_of in zip(lines, spans):
                found[slot] = (i, j, None, _line_centre(items[j], spans_of))
                line_share[slot] = (_line_length(items[j], spans_of)
                                    * float(items[j].get("width", 1.0)))
            lines.clear()

    fill_areas = [0.0] * len(fill_pairs)
    fill_centres = _fill_fill_centres(items, bbox, fill_pairs, areas=fill_areas)
    pairs = []
    centres = []
    shares = []
    requests = [[] for _ in items]
    for slot, (i, j, gap, shared) in enumerate(found):
        if gap is not None:
            pairs.append((i, j, gap, None))
            continue
        if kinds[j] == FILL_KIND:
            share = fill_areas[shared]
            shared = fill_centres[shared]
        else:
            share = line_share.get(slot, 0.0)
        if shared is None:
            continue
        q = len(centres)
        centres.append(shared)
        shares.append(share)
        requests[i].append(2 * q)
        requests[j].append(2 * q + 1)
        pairs.append((i, j, None, q))

    depths = np.empty(2 * len(centres))
    if centres:
        xy = np.asarray(centres, dtype=np.float64)
        for it, slots in zip(items, requests):
            if slots:
                slots = np.asarray(slots, dtype=np.intp)
                q = slots // 2
                depths[slots] = _depths_at(it, xy[q, 0], xy[q, 1], is_ortho)
    deltas = depths[0::2] - depths[1::2]
    # What breaking each edge would paint wrong: the screen the two share.
    cost = {}
    for i, j, gap, q in pairs:
        if q is None:
            add_edge(i, j, gap)
            continue
        cost[(i, j)] = cost[(j, i)] = shares[q]
        delta = float(deltas[q])
        if abs(delta) <= tol:
            # Coplanar or touching: depth cannot decide, and leaving it to
            # the seed order makes the pair flicker as the seeds jitter
            # frame to frame (mast legs standing on the ground). Break it
            # deterministically -- lines belong on top of surfaces.
            # Weakest genuine class: strictly below tol, so below every
            # constraint that sampling actually decided. If a cycle has to
            # give something up, these go first -- by construction depth had
            # no opinion about them anyway.
            if kinds[i] == FILL_KIND and kinds[j] != FILL_KIND:
                add_edge(i, j, tol)
            elif kinds[j] == FILL_KIND and kinds[i] != FILL_KIND:
                add_edge(j, i, tol)
            continue
        # Weighting this by *agreement* instead of by gap was tried and
        # measured, 2026-09-12, and is worse. The reasoning was sound: a
        # thruster bolted to a service module is in front of it by a hair
        # and by that hair at every sample, so ranked by gap it is the
        # weakest thing in the graph and the first a cycle breaker spends,
        # while a pair that genuinely crosses can be far apart at the
        # centre and disagree a millimetre away. Sampling the shared region
        # and scoring `agree * span + abs(delta)` fixes exactly that on the
        # bench -- it resolves the stray line test_order_cycle.py pins as
        # unfixable, keeps the assertion, and costs nothing once the
        # samples are reused from `_overlap_centre` rather than taken
        # again.
        #
        # On the probe it is a clear regression: 176 errors -> 202, 1467
        # wrong pixels -> 1805, 4.89% -> 6.02%, and `order violated` --
        # the category the change exists to reduce -- 75 -> 102.
        #
        # The mechanism is weight compression. Almost every constraint has
        # high agreement, so scoring agreement first packs the whole graph
        # into a narrow band just under `span` and the gap, which used to
        # order them finely across the full range, is reduced to a
        # tiebreak inside it. The breaker drops the weakest tenth by
        # sort order; once the weights are nearly tied that tenth is close
        # to arbitrary, and it discards good constraints it previously
        # ranked safely above the bad ones. Dropping *fewer* constraints
        # (281 -> 257 on a synthetic crowd) looked like an improvement and
        # was the opposite: fewer but worse-chosen.
        #
        # So the gap stays. Anything that replaces it has to keep the
        # weights spread out, not just get their order right at the top.
        if delta > 0:
            add_edge(i, j, abs(delta))    # i is further away where they overlap
        else:
            add_edge(j, i, abs(delta))

    # An edge lies *on* the faces it borders, so a face can never legitimately
    # be painted over it. Sampling cannot discover this: the two are coincident,
    # every sampled delta lands inside tol, and the pair falls through to the
    # seed order -- which puts the fill on top about half the time. The fill
    # then eats the inner half of the stroke's width and the line reads as
    # thinned, or vanishes wherever the coverage is total.
    #
    # Weighted by the scene's full depth span so it outranks every measured
    # delta, but stays finite: where geometry genuinely interpenetrates this
    # still has to be breakable like anything else.
    index = {it["key"]: k for k, it in enumerate(items)}
    asserted = set()
    missing_face = 0

    def lookup(face_key):
        """An entry is a key, or a tuple of spellings of the same surface.

        A weld names its neighbour without knowing whether that object merged,
        so it offers both `name/m` and `name/f<n>`. Exactly one can be an
        element, and counting the other as missing would put a few hundred
        phantoms into `assertions_unmatched` every bake.
        """
        if isinstance(face_key, tuple):
            for alt in face_key:
                k = index.get(alt)
                if k is not None:
                    return k
            return None
        return index.get(face_key)

    for j, it in enumerate(items):
        for face_key in it.get("on_faces", ()):
            i = lookup(face_key)
            if i is None or i == j:
                missing_face += i is None
                continue
            asserted.add((i, j))
            if j in after[i]:
                # Sampling already agrees, so there is no edge to add: leave it
                # at its measured weight rather than raising it to span.
                #
                # It is still recorded as an assertion, and the record is the
                # point. `asserted` is the set the cycle breaker refuses to
                # spend, so listing the pair is what keeps the breaker off the
                # edge holding this fill behind its own outline -- even though
                # nothing here created that edge.
                #
                # Not recording it was tried, on the reasoning that a measured
                # edge should stay breakable like any other. It is, and the
                # breaker breaks it: `lines_under_own_face` went 0 -> 1853 over
                # seven frames of the probe, buying 877 fewer violations
                # elsewhere. A face eating its own outline is visible; those
                # were not.
                continue
            if i in after[j]:
                # Sampling put the fill over its own boundary -- this is the
                # error, caused by fitting a plane to an n-gon that is not quite
                # planar, so the fit misses its own edge by a hair. Retract it
                # before asserting the truth, or the two sit in the graph as a
                # 2-cycle that the breaker then has to pay to resolve.
                after[j].discard(i)
                before[i].discard(j)
                indeg[i] -= 1
                weight.pop((j, i), None)
            add_edge(i, j, span)

    # The same statement the other way up, and it exists because a merged fill
    # cannot make it for itself. `on_faces` says "this surface is under this
    # edge", which is right for an edge on the near wall and wrong for the same
    # kind of edge round the back: one opaque silhouette draws a part's near
    # wall and its far wall at one depth, so the fill that must stay behind the
    # near rim must also cover the far one.
    #
    # Only a weld can tell them apart, and it already did -- it recorded the far
    # face's normal and centre, so whether that face points at the camera is
    # known per frame. A cut cylinder is the case: the cap's rim at the near end
    # was being eaten by the tube's fill, and the rim at the far end was being
    # drawn straight through it. See bake.ObjectPlan.edge_weld_keys.
    for j, it in enumerate(items):
        for face_key in it.get("under_faces", ()):
            i = lookup(face_key)
            if i is None or i == j:
                missing_face += i is None
                continue
            asserted.add((j, i))
            if i in after[j]:
                continue                      # sampling already agrees
            if j in after[i]:
                after[i].discard(j)
                before[j].discard(i)
                indeg[j] -= 1
                weight.pop((i, j), None)
            add_edge(j, i, span)

    # Kahn's algorithm, tie-broken by seed depth so unconstrained elements keep
    # their natural far-to-near ordering.
    constraints = sum(len(s) for s in after)
    cycle_breaks = 0
    dropped = 0
    assertions_lost = 0
    heap = [(-seed[i], i) for i in range(n) if indeg[i] == 0]
    heapq.heapify(heap)
    ranking = []
    blocked = set(i for i in range(n) if indeg[i] > 0)
    while len(ranking) < n:
        if not heap:
            cycle_breaks += 1
            # A genuine cycle: geometry that touches or interpenetrates, where
            # no single order is right. Give up the *weakest* constraint in play
            # -- the one derived from the smallest depth difference, i.e. the
            # least evidence -- rather than discarding every constraint on some
            # element, which throws away good evidence along with bad.
            # Only edges *inside* a strongly connected component are on a cycle.
            # Everything else feeding a blocked node is a constraint that will
            # be satisfied perfectly well once the deadlock clears, and dropping
            # it just manufactures an occlusion error somewhere unrelated.
            comp = _scc(blocked, after)
            sizes = {}
            for c in comp.values():
                sizes[c] = sizes.get(c, 0) + 1
            live = [(weight.get((src, node), 0.0), src, node)
                    for node in blocked for src in before[node]
                    if comp.get(src, -1) == comp.get(node, -2) and sizes[comp[node]] > 1]
            if not live:
                node = max(blocked, key=lambda k: seed[k])
                blocked.discard(node)
                indeg[node] = 0
                heapq.heappush(heap, (-seed[node], node))
                continue
            # Drop the weakest tenth in one pass. Removing a single edge per
            # break needs as many breaks as there are cyclic edges, and each
            # costs a full scan -- on interpenetrating geometry that is
            # thousands of passes.
            #
            # `live` spans every component that is stuck, so this tenth is a
            # tenth of all of them together, and one deadlock can cost evidence
            # in components that merely happen to be stuck at the same moment.
            # Taking the weakest edge per component instead was measured: it
            # does discard less -- 23,724 -> 21,540 constraints over seven
            # frames, and 19 of the 108 extra violations per frame come back --
            # and it costs 179 cycle breaks -> 17,491, which is 17,491 full
            # scans and takes a 2.4-minute bake past 10.5. The violations that
            # matter got worse doing it (clear ones 107 -> 117). The tenth
            # stays.
            #
            # Making the scan incremental was the obvious next move and it does
            # not work, for a reason worth writing down. Decompose once, then
            # break each component on its own and re-decompose only the one that
            # changed: components are independent, since an edge is only ever
            # dropped from inside one, so the rescan should cost that
            # component's size instead of the whole blocked set. Built, and it
            # behaves exactly as designed -- cycle breaks 179 -> 7, roughly one
            # a frame, discards down to the minimum 23,724 -> 21,546, the clear
            # violations back from 107 to 82, `lines_under_own_face` still 0.
            #
            # It also takes the 13-frame bake from 146s to 1411s, ordering alone
            # 78% of it, because the premise is false here. Measured at the
            # first break of a frame: 6,764 elements blocked, in 10 components,
            # and the biggest holds 5,215 of them. The deadlock is one enormous
            # strongly connected component, so "re-check the component that
            # changed" is "re-check almost everything", once per dropped edge.
            #
            # Which moves the question. With one 5,215-node SCC there is no
            # cheaper schedule to find -- batching is already the right shape
            # for this graph, and the tenth is already near the best it can do.
            # The thing worth attacking is why 77% of the blocked elements are
            # mutually deadlocked in the first place, not how the breaker pays
            # for it afterwards.
            #
            # Bound that tenth by *count*. Taking the tenth-percentile weight as
            # a threshold and dropping everything at or below it is not the same
            # thing: weights are heavily tied, so one break discarded every edge
            # sharing the cutoff value -- 15736 of 24456 constraints, 64% of all
            # the evidence in the scene, in a single pass. The sort already puts
            # the least-supported constraints first; just take that many.
            # Spend the pair that paints the least wrong, and only then the one
            # with the least evidence behind it. What a break costs is the
            # screen the two share: area for two fills, length x width for a
            # line inside a fill.
            #
            # Every see-through part on Apollo-Soyuz came from this, in one
            # shape: a fill under its own edge (asserted), that edge measured
            # behind a second fill, and the second fill measured behind the
            # first. Both measurements are true in different places -- the
            # geometry crosses -- and the gaps say nothing about which is
            # cheaper to break. Ordered by gap, the fill pair went first and a
            # surface was painted over a part: apollo_003/f5 over the mount
            # apollo_013 in every frame, 209 sampled px, on a gap of 0.126
            # against the line's 0.161. The one-frame blink of apollo_018.003
            # under apollo_003/f15 was the same cycle through apollo_014's edge.
            #
            # Ray cast: Apollo 1623 wrong px -> 1012, Voyager 3018 -> 2105.
            # `order violated` 1071 -> 284 and 1460 -> 648. It costs about a
            # third more CPU in this pass -- 5.0 s -> 6.5 s on one Apollo frame
            # -- because cheap edges break fewer cycles per pass: 31 breaks and
            # 6961 constraints dropped -> 53 and 18,525 on frame 15.
            #
            # Two cheaper rules were measured and are worse. Spending any line
            # edge before any fill pair: Apollo 1022, Voyager 3132 -- worse than
            # doing nothing there, because part_043.001's chained outline is
            # long, and drawing it over part_003.001 for 60 frames costs more
            # than the sliver of fill overlap it saved. Weighing range-disjoint
            # pairs by their measured separation instead of their range gap
            # (which fixes the blink on its own): Apollo 1589, Voyager 2730.
            # See docs/audits.md section 15 and scripts/test_order_spend_cost.py.
            if certain:
                _share_certain(items, bbox, certain, comp, sizes, after, cost)
                certain = []
            live.sort(key=lambda item: (cost.get((item[1], item[2]), 0.0), item[0]))
            # Spend measured constraints before asserted ones. An assertion is a
            # geometric fact -- the edge lies on the face -- so it outranks any
            # amount of sampled evidence, and every cycle through one also runs
            # through a stroke->fill edge that can go instead. Weight alone is
            # not enough to express that: a component dominated by assertions
            # exhausts its few measured edges partway through the slice and the
            # rest of the budget comes out of the facts.
            #
            # Spending assertions first was tried, to fix a stray line: a face
            # raked steeply away from the camera spans a lot of depth, measures
            # as being in front of something its own far edge is behind, and
            # drags that edge in front along with it. Breaking the assertion
            # does fix it, and the still-frame cost looked cheap -- the face
            # covers the inner half of its own edge, 47-50% of its pixels.
            #
            # It is not cheap in motion, which is the only way anyone sees this.
            # Whether the cycle exists at all depends on the frame, so the same
            # edge loses the assertion in some frames and keeps it in others and
            # its width alternates: 18 edges of 196 on the Voyager probe, over
            # 14 frames. A line that changes thickness as the camera moves is a
            # worse artefact than the one being fixed, and unlike it, it is
            # everywhere rather than in one place. Unprotecting only the
            # assertions the measured graph already contradicts halves it, to
            # 11, and no arrangement reaches zero: an assertion that is broken
            # per frame is a width that changes per frame.
            #
            # A crossing pair cannot be ordered correctly by one scalar in any
            # case. The fix is to stop the crossing line running through the
            # middle of an element -- split the oversized face -- not to shuffle
            # which of the two wrong answers is chosen. See test_order_cycle.py.
            pool = [e for e in live if (e[1], e[2]) not in asserted] or live
            for w, src, node in pool[:max(1, len(pool) // 10)]:
                if node in after[src]:
                    after[src].discard(node)
                    before[node].discard(src)
                    indeg[node] -= 1
                    dropped += 1
                    if (src, node) in asserted:
                        assertions_lost += 1
            for node in list(blocked):
                if indeg[node] == 0:
                    blocked.discard(node)
                    heapq.heappush(heap, (-seed[node], node))
            continue
        _, i = heapq.heappop(heap)
        ranking.append(i)
        for j in after[i]:
            before[j].discard(i)
            indeg[j] -= 1
            if indeg[j] == 0:
                blocked.discard(j)
                heapq.heappush(heap, (-seed[j], j))

    LAST_STATS.clear()
    LAST_STATS.update({"elements": n, "constraints": constraints,
                       "cycle_breaks": cycle_breaks, "constraints_dropped": dropped,
                       "assertions": len(asserted), "assertions_lost": assertions_lost,
                       "assertions_face_not_drawn": missing_face})

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


def _share_certain(items, bbox, certain, comp, sizes, after, cost):
    """The screen shared by pairs settled on disjoint depth ranges, where it matters.

    Those pairs are never sampled, so nothing measured what breaking them would
    cost. Only the breaker asks, and only about edges inside a strongly connected
    component, and components only split once breaking starts -- so measuring
    the certain edges inside today's components answers every later break.
    """
    todo = [c for c in certain
            if comp.get(c[2], -1) == comp.get(c[3], -2) and sizes[comp[c[2]]] > 1
            and c[3] in after[c[2]]]
    if not todo:
        return
    fills = [k for k, c in enumerate(todo) if items[c[1]]["kind"] == FILL_KIND]
    if fills:
        areas = [0.0] * len(fills)
        _fill_fill_centres(items, bbox, [(todo[k][0], todo[k][1]) for k in fills],
                           areas=areas)
        for k, a in zip(fills, areas):
            cost[(todo[k][2], todo[k][3])] = a
    by_fill = {}
    for k, c in enumerate(todo):
        if items[c[1]]["kind"] != FILL_KIND:
            by_fill.setdefault(c[0], []).append(k)
    for i, ks in by_fill.items():
        spans = _line_spans(items[i], [items[todo[k][1]] for k in ks])
        for k, spans_of in zip(ks, spans):
            line = items[todo[k][1]]
            cost[(todo[k][2], todo[k][3])] = (_line_length(line, spans_of)
                                               * float(line.get("width", 1.0)))


def hold_unranked(slots, ranked):
    """Carry each element's paint rank across the frames it was not ranked in.

    `resolve()` only ranks what is actually drawn, because ranking is the
    expensive pass and there is no point ordering something nobody sees. So an
    element that is drawn for part of the clip and culled for the rest ends up
    with an integer rank in some frames and, in the others, whatever was in the
    slot beforehand -- its raw camera distance.

    Those are not the same quantity. Ranks here run 0..n and mean "how many
    elements are in front of this one"; a camera distance is metres. On the
    Voyager probe every culled frame held ~10.0 while the ranks spanned 9..208,
    and smaller means nearer, so an element leaving the frame dived from the
    back of the scene to the very front. The player interpolates between keys,
    so across the one-frame gap where it fades out it sweeps through every rank
    in between, painting over geometry it belongs behind the whole way -- and
    at partial opacity, because the visibility track is fading over the same
    gap. That is the "shafts inside the hull flip into view" and the "things go
    translucent even though nothing is translucent" report, and it is why
    turning Backface Cull off made both disappear: with nothing culled per
    frame, every element is ranked in every frame and the scale never breaks.

    The value in a culled frame is unobservable -- the element is not drawn --
    so any in-scale number will do, and the one that costs least is a copy of
    the *nearest* ranked frame. Copying only forwards is not enough: it leaves
    the far end of a culled run holding a stale rank, so an element that
    disappears at rank 40 and comes back at 54 interpolates across those 14
    ranks over the gap where it fades back in, arriving too far forward. Taking
    whichever ranked frame is closer makes both ends of the run flat, which is
    where the fades are, and puts the one step in the middle where nothing is
    drawn. Runs of repeated values also delta-code to zeros, so the track gets
    *smaller* rather than larger.

    Returns (filled, never) -- frames repaired, and elements that carry a rank
    in no frame at all. `never` should be zero; anything else means an element
    reached the emitter on the distance scale.
    """
    filled = never = 0
    for key, mask in ranked.items():
        n = len(mask)
        hits = np.nonzero(mask)[0]
        if len(hits) == n:
            continue
        if len(hits) == 0:
            # Visible somewhere but ranked nowhere. Its depths are all raw, so
            # at least they agree with each other; leave them and report it.
            if slots[key]["vis"].max() > 0.0:
                never += 1
            continue
        pos = np.arange(n)
        prev = np.where(mask, pos, -1)
        np.maximum.accumulate(prev, out=prev)
        nxt = np.minimum.accumulate(np.where(mask, pos, n)[::-1])[::-1]
        # A run at either end of the clip has a ranked frame on one side only;
        # FAR makes the missing side lose every comparison.
        FAR = n + 1
        d_prev = np.where(prev >= 0, pos - prev, FAR)
        d_next = np.where(nxt < n, nxt - pos, FAR)
        src = np.where(d_prev <= d_next, prev, nxt)
        depth = slots[key]["depth"]
        depth[:] = depth[src]             # fancy indexing copies before writing
        filled += n - len(hits)
    return filled, never
