# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""A merged outline whose points are positions on the loop, not mesh vertices.

`merge.MergedPath` spends one slot per mesh vertex and fixes the slot *order*
from the first ring it sees. Every later frame then has to present its
silhouette vertices in that same cyclic order, and a solid turning through 360
degrees simply does not: the silhouette is a different set of vertices a
quarter turn later, in a different order around the outline. So the check in
`MergedPath._consistent` fails, `stable` goes false, and the object is
re-sampled unmerged. On the seven-primitive lab scene that is five of seven
objects -- cone, cube, cylinder, sphere and tube all find a clean ring in every
frame and are rejected purely on order.

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
    """The ring's vertex positions, open (no repeated final point)."""
    return np.asarray(xy, dtype=np.float64)[np.asarray(loop, dtype=np.int64)]


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


def max_error(positions, poly):
    """How far the resampled outline strays from the true one, in pixels."""
    pts = evaluate(positions, poly)
    m = len(poly)
    worst = 0.0
    k = len(positions)
    for j in range(k):
        # The true vertices lying strictly between resampled point j and j+1.
        # A point at fraction 0 *is* its vertex and must not be measured
        # against its own chord; a point partway along a segment leaves that
        # segment's start vertex behind it. Getting this wrong reports zero
        # error for outlines that miss by half the shape.
        nxt = (j + 1) % k
        lo = int(positions[j, 0]) + 1
        end = int(positions[nxt, 0])
        hi = end + 1 if positions[nxt, 1] > 0 else end
        span = (hi - lo) % m
        if span < 1:
            continue
        between = (lo + np.arange(span)) % m
        d = _seg_distance(poly[between], pts[j], pts[nxt])
        worst = max(worst, float(d.max()))
    return worst


def align(positions, poly, previous):
    """Roll the positions so slot 0 lands where it did last frame.

    Which vertex the refinement picks first is arbitrary and changes as the
    shape turns. Left alone, every slot would slide right round the outline
    between frames -- the shape stays correct, but each point's path becomes a
    long tour of the silhouette instead of a short arc, and the reducer has to
    key every frame of it.

    Both directions are tried: a silhouette can come back from `walk_loop`
    wound the other way, and matching a reversed loop slot-for-slot would pair
    every point with the one opposite it.
    """
    if previous is None or len(previous) != len(positions):
        return positions
    pts = evaluate(positions, poly)
    n = len(pts)
    best, best_cost = positions, None
    for flip in (False, True):
        cand_pts = pts[::-1] if flip else pts
        cand_pos = positions[::-1] if flip else positions
        # cost of every cyclic offset at once
        for r in range(n):
            rolled = np.roll(cand_pts, -r, axis=0)
            cost = float(((rolled - previous) ** 2).sum())
            if best_cost is None or cost < best_cost:
                best_cost = cost
                best = np.roll(cand_pos, -r, axis=0)
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
