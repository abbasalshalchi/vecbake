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

What does not work is loops that never touch -- a tube seen down its bore, an
outer ring and an inner one with nothing between them. Joining those needs a
segment bridging the gap and back, which encloses no area and would fill
correctly, but a merged outline is stroked, so the bridge draws as a line
across the hole. Those objects keep drawing face by face. On the probe that is
37 of the 81; the other 44 merge.

An earlier attempt resolved junctions and stopped there, and bought no merges
at all: it fixed branches, and the objects that branch in one frame split in
another. The three failures had to be answered together or none of them counted.
"""


from __future__ import annotations

import collections

import numpy as np


# How much of a silhouette's area may be thrown away with pieces that do not
# touch the main one. A speck below this is worth losing to gain the merge; a
# tube's bore is nowhere near it, and keeps the object drawing face by face.
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
        best = int(np.argmax(areas))
        total = sum(areas)
        if total > 0.0 and (total - areas[best]) / total >= SPECK:
            # Real geometry sits in a piece that does not touch the one being
            # kept, and no way to reach it that a stroke would not draw.
            return None
        ring = _splice([loops[i] for i in groups[best]])
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
