# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""A merged outline whose points are positions on the loop, not mesh vertices.

Binding a slot to a mesh vertex, one slot per vertex with the order committed
from the first ring seen, cannot survive a full turn. A quarter rotation later
the silhouette is a different set of vertices in a different cyclic order, the
committed order stops holding, and the object falls back to unmerged. On the
seven-primitive lab scene that rejected five of seven objects -- cone, cube,
cylinder, sphere and tube all found a clean ring in every frame and were
turned down purely on order.

The fault is the binding. A silhouette is not a topological object, so slots
must not be bound to topology.

Here a slot is a **position around the loop**, written as a segment index and a
fraction along it. Positions are kept sorted, so slot k is always further round
the outline than slot k-1, and a polygon whose points run monotonically around
a simple loop cannot cross itself. There is no order to commit to, nothing to
check, and no way for a rotation to invalidate it.

What that buys, on the sphere in the lab scene:

    today          482 slots -- one per mesh vertex, to draw a circle
    ring vertices   59 slots
    by tolerance    24 slots -- a 24-gon is within 0.24px of a 113px circle

The count comes from the error, not from counting vertices. `choose` inserts
the worst-deviating vertex until the outline is within tolerance, which lands
points on corners first because corners are where deviation is; `pad_to` then
tops the set up to the fixed count the format requires, splitting the longest
spans, where an extra point costs bytes and changes nothing.

The count still has to be one number for the whole clip -- an element declares
its point total once and correspondence depends on it -- so the caller takes
the largest any frame needs. In frames wanting fewer, the extra points sit on
straight runs of the outline, where they delta-code to a constant and draw
exactly nothing.
"""

from __future__ import annotations

import numpy as np


def polyline(loop, xy):
    """The ring's vertex positions, wound consistently, open at the ends.

    `walk_loop` starts wherever it reaches first and runs whichever way the
    edge list leads, so the same silhouette comes back clockwise on one frame
    and anticlockwise on the next. Nothing downstream minds a direction as
    long as it does not change -- but it does, and interpolating a polygon
    against its own mirror takes it through zero area. 151 of 217 merged
    outlines on the Voyager probe changed winding at least once, one of them
    eight times, and each change collapses the shape to a line somewhere
    between two keyframes and reopens it inverted. Stepping frame by frame
    shows nothing; playing slowly shows all of it.

    The object is not doing that. Sampled between two of those frames its true
    silhouette runs 526.4, 524.2, 521.9 ... 506.1 -- smooth, positive, never
    near zero. The flip was ours.

    So the direction is settled here, once, where the ring becomes geometry.
    """
    pts = np.asarray(xy, dtype=np.float64)[np.asarray(loop, dtype=np.int64)]
    if len(pts) >= 3:
        x, y = pts[:, 0], pts[:, 1]
        if float((x * np.roll(y, -1) - y * np.roll(x, -1)).sum()) < 0.0:
            pts = pts[::-1]
    return pts


def evaluate(positions, poly):
    """Positions -> (k, 2) points. `positions` is (k, 2) of (segment, fraction)."""
    m = len(poly)
    idx = positions[:, 0].astype(np.int64) % m
    t = positions[:, 1][:, None]
    a = poly[idx]
    b = poly[(idx + 1) % m]
    return a + (b - a) * t


def _seg_distance(p, a, b):
    """Perpendicular distance from each point in `p` to segment a->b."""
    ab = b - a
    denom = float(ab @ ab)
    if denom <= 0.0:
        return np.linalg.norm(p - a, axis=1)
    u = np.clip(((p - a) @ ab) / denom, 0.0, 1.0)
    return np.linalg.norm(p - (a + u[:, None] * ab), axis=1)


def _worst_between(poly, lo, hi):
    """The vertex between lo and hi (cyclic) furthest from the chord, and how far.

    `hi` may wrap past the end of the array, which is what makes this work on a
    closed loop: the span from the last chosen vertex back round to the first
    is measured like any other.
    """
    m = len(poly)
    span = (hi - lo) % m
    if span < 2:
        return -1, 0.0
    between = (lo + np.arange(1, span)) % m
    d = _seg_distance(poly[between], poly[lo % m], poly[hi % m])
    k = int(np.argmax(d))
    return int(between[k]), float(d[k])


def choose(poly, tol, cap=None):
    """Vertex indices whose polygon is within `tol` of the full outline.

    Closed-loop RDP, driven by worst error rather than by a budget, so the
    answer is the smallest set that meets the tolerance rather than a fixed
    count someone guessed. Corners are chosen first because a corner is where
    the deviation is; a smooth arc gets points only as fast as its curvature
    earns them.
    """
    m = len(poly)
    if m <= 3:
        return list(range(m))
    cap = m if cap is None else min(int(cap), m)
    keep = sorted({0, m // 3, (2 * m) // 3})
    while len(keep) < cap:
        worst_i, worst_d = -1, tol
        for k in range(len(keep)):
            lo = keep[k]
            hi = keep[(k + 1) % len(keep)] + (m if k + 1 == len(keep) else 0)
            i, d = _worst_between(poly, lo, hi)
            if d > worst_d:
                worst_i, worst_d = i, d
        if worst_i < 0:
            break
        keep.append(worst_i)
        keep.sort()

    # Refinement only ever adds, so the three seeds it started from are still
    # in the set whether or not the shape needs them. On a rectangle that is
    # the difference between six points and four: the corners get found, and
    # two arbitrary mid-edge seeds ride along forever. Drop anything whose
    # absence still meets the tolerance.
    k = 0
    while k < len(keep) and len(keep) > 3:
        trial = keep[:k] + keep[k + 1:]
        lo = trial[k - 1]
        hi = trial[k % len(trial)] + (m if k == len(trial) else 0)
        _, d = _worst_between(poly, lo, hi)
        if d <= tol:
            keep = trial
        else:
            k += 1
    return keep


def pad_to(keep, poly, n):
    """Top a chosen vertex set up to exactly `n` positions.

    Extra points split the longest spans at their midpoint. They land on the
    outline, so they change no shape; they exist because the format fixes an
    element's point count for the whole clip and this frame wants fewer than
    the busiest one does.
    """
    m = len(poly)
    pos = [(float(i), 0.0) for i in keep]
    if len(pos) >= n:
        return np.asarray(pos[:n], dtype=np.float64)

    # Work in arc position so a split is unambiguous: segment index plus
    # fraction, compared cyclically.
    while len(pos) < n:
        widest, at = -1.0, 0
        for k in range(len(pos)):
            a = pos[k]
            b = pos[(k + 1) % len(pos)]
            span = ((b[0] + b[1]) - (a[0] + a[1])) % m
            if span > widest:
                widest, at = span, k
        a = pos[at]
        b = pos[(at + 1) % len(pos)]
        mid = ((a[0] + a[1]) + widest / 2.0) % m
        seg = int(mid)
        pos.insert(at + 1, (float(seg), mid - seg))
    return np.asarray(pos, dtype=np.float64)


def _between(pa, pb, m):
    """The true vertices lying strictly between two positions: (first, count).

    A point at fraction 0 *is* its vertex and must not be measured against its
    own chord; a point partway along a segment leaves that segment's start
    vertex behind it. Getting this wrong reports zero error for outlines that
    miss by half the shape.
    """
    lo = int(pa[0]) + 1
    end = int(pb[0])
    hi = end + 1 if pb[1] > 0 else end
    return lo, (hi - lo) % m


def _chord_error(pa, pb, pta, ptb, poly):
    """Worst distance from the outline to the chord joining two positions."""
    m = len(poly)
    lo, span = _between(pa, pb, m)
    if span < 1:
        return 0.0
    idx = (lo + np.arange(span)) % m
    return float(_seg_distance(poly[idx], pta, ptb).max())


def span_errors(positions, poly):
    """Per-span deviation of the resampled outline from the real one."""
    k = len(positions)
    if k < 2:
        return np.zeros(max(k, 1))
    pts = evaluate(positions, poly)
    return np.array([_chord_error(positions[j], positions[(j + 1) % k],
                                  pts[j], pts[(j + 1) % k], poly)
                     for j in range(k)])


def max_error(positions, poly):
    """How far the resampled outline strays from the true one, in pixels."""
    if not len(positions):
        return 0.0
    return float(span_errors(positions, poly).max())


def align(positions, poly, previous):
    """Roll the positions so slot 0 lands where it did last frame.

    Which vertex the refinement picks first is arbitrary and changes as the
    shape turns. Left alone, every slot would slide right round the outline
    between frames -- the shape stays correct, but each point's path becomes a
    long tour of the silhouette instead of a short arc, and the reducer has to
    key every frame of it.

    Rotations only, never reversals. `polyline` winds every ring the same way
    now, so a reversal is not another way of matching the same shape -- it is
    a different shape, and picking it is what let an outline interpolate
    through zero area.
    """
    if previous is None or len(previous) != len(positions):
        return positions
    pts = evaluate(positions, poly)
    n = len(pts)
    best, best_cost = positions, None
    for r in range(n):
        rolled = np.roll(pts, -r, axis=0)
        cost = float(((rolled - previous) ** 2).sum())
        if best_cost is None or cost < best_cost:
            best_cost = cost
            best = np.roll(positions, -r, axis=0)
    return best


def slots_needed(frames, tol, cap=None, floor=3):
    """The point count one element needs to hold every frame within tolerance.

    `frames` is an iterable of (m, 2) outlines. The answer is the largest any
    single frame asks for, because the count is declared once and every frame
    has to fit inside it.
    """
    need = floor
    for poly in frames:
        if poly is None or len(poly) < 3:
            continue
        need = max(need, len(choose(poly, tol, cap)))
    return need if cap is None else min(need, int(cap))


# ---------------------------------------------------------------------------
# carrying an outline from one frame to the next
# ---------------------------------------------------------------------------
#
# `choose` decides afresh every frame, and on a smooth outline that is a coin
# toss. Every candidate on a circle deviates by nearly the same amount -- on
# the lab sphere's 58-vertex ring the whole spread is 0.33px -- so which vertex
# wins is arbitrary, and it flips as the shape turns. Measured: 32% of the
# chosen set changed per frame, a third of the slots re-assigned for no reason
# at all.
#
# The cost is not a wrong shape; the outline was correct every frame. It is
# that each slot's *path* becomes a jitter of about one slot spacing, so the
# reducer cannot drop a single keyframe. The sphere's silhouette is a
# stationary circle and its track still carried a key in all 24 frames, at 15px
# of recorded movement per frame that never happened.
#
# So carry the previous frame's positions instead, and re-derive only when they
# stop being good enough. Near-ties then resolve in favour of staying put,
# which is the whole fix.
#
# Positions travel as *normalised arc length*, not as segment indices: the ring
# has a different vertex count from one frame to the next -- 58 then 59 on the
# sphere -- so an index means nothing across the gap, while "62% of the way
# round" means the same thing on both.


def arc_table(poly):
    """Cumulative arc length around the closed loop, and its total."""
    seg = np.linalg.norm(np.roll(poly, -1, axis=0) - poly, axis=1)
    return np.concatenate([[0.0], np.cumsum(seg)]), float(seg.sum())


def arc_of(positions, poly):
    """Positions -> where they sit around the loop, as fractions in [0, 1)."""
    cum, total = arc_table(poly)
    if total <= 0.0:
        return np.zeros(len(positions))
    idx = positions[:, 0].astype(np.int64) % len(poly)
    here = cum[idx] + positions[:, 1] * (cum[idx + 1] - cum[idx])
    return (here / total) % 1.0


def at_arc(s, poly):
    """Fractions in [0, 1) -> positions on this loop. The inverse of arc_of."""
    cum, total = arc_table(poly)
    m = len(poly)
    if total <= 0.0:
        return np.zeros((len(s), 2))
    want = (np.asarray(s, dtype=np.float64) % 1.0) * total
    idx = np.clip(np.searchsorted(cum, want, side="right") - 1, 0, m - 1)
    span = np.maximum(cum[idx + 1] - cum[idx], 1e-12)
    return np.stack([idx.astype(np.float64), (want - cum[idx]) / span], axis=1)


def nearest_arc(poly, point):
    """Where on the loop the given point lands, as a fraction in [0, 1).

    The anchor for carrying a set forward. `walk_loop` starts its ring at
    whichever vertex it happened to reach first, so the *origin* of arc length
    moves between frames even when nothing else does. Re-finding one known
    point pins it.
    """
    a = poly
    b = np.roll(poly, -1, axis=0)
    ab = b - a
    denom = np.einsum("ij,ij->i", ab, ab)
    denom = np.where(denom > 0, denom, 1e-12)
    u = np.clip(np.einsum("ij,ij->i", point - a, ab) / denom, 0.0, 1.0)
    foot = a + u[:, None] * ab
    k = int(np.argmin(np.linalg.norm(foot - point, axis=1)))
    cum, total = arc_table(poly)
    if total <= 0.0:
        return 0.0
    return float((cum[k] + u[k] * (cum[k + 1] - cum[k])) / total) % 1.0


def follow(poly, n, tol, prev=None):
    """This frame's `n` positions, staying where they were where it can.

    `prev` is `(fractions, points)` as returned last frame. Returns the same
    pair plus the positions, so a caller can hand it straight back.

    Re-deriving is not avoided at the cost of accuracy: the carried set is
    measured against the real outline first and only kept if it is inside
    `tol`. When the shape has changed enough that it is not -- a cylinder
    turning edge-on, a corner arriving -- the frame is chosen from scratch and
    rolled to sit as close to the old one as it can.
    """
    if prev is not None:
        prev_s, prev_pts = prev
        if len(prev_s) == n and len(poly) >= 3:
            shift = nearest_arc(poly, prev_pts[0]) - prev_s[0]
            carried = at_arc(prev_s + shift, poly)
            if max_error(carried, poly) <= tol:
                return carried, arc_of(carried, poly), evaluate(carried, poly)

    pos = pad_to(choose(poly, tol, n), poly, n)
    if prev is not None and len(prev[1]) == n:
        pos = align(pos, poly, prev[1])
    return pos, arc_of(pos, poly), evaluate(pos, poly)


def is_simple(poly):
    """Does this closed polyline avoid crossing itself?

    `walk_loop` returns a ring of mesh edges, and a ring in 3D is not a simple
    polygon once projected. On a non-convex part the silhouette folds behind
    the body and the fold crosses the outer boundary on screen -- 45% of the
    Voyager probe's merged outlines did so in at least one frame, drawing as
    tangled blobs.

    Monotone slot positions guarantee the *resampling* adds no crossing. They
    cannot remove one the loop already had, so this is what decides whether an
    object can be merged at all.

    All segment pairs at once. A silhouette ring runs to a few hundred edges,
    so the quadratic is a few tens of thousands of comparisons in numpy -- far
    cheaper than the frame that produced it.
    """
    m = len(poly)
    if m < 4:
        return True
    a = poly
    b = np.roll(poly, -1, axis=0)
    i, j = np.triu_indices(m, k=2)
    # Neighbouring segments share an endpoint, and so do the first and last.
    keep = ~((i == 0) & (j == m - 1))
    i, j = i[keep], j[keep]
    if not len(i):
        return True
    p, p2 = a[i], b[i]
    q, q2 = a[j], b[j]

    def side(o, e, pt):
        return ((e[:, 0] - o[:, 0]) * (pt[:, 1] - o[:, 1])
                - (e[:, 1] - o[:, 1]) * (pt[:, 0] - o[:, 0]))

    d1, d2 = side(p, p2, q), side(p, p2, q2)
    d3, d4 = side(q, q2, p), side(q, q2, p2)
    # Proper crossings only: touching at a shared endpoint is how a polygon is
    # built, and a collinear graze is not a tangle.
    return not bool((((d1 > 0) != (d2 > 0)) & ((d3 > 0) != (d4 > 0))).any())
