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


def polyline(loop, xy, pts=None):
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
    return wound(loop, xy, pts)[1]


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


def span_errors(positions, poly, table=None):
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
    cum, total = arc_table(poly) if table is None else table
    if total <= 0.0:
        return np.zeros(k)
    pts = evaluate(positions, poly)
    s = arc_of(positions, poly, (cum, total)) * total
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


def max_error(positions, poly, table=None):
    """How far the resampled outline strays from the true one, in pixels."""
    if not len(positions):
        return 0.0
    return float(span_errors(positions, poly, table).max())


def arc_table(poly):
    """Cumulative arc length around the closed loop, and its total."""
    seg = np.linalg.norm(np.roll(poly, -1, axis=0) - poly, axis=1)
    return np.concatenate([[0.0], np.cumsum(seg)]), float(seg.sum())


def arc_of(positions, poly, table=None):
    """Positions -> where they sit around the loop, as fractions in [0, 1)."""
    cum, total = arc_table(poly) if table is None else table
    if total <= 0.0:
        return np.zeros(len(positions))
    idx = positions[:, 0].astype(np.int64) % len(poly)
    here = cum[idx] + positions[:, 1] * (cum[idx + 1] - cum[idx])
    return (here / total) % 1.0


def at_arc(s, poly, table=None):
    """Fractions in [0, 1) -> positions on this loop. The inverse of arc_of."""
    cum, total = arc_table(poly) if table is None else table
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


def weights(poly, tol, n):
    """What each vertex is worth, as a share of one point: `d / tol`, capped.

    The window is the sampling spacing -- a vertex is worth what it adds at the
    resolution the outline is actually drawn at.
    """
    seg = np.linalg.norm(np.roll(poly, -1, axis=0) - poly, axis=1)
    length = float(seg.sum())
    if len(poly) < 3 or length <= 0.0 or n < 1:
        return None
    d = _worth(poly, length / n)
    return np.clip(d / max(float(tol), 1e-9), 0.0, 1.0)


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
    weight = weights(poly, tol, n)
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


def spread(poly, n, tol, phase=0.0, knots=None, table=None):
    """`n` positions at equal steps of the measure, the first one at `phase`."""
    knots = measure(poly, tol, n) if knots is None else knots
    if knots is None or n < 1:
        return np.zeros((max(n, 0), 2))
    mu, at, mu_total, length = knots
    want = (float(phase) + np.arange(n) * (mu_total / n)) % mu_total
    return at_arc(np.interp(want, mu, at) / length, poly, table)


def _phase_cost(poly, n, phases, prev_pts, knots, table=None):
    """How far `n` points at each phase sit from where they were last frame."""
    mu, at, mu_total, length = knots
    want = ((np.asarray(phases, dtype=np.float64)[:, None]
             + np.arange(n) * (mu_total / n)) % mu_total)
    pos = at_arc(np.interp(want.ravel(), mu, at) / length, poly, table)
    pts = evaluate(pos, poly).reshape(len(phases), n, 2)
    return ((pts - prev_pts[None, :, :]) ** 2).sum(axis=(1, 2))


def _settle_phase(poly, n, prev_pts, knots, table=None):
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
        _phase_cost(poly, n, grid, prev_pts, knots, table)))])
    lo, hi = best - mu_total / coarse, best + mu_total / coarse
    for _ in range(20):
        a, b = lo + (hi - lo) / 3.0, hi - (hi - lo) / 3.0
        ca, cb = _phase_cost(poly, n, np.array([a, b]), prev_pts, knots,
                             table)
        if ca <= cb:
            hi = b
        else:
            lo = a
    return 0.5 * (lo + hi)


def place(poly, n, tol, prev_pts=None, table=None):
    """This frame's `n` positions. The shape says where; last frame says which."""
    knots = measure(poly, tol, n)
    if knots is None:
        return np.zeros((n, 2))
    phase = 0.0
    if prev_pts is not None and len(prev_pts) == n and n > 0:
        phase = _settle_phase(poly, n, np.asarray(prev_pts, dtype=np.float64),
                              knots, table)
    return spread(poly, n, tol, phase, knots, table)


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
    # A point for every vertex draws the ring exactly, so more than that buys
    # no accuracy at all -- there is nothing left to be accurate about. It buys
    # jitter instead: the surplus has nowhere it needs to be, and part_140 spent
    # a frame with ten of its twenty-four slots stacked on a single vertex and
    # the next scattering them 40 to 60px. The count came out at 24 for a ring
    # of 6 because the ceiling was three times the ring, and a frame that could
    # not meet the tolerance at any count returned that ceiling.
    #
    # Two spare, because the sampler needs a little slack to land on every
    # vertex at once -- a 59-gon settles at 61.
    ceiling = m + 2
    limit = max(ceiling if cap is None else min(int(cap), ceiling), floor, 3)

    table = arc_table(poly)

    def ok(n):
        return max_error(spread(poly, min(n, limit), tol, table=table), poly,
                         table) <= tol

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
# A vertex does not leave the silhouette gently. It leaves when the faces
# meeting at it stop straddling the facing boundary, and by then it has rotated
# *into* the projected shape: measured across two parts, a departing anchor
# sits 22px inside the new outline typically and 87px at worst. So the slot it
# held cannot simply stay put, and it cannot be re-spread with the rest of its
# span either -- on part_113, a stick, that threw one slot 694px, the length of
# the object, and opened the fill into a wedge four times its true area.
#
# What it can do is keep its place: the point is put back where it was, clamped
# onto the ring it now lives on. The span is re-spread by the measure only when
# keeping everything still would draw the shape outside tolerance, which is
# what stops the arrangement going stale.
#
# On part_042 that takes the worst error between keyframes from 5.83px to
# 5.25px and the typical one to about 2px, against a floor of 1.5px -- and it
# clears the frames the artefact was reported at outright, which now measure
# under 1.3px. What is left is not the shifting: it is the moment an anchor
# leaves the silhouette for good, when the span it divided has to be re-spread
# and its slots move together.


def wound(loop, xy, pts=None, ccw=True):
    """The ring as `(vertex ids, points)`, wound the way `polyline` describes.

    Same rule, with the ids kept alongside: reversing the points to fix the
    winding has to reverse the ids too, or every anchor binds to the wrong
    vertex.

    `pts` is given when the ring visits a vertex more than once -- a boundary
    that pinches, spliced into one walk. Then an id is not enough to find the
    position, because the repeat carries -1 to keep it from claiming a vertex
    twice, and -1 would index the last vertex in the mesh.

    `ccw` is False for a ring that has to run the other way: one subpath of a
    multi-piece fill that nests inside another is a hole, and under the nonzero
    winding rule a hole is only a hole if it is wound against its container.
    """
    ids = np.asarray(loop, dtype=np.int64)
    pts = (np.asarray(xy, dtype=np.float64)[ids] if pts is None
           else np.asarray(pts, dtype=np.float64))
    if len(pts) >= 3:
        x, y = pts[:, 0], pts[:, 1]
        area = float((x * np.roll(y, -1) - y * np.roll(x, -1)).sum())
        if (area < 0.0) == bool(ccw):
            return ids[::-1], pts[::-1]
    return ids, pts


def _monotone(cand, n, m):
    """Keep only what can be reached going forwards round both circles."""
    cand = list(cand)
    while len(cand) >= 2:
        ring = [c[1] for c in cand]
        step = [(ring[(t + 1) % len(ring)] - ring[t]) % m
                for t in range(len(ring))]
        if sum(step) == m and min(step) > 0:
            break
        cand.pop(max(range(len(cand)), key=lambda t: step[t - 1] + step[t]))
    return cand


# Which slot holds which vertex is decided by where things sit *around the
# ring*, not by which mesh vertex they are and not by where they are on screen.
#
# Identity is not enough, and a rod shows why. Its silhouette is three vertices
# down one side and three back up the other, and a quarter turn later it is
# still three and three -- but the vertices it shares with the frame before sit
# at a different offset within that pattern. Pinning them where they are then
# *forces* two slots to cross the length of the object, because the order round
# the ring says so. The right answer slides the shared vertices along their own
# end of the rod, which identity cannot express.
#
# Screen distance is not enough either. It has to know how the object moved
# before it can say what is near, and a part crossing the frame moves 80px
# between keyframes while the detail being matched is 6px across. A median
# shift is 40px wrong at both ends of a turning rod; a fitted turn and scale is
# better and still not enough on a part that is also foreshortening. Both were
# built, and what was left over let the whole assignment rotate several places
# round the ring at some frames.
#
# Arc length round the ring needs neither. It is a property of the shape, so
# the motion cancels of its own accord, and the two rings are lined up by
# nothing more than the vertices they share. What is left is a monotone
# matching: for each slot in turn, either take the next vertex, skip that
# vertex, or leave the slot unanchored. One sweep, one numpy operation per slot.


def _around(poly, loop):
    """Each vertex's place round the ring, as a fraction, and the ring's length."""
    cum, total = arc_table(poly)
    if total <= 0.0:
        return np.zeros(len(loop)), 1.0
    return cum[:len(loop)] / total, total


def _agree(prev_ids, prev_pts, poly, loop, prev_loop=None, prev_poly=None):
    """Which slot holds which ring vertex, carried over from last frame."""
    n, m = len(prev_ids), len(loop)
    # A ring that pinches visits one vertex twice and carries -1 on the second
    # visit. That is "no anchor here", not a vertex: keyed into these maps it
    # would make every pinch point look like the same shared vertex, and two
    # rings would be lined up by a coincidence of shape rather than by mesh.
    where = {int(v): i for i, v in enumerate(loop) if int(v) >= 0}
    plain = _monotone([(j, where[int(v)]) for j, v in enumerate(prev_ids)
                       if int(v) >= 0 and int(v) in where], n, m)
    if prev_loop is None or prev_poly is None or m < 3 or len(prev_loop) < 3:
        return plain

    here, _ = _around(poly, loop)
    before, _ = _around(prev_poly, prev_loop)
    back = {int(v): i for i, v in enumerate(prev_loop) if int(v) >= 0}
    both = [(back[int(v)], i) for i, v in enumerate(loop)
            if int(v) >= 0 and int(v) in back]
    if len(both) < 1:
        return plain

    # Line the two rings up by the vertices they share, as an angle so that
    # the wrap takes care of itself.
    turn = np.exp(2j * np.pi * (here[[i for _, i in both]]
                                - before[[p for p, _ in both]]))
    offset = float(np.angle(turn.sum()) / (2.0 * np.pi))

    seats = np.array([j for j, v in enumerate(prev_ids)
                      if int(v) >= 0 and int(v) in back], dtype=np.int64)
    if len(seats) < 2:
        return plain
    want = (before[[back[int(prev_ids[j])] for j in seats]] + offset) % 1.0
    gap = np.abs(want[:, None] - here[None, :])
    cost = np.minimum(gap, 1.0 - gap) ** 2

    k = len(seats)

    # Which vertex the first slot takes settles the rest, so every choice of
    # it is tried rather than guessed. Guessing it -- pinning the cheapest
    # single pair and matching around that -- is what put a rod's slots on the
    # wrong ends: the cheapest pair was individually perfect and belonged to
    # the wrong arrangement. All of them at once is the same sweep with the
    # choices stacked along an axis, so it costs an array rather than a loop.
    #
    # What it costs to leave a slot unanchored, against the squared distance
    # round the ring that anchoring it would cost. One slot spacing: a slot is
    # better left free than dragged further than the gap to its neighbour.
    #
    # Priced above every distance instead, the sweep has to fill every slot,
    # and there are frames where that cannot be done at any price. A rod's
    # silhouette goes from four vertices on the near end and four on the far
    # one to three and five; insisting each vertex hold a slot then *forces*
    # one of them to cross the 760px length of the object. Declining is the
    # right answer there, and it has to be affordable to be reachable.
    LOOSE = (1.0 / max(k, 1)) ** 2
    order = (np.arange(m)[:, None] + np.arange(m)[None, :]) % m
    if m <= 48:
        laid = cost[:, order]                       # (k, cut, vertex)
        rows = np.empty((m, m))
        rows[:, :] = laid[0, :, 0][:, None]
        for t in range(1, k):
            was = rows
            step = np.empty((m, m))
            step[:, 0] = was[:, 0] + LOOSE
            step[:, 1:] = np.minimum(was[:, :-1] + laid[t, :, 1:],
                                     was[:, 1:] + LOOSE)
            rows = np.minimum.accumulate(step, axis=1)
        cut = int(np.argmin(rows[:, -1]))
    else:
        # Too big to try every cut. A ring of hundreds is a smooth shape,
        # where the vertex nearest a slot is the one it was already on and any
        # sound cut finds the same answer.
        #
        # The bound was 20 for a while, on the belief that trying every cut was
        # what had made the bake four times longer. It was not -- that was the
        # tolerance chasing above -- and 20 was far too mean: part_021 has a
        # 48-vertex ring and was drawn 9.90px from its true silhouette where
        # 0.07px was available. The work still grows as the square of the ring,
        # so it is not free either: 2.5ms a frame at 48, 7ms at 64, 23ms at 128.
        cut = int(np.argmin(cost) % m)

    vr = order[cut]
    laid = cost[:, vr]
    rows = np.empty((k, m))
    rows[0, :] = laid[0, 0]
    for t in range(1, k):
        was = rows[t - 1]
        step = np.empty(m)
        step[0] = was[0] + LOOSE
        step[1:] = np.minimum(was[:-1] + laid[t, 1:], was[1:] + LOOSE)
        rows[t] = np.minimum.accumulate(step)

    out, t, col = [], k - 1, m - 1
    while t > 0:
        while col > 0 and rows[t][col] == rows[t][col - 1]:
            col -= 1
        if col > 0 and rows[t][col] == rows[t - 1][col - 1] + laid[t][col]:
            out.append((int(seats[t]), int(vr[col])))
            col -= 1
        t -= 1
    out.append((int(seats[0]), int(vr[0])))
    out.sort()
    return out if len(out) >= 2 else plain


def _span_spread(cum, weight, vi, nvi, m, k, total):
    """`k` arc positions strictly inside the span from vertex `vi` to `nvi`.

    The same rule as the whole loop, solved for this span. That distinction is
    the whole of it: `kappa` makes a full-weight corner's step exactly one
    sampling step wide, and a span samples at *its* step, not the loop's. Using
    the loop's, a corner inside a short span had a share many local steps wide
    and swallowed every point in it -- on part_113, a stick, two slots landed
    on the same corner while the far end of the object went unrepresented, and
    one of them travelled the 693px length of the stick to get there.
    """
    steps = (nvi - vi) % m
    width = ((cum[nvi] - cum[vi]) % total) or total
    inner = (vi + np.arange(1, steps)) % m
    if not len(inner) or k < 1:
        return (cum[vi] + width * np.arange(1, k + 1) / (k + 1)) % total
    w = weight[inner]
    kappa = width / max((k + 1) - float(w.sum()), 0.05 * (k + 1))
    d = (cum[inner] - cum[vi]) % total
    before = d + kappa * np.concatenate([[0.0], np.cumsum(w[:-1])])
    after = before + kappa * w
    size = 2 * len(inner) + 2
    xs, ys = np.empty(size), np.empty(size)
    xs[0] = ys[0] = 0.0
    xs[1:-1:2], xs[2:-1:2] = before, after
    ys[1:-1:2] = ys[2:-1:2] = d
    xs[-1] = width + kappa * float(w.sum())
    ys[-1] = width
    want = xs[-1] * np.arange(1, k + 1) / (k + 1)
    return (cum[vi] + np.interp(want, xs, ys)) % total


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


def _onto(poly, pts):
    """Where each point lands on the loop, as arc length along it.

    Every point against every segment at once. Rings run to a few hundred
    edges and slot counts to a few dozen, so the product is small and numpy
    swallows it whole.
    """
    cum, total = arc_table(poly)
    a = poly
    ab = np.roll(poly, -1, axis=0) - poly
    den = np.einsum("ij,ij->i", ab, ab)
    den = np.where(den > 0, den, 1e-12)
    rel = np.asarray(pts, dtype=np.float64)[:, None, :] - a[None, :, :]
    u = np.clip(np.einsum("kij,ij->ki", rel, ab) / den[None, :], 0.0, 1.0)
    foot = a[None, :, :] + u[:, :, None] * ab[None, :, :]
    k = np.argmin(np.linalg.norm(foot - np.asarray(pts)[:, None, :], axis=2),
                  axis=1)
    j = np.arange(len(pts))
    return cum[k] + u[j, k] * (cum[k + 1] - cum[k])


def _lay_out(poly, cum, weight, keep, counts, n, base, hold=None):
    """Arc position for every slot: anchors on their vertex, the rest between.

    A span listed in `hold` keeps its slots where `hold` says -- their own
    places from last frame, clamped onto this ring. Any other span is spread by
    its own measure, which puts a point on every corner inside it worth one.
    """
    m = len(poly)
    total = float(cum[-1])
    arcs = np.empty(n)
    slot = base
    for t, (_, vi) in enumerate(keep):
        nvi = keep[(t + 1) % len(keep)][1]
        arcs[slot % n] = cum[vi]
        k = counts[t]
        if k:
            idx = (slot + 1 + np.arange(k)) % n
            if hold is not None and t in hold:
                # Their own places, pinned inside the span and back in order,
                # so the slots stay monotone however the shape moved.
                width = ((cum[nvi] - cum[vi]) % total) or total
                rel = np.sort(np.clip((hold[t] - cum[vi]) % total, 0.0, width))
                arcs[idx] = (cum[vi] + rel) % total
            else:
                arcs[idx] = _span_spread(cum, weight, vi, nvi, m, k, total)
        slot += 1 + k
    return arcs


def _span_error(pos, poly, counts, n, base, table=None):
    """Worst deviation inside each span."""
    each = span_errors(pos, poly, table)
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

    `prev` is `(ids, points, ring, outline)` as returned last frame: the mesh
    vertex each slot held or -1, where the slots were drawn, and the ring and
    outline they were drawn on.
    """
    weight = weights(poly, tol, n)
    if weight is None:
        return np.zeros((n, 2)), np.full(n, -1, dtype=np.int64)
    # Worked out once and handed to everything below. It was being rebuilt
    # sixty-four times a frame, which is most of what a frame cost.
    table = arc_table(poly)
    cum, total = table
    if prev is None:
        pos = place(poly, n, tol, None, table)
        return pos, _identify(pos, poly, loop)

    prev_ids, prev_pts, prev_loop, prev_poly = prev
    keep = (_agree(prev_ids, prev_pts, poly, loop, prev_loop, prev_poly)
            if len(prev_ids) == n else [])
    if len(keep) < 2:
        # Nothing recognisable survived -- a fresh silhouette, or the object
        # coming back into view. Place it on its own merits and start again.
        pos = place(poly, n, tol,
                    prev_pts if len(prev_pts) == n else None, table)
        return pos, _identify(pos, poly, loop)

    base = keep[0][0]
    counts = [((keep[(t + 1) % len(keep)][0] - slot) % n) - 1
              for t, (slot, _) in enumerate(keep)]

    # Every slot keeps its place to begin with -- the point put back where it
    # was, clamped onto the ring it now lives on. Only a span that cannot draw
    # itself that way gets re-spread by the measure.
    landed = _onto(poly, prev_pts) if len(prev_pts) == n else None
    hold = None
    if landed is not None:
        hold, slot = {}, base
        for t, k in enumerate(counts):
            if k:
                hold[t] = landed[(slot + 1 + np.arange(k)) % n]
            slot += 1 + k
    arcs = _lay_out(poly, cum, weight, keep, counts, n, base, hold)
    pos = at_arc(arcs / total, poly, table)
    if hold:
        errs = _span_error(pos, poly, counts, n, base, table)
        loose = {t for t, e in enumerate(errs) if e > tol}
        if loose:
            hold = {t: v for t, v in hold.items() if t not in loose}
            arcs = _lay_out(poly, cum, weight, keep, counts, n, base, hold)
            pos = at_arc(arcs / total, poly, table)

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
    #
    # What this many points can do on this outline at all, before asking any
    # arrangement of them to do better. Where the count is short of what the
    # shape needs -- and it often is, since the count is settled once for the
    # whole clip -- no shuffling of slots will reach the tolerance, and both
    # the borrowing below and the fresh placement after it will run their full
    # course every frame to discover that. Chasing a target that is not there
    # cost four times the bake.
    reach = max(tol, max_error(spread(poly, n, tol, table=table), poly, table))
    errs = _span_error(pos, poly, counts, n, base, table)
    for _ in range(6):
        need = int(np.argmax(errs))
        if errs[need] <= reach or len(keep) <= 2:
            break
        best = None
        for edge in (need, (need + 1) % len(keep)):
            k2, c2 = _merge_span(keep, counts, edge)
            b2 = k2[0][0]
            p2 = at_arc(_lay_out(poly, cum, weight, k2, c2, n, b2) / total,
                        poly, table)
            e2 = _span_error(p2, poly, c2, n, b2, table)
            if best is None or max(e2) < max(best[3]):
                best = (k2, c2, p2, e2, b2)
        if max(best[3]) >= max(errs):
            break                      # letting go of either anchor made it worse
        keep, counts, pos, errs, base = best[0], best[1], best[2], best[3], best[4]

    if max(errs) > reach:
        # Honouring the anchors cannot draw this shape. That means they are
        # stale -- the silhouette has become something else -- so place it on
        # its own merits and let the anchors re-form. One frame of shifted
        # slots costs less than a whole clip drawn out of tolerance.
        fresh = place(poly, n, tol,
                      prev_pts if len(prev_pts) == n else None, table)
        if max_error(fresh, poly, table) < max(errs):
            pos = fresh

    return pos, _identify(pos, poly, loop)


# ---------------------------------------------------------------------------
# can this outline be carried across a gap at all?
# ---------------------------------------------------------------------------
#
# Everything above is about carrying an outline well. Some outlines cannot be
# carried at any standard, and no amount of care changes that: when a part
# turns edge-on its silhouette can go from a pentagon to a triangle between one
# frame and the next, and there is no correspondence between a pentagon and a
# triangle. Measured on the probe's blades, every rule lands in the same place
# -- 32 to 44px -- because the limit is the shape, not the sampling. Adding a
# keyframe halfway makes one of those gaps worse rather than better.
#
# A part like that should not be merged. Drawn face by face instead it has no
# correspondence problem at all: the same mesh vertices in every frame, and the
# only error left is how far a vertex's path across the screen bends in a 24th
# of a second, which for the same blade is 1.24px against 48px merged.
#
# Deciding that needs the shape *between* two frames, which no keyframe holds
# but which Blender will evaluate on request. So the question is asked directly
# rather than guessed at from something easier to reach, like the silhouette's
# area collapsing.
#
# It is also asked of the outline as actually written. This once scored an
# idealised even correspondence around the two rings instead, on the reasoning
# that no placement rule could do better -- which held for a plain ring and said
# nothing about one that pinches, where it was optimistic by about four times.
# `stray` is what remains of it: the measurement, without the stand-in. The
# judging itself is in bake.judge_carry, next to the geometry it reads.


def stray(a, b):
    """The worst distance between two closed polylines, measured both ways."""
    def one_way(p, q):
        seg = np.roll(q, -1, axis=0) - q
        den = np.einsum("ij,ij->i", seg, seg)
        den = np.where(den > 0, den, 1e-12)
        rel = p[:, None, :] - q[None, :, :]
        t = np.clip(np.einsum("kij,ij->ki", rel, seg) / den[None, :], 0.0, 1.0)
        foot = q[None, :, :] + t[:, :, None] * seg[None, :, :]
        return float(np.linalg.norm(p[:, None, :] - foot, axis=2).min(axis=1).max())

    if len(a) < 2 or len(b) < 2:
        return 0.0
    return max(one_way(a, b), one_way(b, a))


# ---------------------------------------------------------------------------
# placing points by the area they get wrong
# ---------------------------------------------------------------------------
#
# Everything above places points by a *measure* -- arc length with each vertex
# widening it by what skipping it would cost -- and then checks afterwards how
# far the result strayed. That is a proxy. It is a good proxy, and where it is
# wrong it is wrong quietly: the shapes it misjudges are the ones whose corners
# are not where their length is.
#
# The thing actually being paid for is area. A merged outline is a fill, and
# what a viewer sees is the ground it covers that the object does not, plus the
# ground the object covers that it misses. So place the points against that
# directly, and the proxy stops mattering.
#
# It decomposes, which is what makes it affordable. Every point sits *on* the
# silhouette, so the drawn polygon is inscribed in the true one and the total
# wrongness is the sum, span by span, of the area between a chord and the arc
# it cuts off. Moving one point disturbs exactly two spans. Nothing else in the
# ring has to be looked at, and one trial move costs a handful of arithmetic
# rather than a redrawn polygon:
#
#     area of [pa, v(sa+1) ... v(sb), pb]
#         = cross(pa, v(sa+1)) + SUM cross(v_j, v_j+1) + cross(v(sb), pb)
#           + cross(pb, pa),   all halved
#
# and that middle sum is a prefix along the ring, computed once per frame. So a
# span's area is O(1) whatever the arc spans, and a sweep over every candidate
# position for every point is one numpy expression.


def cross_table(poly):
    """Prefix sums of cross(v_j, v_j+1) round the ring. See `span_area`."""
    nxt = np.roll(poly, -1, axis=0)
    c = poly[:, 0] * nxt[:, 1] - poly[:, 1] * nxt[:, 0]
    return np.concatenate([[0.0], np.cumsum(c)])


def _arc_sum(ctab, i, j, m):
    """Cross-product sum from ring index i forward to j, wrapping if it must."""
    whole = ctab[m]
    straight = ctab[j] - ctab[i]
    return np.where(j >= i, straight, whole - ctab[i] + ctab[j])


def _one_span(poly, ctab, start, end):
    """Area between chord and arc, for spans given as (k, 2) position arrays."""
    m = len(poly)
    sa = start[:, 0].astype(np.int64) % m
    sb = end[:, 0].astype(np.int64) % m
    pa = poly[sa] + start[:, 1][:, None] * (poly[(sa + 1) % m] - poly[sa])
    pb = poly[sb] + end[:, 1][:, None] * (poly[(sb + 1) % m] - poly[sb])

    def cross(u, v):
        return u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]

    total = (cross(pa, poly[(sa + 1) % m])
             + _arc_sum(ctab, (sa + 1) % m, sb, m)
             + cross(poly[sb], pb) + cross(pb, pa))
    # A span that never leaves one segment cuts off nothing at all, and the
    # sum above would be reading a whole wrapped ring instead of an empty arc.
    return np.where(((sb - sa) % m) == 0, 0.0, np.abs(0.5 * total))


def span_area(positions, poly, ctab=None):
    """Area between each chord and the arc of the ring it cuts off.

    One entry per span, always positive: a chord that bulges outside the
    silhouette is as wrong as one cutting inside it, and a fill shows both.
    """
    k = len(positions)
    if k < 2 or len(poly) < 3:
        return np.zeros(max(k, 1))
    ctab = cross_table(poly) if ctab is None else ctab
    return _one_span(poly, ctab, positions, np.roll(positions, -1, axis=0))


def bad_area(positions, poly, ctab=None):
    """Total area the drawn outline gets wrong, in square pixels."""
    return float(span_area(positions, poly, ctab).sum())


def _pair_cost(poly, ctab, before, cand, after):
    """What each candidate place for one point costs, across its two spans."""
    left = np.repeat(before[None, :], len(cand), axis=0)
    right = np.repeat(after[None, :], len(cand), axis=0)
    return (_one_span(poly, ctab, left, cand)
            + _one_span(poly, ctab, cand, right))


def refine(positions, poly, rounds=4, probes=6, limit=None,
           table=None, ctab=None):
    """Slide each point along the ring until the area it gets wrong stops falling.

    Coordinate descent, one point at a time, and each point may only move
    *between its two neighbours*. That constraint is not a detail: it is what
    keeps the points in order round the ring, and points in order round a ring
    are what stop the polygon crossing itself. An optimiser free to move a
    point anywhere would happily trade a self-intersection for a lower area.

    The candidates for a point are the ring vertices inside its gap plus a few
    evenly spaced probes. Vertices matter because a corner is where the arc
    departs a chord fastest, so the best place for a point is very often
    exactly on one; the probes cover the long straight runs between.

    `limit` bounds how far a point may travel from where it arrived, as a
    fraction of the ring. It is what makes this a refinement rather than a
    replacement: the correspondence decides which point is which, and this only
    decides where exactly each one sits.

    Run instead of a correspondence, from arc fractions carried over raw, it is
    a disaster -- part_024 goes 8.45px to 99.63px between keyframes. That is
    worth stating precisely, because the obvious reading of it is wrong. The
    optimiser is not wandering off. A ring begins at whatever vertex the walk
    reached first, which changes as the silhouette does, so a fraction carried
    over raw points at a different place on the object; line the two rings up by
    the vertices they share first, as `_agree` already does, and the same
    experiment gives 5.39px. The measured tell was part_079 holding its
    fractions to 0.0000 while erroring 37px -- fractions perfectly kept and the
    shape badly wrong is a moved origin, not drift.

    On top of the placement that ships it is worth about 8.45px -> 4.62px on
    part_024 and nothing either way elsewhere, with typical error unchanged. A
    leash of one slot spacing, two, and none at all all give the same answer,
    which says it does not wander from a good starting point: it moves only
    where there is area genuinely left to reclaim.

    Crossing each gap in small steps, re-solving at every one so the optimum is
    followed along a path rather than re-found, does not help. Tried properly,
    with the rings lined up, on part_079:

        steps per gap    2      4     10     20     40     80
        worst mid     8.28   7.85   3.97  29.76  29.69   5.82
        typical       2.02   2.02   2.02   2.33   2.18   2.12

    At eighty steps the shape is re-solved every 1/1920 of a second and it is
    still swinging between 5.82 and 29.69.

    The reason is not that this placement cannot move continuously. It was
    supposed to be -- a fixed candidate set that does not get finer as the shape
    step shrinks -- and that was wrong, measured and corrected. Nudge the shape
    by ever less and re-solve *to convergence*, and the answer moves
    proportionally: 1.9e-4 of the ring for a nudge of 1e-2, down to 1.1e-7 for
    1e-5. It is continuous. The plateau that looked structural was a descent
    stopped at four rounds, which keeps making progress whatever the shape does,
    and four rounds was what sat inside every substep of the test above.

    Given a solver that is exactly continuous (`refine_exact`) and run to
    convergence, following the path is *worse*, not better:

        part_079                      worst    typical
        what ships                   11.53px    0.78px
        area, four rounds            22.47px    2.71px
        area, converged              30.20px    3.85px
        area, converged, 4 substeps  26.71px    5.68px
        area, converged, 10 substeps 28.05px    5.32px

    Optimising harder makes it worse, monotonically, and that is the real
    finding.

    Since the leash is the method, its length was swept, per object, against the
    same between-keyframes measure. There is no single right answer: at a
    quarter of a slot spacing, across thirty objects, five improve (part_024
    8.45px -> 4.62px, part_070 1.47 -> 1.19, part_048 3.89 -> 3.48, part_004
    3.96 -> 3.42, part_032 0.91 -> 0.87), four get worse (part_052 0.99 -> 1.29,
    part_067 1.23 -> 1.39, part_035 1.64 -> 1.74, part_116 1.10 -> 1.14) and
    twenty-one do not move at all. So this belongs behind a per-object decision
    made on measurement -- which the gap test in bake.py already knows how to
    make -- and not behind a constant.

    The descent has to converge to be worth having: part_024 gets nothing at
    four rounds, 5.42px at eight and 4.62px at thirty, and part_048 at four
    rounds is worse than not refining at all. Per-frame optimality is not what an interpolated format wants: the
    better each frame's placement is *for that frame*, the more it differs from
    its neighbours' placements, and the interpolation between them is what the
    viewer sees. Stopping the descent early was accidentally a regulariser.
    Which is why the placement that ships holds points to mesh vertices and
    resists moving them, and why the refinement above is leashed.

    The typical error is the honest column: 2.0 to 2.3px across a fortyfold
    range of step size, against 0.78px for the placement that ships. Anyone
    tempted by the 3.97 -- including me, who nearly reported it -- should read
    the row either side of it.
    """
    k, m = len(positions), len(poly)
    if k < 3 or m < 3:
        return positions
    cum, total = arc_table(poly) if table is None else table
    if total <= 0.0:
        return positions
    ctab = cross_table(poly) if ctab is None else ctab

    pos = np.array(positions, dtype=np.float64, copy=True)
    s = arc_of(pos, poly, (cum, total))
    vert_s = cum[:m] / total
    step = np.arange(1, probes + 1) / (probes + 1.0)

    for _ in range(rounds):
        moved = False
        for i in range(k):
            lo, hi = s[(i - 1) % k], s[(i + 1) % k]
            width = (hi - lo) % 1.0
            if width <= 1e-9:
                continue
            inside = (vert_s - lo) % 1.0
            picks = inside[(inside > 1e-9) & (inside < width - 1e-9)]
            off = np.concatenate([step * width, picks])
            if limit is not None:
                # Keep to a leash around where this point arrived, measured
                # the short way round the ring.
                here = (s[i] - lo) % 1.0
                gap = np.abs(off - here)
                off = off[np.minimum(gap, 1.0 - gap) <= limit]
                if not len(off):
                    continue
            want = (lo + off) % 1.0
            cand = at_arc(want, poly, (cum, total))
            before, after = pos[(i - 1) % k], pos[(i + 1) % k]
            cost = _pair_cost(poly, ctab, before, cand, after)
            j = int(np.argmin(cost))
            now = float(_pair_cost(poly, ctab, before, pos[i:i + 1], after)[0])
            if cost[j] < now - 1e-9:
                pos[i] = cand[j]
                s[i] = want[j]
                moved = True
        if not moved:
            break
    return pos


def _arc_span(ct, i, j, m):
    """Cross-product sum from ring index i forward to j. Scalar, on purpose."""
    return ct[j] - ct[i] if j >= i else ct[m] - ct[i] + ct[j]


def _span_affine(px, py, ct, m, fx, fy, fixed_seg, seg, forward):
    """One span's signed doubled area as `(const, slope)` in t along `seg`.

    The moving end is `P(t) = a + t (b - a)` on `seg`; the other end is the
    point `(fx, fy)` sitting on `fixed_seg`. Only two cross products in the
    shoelace touch `P` and both are linear in it, so the whole signed area is
    affine in t. That is what the solving rests on: an affine area means
    `|area|` is convex in t, and a sum of two of them is minimised exactly, at
    an endpoint or a zero crossing, with no search.

    Everything here is one number at a time. That is deliberate -- this ran
    through numpy once and spent three quarters of its time in dispatch,
    because a numpy call on a single value costs more than the arithmetic it
    performs. Plain floats made the sweep six times faster without changing a
    digit of the result.

    `forward` says which way the span runs -- from the fixed end to `P`, or
    from `P` to the fixed end -- because the shoelace is signed and the two are
    not the same span.
    """
    ax, ay = px[seg], py[seg]
    nxt = seg + 1 if seg + 1 < m else 0
    dx, dy = px[nxt] - ax, py[nxt] - ay

    if forward:
        if seg == fixed_seg:
            return 0.0, 0.0                  # same segment: no arc cut off
        i = fixed_seg + 1 if fixed_seg + 1 < m else 0
        gx, gy = px[i], py[i]
        const = (fx * gy - fy * gx) + _arc_span(ct, i, seg, m) + (ax * fy - ay * fx)
        slope = (ax * dy - ay * dx) + (dx * fy - dy * fx)
        return const, slope

    if fixed_seg == seg:
        return 0.0, 0.0
    gx, gy = px[nxt], py[nxt]
    sx, sy = px[fixed_seg], py[fixed_seg]
    const = ((ax * gy - ay * gx) + _arc_span(ct, nxt, fixed_seg, m)
             + (sx * fy - sy * fx) + (fx * ay - fy * ax))
    slope = (dx * gy - dy * gx) + (fx * dy - fy * dx)
    return const, slope


def _best_on_segment(c1, s1, c2, s2, lo, hi):
    """Minimise |c1+s1 t| + |c2+s2 t| over [lo, hi]. Convex, so exactly."""
    best_t, best_v = lo, abs(c1 + s1 * lo) + abs(c2 + s2 * lo)
    v = abs(c1 + s1 * hi) + abs(c2 + s2 * hi)
    if v < best_v:
        best_t, best_v = hi, v
    for c, sl in ((c1, s1), (c2, s2)):
        if sl > 1e-12 or sl < -1e-12:
            t = -c / sl
            if lo < t < hi:
                v = abs(c1 + s1 * t) + abs(c2 + s2 * t)
                if v < best_v:
                    best_t, best_v = t, v
    return best_t, 0.5 * best_v


def _seg_at(cf, m, frac):
    """Arc fraction -> `(segment, fraction along it)`. `at_arc` for one value."""
    frac %= 1.0
    lo, hi = 0, m - 1
    while lo < hi:                            # the last vertex at or below frac
        mid = (lo + hi + 1) // 2
        if cf[mid] <= frac:
            lo = mid
        else:
            hi = mid - 1
    a, b = cf[lo], cf[lo + 1]
    if b <= a:
        return lo, 0.0
    t = (frac - a) / (b - a)
    return lo, 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)


# How far a point may be slid from where the correspondence put it, in slot
# spacings, and how many sweeps to allow. The leash is not delicate -- anything
# from a tenth of a slot to a half gives the same answer -- but a full slot is
# too much and takes part_004 from 3.42px to 6.03px. The rounds are not
# optional: part_024 gains nothing at four, 5.42px at eight, 4.62px at thirty.
REFINE_LEASH = 0.25
REFINE_ROUNDS = 30


def refine_exact(positions, poly, rounds=4, limit=None, table=None, ctab=None):
    """Like `refine`, but each point goes exactly where it belongs.

    `refine` picks the best of a fixed set of candidate places. That set does
    not get finer as the shape changes less, so the placement moves in hops of
    a size that has nothing to do with how far the shape moved -- which is why
    following it through small steps never settled: there was no continuous
    path to follow.

    Here there are no candidates. Confined to one segment a point is
    `a + t (b - a)`, both neighbouring spans have signed areas affine in t, and
    the cost is |affine| + |affine| -- convex, so its exact minimum is an
    endpoint or a zero crossing. Every segment the point may reach is solved in
    closed form and the best taken. The answer moves with the shape instead of
    hopping between fixed places.
    """
    k, m = len(positions), len(poly)
    if k < 3 or m < 3:
        return positions
    cum, total = arc_table(poly) if table is None else table
    if total <= 0.0:
        return positions
    ctab = cross_table(poly) if ctab is None else ctab

    # Out of numpy for the sweep itself. Every quantity below is one number,
    # and a numpy call on one number costs more than the arithmetic it does.
    px = poly[:, 0].tolist()
    py = poly[:, 1].tolist()
    ct = ctab.tolist()
    cf = (np.asarray(cum, dtype=np.float64) / total).tolist()

    seg = [int(v) % m for v in positions[:, 0]]
    frac = [float(v) for v in positions[:, 1]]
    s = [(cf[seg[i]] + frac[i] * (cf[seg[i] + 1] - cf[seg[i]])) % 1.0
         for i in range(k)]
    eps = 1e-9

    for _ in range(rounds):
        moved = False
        for i in range(k):
            li = i - 1 if i else k - 1
            ri = i + 1 if i + 1 < k else 0
            lo = s[li]
            width = (s[ri] - lo) % 1.0
            if width <= 2.0 * eps:
                continue
            off_lo, off_hi = eps, width - eps
            if limit is not None:
                here = (s[i] - lo) % 1.0
                if here - limit > off_lo:
                    off_lo = here - limit
                if here + limit < off_hi:
                    off_hi = here + limit
                if off_hi <= off_lo:
                    continue

            sa, ta = _seg_at(cf, m, lo + off_lo)
            sb, tb = _seg_at(cf, m, lo + off_hi)
            nseg = (sb - sa) % m + 1
            if nseg == 1 and tb < ta:
                continue                       # degenerate wrap: leave it be

            lseg, lt = seg[li], frac[li]
            ln = lseg + 1 if lseg + 1 < m else 0
            lx = px[lseg] + lt * (px[ln] - px[lseg])
            ly = py[lseg] + lt * (py[ln] - py[lseg])
            rseg, rt = seg[ri], frac[ri]
            rn = rseg + 1 if rseg + 1 < m else 0
            rx = px[rseg] + rt * (px[rn] - px[rseg])
            ry = py[rseg] + rt * (py[rn] - py[rseg])

            best_cost = best_seg = best_t = None
            for j in range(nseg):
                sg = (sa + j) % m
                t_lo = ta if j == 0 else 0.0
                t_hi = tb if j == nseg - 1 else 1.0
                if t_hi <= t_lo:
                    continue
                c1, k1 = _span_affine(px, py, ct, m, lx, ly, lseg, sg, True)
                c2, k2 = _span_affine(px, py, ct, m, rx, ry, rseg, sg, False)
                t, cost = _best_on_segment(c1, k1, c2, k2, t_lo, t_hi)
                if best_cost is None or cost < best_cost:
                    best_cost, best_seg, best_t = cost, sg, t
            if best_cost is None:
                continue

            c1, k1 = _span_affine(px, py, ct, m, lx, ly, lseg, seg[i], True)
            c2, k2 = _span_affine(px, py, ct, m, rx, ry, rseg, seg[i], False)
            cur = 0.5 * (abs(c1 + k1 * frac[i]) + abs(c2 + k2 * frac[i]))
            if best_cost < cur - 1e-9:
                seg[i], frac[i] = best_seg, best_t
                s[i] = (cf[best_seg]
                        + best_t * (cf[best_seg + 1] - cf[best_seg])) % 1.0
                moved = True
        if not moved:
            break

    out = np.empty((k, 2), dtype=np.float64)
    out[:, 0] = seg
    out[:, 1] = frac
    return out
