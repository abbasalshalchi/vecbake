# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""Which vertices a path bends smoothly through.

A baked circle is a 32-gon, and no tolerance setting fixes that: the points
*are* the shape. Drawing a curve through them does, and it costs nothing --
the control points are derived from the points either side, so nothing extra
is stored and nothing extra animates.

That last part is the whole reason this is a per-vertex *mask* rather than
fitted Bezier control points. Fitted controls would have to be stored and
interpolated like any other geometry, which doubles the data and lets the
handles wobble independently of the curve they belong to. Deriving them at
draw time from neighbouring points keeps one source of truth.

Stability
---------
The mask is decided **once** for the whole bake, never per frame. If a vertex
could be a corner in one frame and smooth in the next, the shape would visibly
pop as the camera moved. So the 2D test asks whether a vertex is sharp in *any*
frame, not in the current one: a real corner that happens to project straight
in one view stays a corner.

The tangent
-----------
Uniform Catmull-Rom, written as a cubic Bezier. At vertex i:

    T[i] = (P[i+1] - P[i-1]) / 2      smooth
    T[i] = 0                          corner

and the segment i -> i+1 is a cubic with controls P[i] + T[i]/3 and
P[i+1] - T[i+1]/3. Both tangents zero collapses that to a straight line, so a
run of corners renders exactly as the polyline it replaced -- which is what
makes NONE and a fully-corner mask identical, and lets the two mix freely
within one path. `emit_svg.py` and the web player both implement this same
formula; if you change it here, change it in all three.
"""

from __future__ import annotations

import numpy as np

# curve_mode values
NONE = "NONE"
SMOOTH = "SMOOTH"
ANGLE = "ANGLE"
SHADING = "SHADING"

MODES = (NONE, SMOOTH, ANGLE, SHADING)

# No vertex turning more than this is ever drawn as a curve, in any mode.
#
# A curve through a hard corner cannot stay inside the shape. It has to pass
# *through* the corner, so between two corners it bows outward: smoothing the
# four 90-degree corners of a cylinder's side quad turns the flat face into a
# barrel that overflows its own vertical edges. There is no tangent scaling
# that avoids this -- only not smoothing the corner does.
#
# 80 degrees keeps quads (90) and triangles (120) flat while still rounding a
# cylinder as coarse as five sides (72). Genuinely faceted hard-surface shapes
# that fall under it -- a hex nut at 60 -- are what SHADING mode is for: they
# are normally shade-flat, and it will leave them alone.
HARD_CORNER = 80.0


def turn_angles(points, closed):
    """Degrees the path turns at each vertex. Straight is 0.

    Endpoints of an open path come back as inf: a path cannot bend through a
    vertex that has nothing on one side of it.
    """
    P = np.asarray(points, dtype=np.float64)
    n = len(P)
    out = np.full(n, np.inf)
    if n < 3:
        return out

    if closed:
        a, b, c = np.roll(P, 1, axis=0), P, np.roll(P, -1, axis=0)
        target = np.arange(n)
    else:
        a, b, c = P[:-2], P[1:-1], P[2:]
        target = np.arange(1, n - 1)

    u, v = b - a, c - b
    nu = np.linalg.norm(u, axis=1)
    nv = np.linalg.norm(v, axis=1)
    ok = (nu > 1e-12) & (nv > 1e-12)

    cos = np.ones(len(u))
    cos[ok] = (u[ok] * v[ok]).sum(axis=1) / (nu[ok] * nv[ok])
    ang = np.degrees(np.arccos(np.clip(cos, -1.0, 1.0)))
    # a repeated point is not a turn, it is a duplicate
    ang[~ok] = 0.0
    out[target] = ang
    return out


def _mask_2d(slot, npts, closed, threshold):
    """Sharp in any frame wins. See "Stability" above."""
    mask = np.ones(npts, dtype=bool)
    seen = False
    for frame in slot["geo"]:
        if frame is None:
            continue
        pts = np.asarray(frame, dtype=np.float64).reshape(-1, 2)
        if len(pts) != npts:
            continue
        seen = True
        mask &= turn_angles(pts, closed) <= threshold
    if not seen:
        return np.zeros(npts, dtype=bool)
    if not closed:
        mask[0] = False
        mask[-1] = False
    return mask


def _mask_3d(plan, verts, closed, threshold):
    """None when the element's mesh vertices are not known, so 2D can stand in."""
    co = getattr(plan, "vert_co_world", None) if plan is not None else None
    if co is None or verts is None or len(verts) < 3:
        return None
    mask = turn_angles(co[np.asarray(verts, dtype=np.int64)], closed) <= threshold
    if not closed:
        mask[0] = False
        mask[-1] = False
    return mask


def _mask(plan, verts, slot, npts, closed, threshold, prefer_3d):
    """Smooth mask from the shape itself where possible, else from the screen.

    3D is the better judge: it describes the geometry rather than one view of
    it, so a face seen edge-on is not mistaken for a straight line. It is only
    available for elements whose mesh vertices we tracked, which is why the 2D
    test remains the fallback rather than an error.
    """
    if prefer_3d:
        mask = _mask_3d(plan, verts, closed, threshold)
        if mask is not None and len(mask) == npts:
            return mask
    if slot is None:
        return None
    return _mask_2d(slot, npts, closed, threshold)


def classify(elements, slots, element_verts, plans, settings):
    """Set `el.smooth` on every element. Returns a small report."""
    mode = getattr(settings, "curve_mode", NONE)
    if mode == NONE or mode not in MODES:
        return {"mode": NONE, "curved": 0, "vertices": 0}

    curved = 0
    vertices = 0
    for el in elements:
        npts = el.npts
        closed = el.is_closed()
        if npts < 3:
            continue                      # a segment has no interior vertex
        plan = plans.get(el.key.split("/")[0])
        slot = slots.get(el.key)
        verts = element_verts.get(el.key)

        if mode == SMOOTH:
            # "everything" still means everything that can be bent without
            # overflowing its own outline -- see HARD_CORNER.
            mask = _mask(plan, verts, slot, npts, closed, HARD_CORNER, True)
        elif mode == ANGLE:
            mask = _mask(plan, verts, slot, npts, closed,
                         min(settings.curve_angle, HARD_CORNER), False)
        else:                             # SHADING
            if plan is None:
                continue
            if closed and plan.face_sharp is not None:
                fidx = _face_index(el.key)
                if fidx is not None and fidx < len(plan.face_sharp) and plan.face_sharp[fidx]:
                    continue              # this face is shade-flat
            if plan.shade_mode == "FLAT":
                continue
            limit = (HARD_CORNER if plan.shade_mode == "SMOOTH"
                     else min(plan.shade_angle, HARD_CORNER))
            mask = _mask(plan, verts, slot, npts, closed, limit, True)

        if mask is None or not mask.any():
            continue
        el.smooth = mask
        curved += 1
        vertices += int(mask.sum())

    return {"mode": mode, "curved": curved, "vertices": vertices}


def _face_index(key):
    tail = key.rsplit("/", 1)[-1]
    if tail.startswith("f"):
        try:
            return int(tail[1:])
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# shared curve maths -- emit_svg.py mirrors this, player.js reimplements it
# ---------------------------------------------------------------------------

def tangents(P, mask, closed):
    """Catmull-Rom tangent per vertex; zero at corners."""
    P = np.asarray(P, dtype=np.float64)
    n = len(P)
    T = np.zeros((n, P.shape[1]), dtype=np.float64)
    if n < 2:
        return T
    if closed:
        T = (np.roll(P, -1, axis=0) - np.roll(P, 1, axis=0)) * 0.5
    elif n > 2:
        T[1:-1] = (P[2:] - P[:-2]) * 0.5
    if mask is not None:
        T[~np.asarray(mask, dtype=bool)] = 0.0
    return T
