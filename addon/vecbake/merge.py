# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""One object, one filled outline.

A shade-flat cylinder emits one fill per face, and every one of them animates,
sorts and draws separately. For a part that only ever needs to read as a
silhouette -- an insect body, a limb, anything the viewer sees as a solid --
that is a lot of machinery for a shape with no interior detail. The butterfly's
body is 82 faces and 328 points; its outline is 30 points.

So: emit the outline instead.

What lives here is the topological half of that -- turning a set of silhouette
edges into one ring of vertices, and saying so when they do not make one. What
the ring's points become, and how a fixed number of slots is spread around a
ring whose length changes every frame, is outline.py's problem.

Slots used to be bound to mesh vertices, one per vertex, ordered from the first
ring seen. That cannot survive a full turn: a quarter rotation later the
silhouette is a different set of vertices in a different cyclic order, so the
committed order stops holding and the outline crosses itself. On the
seven-primitive lab scene it rejected five of seven objects on ordering alone,
each of which had a clean ring in every frame. A silhouette is not a
topological object, and slots must not be bound to topology. See outline.py.

Loops, junctions and lobes
--------------------------
The silhouette rarely arrives as one tidy ring. On the Voyager probe 81 of 304
objects never merged at all, every one of them because this file said no in at
least one frame -- and the merge rule needs every frame, so one bad frame out of
sixty costs the object its whole clip.

Three shapes came back instead of a ring, and the useful thing is that they are
one shape seen three ways. Walk the edges keeping the drawn region on the same
side, taking the sharpest turn at any vertex carrying more than two of them, and
what comes out is a set of closed loops: the true boundary of what is drawn.
A "branch" is that boundary pinching at a vertex, a "split" is it arriving in
more than one piece, a "dangling end" is a chain that never closes.

Loops that meet at a vertex are still one region's boundary, and they can be
spliced into a single closed walk through the shared vertex. That is not a
trick: the player fills with the nonzero winding rule, each loop is traced with
the region on the same side, so an outer boundary comes back positive and a
hole negative and splicing them *without* normalising the direction fills the
hole as a hole. 53 frames of the probe's clip need exactly that, so the
orientation is preserved rather than tidied.

Loops that never touch -- a tube seen down its bore, an outer ring and an inner
one with nothing between them -- are joined by a segment run out and back. It
encloses no area, so the fill is exactly right: outer boundary one way, bore
the other, and the nonzero rule leaves the hole open. Measured on a 100/60
annulus, the bridged path encloses 20,102 square pixels where the true annulus
is 20,106 and a hole-less fill would give 31,416.

The bridge is drawn, though, because a merged outline is stroked, and that was
the objection that kept these objects drawing face by face. It is answerable:
the bridge runs from the outer ring to the inner one, which crosses the
*material*, not the hole. So the same shape filled again on top of it, without
a stroke, covers the line exactly -- and that second fill is where bake.py
hides the seam.

An earlier attempt resolved junctions and stopped there, and bought no merges
at all: it fixed branches, and the objects that branch in one frame split in
another. The three failures had to be answered together or none of them counted.
"""


from __future__ import annotations

import collections

import math

import numpy as np


# How small a separate piece has to be before it is dropped rather than bridged
# to. A speck this size is not worth a seam across the shape to reach; anything
# bigger is real geometry and gets bridged.
SPECK = 0.01


def silhouette_edges(plan, drawn):
    """Edge indices with exactly one drawn face on them.

    Covers both cases that matter with one test: an open mesh's border, where
    the edge has a single face full stop, and a solid's silhouette, where it
    divides a front face from a back one.
    """
    a, b = plan.edge_face_a, plan.edge_face_b
    count = np.zeros(plan.nedges, dtype=np.int32)
    for side in (a, b):
        has = side >= 0
        if has.any():
            count[has] += drawn[side[has]].astype(np.int32)
    return np.nonzero(count == 1)[0]


def _area(pts):
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _loops(ev, xy):
    """Every closed loop in a set of silhouette edges, as vertex indices.

    A vertex carrying more than two silhouette edges is where the boundary
    touches itself. Arriving along one edge, the walk leaves along the
    neighbour that turns furthest one way -- the standard trace of a single
    face of a plane subdivision, and exact here in a way it never was for split
    segments: the graph is mesh vertices, so identity is an integer and only
    the angles come from the projection. Nothing is welded, no tolerance is
    involved.

    Chains that never close are dropped. They are a dangling end, which bounds
    no area and cannot be part of a fill.
    """
    # Plain Python ints throughout. This walk runs for every object in every
    # frame, four times over between the measuring pass, the writing pass and
    # the two the gap test asks for, and stepping a numpy row at a time costs
    # more than the walk itself.
    pairs = ev.tolist()
    adj = collections.defaultdict(list)
    for i, (a, b) in enumerate(pairs):
        adj[a].append((b, i))
        adj[b].append((a, i))

    used, loops = set(), []
    for seed in range(len(pairs)):
        if seed in used:
            continue
        start, nxt = pairs[seed]
        taken = [seed]
        used.add(seed)
        loop, prev, cur, came, closed = [start], start, nxt, seed, True
        while cur != start:
            loop.append(cur)
            nbrs = adj[cur]
            if len(nbrs) == 2:
                # The ordinary case, and worth keeping cheap: two edges meet,
                # so leave by the one that is not the one just arrived on.
                n, e = nbrs[0] if nbrs[0][1] != came else nbrs[1]
                if e in used:
                    closed = False
                    break
            else:
                free = [ne for ne in nbrs if ne[1] not in used]
                if not free:
                    closed = False
                    break
                if len(free) == 1:
                    n, e = free[0]
                else:
                    vin = xy[cur] - xy[prev]
                    n, e = min(free, key=lambda ne: np.arctan2(
                        float(vin[0] * (xy[ne[0]] - xy[cur])[1]
                              - vin[1] * (xy[ne[0]] - xy[cur])[0]),
                        float(vin @ (xy[ne[0]] - xy[cur]))))
            used.add(e)
            taken.append(e)
            prev, cur, came = cur, n, e
            if len(loop) > len(pairs) + 2:
                closed = False
                break
        if closed and len(loop) >= 3:
            loops.append(loop)
        else:
            # A walk that dead-ends has to give its edges back. Keeping them
            # starves every later walk of the edges it needs, and one dangling
            # chain hanging off a square then loses the square as well: the
            # first walk turns down the chain, strands two of the square's
            # edges, and nothing can close afterwards.
            used.difference_update(taken)
    return loops


def _touching(loops):
    """Group loops that share a vertex, largest group by area first."""
    parent = list(range(len(loops)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    first = {}
    for i, loop in enumerate(loops):
        for v in loop:
            if v in first:
                a, b = find(i), find(first[v])
                if a != b:
                    parent[a] = b
            else:
                first[v] = i
    out = collections.defaultdict(list)
    for i in range(len(loops)):
        out[find(i)].append(i)
    return list(out.values())


def _splice(loops):
    """One closed walk through loops that share vertices.

    The shared vertex is visited twice, which is what makes the walk closed:
    down into the second lobe, round it, and back out to carry on round the
    first. Each loop keeps the direction it was traced in, so a hole stays
    wound against its outer boundary and fills as a hole.
    """
    ring = list(loops[0])
    rest = list(loops[1:])
    while rest:
        seat = {v: i for i, v in enumerate(ring)}
        for k, loop in enumerate(rest):
            hit = next((j for j, v in enumerate(loop) if v in seat), None)
            if hit is None:
                continue
            v = loop[hit]
            at = seat[v]
            ring[at + 1:at + 1] = loop[hit + 1:] + loop[:hit + 1]
            rest.pop(k)
            break
        else:
            break                        # nothing left that touches: give up
    return ring


def _bridge(ring, piece, xy):
    """Join a piece that touches nothing to the ring, by a seam out and back.

    The seam is doubled -- along and straight back -- so it encloses no area at
    all and the fill is decided entirely by the two boundaries it joins. Each
    keeps the direction it was traced in, so a bore stays wound against the
    outside of the tube and the nonzero rule leaves the hole open.

    It is placed at the closest pair of vertices between the two, which keeps
    it short, keeps it inside the material it crosses, and makes it a stable
    choice from frame to frame: the nearest approach of a bore to its own
    outside does not jump about as the object turns.
    """
    a, b = np.asarray(ring), np.asarray(piece)
    gap = np.linalg.norm(xy[a][:, None, :] - xy[b][None, :, :], axis=2)
    i, j = np.unravel_index(int(np.argmin(gap)), gap.shape)
    rot = list(piece[j:]) + list(piece[:j])
    out = list(ring)
    out[i + 1:i + 1] = rot + [rot[0], ring[i]]
    return out


# How many separate pieces one merged fill may carry. A silhouette that comes
# apart further than this is not a shape a single element should be describing.
MAX_PIECES = 8


def _face_boxes(plan, xy):
    """Screen bounding box of every face, to reject most of them per probe."""
    lv = plan.loop_verts
    pts = xy[lv]
    st = plan.loop_start
    return (np.minimum.reduceat(pts[:, 0], st), np.maximum.reduceat(pts[:, 0], st),
            np.minimum.reduceat(pts[:, 1], st), np.maximum.reduceat(pts[:, 1], st))


def _covers(plan, xy, drawn, boxes, pt):
    """Is this screen point under any drawn face of the object?

    Not "is it inside this loop" -- inside is not defined for a spliced ring,
    and it is the wrong question anyway. What settles a subpath is whether the
    object is *there*, which other parts of the same object can decide from far
    away: the far side of a torus fills the hole its near rim describes.
    """
    x, y = float(pt[0]), float(pt[1])
    mnx, mxx, mny, mxy = boxes
    cand = np.nonzero(drawn & (mnx <= x) & (mxx >= x) & (mny <= y) & (mxy >= y))[0]
    for f in cand:
        st = int(plan.loop_start[f])
        n = int(plan.loop_total[f])
        poly = xy[plan.loop_verts[st:st + n]]
        inside = False
        j = n - 1
        for i in range(n):
            yi, yj = poly[i, 1], poly[j, 1]
            if (yi > y) != (yj > y):
                if x < (poly[j, 0] - poly[i, 0]) * (y - yi) / (yj - yi + 1e-300) + poly[i, 0]:
                    inside = not inside
            j = i
        if inside:
            return True
    return False


def _coverage_side(plan, ring, xy, drawn, boxes, eps=0.75, probes=9):
    """Which way this loop must run, or None if it should not be drawn at all.

    A silhouette loop is not always a boundary of what the object covers. Where
    the surface turns away but more of the same object lies behind it, the loop
    is a fold: covered on both sides, bounding nothing. Drawn as a subpath it
    punches a hole that is not there -- the torus at a shallow angle, whose far
    side fills the middle, and the boss standing on a plate.

    So each loop is sampled on both sides. Covered on one side only makes it a
    real boundary, and it is wound so the covered side is on the left; that one
    rule gives an outer boundary and a hole opposite windings without either
    knowing the other exists, and gives two overlapping islands the same one.
    Covered on both sides, or neither, and it is dropped.

    Returns True to keep the traversal, False to reverse it, None to drop.
    """
    n = len(ring)
    if n < 3:
        return None
    left = right = 0
    step = max(1, n // probes)
    for i in range(0, n, step):
        a = xy[ring[i]]
        b = xy[ring[(i + 1) % n]]
        dx, dy = b[0] - a[0], b[1] - a[1]
        ln = math.hypot(dx, dy)
        if ln < 1e-9:
            continue
        mx, my = (a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5
        nx, ny = -dy / ln, dx / ln          # left of a->b
        cl = _covers(plan, xy, drawn, boxes, (mx + nx * eps, my + ny * eps))
        cr = _covers(plan, xy, drawn, boxes, (mx - nx * eps, my - ny * eps))
        if cl and not cr:
            left += 1
        elif cr and not cl:
            right += 1
    if left == 0 and right == 0:
        return None                          # a fold: covered either side, or neither
    return left >= right


def walk_pieces(plan, edges, xy, drawn, max_pieces=MAX_PIECES):
    """The silhouette as a list of rings, one per separate piece.

    `walk_loop` has to hand back a single ring, so where the silhouette comes
    apart it bridges the pieces together -- and refuses outright past two,
    because the bridge is cut somewhere different almost every frame and nothing
    downstream can carry it.

    A fill that can hold several subpaths needs no bridge. Each piece is its own
    ring, carried on its own slots, and the nonzero winding rule fills them
    together -- but only if they are wound to say what they are. `wound` forces
    every ring counter-clockwise, which would fill a nozzle's mouth solid
    instead of cutting it out, so the orientation has to come from nesting:
    a piece inside an odd number of others is a hole and runs the other way.
    That is returned alongside each piece rather than decided downstream, where
    the other pieces are no longer in view.

    Touching loops are still spliced. They share vertices, so the seam between
    them is real geometry rather than a line invented to join two things that
    were never connected.

    Returns a list of `(anchor ids, vertex indices, counter-clockwise)` in
    descending area, or None where there is nothing worth drawing.
    """
    if len(edges) < 3:
        return None
    ev = plan.edge_verts[edges]
    loops = _loops(ev, xy)
    if not loops:
        return None

    groups = _touching(loops)
    areas = [sum(abs(_area(xy[np.asarray(loops[i])])) for i in g) for g in groups]
    total = sum(areas)
    order = sorted(range(len(groups)), key=lambda g: -areas[g])
    big = [g for g in order if total <= 0.0 or areas[g] / total >= SPECK]
    if not big or len(big) > max_pieces:
        return None

    rings = []
    for g in big:
        ring = _splice([loops[i] for i in groups[g]])
        if len(ring) >= 3:
            rings.append(ring)
    if not rings:
        return None

    boxes = _face_boxes(plan, xy)
    out = []
    for ring in rings:
        keep = _coverage_side(plan, ring, xy, drawn, boxes)
        if keep is None:
            continue                         # a fold, bounding nothing
        verts = np.asarray(ring, dtype=np.int64)
        ids = verts.copy()
        seen = set()
        for k, v in enumerate(ring):
            if v in seen:
                ids[k] = -1
            else:
                seen.add(v)
        # `keep` is relative -- it says whether *this* traversal already has the
        # covered side on its left -- and `wound` wants an absolute winding. The
        # two are not the same thing, and confusing them gave two overlapping
        # islands opposite windings that cancelled where they met: `_loops`
        # walks them in whatever direction it finds them, so "reverse" and
        # "keep" can mean the same final orientation for two different loops.
        # Turn it into an absolute here, against the ring's own signed area.
        a = _area(xy[verts])
        out.append((ids, verts, (a > 0.0) if keep else (a < 0.0)))
    return out or None


def walk_loop(plan, edges, xy):
    """The silhouette as `(anchor ids, vertex indices)`, or None.

    Two arrays, because a spliced ring visits its pinch vertex twice and the
    two readers want different things from that. `verts` is where the point
    is -- a real mesh vertex at every position, for looking up a screen
    position or a depth. `ids` is what the point may be anchored to, and the
    second visit carries -1 there: the vertex is already claimed by the first,
    and letting two slots claim one vertex is what threw away every other
    anchor on the ring. Downstream already reads a negative id as "no anchor".
    """
    if len(edges) < 3:
        return None
    ev = plan.edge_verts[edges]
    loops = _loops(ev, xy)
    if not loops:
        return None

    if len(loops) == 1:
        # One loop is the common case by far -- 223 of the probe's 304 objects
        # never do anything else -- and it needs none of what follows.
        ring = loops[0]
    else:
        groups = _touching(loops)
        areas = [sum(abs(_area(xy[np.asarray(loops[i])])) for i in g)
                 for g in groups]
        total = sum(areas)
        order = sorted(range(len(groups)), key=lambda g: -areas[g])
        big = [g for g in order
               if total <= 0.0 or areas[g] / total >= SPECK]
        # A tube seen down its bore is two pieces. A silhouette that has come
        # apart into five is not a tube, and bridging it does not help: on
        # part_003 the nearest approach between pieces moves in 59 frames of
        # 60, so the ring is cut somewhere different almost every frame and
        # nothing downstream can carry it. Those keep drawing face by face.
        if len(big) > 2:
            return None
        ring = _splice([loops[i] for i in groups[order[0]]])
        for g in order[1:]:
            if g not in big:
                continue                  # a speck, not worth a seam to reach
            ring = _bridge(ring, _splice([loops[i] for i in groups[g]]), xy)
    if len(ring) < 3:
        return None

    verts = np.asarray(ring, dtype=np.int64)
    ids = verts.copy()
    seen = set()
    for i, v in enumerate(ring):
        if v in seen:
            ids[i] = -1
        else:
            seen.add(v)
    return ids, verts
