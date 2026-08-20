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
    for k in range(len(pts) - 1):
        ax, ay = pts[k]
        bx, by = pts[k + 1]
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
            # Weight these by the actual gap. They are the *most* certain
            # constraints in the graph -- the depth ranges do not even touch --
            # so leaving them at the default strength of zero made the cycle
            # breaker treat them as worthless and discard them first.
            if dmax[j] < dmin[i]:
                add_edge(i, j, float(dmin[i] - dmax[j]))
                continue
            if dmax[i] < dmin[j]:
                add_edge(j, i, float(dmin[j] - dmax[i]))
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
                # Weakest genuine class: strictly below tol, so below every
                # constraint that sampling actually decided. If a cycle has to
                # give something up, these go first -- by construction depth had
                # no opinion about them anyway.
                if kinds[i] == FILL_KIND and kinds[j] != FILL_KIND:
                    add_edge(i, j, tol)
                elif kinds[j] == FILL_KIND and kinds[i] != FILL_KIND:
                    add_edge(j, i, tol)
                continue
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
    for j, it in enumerate(items):
        for face_key in it.get("on_faces", ()):
            i = index.get(face_key)
            if i is None or i == j:
                missing_face += i is None
                continue
            asserted.add((i, j))
            if j in after[i]:
                # Sampling already agrees. Leave it alone rather than raising it
                # to span: a measured edge at its measured weight stays available
                # to the cycle breaker, and there is nothing to correct here.
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
            # Bound that tenth by *count*. Taking the tenth-percentile weight as
            # a threshold and dropping everything at or below it is not the same
            # thing: weights are heavily tied, so one break discarded every edge
            # sharing the cutoff value -- 15736 of 24456 constraints, 64% of all
            # the evidence in the scene, in a single pass. The sort already puts
            # the least-supported constraints first; just take that many.
            live.sort(key=lambda item: item[0])
            # Spend assertions first, cheapest-visible-error first.
            #
            # The reasoning used to be that a cycle through an assertion always
            # runs through a stroke->fill edge that can go instead. It does --
            # but that edge is sometimes the one holding a line behind the thing
            # that hides it, and giving it up is far from free. A face raked
            # steeply away from the camera spans a lot of depth, so it measures
            # as being in front of something its own far edge is behind; the
            # assertion then drags the edge forward with it and the line is
            # painted across a surface that covers it. The cycle is real and
            # something has to give, so compare what each choice costs on
            # screen: violating the assertion lets a face eat the inner half of
            # its own edge -- measured at 47-50% of the edge's pixels, a line
            # that reads thinner -- while violating the occlusion constraint
            # draws a whole line where there should be none. The thin line is
            # the cheaper wrong answer.
            #
            # Reported as a stray line across the golden record disk on the
            # Voyager probe: bus/f8 spans 1.71 units of depth, measures 0.778 in
            # front of the disk over 274 samples, and carried its far edge
            # bus/e14 -- which is 0.046 *behind* the disk -- along with it.
            #
            # It has to be an explicit ordering, not just dropping the old
            # filter. Assertions weigh `span`, so weight alone sorts them last
            # and the slice below only ever reaches one in a component big
            # enough for a tenth of it to run past every measured edge. That
            # fixed the probe by luck of component size and left the minimal
            # three-element cycle -- face, disk, and the face's own far edge --
            # broken exactly as before.
            pool = sorted(live, key=lambda e: ((e[1], e[2]) not in asserted, e[0]))
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
