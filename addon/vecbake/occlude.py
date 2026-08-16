# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""Drop fills that never show a pixel.

Backface culling removes what faces away. Offscreen culling removes what is out
of frame. Neither removes a wall standing directly behind another wall, and in
a dense scene that is nearly everything: a city of 6,000 boxes paints every
canvas pixel about fifty times over, and all but one of those paints is thrown
away again by the painter's algorithm at the end.

So this walks each frame front to back against a coarse coverage grid. A fill
that never lands on an uncovered cell, in any frame, is not shipped -- and it
is removed from the ordering pass too, which is where most of the saving is:
resolving paint order costs roughly the square of what survives.

All or nothing, per element. A fill is removed from the whole clip or kept for
the whole clip -- never hidden for part of it. Per-frame hiding looks like the
larger saving and produces two artefacts that are worse than the cost: an
element hidden at one keyframe and shown at the next fades across the gap
rather than appearing, and an element ordered in some frames but not others
carries an integer rank at one end of a segment and a raw camera distance at
the other. Both read as flicker, and the second corrupts paint order outright.

Approximate by construction. The grid has a resolution, and something visible
only through a gap narrower than one cell can be missed. Three rules keep the
error on the side of keeping too much:

  * only a fully opaque fill marks coverage, so anything translucent still lets
    what is behind it count as visible;
  * a fill too small to contain one cell centre is never culled -- there is no
    evidence either way, and guessing would pop it out of the scene;
  * strokes are neither culled nor allowed to occlude. Hidden-line removal
    already decides which edges are behind geometry, and it does it against the
    real mesh rather than a grid.
"""

from __future__ import annotations

import numpy as np

from .ir import FILL


def _inside(px, py, xs, ys):
    """Even-odd point-in-polygon, vectorised over the sample points.

    Handles the concave outlines a merged fill or a thinned face can produce;
    a half-plane test would be faster but only correct for convex ones.
    """
    inside = np.zeros(px.shape, dtype=bool)
    n = len(xs)
    j = n - 1
    for i in range(n):
        yi, yj = ys[i], ys[j]
        straddles = (yi > py) != (yj > py)
        if straddles.any():
            dy = yj - yi
            # `straddles` is already false for a horizontal edge, so this only
            # guards the division itself
            dy = dy if dy != 0.0 else 1e-30
            cut = (xs[j] - xs[i]) * (py - yi) / dy + xs[i]
            inside ^= straddles & (px < cut)
        j = i
    return inside


def apply(elements, slots, frame_items, settings, nframes, width, height):
    """Zero the visibility of every fill hidden in every frame.

    `_finish` drops elements whose visibility never rises above zero, so
    clearing the flag is all the removal that is needed here.
    """
    cell = float(max(1.0, settings.occlusion_px))
    gw = max(1, int(round(width / cell)))
    gh = max(1, int(round(height / cell)))
    cw = width / gw
    chh = height / gh
    cx = (np.arange(gw) + 0.5) * cw
    cy = (np.arange(gh) + 0.5) * chh

    fills = [el for el in elements if el.kind == FILL]
    if not fills:
        return {"culled": 0, "hidden": 0, "cells": 0}

    # Frames in which each fill shows at least one pixel. Counted, never acted
    # on mid-walk: an element is removed for the whole clip or not at all.
    #
    # Hiding an element for *some* frames looks like the bigger win and is a
    # trap. Visibility becomes an opacity track, and the player interpolates
    # it, so a fill hidden at one keyframe and visible at the next fades in
    # across the entire gap instead of appearing -- translucent while it is
    # the frontmost thing on screen. Worse, dropping it from the ordering pass
    # for those frames leaves its depth as a raw camera distance while every
    # ordered element carries an integer rank, and interpolating between the
    # two is meaningless. Both faults read as flicker.
    seen = {el.key: 0 for el in fills}
    hidden = 0

    for fi in range(nframes):
        live = [el for el in fills if slots[el.key]["vis"][fi] > 0.0]
        if not live:
            continue
        # Nearest first. Bigger depth is further away -- see ir.Element.
        live.sort(key=lambda el: slots[el.key]["depth"][fi])

        cover = np.zeros((gh, gw), dtype=bool)
        full = False

        for el in live:
            slot = slots[el.key]
            v = slot["geo"][fi]
            if not v:
                seen[el.key] += 1             # no geometry to judge -- keep it
                continue
            xs = np.asarray(v[0::2], dtype=np.float64)
            ys = np.asarray(v[1::2], dtype=np.float64)

            x0 = max(0, int(np.floor(xs.min() / cw)))
            x1 = min(gw, int(np.ceil(xs.max() / cw)))
            y0 = max(0, int(np.floor(ys.min() / chh)))
            y1 = min(gh, int(np.ceil(ys.max() / chh)))
            if x1 <= x0 or y1 <= y0:
                # Outside the canvas, which is Cull Offscreen's decision, not
                # this one. It deliberately keeps a margin so geometry crossing
                # the frame edge still draws; counting these as hidden would
                # overrule that and pop things at the border.
                seen[el.key] += 1
                continue

            block = cover[y0:y1, x0:x1]       # a view, so |= writes through

            # Two early outs, and they are the whole reason this is affordable
            # on a dense scene. Once the near geometry has filled the grid,
            # everything behind it costs one bounding box test and no polygon
            # work at all.
            if full or block.all():
                hidden += 1
                continue

            gx, gy = np.meshgrid(cx[x0:x1], cy[y0:y1])
            hit = _inside(gx.ravel(), gy.ravel(), xs, ys).reshape(gy.shape)
            if not hit.any():
                seen[el.key] += 1             # thinner than one cell -- keep it
                continue

            if not (hit & ~block).any():
                hidden += 1
                continue

            seen[el.key] += 1
            if slot["vis"][fi] >= 0.999:      # only an opaque fill hides things
                block |= hit
                if cover.all():
                    full = True

    # Nothing above touched a slot. Only fills that showed nothing in any frame
    # are removed, and they are removed everywhere at once -- so no element
    # ever gains a fade, and no element is ordered in one frame and not another.
    gone = {k for k, n in seen.items() if n == 0}
    for key in gone:
        slots[key]["vis"][:] = 0.0
    if gone and frame_items:
        for items in frame_items:
            if items:
                items[:] = [it for it in items if it["key"] not in gone]

    return {"culled": len(gone), "hidden": hidden, "cells": gw * gh}
