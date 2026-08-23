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
