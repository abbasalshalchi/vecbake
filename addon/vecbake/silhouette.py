# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""Put the silhouette where the curve says the surface ends.

Curving an outline moves it. A cylinder's rim is a polygon inscribed in the real
circle, so the curve drawn through it reaches *past* every vertex -- on the demo
cylinder the rim's furthest point is 8.3px outside the furthest vertex, and it
sits between two vertices rather than on one.

The side of the cylinder does not know that. Its silhouette is a mesh edge, and
a mesh edge runs through a vertex, so the vertical line stops 8.3px short of the
shape the curve just drew. What you get is the rim bulging into open space and
the side stepping back in underneath it -- a bump, where a cylinder should have
a straight edge running between the two rims' furthest points.

So the silhouette moves out to meet it. For each edge separating a drawn face
from a hidden one, this walks the rim curve either side of the corner, finds its
outermost point -- outermost meaning perpendicular to the silhouette edge, away
from the face -- and puts the corner there.

*Either side*, and that matters. The silhouette of the reconstructed surface
falls anywhere within half a facet of the mesh edge, so as the camera turns it
crosses the vertex and keeps going. Searching only outwards would pin the corner
to the vertex for half of every facet and then jump, which reads as a stutter
running round the object. Searching both segments slides the corner continuously
through the vertex instead: past it the face is extended, short of it the face is
trimmed, and at the vertex the two agree.

This is a reconstruction, not a measurement. It draws the silhouette of the
smooth shape the mesh stands for -- the same shape `curves.py` draws the outline
of, and deliberately not the faceted one Blender itself renders. With `Curves`
off, none of it runs.
"""

from __future__ import annotations

import math

import numpy as np

from . import follow
from .ir import FILL

# Samples per rim segment when hunting the outermost point. What is being
# maximised is a cubic along the segment, so there is a single peak to find.
PROBE = 96


def _paths(elements, element_verts):
    out = {}
    for el in elements:
        path = element_verts.get(el.key)
        if path is not None and len(path) >= 2:
            out[el.key] = [int(v) for v in path]
    return out


def _index(elements, paths):
    """Which elements draw each mesh edge, keyed by original vertex path."""
    edges = {}
    for el in elements:
        path = paths.get(el.key)
        if path is None:
            continue
        for i, j in follow._pairs(len(path), el.is_closed()):
            a, b = path[i], path[j]
            if a != b:
                edges.setdefault((el.key.split("/")[0], min(a, b), max(a, b)),
                                 []).append((el, i, j))
    return edges


def _decider(edges, obj, a, b):
    """The element whose curve this edge takes, or None if it is drawn straight.

    Same choice `follow.owners` makes, so the curve a corner is placed on is the
    curve that actually gets drawn there.
    """
    members = edges.get((obj, min(a, b), max(a, b)))
    if not members:
        return None
    best = min(members, key=lambda m: (-follow._smooth_ends(*m), m[0].key))
    return best if follow._smooth_ends(*best) else None


def _slot_index(slot, vertex_pos):
    """Where an original vertex sits now, after the follow pass moved things."""
    origin = slot.get("origin")
    if origin is None:
        return vertex_pos
    for k, src in enumerate(origin):
        if src == vertex_pos:
            return k
    return None


def _rim_spans(vertex, obj, paths, by_key, slots):
    """Every curved rim segment meeting a mesh vertex, whoever draws it.

    Deliberately not just the element bounding the face. At the bottom of a
    cylinder the rim arc *ends* at the silhouette -- the far half is not drawn
    at all -- so asking it where the rim goes next gets no answer, and the
    corner never extends. The culled bottom cap knows: it is a closed ring over
    the whole rim, and it is sampled every frame whether it is drawn or not.
    Its curve is the same rim curve, so it can say where the surface turns away
    even though nothing paints it there.
    """
    spans = []
    for key, path in paths.items():
        if key.split("/")[0] != obj:
            continue
        el = by_key[key]
        if el.smooth is None or slots[key].get("origin") is not None:
            continue                      # rewritten: its indices moved
        n = len(path)
        for k, v in enumerate(path):
            if v != vertex:
                continue
            prev_, next_ = _ends(n, el.is_closed(), k)
            for other in (prev_, next_):
                if other is None or follow._smooth_ends(el, k, other) == 0:
                    continue
                # traversal order matters: the tangents are stated along
                # increasing index, so a reversed pair is a different curve
                span = (k, other) if other == (k + 1) % n else (other, k)
                spans.append((el, span[0], span[1]))
    return spans


def _outermost(spans, slots, paths, fi, nx, ny, inside_pair):
    """Furthest point outward across the rim segments meeting at a corner.

    Reports whether it landed on the segment inside the face, which is what
    separates extending the face past its corner from trimming it back.
    """
    best = None
    for el, a, b in spans:
        pts = slots[el.key]["geo"][fi]
        if pts is None:
            continue
        curve = follow._cubic(pts, el.npts, el.smooth, el.is_closed(), a, b)
        path = paths[el.key]
        inside = {path[a], path[b]} == inside_pair
        for s in range(PROBE + 1):
            x, y = follow._at(curve, s / PROBE)
            reach = nx * x + ny * y
            if best is None or reach > best[0]:
                best = (reach, x, y, inside)
    return best


def _target(el, path, c, inside, other, obj, edges, paths, by_key, slots, fi):
    """Where one corner belongs in one frame, or None to leave it where it is."""
    if _decider(edges, obj, path[inside], path[c]) is None:
        return None                     # the edge into the face is straight too
    spans = _rim_spans(path[c], obj, paths, by_key, slots)
    if not spans:
        return None
    face = slots[el.key]["geo"][fi]
    if face is None:
        return None

    ci = _slot_index(slots[el.key], c)
    ei = _slot_index(slots[el.key], other)
    ii = _slot_index(slots[el.key], inside)
    if ci is None or ei is None or ii is None:
        return None
    ux, uy = face[ci * 2], face[ci * 2 + 1]
    ex, ey = face[ei * 2] - ux, face[ei * 2 + 1] - uy
    span = math.hypot(ex, ey)
    if span < 1e-9:
        return None
    nx, ny = -ey / span, ex / span
    if nx * (face[ii * 2] - ux) + ny * (face[ii * 2 + 1] - uy) > 0.0:
        nx, ny = -nx, -ny             # away from the face this edge bounds

    best = _outermost(spans, slots, paths, fi, nx, ny, {path[inside], path[c]})
    if best is None:
        return None
    reach, x, y, inside_hit = best
    # trimming when the peak is on the segment inside the face, extending past
    return {"x": x, "y": y, "trim": inside_hit, "n": (nx, ny), "reach": reach}


def _ends(n, closed, c):
    """The point either side of index c; None where an open path stops."""
    if closed:
        return (c - 1) % n, (c + 1) % n
    return (c - 1 if c > 0 else None), (c + 1 if c < n - 1 else None)


def _on_rim(el, path, c, obj, edges):
    """Does this element draw a curved edge at that vertex?

    What separates the rim arc, whose end genuinely stops at the silhouette,
    from any other open stroke that happens to finish on the same vertex.
    """
    for other in _ends(len(path), el.is_closed(), c):
        if other is not None and _decider(edges, obj, path[other], path[c]):
            return True
    return False


def _movers(elements, members, paths, obj, edges, a, b):
    """Everything that has to move with a silhouette, by mesh vertex.

    Not just the face. The line drawn along the silhouette edge has to travel
    with it or it ends up sitting 8px inside the fill it is supposed to outline,
    and so does the rim arc that stops there.
    """
    out = {}
    for el, i, j in members:
        path = paths[el.key]
        n = len(path)
        for c, other in ((i, j), (j, i)):
            after = other == (c + 1) % n
            inside = (c - 1) % n if after else (c + 1) % n
            # Keeping the original vertex is only right for something that
            # follows the rim through it -- a face, or the rim arc. The line
            # drawn *along* the silhouette does not go near it: for that one
            # the corner simply moves, or the line overshoots to the old vertex
            # and doubles back, sticking out past the fill it outlines.
            out.setdefault(path[c], []).append(
                (el, c, after, inside, _on_rim(el, path, c, obj, edges)))

    seen = {(el.key, c) for verts in out.values() for el, c, *_r in verts}
    for el in elements:
        # Same object only. `a` and `b` are mesh vertex indices and every object
        # numbers its own from zero, so a bare id match sweeps in strokes from
        # unrelated objects and hands them a target computed somewhere else
        # entirely -- two parts a thousand pixels apart swapping corners. The
        # edge index above is keyed by object for exactly this reason; this loop
        # was the one place that forgot.
        if el.key.split("/")[0] != obj:
            continue
        path = paths.get(el.key)
        if path is None or el.is_closed() or len(path) < 2:
            continue
        for c in (0, len(path) - 1):
            if path[c] not in (a, b) or (el.key, c) in seen:
                continue
            if not _on_rim(el, path, c, obj, edges):
                continue
            after = c == len(path) - 1
            out.setdefault(path[c], []).append(
                (el, c, after, c - 1 if after else 1, True))
    return out


def apply(elements, slots, element_verts, nframes):
    """Move silhouette corners onto the curve. Returns a small report."""
    paths = _paths(elements, element_verts)
    edges = _index(elements, paths)
    by_key = {el.key: el for el in elements}
    moves = {}

    for (obj, a, b), members in edges.items():
        # A silhouette is drawn straight and separates a drawn face from a
        # hidden one. An edge the curve already owns is a rim, not a silhouette:
        # the outline runs *along* it, and it is already where it should be.
        if _decider(edges, obj, a, b) is not None:
            continue
        faces = [m for m in members if m[0].kind == FILL]
        if len(faces) != 2:
            continue
        vis = [slots[m[0].key]["vis"] for m in faces]
        frames = [fi for fi in range(nframes)
                  if (vis[0][fi] > 0.0) != (vis[1][fi] > 0.0)]
        if not frames:
            continue
        movers = _movers(elements, members, paths, obj, edges, a, b)

        for fi in frames:
            el, i, j = faces[0] if vis[0][fi] > 0.0 else faces[1]
            path = paths[el.key]
            n = len(path)
            for c, other in ((i, j), (j, i)):
                inside = (c - 1) % n if other == (c + 1) % n else (c + 1) % n
                # the target belongs to the edge and the frame, not to the
                # element, so the face and its lines all land on one point
                hit = _target(el, path, c, inside, other, obj, edges, paths,
                              by_key, slots, fi)
                if hit is None:
                    continue
                for m_el, m_c, m_after, m_inside, m_keep in movers.get(path[c], ()):
                    entry = moves.setdefault(
                        (m_el.key, m_c),
                        {"after": m_after, "inside": m_inside,
                         "keep": m_keep, "at": {}})
                    entry["at"][fi] = hit

    if not moves:
        return {"corners": 0, "elements": 0, "points": 0}
    return _rewrite(moves, paths, by_key, slots, nframes)


def _along(px, py, ax, ay, bx, by):
    """How far along the chord A->B a point sits, as a fraction.

    Ordering along the *edge*, which is what decides whether a point is still
    before the corner. Distance outward cannot answer that: the curve bulges,
    so a point can be nearer the chord than the corner is and still lie beyond
    it -- which is how the outline ended up doubling back on itself.
    """
    vx, vy = bx - ax, by - ay
    den = vx * vx + vy * vy
    if den < 1e-12:
        return 0.0
    return ((px - ax) * vx + (py - ay) * vy) / den


def _rewrite(moves, paths, by_key, slots, nframes):
    """Give every moved corner a point of its own, and place it each frame."""
    added = 0
    touched = 0
    for key in sorted({k for k, _c in moves}):
        el = by_key[key]
        slot = slots[key]
        mine = {c: entry for (k, c), entry in moves.items() if k == key}
        origin = slot.get("origin") or list(range(el.npts))
        where = {src: idx for idx, src in enumerate(origin) if isinstance(src, int)}

        # The extra point goes on the side facing the silhouette edge, so the
        # boundary reads ...rim, corner, silhouette point, down the edge...
        layout = []
        for idx, src in enumerate(origin):
            entry = mine.get(src) if isinstance(src, int) else None
            if entry is None or not entry["keep"]:
                layout.append(("keep", idx, src if entry else None))
            elif entry["after"]:
                layout.append(("keep", idx, src))
                layout.append(("edge", idx, src))
            else:
                layout.append(("edge", idx, src))
                layout.append(("keep", idx, src))

        # follow's split points on the segment running into the corner, which
        # have to stop where the corner now does
        near = {}
        for corner, entry in mine.items():
            for idx, src in enumerate(origin):
                if isinstance(src, tuple) and src[0] in (corner, entry["inside"]):
                    near.setdefault(idx, []).append(corner)

        for fi in range(nframes):
            pts = slot["geo"][fi]
            if pts is None:
                continue

            # where each moved corner now sits along its own edge
            cut = {}
            for corner, entry in mine.items():
                hit = entry["at"].get(fi)
                a = where.get(entry["inside"])
                b = where.get(corner)
                if hit is None or a is None or b is None:
                    continue
                cut[corner] = (_along(hit["x"], hit["y"], pts[a * 2], pts[a * 2 + 1],
                                      pts[b * 2], pts[b * 2 + 1]),
                               a, b, hit)

            out = []
            for kind, idx, src in layout:
                entry = mine.get(src) if src is not None else None
                hit = entry["at"].get(fi) if entry else None
                if kind == "edge":
                    if hit is None:                 # not a silhouette this frame
                        out += [pts[idx * 2], pts[idx * 2 + 1]]
                    else:
                        out += [hit["x"], hit["y"]]
                    continue
                x, y = pts[idx * 2], pts[idx * 2 + 1]
                # the corner itself retreats when trimmed, and always when it
                # is the only point this element has there
                if hit is not None and (hit["trim"] or not entry["keep"]):
                    x, y = hit["x"], hit["y"]
                for corner in near.get(idx, ()):
                    stop = cut.get(corner)
                    if stop is None:
                        continue
                    limit, a, b, hit2 = stop
                    if _along(x, y, pts[a * 2], pts[a * 2 + 1],
                              pts[b * 2], pts[b * 2 + 1]) > limit:
                        x, y = hit2["x"], hit2["y"]
                out += [x, y]
            slot["geo"][fi] = out

        if el.smooth is not None:
            flags = [bool(el.smooth[idx]) if kind == "keep" else False
                     for kind, idx, _s in layout]
            el.smooth = np.array(flags, dtype=bool) if any(flags) else None
        slot["origin"] = [origin[idx] if kind == "keep" else None
                          for kind, idx, _s in layout]
        added += len(layout) - el.npts
        el.npts = len(layout)
        slot["npts"] = el.npts
        touched += 1

    return {"corners": len(moves), "elements": touched, "points": added}
