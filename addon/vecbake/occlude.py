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

    hidden = 0
    for fi in range(nframes):
        live = [el for el in fills if slots[el.key]["vis"][fi] > 0.0]
        if not live:
            continue
        # Nearest first. Bigger depth is further away -- see ir.Element.
        live.sort(key=lambda el: slots[el.key]["depth"][fi])

        cover = np.zeros((gh, gw), dtype=bool)
        full = False
        drop = set()

        for el in live:
            slot = slots[el.key]
            v = slot["geo"][fi]
            if not v:
                continue
            xs = np.asarray(v[0::2], dtype=np.float64)
            ys = np.asarray(v[1::2], dtype=np.float64)

            x0 = max(0, int(np.floor(xs.min() / cw)))
            x1 = min(gw, int(np.ceil(xs.max() / cw)))
            y0 = max(0, int(np.floor(ys.min() / chh)))
            y1 = min(gh, int(np.ceil(ys.max() / chh)))
            if x1 <= x0 or y1 <= y0:
                continue                      # off canvas; not this pass's job

            block = cover[y0:y1, x0:x1]       # a view, so |= writes through

            # Two early outs, and they are the whole reason this is affordable
            # on a dense scene. Once the near geometry has filled the grid,
            # everything behind it costs one bounding box test and no polygon
            # work at all.
            if full or block.all():
                drop.add(el.key)
                slot["vis"][fi] = 0.0
                hidden += 1
                continue

            gx, gy = np.meshgrid(cx[x0:x1], cy[y0:y1])
            hit = _inside(gx.ravel(), gy.ravel(), xs, ys).reshape(gy.shape)
            if not hit.any():
                continue                      # thinner than one cell -- keep it

            if not (hit & ~block).any():
                drop.add(el.key)
                slot["vis"][fi] = 0.0
                hidden += 1
                continue

            if slot["vis"][fi] >= 0.999:      # only an opaque fill hides things
                block |= hit
                if cover.all():
                    full = True

        # Take them out of the ordering pass as well. This is the saving that
        # matters: order.resolve compares surviving elements pairwise.
        if drop and frame_items and frame_items[fi]:
            frame_items[fi][:] = [it for it in frame_items[fi]
                                  if it["key"] not in drop]

    culled = sum(1 for el in fills if slots[el.key]["vis"].max() <= 0.0)
    return {"culled": culled, "hidden": hidden, "cells": gw * gh}
