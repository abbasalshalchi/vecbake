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

# How far apart two endpoints may be and still be the same point. Coordinates
# are in pixels and the bake's own tolerance is 0.35, so a thousandth of a
# pixel is far below anything it can mean.
#
# It is not arbitrary at the bottom end either. Two pieces meeting at one
# corner arrive from different faces, and the same vertex projected along two
# paths differs in its last bits: 1.7e-5 px, measured. Rounding to 1e-6 -- the
# first thing this did -- split such a corner into two nodes of degree one, the
# walk could not close the loop, and the whole part vanished for that frame.
#
# Rounding to a coarser grid does not fix it. Two points 1.7e-5 apart still
# land in different cells whenever they straddle a boundary, so the failure
# comes back at a lower rate and looks like bad luck. Snapping to a shared
# representative is what actually makes "the same point" mean the same thing.
WELD = 1e-3

# How far apart an open chain's two ends may be, as a fraction of the chain's
# own extent, before closing it would be inventing geometry rather than
# completing it.
CLOSE_GAP = 0.25


class _Weld:
    """Gives every endpoint within WELD of each other one shared key.

    A dict on rounded coordinates alone cannot do this -- that is a grid, and a
    grid cuts somewhere. Each point is looked up against the nine cells around
    it, so a neighbour just over a cell boundary is still found.
    """

    def __init__(self, eps=WELD):
        self.eps = eps
        self.cells: dict = {}

    def key(self, p):
        x, y = float(p[0]), float(p[1])
        cx, cy = int(x // self.eps), int(y // self.eps)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for kx, ky in self.cells.get((cx + dx, cy + dy), ()):
                    if abs(kx - x) <= self.eps and abs(ky - y) <= self.eps:
                        return (kx, ky)
        self.cells.setdefault((cx, cy), []).append((x, y))
        return (x, y)


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
    weld = _Weld()
    ends = [(weld.key(p), weld.key(q)) for p, q in pieces]
    nodes = {}
    for idx, (ka, kb) in enumerate(ends):
        for k, other in ((ka, kb), (kb, ka)):
            nodes.setdefault(k, []).append((idx, np.asarray(k), np.asarray(other)))

    used = np.zeros(len(pieces), dtype=bool)
    loops = []

    def step(cur, prev):
        """The next unused piece at `cur`, taking the sharpest turn available."""
        back = math.atan2(prev[1] - cur[1], prev[0] - cur[0])
        best, best_turn = None, None
        for idx, _end, other in nodes.get(tuple(cur), ()):
            if used[idx]:
                continue
            turn = (math.atan2(other[1] - cur[1], other[0] - cur[0]) - back) % (2 * math.pi)
            if best_turn is None or turn < best_turn:
                best, best_turn = (idx, other), turn
        return best

    for seed in range(len(pieces)):
        if used[seed]:
            continue
        ka, kb = ends[seed]
        used[seed] = True
        loop = [np.asarray(ka), np.asarray(kb)]
        closed = False

        # Grow forward, then backward from the seed. Both directions matter:
        # a seed landing in the middle of an open chain would otherwise walk
        # one way, stop at the break, and leave the rest to be found as a
        # second fragment -- two half outlines where there is one.
        cur, prev = np.asarray(kb), np.asarray(ka)
        for _ in range(len(pieces) + 1):
            if tuple(cur) == ka:
                closed = True
                loop.pop()
                break
            nxt = step(cur, prev)
            if nxt is None:
                break
            used[nxt[0]] = True
            prev, cur = cur, nxt[1]
            loop.append(np.asarray(cur))

        if not closed:
            head, after = np.asarray(ka), np.asarray(kb)
            for _ in range(len(pieces) + 1):
                nxt = step(head, after)
                if nxt is None:
                    break
                used[nxt[0]] = True
                after, head = head, nxt[1]
                loop.insert(0, np.asarray(head))
                if tuple(head) == tuple(loop[-1]):
                    closed = True
                    loop.pop()
                    break

        if len(loop) < min_points:
            continue
        if not closed:
            # The chain ran out of pieces without returning to its start. That
            # is one missing piece, not a missing outline: on part_012 the
            # boundary came back as 35 correct pieces over 36 nodes, 34 of them
            # properly paired, and discarding the lot lost the part for that
            # frame. An open chain of boundary pieces is an outline that needs
            # closing, and a fill closes implicitly anyway.
            #
            # Only across a gap small next to the shape itself. A wide gap
            # means these pieces do not describe one region, and joining them
            # would invent an edge that is nowhere in the geometry.
            span = np.asarray(loop)
            extent = float(np.hypot(*(span.max(axis=0) - span.min(axis=0))))
            gap = float(np.hypot(*(span[-1] - span[0])))
            if extent <= 0.0 or gap > CLOSE_GAP * extent:
                continue
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
    weld = _Weld()
    seen, unique = set(), []
    for p, q in kept:
        a, b = weld.key(p), weld.key(q)
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


def classify(loops):
    """For each loop, is it a region the object covers, or a hole in one?

    Not by winding. A loop's direction here is inherited from whichever mesh
    edge the walk happened to start on, and mesh edges on screen are wound
    every which way once projected -- so orientation says nothing. It reads as
    working on synthetic input only because faces built by hand come out
    consistently wound.

    Containment is the real question, and it is the definition: a hole is a
    loop that lies inside another loop of the same object. Loops arrive
    largest first, so anything a bigger one contains is a hole in it, and two
    loops side by side are two regions.
    """
    out = []
    for i, loop in enumerate(loops):
        c = loop.mean(axis=0)
        inside_bigger = False
        for j in range(i):
            if bool(_inside(np.array([c[0]]), np.array([c[1]]), loops[j])[0]):
                inside_bigger = True
                break
        out.append(not inside_bigger)
    return out


def match_loops(loops, previous, loop_kind=None, slot_kind=None):
    """Assign this frame's loops to the slots last frame's loops occupied.

    Loops arrive largest first, and taking that order as the slot order is
    wrong the moment two of them change places -- the elements swap geometry
    and both jump the width of the object. On the Voyager probe part_001/m1
    moved 3205px in one frame that way, on a loop of four points.

    Size is not identity; position is. Each loop takes the free slot whose
    loop was nearest to it last frame, largest first, so the slot follows the
    region rather than the ranking. `previous` maps slot -> the points it held;
    slots with no history, and loops with no slot left, fall back to order.

    Position is not enough on its own either. A ring's outer boundary and its
    hole share a centroid, so the distance test between them is noise, and on
    the Voyager probe part_025 traded the two every single frame: the fill
    took the hole's outline, the hole took the fill's, and both jumped. What
    separates them is what they *are*, which containment already answers and
    the slot already fixed for the whole clip. A region only ever matches a
    region slot and a hole only a hole slot; a loop with no slot of its kind
    free is simply not drawn this frame.

    Returns slot -> loop index, for the slots that have a loop this frame.
    """
    order = sorted(range(len(loops)), key=lambda i: -_area(loops[i]))
    slots = list(previous.keys())

    def compatible(sl, i):
        if loop_kind is None or slot_kind is None:
            return True
        if i >= len(loop_kind) or sl >= len(slot_kind):
            return True
        return bool(loop_kind[i]) == bool(slot_kind[sl])

    out = {}
    taken = set()
    for i in order:
        here = loops[i].mean(axis=0)
        best, best_d = None, None
        for sl in slots:
            if sl in taken or previous.get(sl) is None or not compatible(sl, i):
                continue
            d = float(np.hypot(*(previous[sl].mean(axis=0) - here)))
            if best_d is None or d < best_d:
                best, best_d = sl, d
        if best is not None:
            out[best] = i
            taken.add(best)
    # anything unmatched drops into the lowest free slot of its own kind,
    # which is what a first frame does for every loop
    free = [sl for sl in slots if sl not in taken]
    for i in order:
        if i in out.values():
            continue
        pick = next((sl for sl in free if compatible(sl, i)), None)
        if pick is None:
            continue
        free.remove(pick)
        out[pick] = i
    return out


def _area(loop):
    x, y = loop[:, 0], loop[:, 1]
    return abs(float((x * np.roll(y, -1) - y * np.roll(x, -1)).sum())) / 2.0
