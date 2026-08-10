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
The *order* of the slots is fixed once, from the first frame sampled. Actives
have to appear around the loop in that order. For anything that does not rotate
much, and for any single flat face, they always do. When they do not, this
reports it rather than drawing a shape with its outline shuffled -- see
`stable` on the result, which the caller turns into a per-face fallback.
"""

from __future__ import annotations

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


def walk_loop(plan, edges):
    """Order silhouette edges into one closed ring of vertex indices.

    Returns None when they do not form exactly one simple ring -- a branch, a
    dangling end, or two separate rings. A merged fill cannot represent those,
    so the caller falls back rather than inventing a path through them.
    """
    if len(edges) < 3:
        return None
    ev = plan.edge_verts[edges]
    adj = {}
    for a, b in ev:
        adj.setdefault(int(a), []).append(int(b))
        adj.setdefault(int(b), []).append(int(a))
    if any(len(n) != 2 for n in adj.values()):
        return None                      # branching or dangling: not a ring

    start = int(ev[0][0])
    loop = [start]
    prev, cur = None, start
    while True:
        nxt = adj[cur][0] if adj[cur][0] != prev else adj[cur][1]
        if nxt == start:
            break
        loop.append(nxt)
        prev, cur = cur, nxt
        if len(loop) > len(adj):
            return None                  # walked past the ring: malformed
    return loop if len(loop) == len(adj) else None   # one ring, not several


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
