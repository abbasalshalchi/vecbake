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
  * a stroke never marks coverage. It is a line, not an area: the cells it
    crosses are mostly paper, and letting it hide what is behind it would cull
    things that are plainly visible.

Strokes *are* culled, under Cull Hidden Strokes, and the reason they once were
not was wrong. The claim was that hidden-line removal already decides which
edges are behind geometry. It does not: it asks only whether an edge is behind
*its own object* -- backface culling and face adjacency -- and bake.py says so
where the cull is applied. An edge behind a *different* object survives every
test in the baker and is drawn, then painted over. On the Voyager that is the
larger half of the scene: 5641 strokes against 1724 fills.

They are held to a stricter test than fills. A fill marks a cell covered when
it contains the cell centre, which rounds a sliver of the fill's own edge into
full coverage -- harmless for another fill, and not harmless for a stroke,
because strokes lie *along* edges and that sliver is exactly where they live.
So a stroke is culled only where its cell and all four neighbours are covered.

Measured on 20 frames of the reduced Voyager, against the same bake with it off:

  strokes        5641 -> 4884   (757 culled, 13.4%)
  drawn/frame    5584 -> 5231   (-6.3%)
  delivered      0.97 -> 0.92MB gzipped

and it is **default off**, because that is not free. 0.02-0.04% of pixels change,
and they change outright rather than at the edges: short dashes go missing along
the dish's lower rim and around the bus junctions, wherever a stroke runs along
the boundary of a fill in front of it.

A wider margin does not fix that, and it is worth knowing why before anyone
tries. This pass sorts by centroid depth, and `order.resolve` exists precisely
because centroid depth is wrong -- 449 pairs of it on this scene. Culling runs
first on purpose, since every element it removes is one the ordering pass never
has to compare, so it cannot consult the answer that would correct it. A stroke
at an ambiguous depth against a large fill therefore gets buried by an ordering
the painter later disagrees with. Tuning `occlusion_px` moves the grid error and
leaves this one alone.

So: worth having where the saving matters more than a few rim fragments, and
not something to turn on by default.
"""

from __future__ import annotations

import numpy as np

from .ir import FILL, STROKE


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


def cullable(elements, settings):
    """What this pass is allowed to remove: fills always, strokes on request."""
    kinds = {FILL}
    if getattr(settings, "occlusion_strokes", False):
        kinds.add(STROKE)
    return [el for el in elements if el.kind in kinds]


def frame_grid(settings, width, height):
    """The coverage grid, as plain numbers a worker process can be handed."""
    cell = float(max(1.0, settings.occlusion_px))
    gw = max(1, int(round(width / cell)))
    gh = max(1, int(round(height / cell)))
    return {"gw": gw, "gh": gh, "cw": width / gw, "ch": height / gh}


def frame_coverage(live, grid):
    """Walk one frame's fills near to far. Returns (keys that showed, hidden).

    `live` is `(key, vis, geo)` per visible fill, **already sorted nearest
    first** -- the sort needs the depth slots, which stay in the baker.

    Split out of `apply` so it can run in a worker: every frame's answer
    depends only on that frame, and the two counters `apply` keeps are a union
    and a sum over frames. Nothing here touches a slot, which is what makes it
    safe to run somewhere the slots do not exist. See parallel.py.
    """
    gw, gh, cw, chh = grid["gw"], grid["gh"], grid["cw"], grid["ch"]
    cx = (np.arange(gw) + 0.5) * cw
    cy = (np.arange(gh) + 0.5) * chh

    cover = np.zeros((gh, gw), dtype=bool)
    full = False
    seen = []
    hidden = 0

    for key, vis, v, is_stroke in live:
        if not v:
            seen.append(key)                  # no geometry to judge -- keep it
            continue
        xs = np.asarray(v[0::2], dtype=np.float64)
        ys = np.asarray(v[1::2], dtype=np.float64)

        if is_stroke:
            if full or _stroke_buried(xs, ys, cover, cw, chh, gw, gh):
                hidden += 1
            else:
                seen.append(key)
            continue                          # a stroke never marks coverage

        x0 = max(0, int(np.floor(xs.min() / cw)))
        x1 = min(gw, int(np.ceil(xs.max() / cw)))
        y0 = max(0, int(np.floor(ys.min() / chh)))
        y1 = min(gh, int(np.ceil(ys.max() / chh)))
        if x1 <= x0 or y1 <= y0:
            # Outside the canvas, which is Cull Offscreen's decision, not
            # this one. It deliberately keeps a margin so geometry crossing
            # the frame edge still draws; counting these as hidden would
            # overrule that and pop things at the border.
            seen.append(key)
            continue

        block = cover[y0:y1, x0:x1]           # a view, so |= writes through

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
            seen.append(key)                  # thinner than one cell -- keep it
            continue

        if not (hit & ~block).any():
            hidden += 1
            continue

        seen.append(key)
        if vis >= 0.999:                      # only an opaque fill hides things
            block |= hit
            if cover.all():
                full = True

    return seen, hidden


def _stroke_buried(xs, ys, cover, cw, ch, gw, gh, step=0.5):
    """Is every point along this polyline under covered grid, with a margin?

    Walked as points rather than filled as an area, because a stroke has no
    interior -- the question is which cells the line passes through. Samples sit
    half a cell apart so a segment cannot skip over one.

    The margin is the point of it. `cover` marks a cell when a fill contained
    its centre, so a cell straddling a fill's own boundary reads as fully
    covered when it is partly paper. Strokes run along boundaries, so testing
    the bare cell would cull the outlines of things standing in front of a
    larger shape. Requiring the four neighbours as well pulls the test one cell
    back from every coverage edge; anything outside falls out as visible.
    """
    for i in range(len(xs) - 1):
        x0, y0, x1, y1 = xs[i], ys[i], xs[i + 1], ys[i + 1]
        span = max(abs(x1 - x0) / cw, abs(y1 - y0) / ch)
        n = max(1, int(np.ceil(span / step)))
        for k in range(n + 1):
            u = k / n
            gx = int((x0 + (x1 - x0) * u) / cw)
            gy = int((y0 + (y1 - y0) * u) / ch)
            if gx < 0 or gy < 0 or gx >= gw or gy >= gh:
                return False                  # off the grid: not evidence of cover
            if not cover[gy, gx]:
                return False
            # the margin: one cell back from any coverage edge
            if (gx == 0 or gy == 0 or gx == gw - 1 or gy == gh - 1
                    or not cover[gy, gx - 1] or not cover[gy, gx + 1]
                    or not cover[gy - 1, gx] or not cover[gy + 1, gx]):
                return False
    return True


def live_elements(items, slots, fi):
    """This frame's visible items as `(key, vis, geo, is_stroke)`, nearest first.

    Strokes sort by the same depth the painter will use, which already carries
    the bias that puts an edge in front of the faces it borders -- so an object's
    own outline is walked before its fills and can never be culled by them. Only
    another object, standing nearer, can bury it.
    """
    live = [el for el in items if slots[el.key]["vis"][fi] > 0.0]
    # Nearest first. Bigger depth is further away -- see ir.Element.
    live.sort(key=lambda el: slots[el.key]["depth"][fi])
    return [(el.key, float(slots[el.key]["vis"][fi]), slots[el.key]["geo"][fi],
             el.kind == STROKE)
            for el in live]


def finish(items, seen, hidden, slots, frame_items, grid):
    """Remove the elements that showed nothing, in any frame, anywhere."""
    # Nothing above touched a slot. Only fills that showed nothing in any frame
    # are removed, and they are removed everywhere at once -- so no element
    # ever gains a fade, and no element is ordered in one frame and not another.
    gone = {el.key for el in items if el.key not in seen}
    for key in gone:
        slots[key]["vis"][:] = 0.0
    if gone and frame_items:
        for items in frame_items:
            if items:
                items[:] = [it for it in items if it["key"] not in gone]
    return {"culled": len(gone), "hidden": hidden,
            "cells": grid["gw"] * grid["gh"]}


def apply(elements, slots, frame_items, settings, nframes, width, height):
    """Zero the visibility of every fill hidden in every frame.

    `_finish` drops elements whose visibility never rises above zero, so
    clearing the flag is all the removal that is needed here.
    """
    grid = frame_grid(settings, width, height)

    items = cullable(elements, settings)
    if not items:
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
    seen: set = set()
    hidden = 0

    for fi in range(nframes):
        live = live_elements(items, slots, fi)
        if not live:
            continue
        showed, count = frame_coverage(live, grid)
        seen.update(showed)
        hidden += count

    return finish(items, seen, hidden, slots, frame_items, grid)
