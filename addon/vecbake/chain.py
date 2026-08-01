"""Merge runs of edges into single polylines, and dissolve redundant vertices.

Two separate savings, both of which the mesh hands you for free:

* **Chaining** turns a run of edges meeting end to end into one polyline
  element. Fewer elements means fewer depth-sort entries, fewer draw calls and
  a smaller file (shared vertices stop being stored twice), and the stroke gets
  real line joins instead of butting segments together.
* **Dissolving** then drops interior vertices that the projection makes
  redundant -- a straight rail modelled as eight collinear segments becomes two
  points.

Safety is the whole problem. Two edges may only merge if they are drawn
*identically in every frame*, otherwise a chain would light up sections that
should be hidden. Rather than guess from topology, we merge only when the
sampled visibility arrays are bit-for-bit equal over the whole animation, which
is exact and needs no assumptions about the edge mode in force.

Dissolving reuses the keyframe reducer's philosophy: a vertex goes only if
removing it never moves the drawn line by more than a tolerance **in pixels**,
measured across every frame the chain is visible.
"""

from __future__ import annotations

import math

import numpy as np

from .ir import STROKE, Element


def _continuations(edge_verts, eligible, keys, coords, max_turn, through_junctions):
    """Decide, for each edge arriving at each vertex, which edge it continues into.

    Junctions do not have to end a chain. An X in the mesh is four edges, but
    a vector renderer can draw it as two straight polylines that simply cross --
    strokes may overlap for free, and splitting them costs an element and a
    duplicated vertex for nothing. So at a junction we pair edges up by angular
    continuity, straightest first, and a chain runs straight through.

    Pairing uses 3D directions, not projected ones, so it is decided once and
    stays fixed for every frame -- element identity depends on it.
    """
    incident: dict[int, list[int]] = {}
    for e in eligible:
        a, b = edge_verts[e]
        incident.setdefault(int(a), []).append(e)
        incident.setdefault(int(b), []).append(e)

    # straight through means the two outgoing directions are opposite (dot -1)
    limit = -math.cos(math.radians(max_turn))
    cont: dict[tuple[int, int], int] = {}

    for vertex, here in incident.items():
        if len(here) < 2:
            continue
        if len(here) == 2:
            e1, e2 = here
            if keys[e1] == keys[e2]:
                cont[(vertex, e1)] = e2
                cont[(vertex, e2)] = e1
            continue
        if not through_junctions:
            continue

        directions = {}
        for e in here:
            a, b = (int(v) for v in edge_verts[e])
            other = b if a == vertex else a
            d = coords[other] - coords[vertex]
            length = float(np.linalg.norm(d))
            if length > 1e-12:
                directions[e] = d / length

        candidates = []
        edges = [e for e in here if e in directions]
        for i in range(len(edges)):
            for j in range(i + 1, len(edges)):
                a, b = edges[i], edges[j]
                if keys[a] != keys[b]:
                    continue
                dot = float(directions[a] @ directions[b])
                if dot <= limit:
                    candidates.append((dot, a, b))
        candidates.sort()                     # straightest pair first
        taken = set()
        for _dot, a, b in candidates:
            if a in taken or b in taken:
                continue
            taken.add(a)
            taken.add(b)
            cont[(vertex, a)] = b
            cont[(vertex, b)] = a
    return cont


def _trace(edge_verts, eligible, keys, cont):
    """Walk runs of edges into ordered vertex paths, following continuations."""
    used = set()
    chains = []
    for seed in eligible:
        if seed in used:
            continue
        used.add(seed)
        a, b = (int(v) for v in edge_verts[seed])
        path = [a, b]
        seen = {a, b}
        members = [seed]
        key = keys[seed]

        for forward in (True, False):
            current = seed
            while True:
                tip = path[-1] if forward else path[0]
                nxt = cont.get((tip, current))
                if nxt is None or nxt in used or keys[nxt] != key:
                    break
                va, vb = (int(v) for v in edge_verts[nxt])
                other = vb if va == tip else va
                if other in seen:
                    break                       # closed loop -- leave it open
                used.add(nxt)
                members.append(nxt)
                seen.add(other)
                if forward:
                    path.append(other)
                else:
                    path.insert(0, other)
                current = nxt
        chains.append((path, members))
    return chains


def _max_deviation(points, a, b, i):
    """Worst distance, over all frames, from vertex i to the segment a..b."""
    A = points[:, a]
    B = points[:, b]
    Q = points[:, i]
    AB = B - A
    denom = (AB * AB).sum(axis=1)
    t = np.where(denom > 1e-12, ((Q - A) * AB).sum(axis=1) / np.maximum(denom, 1e-12), 0.0)
    t = np.clip(t, 0.0, 1.0)
    proj = A + AB * t[:, None]
    return float(np.linalg.norm(Q - proj, axis=1).max())


def _dissolve(points, tol):
    """RDP over a polyline's vertices, error measured in pixels across frames."""
    n = points.shape[1]
    if n <= 2 or tol <= 0.0:
        return list(range(n))
    keep = [False] * n
    keep[0] = keep[n - 1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        worst, worst_i = 0.0, -1
        for i in range(a + 1, b):
            d = _max_deviation(points, a, b, i)
            if d > worst:
                worst, worst_i = d, i
        if worst > tol and worst_i >= 0:
            keep[worst_i] = True
            stack.append((a, worst_i))
            stack.append((worst_i, b))
    return [i for i, k in enumerate(keep) if k]


def apply(plans, elements, slots, frame_items, vertex_cache, settings, nframes):
    """Rewrite elements/slots/frame_items in place. Returns a small report."""
    removed_keys = set()
    added: list[Element] = []
    chain_items = {}          # frame index -> list of replacement items
    merged = 0

    for plan in plans.values():
        if not plan.strokes or not plan.nedges:
            continue
        eligible = [e for e in range(plan.nedges) if f"{plan.name}/e{e}" in slots]
        if len(eligible) < 2:
            continue

        # Only merge edges drawn identically in every frame. Colour and width
        # have to match too or the merged stroke would change appearance.
        keys = {}
        for e in eligible:
            slot = slots[f"{plan.name}/e{e}"]
            colour = plan.edge_colors[e] if plan.edge_colors else plan.stroke_color
            keys[e] = ((slot["vis"] > 0.0).tobytes(), colour)

        coords = plan.vert_co @ plan.matrix3.T
        cont = _continuations(plan.edge_verts, eligible, keys, coords,
                              settings.chain_max_turn, settings.chain_junctions)

        for index, (path, members) in enumerate(
                _trace(plan.edge_verts, eligible, keys, cont)):
            if len(members) < 2:
                continue

            member_keys = [f"{plan.name}/e{e}" for e in members]
            vis = slots[member_keys[0]]["vis"]
            visible_frames = [fi for fi in range(nframes) if vis[fi] > 0.0]
            if not visible_frames:
                continue

            # projected positions of this path, for every frame it is visible
            points = np.stack([
                vertex_cache[fi][plan.name][0][path] for fi in visible_frames
            ])
            kept = _dissolve(points, settings.dissolve_px)
            final_path = [path[i] for i in kept]
            if len(final_path) < 2:
                continue

            key = f"{plan.name}/c{index}"
            first = slots[member_keys[0]]
            depth = np.min(np.stack([slots[k]["depth"] for k in member_keys]), axis=0)

            geo = [None] * nframes
            last_seen = None
            for fi in range(nframes):
                cached = vertex_cache[fi].get(plan.name)
                if cached is None:
                    geo[fi] = last_seen
                    continue
                last_seen = cached[0][final_path].ravel().tolist()
                geo[fi] = last_seen

            slots[key] = {"geo": geo, "vis": vis.copy(), "depth": depth,
                          "shade": np.ones(nframes), "npts": len(final_path)}
            colour = plan.edge_colors[members[0]] if plan.edge_colors else plan.stroke_color
            added.append(Element(key=key, kind=STROKE, npts=len(final_path),
                                 group=plan.group, color=colour,
                                 width=settings.stroke_width))
            removed_keys.update(member_keys)
            merged += len(members)

            # replace the member items the ordering pass will see
            for fi in visible_frames:
                xy, depths = vertex_cache[fi][plan.name]
                seed = min(float(slots[k]["depth"][fi]) for k in member_keys)
                chain_items.setdefault(fi, []).append({
                    "key": key, "kind": 0, "seed": seed,
                    "pts": xy[final_path], "depths": depths[final_path],
                })

    if not added:
        return {"chains": 0, "edges_merged": 0}

    for key in removed_keys:
        slots.pop(key, None)
    elements[:] = [el for el in elements if el.key not in removed_keys] + added

    for fi, items in enumerate(frame_items):
        if items is None:
            continue
        items[:] = [it for it in items if it["key"] not in removed_keys]
        items.extend(chain_items.get(fi, []))

    return {"chains": len(added), "edges_merged": merged}
