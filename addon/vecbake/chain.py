# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
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

from . import order
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
    """Walk runs of edges into ordered vertex paths, following continuations.

    A walk that arrives back at its own start is reported *closed*: it takes
    that last edge and repeats the starting vertex at the end of the path.

    Leaving the ring open instead -- which is what this used to do -- cost the
    join twice over. The edge that would have closed it was never claimed, so it
    resurfaced as a lone two-point stroke lying straight across the join, and
    the two ends of the ring became path endpoints, where no tangent can be
    measured and the curve therefore has to straighten. On a cylinder's rim
    that is a visible chord cutting the corner off the oval.

    `members[i]` is always the edge joining `path[i]` to `path[i + 1]`, in both
    directions of growth -- the straddle test slices the two together.
    """
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
        closed = False

        for forward in (True, False):
            if closed:
                break
            current = seed
            while True:
                tip = path[-1] if forward else path[0]
                nxt = cont.get((tip, current))
                if nxt is None or nxt in used or keys[nxt] != key:
                    break
                va, vb = (int(v) for v in edge_verts[nxt])
                other = vb if va == tip else va
                revisit = other in seen
                if revisit and other != (path[0] if forward else path[-1]):
                    break        # a figure-eight: no one traversal closes it
                used.add(nxt)
                if forward:
                    members.append(nxt)
                    path.append(other)
                else:
                    members.insert(0, nxt)
                    path.insert(0, other)
                if revisit:
                    closed = True
                    break
                seen.add(other)
                current = nxt
        chains.append((path, members, closed))
    return chains


def _fills_by_frame(frame_items, nframes, ortho_by_frame):
    """Per frame, every visible fill with the data needed to depth-test it."""
    out = []
    for fi in range(nframes):
        items = (frame_items[fi] if fi < len(frame_items) else None) or []
        frame = []
        for it in items:
            if it["kind"] != 1:
                continue
            pts = it["pts"]
            frame.append({
                "kind": 1,
                "pts": pts,
                "depths": it["depths"],
                "field": order._fit_field(pts, it["depths"], ortho_by_frame[fi]),
                "bbox": (pts[:, 0].min(), pts[:, 0].max(), pts[:, 1].min(), pts[:, 1].max()),
            })
        out.append(frame)
    return out


def _straddle_cuts(members, path, plan_name, vertex_cache, fills, visible_frames,
                   ortho_by_frame, tol=1e-4):
    """Member indices where a chain genuinely cannot take a single depth.

    Depth values are only a sort key, so a chain that is behind one object and
    in front of another needs no splitting at all -- the ordering pass just
    ranks it between them. The single case no rank can express is one surface
    that is in front of *part* of a chain and behind another part: whichever
    side of it the chain is placed, the other half is wrong.

    So this looks for exactly that. For each fill the chain actually overlaps on
    screen, it compares depths at the midpoint of every member that lies inside
    it; a chain only gets cut where that comparison changes sign along its
    length. Anything else is left whole for the ordering pass to place.
    """
    cuts = set()
    for fi in visible_frames:
        xy, depths = vertex_cache[fi][plan_name]
        pts = xy[path]
        chain = {"kind": 0, "pts": pts, "depths": depths[path]}
        box = (pts[:, 0].min(), pts[:, 0].max(), pts[:, 1].min(), pts[:, 1].max())
        is_ortho = ortho_by_frame[fi]
        mid_x = (pts[:-1, 0] + pts[1:, 0]) * 0.5
        mid_y = (pts[:-1, 1] + pts[1:, 1]) * 0.5

        for fill in fills[fi]:
            fb = fill["bbox"]
            if fb[0] > box[1] or fb[1] < box[0] or fb[2] > box[3] or fb[3] < box[2]:
                continue
            # Test every member at once. Doing this a member at a time meant a
            # point-in-polygon call per member per fill, each with a single
            # query point -- all numpy overhead and no arithmetic, and it
            # dominated the entire bake.
            covered = np.nonzero(order._inside(fill, mid_x, mid_y))[0]
            if len(covered) < 2:
                continue                  # a flip needs two covered members
            cx, cy = mid_x[covered], mid_y[covered]
            delta = (order._depths_at(chain, cx, cy, is_ortho)
                     - order._depths_at(fill, cx, cy, is_ortho))
            # Drop members too close to the surface to call, *then* look for a
            # sign change between what remains. Requiring both sides of a pair
            # to be decisive instead would miss a flip separated by an
            # undecidable member in the middle.
            decisive = np.abs(delta) > tol
            index = covered[decisive]
            behind = (delta > 0)[decisive]
            for a in range(len(index) - 1):
                if behind[a] != behind[a + 1]:
                    cuts.add(int(index[a + 1]))
    return cuts


def _runs(count, cuts):
    """Split [0, count) at the given indices into contiguous runs."""
    bounds = [0, *sorted(cuts), count]
    return [(a, b) for a, b in zip(bounds, bounds[1:]) if b > a]


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


def _pair_index(plan):
    """(lo, hi) vertex pair -> edge index, built once per plan."""
    cached = getattr(plan, "_pair_index_cache", None)
    if cached is None:
        cached = {
            (int(a), int(b)) if a < b else (int(b), int(a)): i
            for i, (a, b) in enumerate(plan.edge_verts)
        }
        plan._pair_index_cache = cached
    return cached


def _path_all_loose(plan, path):
    """Every step along this closed path is an edge with fewer than two faces.

    Doubles as the test for "is this outline safe to move". A face inside a
    surface shares its edges with neighbours, and thinning its outline would
    open a gap along the seam. It also excludes a merged path that carries
    parked vertices, because there consecutive slots are not mesh edges at all
    -- which is what keeps the merged body, whose parked points exist to be
    pulled out on rotation, out of this.
    """
    lookup = _pair_index(plan)
    n = len(path)
    if n < 4:
        return False
    for i in range(n):
        a, b = int(path[i]), int(path[(i + 1) % n])
        e = lookup.get((a, b) if a < b else (b, a))
        if e is None or not plan.edge_loose[e]:
            return False
    return True


def thin_fills(plans, elements, slots, frame_items, vertex_cache, settings, nframes):
    """Drop interior points from fill outlines built only of loose edges.

    Chaining has always done this for strokes; fills never had it, so a face
    kept every vertex of its loop however straight the run. Invisible on a
    wall, expensive on a wing -- there the whole silhouette is one open border
    traced at modelling density, and it is drawn once per instance.

    Runs before the ordering pass, and rewrites the items that pass will see,
    for the same reason chaining does: change the geometry afterwards and the
    resolved paint order no longer describes the shapes being drawn.

    Returns {element key: kept vertex path} for element_verts.
    """
    tol = float(getattr(settings, "loose_dissolve_px", 0.0) or 0.0)
    if tol <= 0.0:
        tol = float(settings.dissolve_px)
    if tol <= 0.0:
        return {}

    by_key = {el.key: el for el in elements}
    paths = {}
    dropped = 0

    for plan in plans.values():
        if not plan.fills:
            continue

        candidates = []
        if plan.merge_fill and plan.merged is not None and plan.merged.order is not None:
            candidates.append((f"{plan.name}/m", list(plan.merged.order)))
        elif not plan.merge_fill:
            for fidx in range(plan.nfaces):
                s = int(plan.loop_start[fidx])
                e = s + int(plan.loop_total[fidx])
                candidates.append((f"{plan.name}/f{fidx}", list(plan.loop_verts[s:e])))

        for key, path in candidates:
            el = by_key.get(key)
            slot = slots.get(key)
            if el is None or slot is None or len(path) < 4:
                continue
            if not _path_all_loose(plan, path):
                continue

            visible = [fi for fi in range(nframes)
                       if slot["vis"][fi] > 0.0 and vertex_cache[fi].get(plan.name) is not None]
            if not visible:
                continue

            idx = np.asarray(path, dtype=np.int64)
            points = np.stack([vertex_cache[fi][plan.name][0][idx] for fi in visible])
            kept = _dissolve(points, tol)
            if len(kept) >= len(path) or len(kept) < 3:
                continue

            new_path = [path[i] for i in kept]
            new_idx = np.asarray(new_path, dtype=np.int64)
            dropped += len(path) - len(new_path)

            last = None
            for fi in range(nframes):
                cached = vertex_cache[fi].get(plan.name)
                if cached is None:
                    slot["geo"][fi] = last
                    continue
                last = cached[0][new_idx].ravel().tolist()
                slot["geo"][fi] = last
            slot["npts"] = len(new_path)
            el.npts = len(new_path)
            paths[key] = new_idx

            # The ordering pass samples these points directly, so they have to
            # be the thinned ones too -- rebuilt from the vertex cache rather
            # than sliced, because a merged element's item points follow the
            # silhouette ring while its slot follows the slot order.
            for fi in visible:
                cached = vertex_cache[fi].get(plan.name)
                if cached is None:
                    continue
                xy, depths = cached
                for item in frame_items[fi]:
                    if item.get("key") == key:
                        item["pts"] = xy[new_idx]
                        item["depths"] = depths[new_idx]

    return {"paths": paths, "points_dropped": dropped}


def apply(plans, elements, slots, frame_items, vertex_cache, settings, nframes,
          ortho_by_frame=None):
    """Rewrite elements/slots/frame_items in place. Returns a small report."""
    removed_keys = set()
    added: list[Element] = []
    chain_items = {}          # frame index -> list of replacement items
    paths = {}                # chain key -> its mesh vertex path, for curves.py
    loose_keys = set()        # chains made only of edges with < 2 faces
    merged = 0
    if ortho_by_frame is None:
        ortho_by_frame = [False] * nframes
    fills = _fills_by_frame(frame_items, nframes, ortho_by_frame)

    for plan in plans.values():
        if not plan.strokes or not plan.nedges:
            continue
        eligible = [e for e in range(plan.nedges) if f"{plan.name}/e{e}" in slots]
        if len(eligible) < 2:
            continue

        # Only merge edges drawn identically in every frame. Colour and width
        # have to match too or the merged stroke would change appearance.
        #
        # And never merge across the surface: a chain gets one depth, so an
        # element running from the near side of an object round to the far side
        # cannot be ordered against anything. With backface culling that never
        # arose -- the far half was not drawn -- but a see-through material
        # draws both, and the whole rim then paints at its nearest member's
        # depth, on top of the very faces that should be covering half of it.
        # Facing is recorded per frame and compared whole, exactly like
        # visibility, so a split only happens where the two genuinely differ.
        keys = {}
        for e in eligible:
            slot = slots[f"{plan.name}/e{e}"]
            colour = plan.edge_colors[e] if plan.edge_colors else plan.stroke_color
            keys[e] = ((slot["vis"] > 0.0).tobytes(), slot["front"].tobytes(), colour)

        coords = plan.vert_co @ plan.matrix3.T
        cont = _continuations(plan.edge_verts, eligible, keys, coords,
                              settings.chain_max_turn, settings.chain_junctions)

        for index, (path, members, ring) in enumerate(
                _trace(plan.edge_verts, eligible, keys, cont)):
            if len(members) < 2:
                continue

            member_keys = [f"{plan.name}/e{e}" for e in members]
            vis = slots[member_keys[0]]["vis"]
            visible_frames = [fi for fi in range(nframes) if vis[fi] > 0.0]
            if not visible_frames:
                continue

            cuts = _straddle_cuts(members, path, plan.name, vertex_cache, fills,
                                  visible_frames, ortho_by_frame)

            for run, (start, stop) in enumerate(_runs(len(members), cuts)):
                if stop - start < 2:
                    continue                      # a lone edge is left as it was
                run_members = members[start:stop]
                run_keys = member_keys[start:stop]
                run_path = path[start:stop + 1]
                # A ring only survives whole. Any straddle cut turns it into an
                # arc, and an arc has two ends like any other open chain.
                closed = ring and not cuts

                # projected positions of this path, for every frame it is visible
                points = np.stack([
                    vertex_cache[fi][plan.name][0][run_path] for fi in visible_frames
                ])
                # Loose chains get their own thinning budget. An open border
                # carries the whole silhouette of a flat shape, so it is both
                # the run you most want to decimate -- it is all detail and no
                # structure -- and the one where over-thinning shows first.
                # Giving it a separate dial beats moving the global one and
                # dragging every surface crease along with it.
                # Decided here, not later from the finished path: _dissolve is
                # about to drop interior vertices, after which consecutive
                # points are no longer mesh edges and nothing downstream can
                # tell what this chain was made of.
                is_loose = bool(plan.edge_loose[np.asarray(run_members)].all())
                loose_px = float(getattr(settings, "loose_dissolve_px", 0.0) or 0.0)
                tol = loose_px if (is_loose and loose_px > 0.0) else settings.dissolve_px
                kept = _dissolve(points, tol)
                final_path = [run_path[i] for i in kept]
                if closed:
                    final_path.pop()      # the seam vertex the ring implies
                if len(final_path) < (3 if closed else 2):
                    continue

                key = f"{plan.name}/c{index}_{run}"
                depth = np.min(np.stack([slots[k]["depth"] for k in run_keys]), axis=0)

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
                colour = (plan.edge_colors[run_members[0]] if plan.edge_colors
                          else plan.stroke_color)
                added.append(Element(key=key, kind=STROKE, npts=len(final_path),
                                     group=plan.group, color=colour,
                                     width=settings.stroke_width, closed=closed))
                paths[key] = final_path
                if is_loose:
                    loose_keys.add(key)
                removed_keys.update(run_keys)
                merged += len(run_members)

                # replace the member items the ordering pass will see
                # a chain lies on every face any of its members lay on
                on_faces = tuple({f for e in run_members for f in plan.edge_face_keys(e)})
                for fi in visible_frames:
                    xy, depths = vertex_cache[fi][plan.name]
                    seed = min(float(slots[k]["depth"][fi]) for k in run_keys)
                    chain_items.setdefault(fi, []).append({
                        "key": key, "kind": 0, "seed": seed,
                        "pts": xy[final_path], "depths": depths[final_path],
                        "on_faces": on_faces,
                    })

    if not added:
        return {"chains": 0, "edges_merged": 0, "paths": {}}

    for key in removed_keys:
        slots.pop(key, None)
    elements[:] = [el for el in elements if el.key not in removed_keys] + added

    for fi, items in enumerate(frame_items):
        if items is None:
            continue
        items[:] = [it for it in items if it["key"] not in removed_keys]
        items.extend(chain_items.get(fi, []))

    return {"chains": len(added), "edges_merged": merged, "paths": paths,
            "loose": loose_keys}
