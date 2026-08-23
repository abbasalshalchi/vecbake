# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""One object, one filled outline.

A shade-flat cylinder emits one fill per face, and every one of them animates,
sorts and draws separately. For a part that only ever needs to read as a
silhouette -- an insect body, a limb, anything the viewer sees as a solid --
that is a lot of machinery for a shape with no interior detail. The butterfly's
body is 82 faces and 328 points; its outline is 30 points.

So: emit the outline instead.

Keeping the point count fixed
-----------------------------
An outline is a *view-dependent* thing. Which edges form the silhouette changes
as an object turns, so the honest shape has a vertex count that changes frame to
frame -- and this whole format rests on the opposite, that an element has a
fixed point count and correspondence between frames is free.

The way out is to spend a slot on every vertex the object has, whether or not it
is on the outline this frame. Vertices on the outline go where they belong; the
rest are parked *on the straight line between their neighbours*, where they
change the shape by exactly nothing. A point flush with the segment it sits on
is invisible to a fill, and it is still there to be pulled out on the frame the
silhouette rotates onto it.

That costs points the shape does not need -- the butterfly body carries 88 slots
to draw a 30-point outline -- but it buys a merged fill with no pre-pass, no
second bake, and no dependence on the silhouette being stable.

Where it gives up
-----------------
Allocation is not the hard part -- ordering is. The *order* of the slots is
fixed once, from the first frame that yields a ring, and actives have to appear
around the loop in that order ever after. Rotate far enough and the silhouette
changes not just which vertices are on it but their cyclic order, so a vertex
pops out into a slot that is no longer in the right place and the outline
crosses itself.

Nothing here can recover from that: an element's point count is fixed before
sampling begins. So `stable` goes false and `bake.py` samples the whole thing
again with that object unmerged. This class of shape is exactly what a static
camera never hits and a moving one always might.
"""

from __future__ import annotations

import math

import numpy as np


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


def walk_loop(plan, edges, xy=None):
    """Order silhouette edges into one closed ring of vertex indices.

    A vertex with three or more silhouette edges on it is where the outline
    touches itself on screen, and it is common: 1027 frames of a sixty-frame
    orbit of the Voyager probe, 37% of everything that could not be merged.
    Refusing them outright cost the whole object, for the whole clip.

    Given screen positions the junction has an answer. Arriving along one edge,
    the outline continues along the neighbour that turns furthest towards the
    inside -- the standard way to trace one face of a planar subdivision, and
    exact here in a way it never was for split segments: the graph is mesh
    vertices, so identity is an integer and only the angles come from the
    projection. Nothing is welded and no tolerance is involved.

    Still returns None for a dangling end or for two separate rings, which are
    different shapes rather than the same shape drawn awkwardly.
    """
    if len(edges) < 3:
        return None
    ev = plan.edge_verts[edges]
    adj = {}
    for a, b in ev:
        adj.setdefault(int(a), []).append(int(b))
        adj.setdefault(int(b), []).append(int(a))
    if any(len(n) < 2 for n in adj.values()):
        return None                      # dangling: the chain stops
    branched = any(len(n) > 2 for n in adj.values())
    if branched and xy is None:
        return None

    start = int(ev[0][0])
    loop = [start]
    prev, cur = None, start
    while True:
        options = [v for v in adj[cur] if v != prev] or list(adj[cur])
        if len(options) == 1:
            nxt = options[0]
        else:
            back = (math.atan2(xy[prev][1] - xy[cur][1], xy[prev][0] - xy[cur][0])
                    if prev is not None else 0.0)
            nxt, best = None, None
            for v in options:
                turn = (math.atan2(xy[v][1] - xy[cur][1],
                                   xy[v][0] - xy[cur][0]) - back) % (2 * math.pi)
                if best is None or turn < best:
                    nxt, best = v, turn
        if nxt == start:
            break
        loop.append(nxt)
        prev, cur = cur, nxt
        if len(loop) > len(adj):
            return None                  # walked past the ring: malformed
    # Every vertex on the silhouette has to end up on the ring. Fewer means
    # this was one ring of several, and those are separate shapes.
    return loop if len(loop) == len(adj) else None


class MergedPath:
    """Fixed slot order for one object's merged outline.

    Built from the first frame that produces a usable ring, then reused for
    every frame after it.
    """

    def __init__(self, nverts):
        self.nverts = int(nverts)
        self.order = None        # slot -> vertex index
        self.slot_of = None      # vertex index -> slot
        self.stable = True
        self.frames_unstable = 0

    def adopt(self, loop):
        """Fix the slot order from this ring.

        Spare vertices are spread evenly around the ring rather than piled at
        the end, so that a vertex arriving on the silhouette later has a slot
        somewhere near where it belongs.
        """
        spare = [v for v in range(self.nverts) if v not in set(loop)]
        order, si = [], 0
        per_gap, extra = divmod(len(spare), len(loop))
        for i, v in enumerate(loop):
            order.append(v)
            take = per_gap + (1 if i < extra else 0)
            order.extend(spare[si:si + take])
            si += take
        order.extend(spare[si:])
        self.order = np.asarray(order, dtype=np.int64)
        self.slot_of = np.empty(self.nverts, dtype=np.int64)
        self.slot_of[self.order] = np.arange(len(self.order))

    def _consistent(self, loop):
        """Does this ring run around the slots in the order we committed to?"""
        slots = self.slot_of[np.asarray(loop, dtype=np.int64)]
        pivot = int(np.argmin(slots))
        rolled = np.roll(slots, -pivot)
        if np.all(np.diff(rolled) > 0):
            return True
        rolled = np.roll(slots[::-1], -int(np.argmin(slots[::-1])))
        return bool(np.all(np.diff(rolled) > 0))

    def points(self, loop, xy):
        """Positions for every slot: outline vertices placed, the rest parked.

        Parked runs are spread along the straight segment between the actives
        either side, so they lie flush with the line and add no shape.
        """
        if self.order is None:
            self.adopt(loop)
        elif not self._consistent(loop):
            self.stable = False
            self.frames_unstable += 1

        active = np.zeros(self.nverts, dtype=bool)
        active[np.asarray(loop, dtype=np.int64)] = True
        n = len(self.order)
        out = np.empty((n, 2), dtype=np.float64)
        live = active[self.order]
        idx = np.nonzero(live)[0]
        if len(idx) == 0:
            out[:] = 0.0
            return out
        out[idx] = xy[self.order[idx]]

        # Fill each gap between consecutive live slots, wrapping at the end.
        for k in range(len(idx)):
            s = int(idx[k])
            e = int(idx[(k + 1) % len(idx)])
            gap = (e - s) % n
            if gap < 2:
                continue
            a, b = out[s], out[e]
            for j in range(1, gap):
                out[(s + j) % n] = a + (b - a) * (j / gap)
        return out
