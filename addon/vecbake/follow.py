# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""Make flat elements follow the curves their neighbours draw.

A curve through an inscribed polygon's vertices lies *outside* it. That is the
whole point -- the polygon is what the circle was cut down to, so the curve is
the more truthful shape. But an edge belongs to more than one element, and only
one of them may be in a position to curve it.

A cylinder is the clean example. The cap curves its rim, because going round
the rim is a run of shallow turns. The side quads meeting that same rim cannot:
their own corners turn 90 degrees, and a curve through those would bow off the
face (see `curves.py`). So the two draw one edge two different ways and the
shape splits along it -- the cap overhangs the sides at the top, and at the
bottom the rim sweeps out past the fills with background showing between.

No per-element rule can fix this, and that is not an oversight. Tangents come
from each element's own points, which is exactly what keeps the format free of
stored control points; the side quad does not contain the rim's next vertex, so
nothing inside it says which way its top edge should bow.

What it can do is **follow**. For every edge one element curves and another
draws straight, this splits the straight one along that curve, adding as few
points as it takes to stay within `dissolve_px` of it in every frame. It is the
dissolve pass's bargain run backwards: that one drops a vertex when keeping it
never moves the line by more than a pixel budget, this one adds a vertex when
not having it would.

Nothing downstream needs to know. A follower is just an element with more
points, so the player, both SVG paths, Lottie and anything else that can open
an SVG agree on the shape without implementing anything.
"""

from __future__ import annotations

import math

import numpy as np

# A cylinder as coarse as ten sides needs three. Past this the curve is being
# chased below the width of the seam stroke that already covers the join, and
# every extra point is paid for in every frame.
MAX_SPLITS = 8


def _pairs(npts, closed):
    """Consecutive index pairs, wrapping when the element is a ring."""
    last = npts if closed else npts - 1
    for i in range(last):
        yield i, (i + 1) % npts


def _tangent(pts, npts, mask, closed, i):
    """The Catmull-Rom tangent `curves.py` defines; zero at a corner."""
    if mask is None or not mask[i]:
        return 0.0, 0.0
    if closed:
        a, b = (i - 1) % npts, (i + 1) % npts
    elif 0 < i < npts - 1:
        a, b = i - 1, i + 1
    else:
        return 0.0, 0.0
    return ((pts[b * 2] - pts[a * 2]) * 0.5,
            (pts[b * 2 + 1] - pts[a * 2 + 1]) * 0.5)


def _cubic(pts, npts, mask, closed, ia, ib):
    """The curve the owner actually draws along one of its edges."""
    x0, y0 = pts[ia * 2], pts[ia * 2 + 1]
    x3, y3 = pts[ib * 2], pts[ib * 2 + 1]
    tax, tay = _tangent(pts, npts, mask, closed, ia)
    tbx, tby = _tangent(pts, npts, mask, closed, ib)
    return (x0, y0, x0 + tax / 3.0, y0 + tay / 3.0,
            x3 - tbx / 3.0, y3 - tby / 3.0, x3, y3)


def _at(c, t):
    x0, y0, x1, y1, x2, y2, x3, y3 = c
    u = 1.0 - t
    a, b, d, e = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
    return (a * x0 + b * x1 + d * x2 + e * x3,
            a * y0 + b * y1 + d * y2 + e * y3)


# where to look for the widest point of the bow; the shape is a cubic in t with
# roots at both ends, so a coarse sweep finds its peak to well under a percent
_PROBE = tuple(i / 12.0 for i in range(1, 12))


def _bow(c):
    """How far the curve leaves its own chord, in pixels.

    Exactly, not by the usual flatness bound. Measured perpendicular to the
    chord the offset is `3t(1-t)[(1-t)d1 + t*d2]`, for control-point distances
    d1 and d2 -- so the answer is a handful of multiplications rather than
    point evaluations, and it does not overstate. The bound would: with one
    tangent zero it reads 70% high, and the split count is its square root, so
    that is points bought for nothing in every frame.
    """
    x0, y0, x1, y1, x2, y2, x3, y3 = c
    dx, dy = x3 - x0, y3 - y0
    span = math.hypot(dx, dy)
    if span < 1e-9:
        return max(math.hypot(x1 - x0, y1 - y0), math.hypot(x2 - x0, y2 - y0))
    d1 = ((x1 - x0) * dy - (y1 - y0) * dx) / span
    d2 = ((x2 - x0) * dy - (y2 - y0) * dx) / span
    worst = 0.0
    for t in _PROBE:
        u = 1.0 - t
        offset = abs(3.0 * t * u * (u * d1 + t * d2))
        if offset > worst:
            worst = offset
    return worst


def _sub(c, a, b):
    """The stretch of a cubic between two parameters, as a cubic of its own."""
    x0, y0 = _at(c, a)
    x3, y3 = _at(c, b)
    dax, day = _deriv(c, a)
    dbx, dby = _deriv(c, b)
    h = (b - a) / 3.0
    return (x0, y0, x0 + dax * h, y0 + day * h, x3 - dbx * h, y3 - dby * h, x3, y3)


def _deriv(c, t):
    x0, y0, x1, y1, x2, y2, x3, y3 = c
    u = 1.0 - t
    a, b, d = 3 * u * u, 6 * u * t, 3 * t * t
    return (a * (x1 - x0) + b * (x2 - x1) + d * (x3 - x2),
            a * (y1 - y0) + b * (y2 - y1) + d * (y3 - y2))


def _residual(c, pieces):
    """What a `pieces`-segment polyline through the curve would still miss by.

    Each piece is the curve restricted to its own parameter range, so its bow
    off its own chord *is* the error of that polyline segment -- no sampling,
    and no assumption that cutting a cubic in n reduces the error by n squared.
    It nearly does, which is why that makes a good first guess, but "nearly" is
    the difference between 0.35 and 0.41 pixels.
    """
    worst = 0.0
    for k in range(pieces):
        worst = max(worst, _bow(_sub(c, k / pieces, (k + 1.0) / pieces)))
    return worst


def _smooth_ends(el, i, j):
    if el.smooth is None:
        return 0
    return int(bool(el.smooth[i])) + int(bool(el.smooth[j]))


def _index_edges(elements, element_verts):
    """Which elements draw each mesh edge, and where in their own point list.

    Keyed by object as well as vertex pair: two objects number their vertices
    from zero, and an edge is only shared if the mesh is.
    """
    edges = {}
    for el in elements:
        path = element_verts.get(el.key)
        if path is None or len(path) < 2 or len(path) != el.npts:
            continue      # no vertex path, or dissolved out of step with one
        obj = el.key.split("/")[0]
        closed = el.is_closed()
        for i, j in _pairs(el.npts, closed):
            a, b = int(path[i]), int(path[j])
            if a == b:
                continue
            key = (obj, a, b) if a < b else (obj, b, a)
            edges.setdefault(key, []).append((el, i, j))
    return edges


def _splits(owner, oi, oj, slots, nframes, tol):
    """How many points it takes to track this edge's curve within `tol`.

    Measured over every frame, and the worst frame decides -- a point count
    that changed with the camera would change what the element *is*.
    """
    need = 0
    closed = owner.is_closed()
    geo = slots[owner.key]["geo"]
    for fi in range(nframes):
        pts = geo[fi]
        if pts is None:
            continue
        curve = _cubic(pts, owner.npts, owner.smooth, closed, oi, oj)
        bow = _bow(curve)
        if bow <= tol:
            continue
        # the n-squared rule gets within a piece; the check settles it
        pieces = max(need + 1, int(math.ceil(math.sqrt(bow / tol))))
        while pieces <= MAX_SPLITS and _residual(curve, pieces) > tol:
            pieces += 1
        need = max(need, pieces - 1)
        if need >= MAX_SPLITS:
            return MAX_SPLITS
    return need


def owners(elements, element_verts, edges=None):
    """Which element decides each shared edge, and who was demoted to follow.

    Whoever curves the edge hardest, ties broken by key so the choice is the
    same in every frame and every re-bake.

    The demotion is the part that matters. One curve per rim, not one per edge:
    an element that loses *anywhere* is made to follow *everywhere*. Deciding
    edge by edge looks equivalent and is not. A chain runs out of neighbours at
    its last vertex, so its tangent there is forced to zero and its final
    segment straightens -- while the faces beside it, which measured that same
    segment against a fuller curve, keep tracking the fuller one. The two part
    company by a few pixels exactly where the eye is on them: at the
    silhouette, where the fill spills past the line on one side of the cylinder
    and falls short of it on the other.
    """
    if edges is None:
        edges = _index_edges(elements, element_verts)
    demoted: set[str] = set()
    found: dict = {}
    for _ in range(len(elements) + 1):
        found = {}
        for key, members in edges.items():
            if len(members) < 2:
                continue                  # nothing to disagree with
            live = [m for m in members if m[0].key not in demoted]
            if not live:
                continue
            best = min(live, key=lambda m: (-_smooth_ends(*m), m[0].key))
            if _smooth_ends(*best):
                found[key] = best
        losing = set()
        for key, win in found.items():
            top = _smooth_ends(*win)
            for m in edges[key]:
                if m[0].key in demoted or m[0] is win[0]:
                    continue
                if 0 < _smooth_ends(*m) < top:
                    losing.add(m[0].key)
        if not losing - demoted:
            break
        demoted |= losing
    return found, demoted


def _plan(elements, slots, element_verts, nframes, tol):
    """For each element, which of its segments to split and along whose curve."""
    edges = _index_edges(elements, element_verts)
    owned, demoted = owners(elements, element_verts, edges)
    # Owner and follower are exclusive roles. An element that decides an edge
    # has to keep its own point list: its tangents come from its neighbouring
    # points, so inserting into it would move the very curve its followers were
    # sampled from, out from under them.
    deciders = {m[0].key for m in owned.values()}

    work = {}
    for key, (owner, oi, oj) in owned.items():
        members = edges[key]

        # Everything that is not the decider follows it: elements drawing the
        # edge dead straight, and elements demoted for losing elsewhere on the
        # same rim. Nothing is left holding a second opinion about one edge.
        followers = [m for m in members if m[0].key not in deciders
                     and (_smooth_ends(*m) == 0 or m[0].key in demoted)]
        if not followers:
            continue

        count = _splits(owner, oi, oj, slots, nframes, tol)
        if count <= 0:
            continue

        owner_a = int(element_verts[owner.key][oi])
        for el, i, j in followers:
            path = element_verts[el.key]
            # the follower may walk the edge the other way round
            flip = int(path[i]) != owner_a
            work.setdefault(el.key, {})[i] = (owner.key, oi, oj, flip, count)
    return work, demoted


def _layout(npts, inserts):
    """The new point order: every original, each followed by its splits."""
    out = []
    for i in range(npts):
        out.append(i)
        spec = inserts.get(i)
        if spec is None:
            continue
        owner_key, oi, oj, flip, count = spec
        for s in range(1, count + 1):
            t = s / (count + 1.0)
            out.append((owner_key, oi, oj, 1.0 - t if flip else t, t, i))
    return out


def apply(elements, slots, element_verts, settings, nframes):
    """Rewrite followers in place. Returns a small report."""
    tol = max(1e-3, float(getattr(settings, "dissolve_px", 0.35)))
    work, demoted = _plan(elements, slots, element_verts, nframes, tol)
    by_key = {el.key: el for el in elements}
    # A demoted element gave up its own opinion of the curve, so it gives up
    # its mask with it: the split points it just received already trace the
    # decider's curve, and bending through them would lay a second curve over
    # the first.
    for key in demoted:
        if key in by_key:
            by_key[key].smooth = None
    if not work:
        return {"followed": 0, "points": 0, "splits": 0}
    followed = 0
    gained = 0
    for key, inserts in work.items():
        el = by_key[key]
        slot = slots[key]
        npts = el.npts
        layout = _layout(npts, inserts)

        for fi in range(nframes):
            pts = slot["geo"][fi]
            if pts is None:
                continue
            cache = {}
            out = []
            for item in layout:
                if not isinstance(item, tuple):
                    out.append(pts[item * 2])
                    out.append(pts[item * 2 + 1])
                    continue
                owner_key, oi, oj, t, t_self, seg = item
                # keyed by the *edge*, not just the owner: an element that
                # follows one owner along several of its edges would otherwise
                # take the first edge's curve for all of them, and every split
                # point after the first would land somewhere else entirely
                ck = (owner_key, oi, oj)
                if ck not in cache:
                    owner = by_key[owner_key]
                    og = slots[owner_key]["geo"][fi]
                    cache[ck] = None if og is None else _cubic(
                        og, owner.npts, owner.smooth, owner.is_closed(), oi, oj)
                curve = cache[ck]
                if curve is None:
                    # owner has no geometry this frame: sit on our own chord,
                    # which is where the point would have been anyway
                    nxt = (seg + 1) % npts
                    x = pts[seg * 2] + (pts[nxt * 2] - pts[seg * 2]) * t_self
                    y = pts[seg * 2 + 1] + (pts[nxt * 2 + 1] - pts[seg * 2 + 1]) * t_self
                else:
                    x, y = _at(curve, t)
                out.append(x)
                out.append(y)
            slot["geo"][fi] = out

        mask = el.smooth
        if mask is not None:
            # a split point is a corner: it already sits on the curve, and
            # bending through it would be a second curve laid over the first
            flags = [bool(mask[i]) if not isinstance(i, tuple) else False for i in layout]
            el.smooth = np.array(flags, dtype=bool) if any(flags) else None

        # Where each point came from, for anything that has to reason about the
        # result later: an original vertex index, or the segment and parameter
        # an inserted one sits at. The silhouette pass needs both.
        slot["origin"] = [i if not isinstance(i, tuple) else (i[5], i[4])
                          for i in layout]
        gained += len(layout) - npts
        el.npts = len(layout)
        slot["npts"] = el.npts
        followed += 1

    return {"followed": followed, "points": gained,
            "splits": sum(len(v) for v in work.values())}
