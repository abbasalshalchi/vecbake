# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""Where a merged outline's points go, and which point is which.

A slot is a **position around the loop**, written as a segment index and a
fraction along it. Positions run monotonically round the ring, so slot k is
always further along than slot k-1, and a polygon whose points do that on a
simple loop cannot cross itself. That much has held from the start.

The two questions a frame has to answer about those positions are different
questions, and conflating them is what made the outline shimmer:

**Where do the points go?** At equal steps of a *measure* -- arc length, with
each vertex widening it by what that vertex is worth, meaning how far the
outline would stray if the point were skipped. A corner is a step that takes no
length, so a point lands exactly on it; a straight run is worth nothing, so
points there are spaced by length alone. Both terms move continuously as the
shape deforms, which choosing a fresh set of vertices per frame does not: RDP
picks a different set as the shape turns, and the outline was measured 0.05px
from the true silhouette at every keyframe and 14.26px from it in between.

**Which slot is which?** By the mesh. A slot that sat on a vertex last frame
sits on the same vertex this frame for as long as that vertex is on the
silhouette; the slots between two anchors stay between them, in the same
number. That is the part a measure cannot do on its own -- its budget divides
between corners and edges in proportion to what the corners are worth, so a
vertex joining or leaving the ring shifts every slot after it one place round
the loop.

The count is still one number for the whole clip -- an element declares its
point total once and correspondence depends on it -- so the caller takes the
largest any frame needs, and `need_for` sizes a frame the same way it will be
drawn. On the lab sphere that is 24 slots to draw a 113px circle within 0.24px,
against 482 for one slot per mesh vertex.

Measured on part_042, an elongated box crossing the frame at 80px a frame,
against Blender's own silhouette evaluated between the keyframes:

    choosing per frame     0.05px at a keyframe   14.26px between
    by measure             0.09px                  5.83px
    anchored to the mesh   0.32px                  5.28px, and ~2px typical
    the price of 24fps        --                   1.50px

The last line is the floor: two keyframes a 24th of a second apart, joined by
straight lines, cannot follow a shape that is turning. It was measured by
resampling the true rings densely and evenly, which is as good as any rule can
be, and it is what the rest is aiming at.
"""

from __future__ import annotations

import os

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
    return wound(loop, xy)[1]


def evaluate(positions, poly):
    """Positions -> (k, 2) points. `positions` is (k, 2) of (segment, fraction)."""
    m = len(poly)
    idx = positions[:, 0].astype(np.int64) % m
    t = positions[:, 1][:, None]
    a = poly[idx]
    b = poly[(idx + 1) % m]
    return a + (b - a) * t


def _point_seg_dist(p, a, b):
    """Distance from each point to *its own* segment. All pairs at once."""
    ab = b - a
    den = np.einsum("ij,ij->i", ab, ab)
    u = np.clip(np.einsum("ij,ij->i", p - a, ab) / np.where(den > 0, den, 1e-12),
                0.0, 1.0)
    return np.linalg.norm(p - (a + u[:, None] * ab), axis=1)


def span_errors(positions, poly):
    """Per-span deviation of the resampled outline from the real one.

    Every ring vertex belongs to exactly one span -- the one it falls inside,
    by arc length -- so the whole measurement is one pass over the ring rather
    than one pass per span. That matters because sizing an element now asks
    this question repeatedly: it went from the cost of the outline squared to
    the cost of the outline.
    """
    k = len(positions)
    m = len(poly)
    if k < 2 or m < 3:
        return np.zeros(max(k, 1))
    cum, total = arc_table(poly)
    if total <= 0.0:
        return np.zeros(k)
    pts = evaluate(positions, poly)
    s = arc_of(positions, poly) * total
    # Slots run monotonically round the loop but need not start at arc zero,
    # and the one that wraps lands back at zero rather than at the total --
    # so rolling to the smallest is not enough to make the sequence ascending,
    # and searchsorted on a sequence that is not ascending returns nonsense.
    # It read a correctly drawn box as 90px wrong. Sort, and the question of
    # where the loop was cut stops arising.
    order = np.argsort(s, kind="stable")
    sr, pr = s[order], pts[order]
    idx = np.searchsorted(sr, cum[:m], side="right") - 1
    # A vertex before the first slot belongs to the span that wraps the end,
    # which is what index -1 already selects.
    d = _point_seg_dist(poly, pr[idx], pr[(idx + 1) % k])
    out = np.zeros(k)
    np.maximum.at(out, idx % k, d)
    return out[np.argsort(order, kind="stable")]


def max_error(positions, poly):
    """How far the resampled outline strays from the true one, in pixels."""
    if not len(positions):
        return 0.0
    return float(span_errors(positions, poly).max())


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
    frac = (want - cum[idx]) / span
    # A point at the far end of a segment *is* the next vertex, and saying so
    # keeps one position for one place. Left alone, a slot that lands a
    # rounding error short of a vertex draws a point a fraction of a
    # picometre from the slot already there, and a pair of segments that
    # plainly share an endpoint reads as two that cross.
    roll = frac >= 1.0 - 1e-12
    idx = np.where(roll, (idx + 1) % m, idx)
    frac = np.where(roll | (frac <= 1e-12), 0.0, frac)
    return np.stack([idx.astype(np.float64), frac], axis=1)


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
    # built, and a collinear graze is not a tangle. Both sides must genuinely
    # straddle, which a zero does not -- with more slots than the ring has
    # vertices two of them coincide, and reading `zero on one side` as a
    # straddle called a plain rounded rectangle self-crossing.
    def straddles(a, b):
        return ((a > 0) & (b < 0)) | ((a < 0) & (b > 0))

    return not bool((straddles(d1, d2) & straddles(d3, d4)).any())


# ---------------------------------------------------------------------------
# where the points go
# ---------------------------------------------------------------------------
#
# Choosing a fresh set of vertices each frame is a *discrete* decision, and a
# discrete decision cannot be a continuous function of a turning shape. On
# part_042 -- an elongated box, the plainest thing in the scene -- that showed
# up exactly as the sentence predicts: the drawn outline sits within 0.05px of
# the real silhouette at every keyframe and strays 14.26px between them, so the
# shape is right whenever it is checked and wrong the whole time in between. At
# normal speed it reads as the outline briefly rotating; played slowly it is
# the only thing you can look at.
#
# Carrying last frame's set forward does not fix it either, and the reason is
# worth writing down: the carry was a rigid rotation of arc length, so it could
# not hold a corner on a corner. Measured on that same box it missed by 12 to
# 30px in all sixty frames and was rejected in all sixty -- the branch never
# once fired. Pinning every mesh vertex the two rings share and interpolating
# arc length between them does hold the corners, and costs 0.34px for one step
# instead of 12.02px, but vertices join and leave a silhouette constantly and
# the error compounds to 17px over a quarter turn.
#
# So neither "choose again" nor "keep what we had" is right. The points have to
# be a continuous function of the shape alone -- no memory, no choosing.
#
# That function is a measure. Walk the loop accumulating arc length *and* what
# each vertex is worth -- how far the outline would stray if it were skipped --
# and put the points at equal steps of the total. A corner is a step of the
# measure that takes no length, so points land exactly on it and the shape
# stays sharp. A straight run is worth nothing, so points there are spaced by
# length alone. Both terms move continuously as the shape deforms: a corner
# that softens gives up its share gradually, and a vertex arriving on the
# silhouette arrives collinear, worth nothing, and takes nothing from anyone.
#
# `share` is the only knob, and it is dimensionless: how much of the budget the
# corners get. Together with the tolerance it settles the count -- a little
# above the number of vertices actually worth drawing.
#
# Only the phase is left to decide -- where around the loop the first point
# sits -- and it is one number, chosen to sit closest to where the points were
# last frame. That is the whole of the frame-to-frame machinery: `choose`,
# `pad_to` and `align` all collapse into it.

def _worth(poly, window):
    """What each vertex is worth: how far the outline would stray without it.

    The distance from a vertex to the chord across it -- the error skipping it
    would cost, in pixels, which is the only currency that matters here. Turn
    angle is not it: a shallow bend at the end of a long edge turns through
    very little and costs a great deal.

    The chord is taken between the points a fixed *arc length* either side, not
    between the neighbouring vertices, and that is the whole difference between
    a measure that holds and one that does not. Neighbouring vertices are a
    topological fact, and a silhouette's topology jumps: when the ring switches
    from one chain of edges to another across the same gap, every vertex there
    gets new neighbours at once and its worth changes discontinuously, though
    the shape on screen barely moved. Measured on part_042 the total worth fell
    from 12.90 to 8.50 and climbed back to 10.57 across two frames, which
    re-partitioned every slot between the corners and the edges and dragged one
    of them 27px further than its neighbours. An arc-length window asks the
    same geometric question of both chains and gets the same answer.

    It is not free. A window says the outline needs a point *somewhere* about
    here, and every vertex inside it says so, so a smooth arc counts the same
    requirement once per vertex: 3442 points across the probe against 3137 for
    the distance to a vertex's own neighbours. Both were tried on the whole
    model and the difference came to 0.2MB, against 0.56px on the worst gap
    between keyframes and better than half a pixel at the frames the artefact
    was reported at. The stable one is worth the bytes. Most of the remaining
    gap to a minimal vertex subset -- 1770 points -- is neither of these; it is
    what an evenly stepped rule costs against one free to put its points
    wherever it likes, and that is the price of not choosing per frame.
    """
    cum, total = arc_table(poly)
    if total <= 0.0:
        return np.zeros(len(poly))
    here = cum[:len(poly)]
    h = min(max(float(window), 1e-9), total / 4.0)
    back = evaluate(at_arc(((here - h) % total) / total, poly), poly)
    fwd = evaluate(at_arc(((here + h) % total) / total, poly), poly)
    return _point_seg_dist(poly, back, fwd)


def measure(poly, tol, n):
    """The loop's sampling measure -- arc length with the corners widened.

    Returns `(mu, arc, mu_total, length)`, matched knots of a monotone
    piecewise-linear map from measure to arc length. Every vertex contributes
    two knots at the same arc: the measure steps across a corner while the arc
    stands still, which is what puts a point exactly on it.

    A vertex's weight is `d / tol`, capped at one: worth the tolerance or more
    and it counts for a whole share, worth less and it counts proportionally.
    That the cap is flat matters. Weighting by raw distance lets one big corner
    dwarf the rest, and an equal-step sampler only lands on a step it cannot
    stride over -- a 15-vertex ring was still 1.18px wrong at 30 points because
    the shallow vertices never earned a step wide enough to catch one. Even a
    soft saturation is not enough: at `d / (d + tol)` a corner worth 12.7px
    against a 0.35px tolerance still weighs 0.973, its step falls 3% short of
    catching a point, and across eight such corners one is missed a fifth of
    the time.

    How wide those steps are is not a taste, and there is exactly one right
    answer. Make them wider than a sampling step and a corner swallows two
    points, which pile up and do nothing -- until the shape shifts, one of them
    decants onto the neighbouring corner, and that point sweeps the length of
    an edge in a single frame. Measured on a bevelled box: 93px in one step.
    Make them narrower and a corner is missed outright.

    So `kappa = length / (n - total_worth)`, which makes a full-weight corner's
    step exactly one sampling step wide, whatever the point count. An interval
    exactly one step wide holds exactly one point of an evenly spaced set --
    never two, never none -- and both its ends map to the corner itself, so
    even the boundary case lands there. The budget then partitions itself: one
    point on each corner worth one, and the remaining `n - total_worth` spread
    along the edges by length. A corner worth half a share gets a step half as
    wide, and so takes a point for half the phases it could sit at -- which is
    what a half-significant corner deserves.

    The total is `length + kappa * total_worth` however the ring happens to be
    indexed, so two frames whose rings start at different vertices still get
    the same spacing. `walk_loop` starts wherever it likes, so that is not a
    nicety.
    """
    m = len(poly)
    seg = np.linalg.norm(np.roll(poly, -1, axis=0) - poly, axis=1)
    length = float(seg.sum())
    if m < 3 or length <= 0.0 or n < 1:
        return None
    # The window is the sampling spacing: a vertex is worth what it adds
    # at the resolution the outline is actually drawn at.
    d = _worth(poly, length / n)
    weight = np.clip(d / max(float(tol), 1e-9), 0.0, 1.0)
    total = float(weight.sum())
    # Below `total` points there is not budget for a point on every corner
    # worth one; the floor keeps kappa finite and spends what there is on the
    # corners worth most.
    kappa = length / max(n - total, 0.05 * n) if total > 1e-12 else 0.0

    arc = np.concatenate([[0.0], np.cumsum(seg)])
    before = arc[1:] + kappa * np.concatenate([[0.0], np.cumsum(weight[1:m])])
    after = before + kappa * np.concatenate([weight[1:m], [weight[0]]])
    mu = np.empty(2 * m + 1)
    at = np.empty(2 * m + 1)
    mu[0] = at[0] = 0.0
    mu[1::2], mu[2::2] = before, after
    at[1::2] = at[2::2] = arc[1:]
    return mu, at, float(mu[-1]), length


def spread(poly, n, tol, phase=0.0, knots=None):
    """`n` positions at equal steps of the measure, the first one at `phase`."""
    knots = measure(poly, tol, n) if knots is None else knots
    if knots is None or n < 1:
        return np.zeros((max(n, 0), 2))
    mu, at, mu_total, length = knots
    want = (float(phase) + np.arange(n) * (mu_total / n)) % mu_total
    return at_arc(np.interp(want, mu, at) / length, poly)


def _phase_cost(poly, n, phases, prev_pts, knots):
    """How far `n` points at each phase sit from where they were last frame."""
    mu, at, mu_total, length = knots
    want = ((np.asarray(phases, dtype=np.float64)[:, None]
             + np.arange(n) * (mu_total / n)) % mu_total)
    pos = at_arc(np.interp(want.ravel(), mu, at) / length, poly)
    pts = evaluate(pos, poly).reshape(len(phases), n, 2)
    return ((pts - prev_pts[None, :, :]) ** 2).sum(axis=(1, 2))


def _settle_phase(poly, n, prev_pts, knots):
    """The phase whose points sit closest to last frame's.

    A coarse pass over the whole loop, because the ring's own starting vertex
    moves and the offset between two frames is not small; then a ternary
    narrowing, because the answer wants to be finer than any grid worth
    evaluating. Bounded work whatever the point count.
    """
    mu_total = knots[2]
    coarse = min(max(4 * n, 32), 128)
    grid = np.arange(coarse) * (mu_total / coarse)
    best = float(grid[int(np.argmin(
        _phase_cost(poly, n, grid, prev_pts, knots)))])
    lo, hi = best - mu_total / coarse, best + mu_total / coarse
    for _ in range(20):
        a, b = lo + (hi - lo) / 3.0, hi - (hi - lo) / 3.0
        ca, cb = _phase_cost(poly, n, np.array([a, b]), prev_pts, knots)
        if ca <= cb:
            hi = b
        else:
            lo = a
    return 0.5 * (lo + hi)


def place(poly, n, tol, prev_pts=None):
    """This frame's `n` positions. The shape says where; last frame says which."""
    knots = measure(poly, tol, n)
    if knots is None:
        return np.zeros((n, 2))
    phase = 0.0
    if prev_pts is not None and len(prev_pts) == n and n > 0:
        phase = _settle_phase(poly, n, np.asarray(prev_pts, dtype=np.float64),
                              knots)
    return spread(poly, n, tol, phase, knots)


def need_for(poly, tol, cap=None, floor=3):
    """The fewest points this outline needs to stay within `tol`.

    Sized the way it is drawn, so the count that comes out is the count that
    gets used.

    Error does not fall strictly as points are added, and the exception is not
    academic. A vertex is guaranteed a point once its share of the measure is a
    full step, so below about the number of corners worth drawing the corners
    are caught or missed by arithmetic luck. That matters beyond
    this frame: the count is settled once for the whole clip from the busiest
    frame, so a quieter frame can be handed *more* points than it asked for and
    must not draw worse for them.

    So the count returned is one that holds when it is exceeded. Bisection
    finds a candidate; the counts just above it are then checked, and the
    search moves up until they hold too. Above the corner threshold the error
    really does fall monotonically, so this settles at once or nearly so.
    """
    m = len(poly)
    if m < 3:
        return max(floor, m)
    limit = max(3 * m if cap is None else min(int(cap), 3 * m), floor, 3)

    def ok(n):
        return max_error(spread(poly, min(n, limit), tol), poly) <= tol

    if not ok(limit):
        return limit
    lo, hi = max(floor, 3), limit
    while lo < hi:
        mid = (lo + hi) // 2
        if ok(mid):
            hi = mid
        else:
            lo = mid + 1
    # Above the threshold where every corner is guaranteed a point the error
    # really does fall monotonically, so this settles at once or nearly so; the
    # bound is there because a sizing pass runs for every merged element in
    # every frame and must not be able to walk the whole range.
    steps = 0
    while lo < limit and steps < 8 and not all(ok(lo + k) for k in (1, 2, 3)):
        lo += 1
        steps += 1
    return lo


# ---------------------------------------------------------------------------
# which slot is which
# ---------------------------------------------------------------------------
#
# The measure settles where the points go, and it does that well: the outline
# is drawn to within 0.09px of the real silhouette at every keyframe. What it
# cannot settle is which slot is which, and that is a separate question with a
# separate answer.
#
# Equal steps of the measure divide the budget between the corners and the
# edges in proportion to what the corners are worth. When a vertex joins or
# leaves the silhouette that division moves by one, every slot after it shifts
# a place round the loop, and one of them travels 30px further than its
# neighbours in a single frame. Measured on part_042: the ring goes 15 to 14
# vertices at frame 28 and the worst slot moves 109px where the median moves
# 79px. Nothing is drawn wrong -- both frames are right -- but the straight
# line the player draws between them cuts the corner, and that is the artefact.
#
# So the correspondence is anchored to the mesh instead. A slot that sat on a
# vertex last frame sits on the same vertex this frame, for as long as that
# vertex is on the silhouette. The slots between two such anchors stay between
# them, in the same number, placed by the measure restricted to that span --
# so a span that gains a vertex still holds the slots it held, and no index
# anywhere else moves.
#
# A vertex leaves the silhouette by becoming collinear with the outline, and
# arrives the same way, so a slot released by a departing anchor is released
# somewhere it already was, and a slot adopting an arriving one slides along a
# stretch that is nearly straight. Both are free. What is expensive -- a point
# crossing a corner -- is exactly what anchoring prevents.
#
# On part_042 that takes the worst error between keyframes from 5.83px to
# 5.25px and the typical one to about 2px, against a floor of 1.5px -- and it
# clears the frames the artefact was reported at outright, which now measure
# under 1.3px. What is left is not the shifting: it is the moment an anchor
# leaves the silhouette for good, when the span it divided has to be re-spread
# and its slots move together.


def wound(loop, xy):
    """The ring as `(vertex ids, points)`, wound the way `polyline` describes.

    Same rule, with the ids kept alongside: reversing the points to fix the
    winding has to reverse the ids too, or every anchor binds to the wrong
    vertex.
    """
    ids = np.asarray(loop, dtype=np.int64)
    pts = np.asarray(xy, dtype=np.float64)[ids]
    if len(pts) >= 3:
        x, y = pts[:, 0], pts[:, 1]
        if float((x * np.roll(y, -1) - y * np.roll(x, -1)).sum()) < 0.0:
            return ids[::-1], pts[::-1]
    return ids, pts


def _agree(prev_ids, loop):
    """Anchors still on the ring, in an order the ring and the slots agree on.

    A vertex can be on both rings and yet sit at a different place in the
    cycle, and honouring all of those at once would ask the slots to run
    backwards. Anything that cannot be reached going forwards round both the
    slot circle and the ring is dropped, worst first, until what is left is a
    single lap of each.
    """
    where = {int(v): i for i, v in enumerate(loop)}
    cand = [(j, where[int(v)]) for j, v in enumerate(prev_ids)
            if int(v) >= 0 and int(v) in where]
    n, m = len(prev_ids), len(loop)
    while len(cand) >= 2:
        ring = [c[1] for c in cand]
        step = [(ring[(t + 1) % len(ring)] - ring[t]) % m
                for t in range(len(ring))]
        if sum(step) == m and min(step) > 0:
            break
        drop = max(range(len(cand)), key=lambda t: step[t - 1] + step[t])
        cand.pop(drop)
    return cand


def _mu_after(mu, i, m):
    """Where the measure stands just past vertex `i`."""
    return float(mu[0]) if i == 0 else float(mu[2 * i])


def _mu_before(mu, i, m):
    """Where the measure stands just short of vertex `i`, a lap along."""
    return float(mu[2 * m - 1]) if i == 0 else float(mu[2 * i - 1])


def _identify(pos, poly, loop):
    """Which slots ended up sitting on a mesh vertex, and on which one.

    A position at a vertex arrives as that vertex's segment at fraction zero,
    exactly, because `at_arc` says so: a corner's share of the measure maps to
    one arc, and a fraction that rounds to either end of a segment is written
    as the vertex it reached. So this is an equality test, not a proximity one.
    """
    ids = np.full(len(pos), -1, dtype=np.int64)
    if not len(pos):
        return ids
    m = len(poly)
    seg = pos[:, 0].astype(np.int64) % m
    on = pos[:, 1] == 0.0
    ids[on] = loop[seg[on]]
    # A vertex anchors one slot and no more. With more slots than the ring has
    # vertices some of them necessarily coincide, and letting two claim the
    # same vertex leaves the anchor list with a zero-width step, which reads as
    # disorder and threw the whole list away -- 15 surviving anchors reduced to
    # one, twice in the first six frames. The extra slots stay free, which is
    # what they are.
    seen = set()
    for j, v in enumerate(ids):
        if v < 0:
            continue
        if int(v) in seen:
            ids[j] = -1
        else:
            seen.add(int(v))
    return ids


def _lay_out(poly, knots, cum, keep, counts, n, base):
    """Arc position for every slot: anchors on their vertex, the rest between.

    The slots inside a span are placed by the measure restricted to that span,
    so a corner inside it still gets a point, and there are exactly `counts[t]`
    of them however the span has changed shape.
    """
    mu, at, mu_total, _ = knots
    m = len(poly)
    arcs = np.empty(n)
    slot = base
    for t, (_, vi) in enumerate(keep):
        nvi = keep[(t + 1) % len(keep)][1]
        arcs[slot % n] = cum[vi]
        k = counts[t]
        if k:
            a0 = _mu_after(mu, vi, m)
            a1 = _mu_before(mu, nvi, m)
            if a1 <= a0:
                a1 += mu_total
            want = a0 + (a1 - a0) * np.arange(1, k + 1) / (k + 1)
            arcs[(slot + 1 + np.arange(k)) % n] = np.interp(
                want % mu_total, mu, at)
        slot += 1 + k
    return arcs


def _span_error(pos, poly, counts, n, base):
    """Worst deviation inside each span."""
    each = span_errors(pos, poly)
    out, slot = [], base
    for k in counts:
        out.append(float(max(each[(slot + q) % n] for q in range(k + 1))))
        slot += 1 + k
    return out


def _merge_span(keep, counts, at):
    """Let go of anchor `at`, pooling the spans it divided.

    The freed slot joins them, and every slot keeps the index it had.
    """
    prev = (at - 1) % len(counts)
    c = list(counts)
    c[prev] = c[prev] + 1 + c[at]
    c.pop(at)
    return keep[:at] + keep[at + 1:], c


def carry(poly, loop, n, tol, prev=None):
    """This frame's `n` positions, and what each one is anchored to.

    `prev` is `(ids, points)` as returned last frame; the ids are mesh vertex
    numbers, or -1 for a slot that was between anchors.
    """
    knots = measure(poly, tol, n)
    if knots is None:
        return np.zeros((n, 2)), np.full(n, -1, dtype=np.int64)
    if prev is None:
        pos = place(poly, n, tol, None)
        return pos, _identify(pos, poly, loop)

    prev_ids, prev_pts = prev
    keep = _agree(prev_ids, loop) if len(prev_ids) == n else []
    if len(keep) < 2:
        # Nothing recognisable survived -- a fresh silhouette, or the object
        # coming back into view. Place it on its own merits and start again.
        pos = place(poly, n, tol, prev_pts if len(prev_pts) == n else None)
        return pos, _identify(pos, poly, loop)

    cum, total = arc_table(poly)
    base = keep[0][0]
    counts = [((keep[(t + 1) % len(keep)][0] - slot) % n) - 1
              for t, (slot, _) in enumerate(keep)]

    arcs = _lay_out(poly, knots, cum, keep, counts, n, base)
    pos = at_arc(arcs / total, poly)

    # A span that has just acquired a corner has no slot to put on it, and
    # without a way to find one the error creeps up frame after frame -- 0.41px,
    # 0.55, 0.69, up to 1.22px against a 0.35px tolerance, one corner cut a
    # little more each time.
    #
    # It finds one by giving up the anchor at its edge, which merges it with
    # the neighbouring span and pools their slots. That costs nothing in
    # correspondence: no slot changes its index, and the vertex let go is still
    # a corner, so the measure over the wider span puts a point back on it --
    # just not a pinned one. Taking a slot from further round the ring instead
    # would shift every anchor in between one place along, and each of those is
    # a point jumping the length of a span: 5.3px of visible error where this
    # costs a fraction of that.
    errs = _span_error(pos, poly, counts, n, base)
    for _ in range(6):
        need = int(np.argmax(errs))
        if errs[need] <= tol or len(keep) <= 2:
            break
        best = None
        for edge in (need, (need + 1) % len(keep)):
            k2, c2 = _merge_span(keep, counts, edge)
            b2 = k2[0][0]
            p2 = at_arc(_lay_out(poly, knots, cum, k2, c2, n, b2) / total, poly)
            e2 = _span_error(p2, poly, c2, n, b2)
            if best is None or max(e2) < max(best[3]):
                best = (k2, c2, p2, e2, b2)
        if max(best[3]) >= max(errs):
            break                      # letting go of either anchor made it worse
        keep, counts, pos, errs, base = best[0], best[1], best[2], best[3], best[4]

    if max(errs) > tol:
        # Honouring the anchors cannot draw this shape. That means they are
        # stale -- the silhouette has become something else -- so place it on
        # its own merits and let the anchors re-form. One frame of shifted
        # slots costs less than a whole clip drawn out of tolerance.
        fresh = place(poly, n, tol, prev_pts if len(prev_pts) == n else None)
        if max_error(fresh, poly) < max(errs):
            pos = fresh

    return pos, _identify(pos, poly, loop)
