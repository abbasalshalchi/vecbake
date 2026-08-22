# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""The outline of what an object actually covers on screen.

`merge.walk_loop` builds an outline by chaining silhouette *edges* -- the mesh
edges with a front-facing face on exactly one side. That set is the right
candidate list and the wrong answer, because a ring of mesh edges closes in 3D
and its projection need not be a simple closed curve at all. Three ways it
fails, and on the Voyager probe they are near enough equal thirds of the 303
parts:

    branching      40   three silhouette edges meeting at one vertex, so
                        there is no single ring to walk
    self-crossing  35   a non-convex part whose silhouette folds behind its
                        own body, crossing itself on screen
    multi-loop     21   an open shell or a hole: two or more rings, and an
                        element is one closed polygon

All three are artefacts of asking a question about mesh topology. The thing
being drawn is the *union of the projected faces*, and its boundary has none of
these problems by construction: it is always a set of simple closed curves,
however the mesh behind it is arranged.

So compute that instead. A silhouette edge is still the candidate -- an edge
between two front-facing faces has the union on both sides and cannot be on its
boundary -- but the candidates are then cut at their crossings and the pieces
that turn out to lie *under* another face are dropped. What survives is the
boundary, and walking it yields one loop per connected region.

Nothing here knows about Blender or about meshes; it takes projected polygons
and returns polylines, which is what makes it testable against shapes whose
answer is known by hand. See scripts/test_boundary.py.
"""

from __future__ import annotations

import math

import numpy as np

# Endpoints are matched by rounding to this many decimals. Coordinates are in
# pixels, so a millionth of a pixel is far below anything the bake can mean,
# and far above the drift of an intersection computed two different ways.
QUANT = 6


def _key(p):
    return (round(float(p[0]), QUANT), round(float(p[1]), QUANT))


def split_at_crossings(segs):
    """Cut every segment where another crosses it.

    Returns `(pieces, source)` -- the (m, 2, 2) pieces and, for each, the index
    of the segment it came from. The source index is what lets a caller carry
    per-segment information across the split; recovering it afterwards by
    testing which segment a piece lies on is both slower and, when two segments
    are collinear, wrong.

    The candidates come from a mesh, so they meet only at shared vertices.
    Where the projection makes two of them cross, that crossing is a real
    corner of the union's boundary and has to become an endpoint, or no walk
    can follow the boundary through it.
    """
    segs = np.asarray(segs, dtype=np.float64).reshape(-1, 2, 2)
    n = len(segs)
    if n == 0:
        return segs, np.zeros(0, dtype=np.int64)
    a, b = segs[:, 0], segs[:, 1]
    d = b - a
    cuts = [[0.0, 1.0] for _ in range(n)]

    for i in range(n - 1):
        j = np.arange(i + 1, n)
        ai, di = a[i], d[i]
        aj, dj = a[j], d[j]
        denom = di[0] * dj[:, 1] - di[1] * dj[:, 0]
        # Parallel lines never cross once. They are not skipped here: a pair
        # that is parallel *and* on the same line still has to be cut, below.
        ok = np.abs(denom) > 1e-12
        rel = aj - ai
        safe = np.where(ok, denom, 1.0)
        t = (rel[:, 0] * dj[:, 1] - rel[:, 1] * dj[:, 0]) / safe
        u = (rel[:, 0] * di[1] - rel[:, 1] * di[0]) / safe
        # Strictly interior to both, so a shared endpoint is not "a crossing"
        hit = ok & (t > 1e-9) & (t < 1 - 1e-9) & (u > 1e-9) & (u < 1 - 1e-9)
        for k in np.nonzero(hit)[0]:
            cuts[i].append(float(t[k]))
            cuts[int(j[k])].append(float(u[k]))

        # Parallel segments never cross, but two faces meeting edge to edge
        # overlap along a shared line -- a fin sitting on a box, two bars in a
        # row. Those have to be cut at each other's endpoints or the pieces
        # never line up and the walk cannot get past the join.
        scale = max(float(np.hypot(*di)), 1.0)
        for k in np.nonzero(~ok)[0]:
            jj = int(j[k])
            off = (aj[k] - ai)
            if abs(di[0] * off[1] - di[1] * off[0]) > 1e-9 * scale:
                continue                    # parallel but not the same line
            dd = float(di @ di)
            if dd > 0:
                for pt in (a[jj], b[jj]):
                    tt = float((pt - ai) @ di) / dd
                    if 1e-9 < tt < 1 - 1e-9:
                        cuts[i].append(tt)
            ddj = float(d[jj] @ d[jj])
            if ddj > 0:
                for pt in (a[i], b[i]):
                    uu = float((pt - a[jj]) @ d[jj]) / ddj
                    if 1e-9 < uu < 1 - 1e-9:
                        cuts[jj].append(uu)

    pieces, source = [], []
    for i in range(n):
        ts = sorted(set(round(t, 9) for t in cuts[i]))
        for lo, hi in zip(ts, ts[1:]):
            if hi - lo <= 1e-9:
                continue
            pieces.append((a[i] + d[i] * lo, a[i] + d[i] * hi))
            source.append(i)
    if not pieces:
        return segs[:0], np.zeros(0, dtype=np.int64)
    return (np.asarray(pieces, dtype=np.float64).reshape(-1, 2, 2),
            np.asarray(source, dtype=np.int64))


def _inside(px, py, poly):
    """Crossing-number test for many points against one polygon."""
    x, y = poly[:, 0], poly[:, 1]
    x2, y2 = np.roll(x, -1), np.roll(y, -1)
    inside = np.zeros(len(px), dtype=bool)
    for k in range(len(poly)):
        x1, y1, xa, ya = x[k], y[k], x2[k], y2[k]
        if y1 == ya:
            continue
        straddles = (y1 > py) != (ya > py)
        with np.errstate(divide="ignore", invalid="ignore"):
            cross = x1 + (py - y1) * (xa - x1) / (ya - y1)
        inside ^= straddles & (px < cross)
    return inside


def drop_covered(pieces, faces, owners=None, skip=None):
    """Keep only the pieces that are not lying under some face.

    A piece of a silhouette edge that runs across the middle of the shape --
    the fold on a bent part -- has the object on both sides of it, and is not
    on the boundary of anything. It is recognised by its midpoint being inside
    another projected face.

    The face a piece came from is skipped: its own edge lies *on* that face's
    boundary, where an inside test is a coin toss.
    """
    if not len(pieces):
        return pieces
    mid = pieces.mean(axis=1)
    px, py = mid[:, 0], mid[:, 1]
    covered = np.zeros(len(pieces), dtype=bool)
    for fi, poly in enumerate(faces):
        if len(poly) < 3:
            continue
        lo = poly.min(axis=0)
        hi = poly.max(axis=0)
        near = (~covered) & (px >= lo[0]) & (px <= hi[0]) & (py >= lo[1]) & (py <= hi[1])
        if skip is not None and owners is not None:
            near &= ~skip(owners, fi)
        if not near.any():
            continue
        idx = np.nonzero(near)[0]
        covered[idx] |= _inside(px[idx], py[idx], poly)
    return pieces[~covered]


def walk_loops(pieces, min_points=3):
    """Chain boundary pieces into closed loops.

    The pieces form a planar graph. Most nodes have exactly two, and where more
    meet -- the branching case -- the walk takes the sharpest left turn
    available, which is the standard way to trace one face of a planar
    subdivision without cutting a corner off it.
    """
    if not len(pieces):
        return []
    nodes = {}
    for idx, (p, q) in enumerate(pieces):
        for end, other in ((p, q), (q, p)):
            nodes.setdefault(_key(end), []).append((idx, np.asarray(end), np.asarray(other)))

    used = np.zeros(len(pieces), dtype=bool)
    loops = []
    for start_idx in range(len(pieces)):
        if used[start_idx]:
            continue
        p, q = pieces[start_idx]
        loop = [np.asarray(p)]
        cur, prev = np.asarray(q), np.asarray(p)
        used[start_idx] = True
        guard = len(pieces) + 2
        while guard > 0:
            guard -= 1
            if _key(cur) == _key(loop[0]):
                break
            loop.append(cur)
            here = nodes.get(_key(cur), [])
            back = math.atan2(prev[1] - cur[1], prev[0] - cur[0])
            best, best_turn = None, None
            for idx, end, other in here:
                if used[idx]:
                    continue
                turn = (math.atan2(other[1] - cur[1], other[0] - cur[0]) - back) % (2 * math.pi)
                if best_turn is None or turn < best_turn:
                    best, best_turn = (idx, other), turn
            if best is None:
                break                      # a dangling end: not a closed loop
            used[best[0]] = True
            prev, cur = cur, best[1]
        if len(loop) >= min_points and _key(cur) == _key(loop[0]):
            loops.append(np.asarray(loop))
    return loops


def outline_of(faces, candidates, owners):
    """The screen boundary of a set of projected faces.

    `candidates` are the segments that could be on it -- the silhouette edges,
    since an edge between two drawn faces has the union on both sides --
    and `owners` says which face each came from, so a piece is never tested
    against the face whose boundary it is.

    Returns loops, largest first, each a closed polyline with no repeated end
    point. An empty list means nothing on screen, or nothing simple enough to
    draw as one.
    """
    if not len(candidates):
        return []
    segs = np.asarray(candidates, dtype=np.float64).reshape(-1, 2, 2)
    owners = np.asarray(owners, dtype=np.int64)

    pieces, source = split_at_crossings(segs)
    if not len(pieces):
        return []

    # A piece is on the boundary exactly when one side of it is covered and
    # the other is not. Testing the midpoint instead cannot decide a piece two
    # faces share -- a fin's root sits on the box's top edge, with the object
    # above it and below it -- because that midpoint lies *on* both faces
    # rather than inside either. Stepping a hair off the line either way asks
    # the question that actually matters.
    mid = pieces.mean(axis=1)
    along = pieces[:, 1] - pieces[:, 0]
    length = np.hypot(along[:, 0], along[:, 1])
    normal = np.stack([-along[:, 1], along[:, 0]], axis=1) / np.maximum(length, 1e-12)[:, None]
    step = np.minimum(length * 0.05, 1e-3)[:, None]
    left, right = mid + normal * step, mid - normal * step

    cov_l = np.zeros(len(pieces), dtype=bool)
    cov_r = np.zeros(len(pieces), dtype=bool)
    for poly in faces:
        poly = np.asarray(poly, dtype=np.float64)
        if len(poly) < 3:
            continue
        lo, hi = poly.min(axis=0), poly.max(axis=0)
        for pts, cov in ((left, cov_l), (right, cov_r)):
            todo = ~cov
            todo &= ((pts[:, 0] >= lo[0]) & (pts[:, 0] <= hi[0])
                     & (pts[:, 1] >= lo[1]) & (pts[:, 1] <= hi[1]))
            if not todo.any():
                continue
            idx = np.nonzero(todo)[0]
            cov[idx] |= _inside(pts[idx, 0], pts[idx, 1], poly)

    kept = pieces[cov_l != cov_r]

    # Two faces meeting along a line contribute the same piece twice, once
    # from each side's edge. Both survive the covering test -- they describe
    # the same boundary and it really is a boundary -- and leaving both in
    # gives the walk a node of degree three where the boundary has degree two,
    # which it cannot get past. One piece of boundary, one segment.
    seen, unique = set(), []
    for p, q in kept:
        a, b = _key(p), _key(q)
        edge = (a, b) if a <= b else (b, a)
        if edge in seen or a == b:
            continue
        seen.add(edge)
        unique.append((p, q))
    if not unique:
        return []

    loops = walk_loops(np.asarray(unique, dtype=np.float64))
    loops.sort(key=lambda L: -_area(L))
    return loops


def _area(loop):
    x, y = loop[:, 0], loop[:, 1]
    return abs(float((x * np.roll(y, -1) - y * np.roll(x, -1)).sum())) / 2.0
