# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""Audit a bake for occlusion errors, against the 3D scene as ground truth.

Reachable from the panel: **Audit > Check Occlusion**. It bakes with the
settings already in the panel, then ray-casts the result against the scene and
lists what is painted with the wrong thing. The findings land in the panel
itself, most costly first.

Why this exists
---------------
Every occlusion bug in this project so far was found by a person looking at a
frame and saying "that line should not be there". That does not scale and it
does not tell you when you are finished. Worse, the obvious automatic check --
sample pixels at random and compare -- finds nothing: a stray line is a few
dozen pixels out of a few hundred thousand, so 7000 random samples returned a
clean sweep on a bake with four real errors in it.

The trick is where to look. An ordering error can only exist where two objects
overlap on screen, which is a tiny fraction of the canvas, so that is the only
part worth testing. Restricted that way the same budget of ray casts finds the
errors instead of missing them.

Ground truth is `scene.ray_cast`. For each candidate pixel it asks what surface
is really nearest, and compares against what the bake painted there.

What counts as wrong
--------------------
An element is wrong at a pixel when all three hold:

  * the pixel is painted by that element -- it is the top of the paint order;
  * the nearest real surface belongs to a *different* object;
  * that surface is nearer than the element's own object is at the same pixel,
    by more than `margin`.

The third condition is what keeps the report honest. A stroke is drawn with
width, so half of it hangs over the silhouette of its own object and the ray
there hits whatever is behind -- correctly. Comparing object identity alone
reports every outline in the scene as an error; comparing depth does not.
`margin` also absorbs surfaces that touch, where neither answer is wrong.

Reading the report
------------------
Errors are grouped by element and ranked by how many pixels they cost. Each one
is diagnosed by re-deriving what the ordering pass had to work with:

  no constraint    the pair never got compared -- a bug, and a fixable one
  order violated   there was a constraint, pointing the right way, and the
                   ranking broke it: dropped to break a cycle
  measured wrong   the constraint exists and points the wrong way, because the
                   two elements *cross*: one is in front over part of the
                   overlap and behind over the rest, and a single depth per
                   element cannot say that

`measured wrong` is not a bug to fix in the baker. It is a modelling signal,
and the report names the culprit: the face whose depth range spans the thing
crossing it. Splitting that face is what gives the ordering enough resolution
to be right, and that is the artist's call, not the baker's.
"""

from __future__ import annotations

import math
import time
from dataclasses import replace

import numpy as np

import bpy

from . import api
from . import bake as bake_mod
from . import order as order_mod
from .settings import BakeSettings


FILL_KINDS = (1, "fill")


# ---------------------------------------------------------------------------
# rasterising
# ---------------------------------------------------------------------------

def _poly_mask(g, gx, gy):
    xs, ys = g[0::2], g[1::2]
    inside = np.zeros(gx.shape, dtype=bool)
    j = len(xs) - 1
    for i in range(len(xs)):
        yi, yj = ys[i], ys[j]
        straddles = (yi > gy) != (yj > gy)
        if straddles.any():
            dy = (yj - yi) or 1e-30
            inside ^= straddles & (gx < (xs[j] - xs[i]) * (gy - yi) / dy + xs[i])
        j = i
    return inside


def _stroke_mask(g, width, gx, gy, closed=False):
    """Pixels within half a stroke width of the polyline.

    `closed` matters more than it looks. A chain that comes back on itself is
    emitted with `cl:1` and the player calls closePath(), so it draws one more
    segment than the point list spells out -- the wrap from the last point back
    to the first. Walking the explicit segments alone leaves that one invisible
    to every check built on this, and it is not a short one: on the Voyager
    probe the bus rim's closing segment runs 126px straight across the feed-horn
    shaft. Three separate audits reported the frame clean because of it.
    """
    mask = np.zeros(gx.shape, dtype=bool)
    half = max(width, 1.0) / 2.0 + 0.35        # +half a pixel for coverage
    spans = [(g[k], g[k + 1], g[k + 2], g[k + 3]) for k in range(0, len(g) - 2, 2)]
    if closed and len(g) >= 4:
        spans.append((g[-2], g[-1], g[0], g[1]))
    for ax, ay, bx, by in spans:
        dx, dy = bx - ax, by - ay
        span = dx * dx + dy * dy
        if span < 1e-12:
            continue
        t = np.clip(((gx - ax) * dx + (gy - ay) * dy) / span, 0.0, 1.0)
        mask |= ((gx - (ax + dx * t)) ** 2 + (gy - (ay + dy * t)) ** 2) <= half * half
    return mask


def _element_mask(el, g, gx, gy):
    if el.kind in FILL_KINDS:
        return _poly_mask(g, gx, gy)
    return _stroke_mask(g, getattr(el, "width", 1.6) or 1.6, gx, gy,
                        bool(getattr(el, "closed", False)))


def _bbox(g):
    return (g[0::2].min(), g[0::2].max(), g[1::2].min(), g[1::2].max())


def _paint(live, width, height):
    """Paint one frame onto the canvas. Returns (topmost element, shared).

    `top` holds the index of the element the player would leave on each pixel,
    and `shared` marks the pixels two different objects both cover -- the only
    pixels where paint order can be wrong at all.

    This used to be done inside a rectangle per *pair* of objects, which is
    quadratic in the object count and unusable the moment a scene stops being a
    test scene: the Voyager probe has 304 objects, so 46,000 rectangles a frame,
    most of them overlapping each other, each re-rasterising every element that
    reaches into it. Nothing was wrong with the answer -- the audit simply never
    finished, and for the one measurement that grades every other, never
    finishing is the same as being wrong.

    Painting the canvas once costs the sum of the element bounding boxes
    instead, which is what the work actually is. Bounds are clipped to the
    canvas, and that is not pedantry: geometry is deliberately kept past the
    frame edge so it still draws when it crosses back in, so element boxes run
    well outside it, and a pixel nobody will ever see reported as the worst
    error in the scene is worse than no report at all. The first version of
    this ranked one at y=1040 on a 1000px canvas.
    """
    w, h = int(width), int(height)
    top = np.full((h, w), -1, dtype=np.int32)
    # Which object claimed a pixel first, and whether a second one has since
    # claimed it too. Counting distinct objects per pixel would need a mask per
    # object -- 304 canvas-sized masks on this scene -- and "at least two" is
    # the whole of the answer wanted.
    first = np.full((h, w), -1, dtype=np.int32)
    shared = np.zeros((h, w), dtype=bool)
    ids = {}
    # far to near, so the last write is what the player draws
    for i in sorted(range(len(live)), key=lambda k: -live[k][2]):
        el, g, _d = live[i]
        pad = 1.0 + (0.0 if el.kind in FILL_KINDS
                     else (getattr(el, "width", 1.6) or 1.6) / 2.0)
        bx0, bx1, by0, by1 = _bbox(g)
        x0 = max(int(math.floor(bx0 - pad)), 0)
        x1 = min(int(math.ceil(bx1 + pad)) + 1, w)
        y0 = max(int(math.floor(by0 - pad)), 0)
        y1 = min(int(math.ceil(by1 + pad)) + 1, h)
        if x1 <= x0 or y1 <= y0:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1) + 0.5, np.arange(y0, y1) + 0.5)
        mask = _element_mask(el, g, gx, gy)
        if not mask.any():
            continue
        gid = ids.setdefault(el.group, len(ids))
        held = first[y0:y1, x0:x1]
        shared[y0:y1, x0:x1] |= mask & (held >= 0) & (held != gid)
        held[mask & (held < 0)] = gid
        top[y0:y1, x0:x1][mask] = i
    return top, shared


# ---------------------------------------------------------------------------
# ground truth
# ---------------------------------------------------------------------------

class _Truth:
    """scene.ray_cast, wrapped so a pixel maps to (object, depth)."""

    def __init__(self, scene, width, height):
        self.scene = scene
        self.w, self.h = width, height
        depsgraph = bpy.context.evaluated_depsgraph_get()
        self.dg = depsgraph
        cam = scene.camera.evaluated_get(depsgraph)
        self.view = np.array(cam.matrix_world.inverted(), dtype=np.float64)
        self.tr, self.br, self.bl, self.tl = cam.data.view_frame(scene=scene)
        self.origin = cam.matrix_world.translation
        self.mw = cam.matrix_world
        self.objs = {o.name: o.evaluated_get(depsgraph)
                     for o in scene.objects if o.type == "MESH"}

    def _depth(self, point):
        p = np.asarray(point, dtype=np.float64)
        return float(-(p @ self.view[:3, :3].T + self.view[:3, 3])[2])

    def _dir(self, px, py):
        u, v = px / self.w, 1.0 - py / self.h
        corner = self.bl + (self.br - self.bl) * u + (self.tl - self.bl) * v
        return ((self.mw @ corner) - self.origin).normalized()

    def nearest(self, px, py):
        d = self._dir(px, py)
        ok, loc, _n, _f, obj, _m = self.scene.ray_cast(self.dg, self.origin, d)
        if not ok or obj is None:
            return None, None
        return obj.name, self._depth(loc)

    def surface_of(self, name, px, py):
        """Depth of one object's own surface at this pixel, or None."""
        obj = self.objs.get(name)
        if obj is None:
            return None
        inv = obj.matrix_world.inverted()
        d = self._dir(px, py)
        ok, loc, _n, _i = obj.ray_cast(inv @ self.origin, (inv.to_3x3() @ d).normalized())
        if not ok:
            return None
        return self._depth(obj.matrix_world @ loc)


# ---------------------------------------------------------------------------
# diagnosis
# ---------------------------------------------------------------------------

def _prep(items, is_ortho):
    """The derived fields resolve() adds before it starts comparing."""
    out = [dict(it) for it in items]
    for it in out:
        pts = it["pts"]
        it["bbox"] = (pts[:, 0].min(), pts[:, 0].max(), pts[:, 1].min(), pts[:, 1].max())
        it["dmin"] = float(it["depths"].min())
        it["dmax"] = float(it["depths"].max())
        it["field"] = (order_mod._fit_field(pts, it["depths"], is_ortho)
                       if it["kind"] == order_mod.FILL_KIND else None)
    return out


def _diagnose(items, is_ortho, key, occluder, px, py):
    """Why was `key` painted over something belonging to `occluder`?"""
    prepped = _prep(items, is_ortho)
    index = {it["key"]: i for i, it in enumerate(prepped)}
    if key not in index:
        return {"cause": "element not in this frame's ordering pass"}
    mine = prepped[index[key]]

    # which of the occluder's fills actually covers that pixel
    qx, qy = np.array([px]), np.array([py])
    covering = [it for it in prepped
                if it["kind"] == order_mod.FILL_KIND
                and it["key"].split("/")[0] == occluder
                and order_mod._inside(it, qx, qy)[0]]
    if not covering:
        # The occluder is really in front here, yet none of its fills is drawn
        # over this pixel. That is not an ordering question at all -- the
        # geometry that should be covering it never reached the paint order, so
        # look at what removed it rather than at how it was sorted.
        present = [it for it in prepped if it["key"].split("/")[0] == occluder]
        return {"cause": "occluder not drawn there",
                "occluder_elements_this_frame": len(present),
                "fix": ("the occluding surface is missing from the frame, not "
                        "mis-sorted -- suspect a culling pass (Occlusion Cull, "
                        "Backface Cull) rather than the ordering")}
    theirs = min(covering, key=lambda it: it["dmin"])

    ranks = order_mod.resolve([dict(it) for it in items], is_ortho)
    shared = order_mod._shared_samples(theirs, mine)
    out = {"occluding_element": theirs["key"],
           "its_depth_span": round(theirs["dmax"] - theirs["dmin"], 3),
           "my_depth_span": round(mine["dmax"] - mine["dmin"], 3)}

    if shared is None:
        out["cause"] = "no constraint"
        out["fix"] = "the pair was never compared -- a baker bug"
        return out

    sx, sy = shared
    delta = float(np.mean(order_mod._depths_at(theirs, sx, sy, is_ortho)
                          - order_mod._depths_at(mine, sx, sy, is_ortho)))
    out["shared_samples"] = int(len(sx))
    out["measured_delta"] = round(delta, 4)
    wants_me_behind = delta < 0
    i_am_on_top = ranks[index[key]] < ranks[index[theirs["key"]]]

    if wants_me_behind and i_am_on_top:
        out["cause"] = "order violated"
        out["fix"] = "constraint was right and got dropped to break a cycle"
    elif not wants_me_behind:
        out["cause"] = "measured wrong"
        wide = theirs if theirs["dmax"] - theirs["dmin"] > mine["dmax"] - mine["dmin"] else mine
        out["crossing_element"] = wide["key"]
        out["fix"] = (f"{wide['key']} spans {wide['dmax'] - wide['dmin']:.2f} units of depth "
                      f"and crosses the other -- split that face in Blender")
    else:
        out["cause"] = "ordered correctly here; the pixel may be a coverage edge"
    return out


# ---------------------------------------------------------------------------
# the check
# ---------------------------------------------------------------------------

def run(settings=None, frames=None, margin=0.05, budget=1200, diagnose=True,
        context=None, verbose=True):
    """Bake, then audit every frame for pixels painted with the wrong thing.

    `budget` caps how many overlap pixels are ray cast per frame. They are taken
    evenly across the candidate set, so the count reported is a sample, not a
    census -- raise it for a closer look at a frame that shows something.
    """
    context = context or bpy.context
    scene = context.scene
    props = getattr(scene, "vecbake", None)
    if settings is None:
        settings = props.to_bake_settings(context) if props else BakeSettings()

    captured = []
    real_resolve = order_mod.resolve

    def spy(items, is_ortho=False, tol=1e-4):
        captured.append(([dict(it) for it in items], is_ortho))
        return real_resolve(items, is_ortho, tol)

    started = time.perf_counter()
    if diagnose:
        # The diagnosis works by watching what the ordering pass was given, and
        # it can only watch a call made in this process. Once ordering moved to
        # a worker pool the spy stopped firing and every fault came back with
        # no cause -- silently, because a missing diagnosis reads the same as
        # an undiagnosable one. Ask for one worker, which runs it here.
        settings = replace(settings, order_workers=1)
        order_mod.resolve = spy
    try:
        vec = api.bake_scene(context, settings)
    finally:
        order_mod.resolve = real_resolve
    baked = time.perf_counter() - started

    n_frames = vec.meta["frames"]
    duration = vec.duration
    if frames is None:
        frames = range(n_frames)
    frames = [f for f in frames if 0 <= f < n_frames]
    original = scene.frame_current
    start = int(vec.meta.get("frame_start", 1))
    step = int(vec.meta.get("frame_step", 1))

    errors = {}
    tested = 0
    available = 0
    try:
        for fi in frames:
            t = duration * fi / (n_frames - 1) if n_frames > 1 else 0.0
            live = [(el, np.array(el.geo.sample(t)), el.depth.sample(t))
                    for el in vec.elements if el.opacity.sample(t) > 0.5]
            if len(live) < 2:
                continue
            scene.frame_set(start + fi * step)
            truth = _Truth(scene, vec.width, vec.height)

            top, shared = _paint(live, vec.width, vec.height)
            # Only pixels two objects actually cover. Where their *boxes* merely
            # meet is mostly pixels neither one is on, and spending the budget
            # evenly over that dilutes the real overlap until nothing is hit: an
            # earlier version found zero faults on a bake with four known ones.
            ys, xs = np.nonzero(shared & (top >= 0))
            available += len(xs)
            if len(xs) > budget:
                keep = np.linspace(0, len(xs) - 1, budget).astype(int)
                ys, xs = ys[keep], xs[keep]
            candidates = [(float(x) + 0.5, float(y) + 0.5, live[int(top[y, x])][0])
                          for x, y in zip(xs, ys)]

            for px, py, el in candidates:
                tested += 1
                name, real_depth = truth.nearest(px, py)
                if name is None or name == el.group:
                    continue
                own = truth.surface_of(el.group, px, py)
                if own is None:
                    continue                      # stroke width past its own edge
                gap = own - real_depth
                if gap <= margin:
                    continue
                rec = errors.setdefault((el.key, name), {
                    "element": el.key, "kind": "fill" if el.kind in FILL_KINDS else "stroke",
                    "occluder": name, "pixels": 0, "frames": {}, "worst_gap": 0.0,
                    "worst_at": None})
                rec["pixels"] += 1
                rec["frames"][fi] = rec["frames"].get(fi, 0) + 1
                if gap > rec["worst_gap"]:
                    rec["worst_gap"] = gap
                    rec["worst_at"] = (fi, px, py)
    finally:
        scene.frame_set(original)

    ranked = sorted(errors.values(), key=lambda r: (-r["pixels"], -r["worst_gap"]))
    if diagnose:
        for rec in ranked:
            fi, px, py = rec["worst_at"]
            if fi < len(captured):
                items, is_ortho = captured[fi]
                rec["diagnosis"] = _diagnose(items, is_ortho, rec["element"],
                                             rec["occluder"], px, py)

    # The pixel counts are a sample, not a census, and the difference matters
    # when reading them: a fault concentrated in one small region is reported at
    # the sampling fraction, so 159 real pixels came back as 12. The ranking is
    # unaffected -- every error is scaled the same -- but the absolute numbers
    # only mean something next to this.
    coverage = 100.0 * tested / max(available, 1)
    report = {
        "elements": len(vec.elements),
        "frames_checked": len(frames),
        "overlap_pixels_available": available,
        "overlap_pixels_tested": tested,
        "sampled_pct": round(coverage, 1),
        "wrong_pixels_estimated_total": int(round(sum(r["pixels"] for r in ranked)
                                                  * 100.0 / max(coverage, 1e-9))),
        "wrong_pixels": sum(r["pixels"] for r in ranked),
        "rate_pct": round(100.0 * sum(r["pixels"] for r in ranked) / max(tested, 1), 3),
        "errors": ranked,
        "bake_seconds": round(baked, 1),
        "total_seconds": round(time.perf_counter() - started, 1),
    }
    if verbose:
        _print(report)
    return report


def _print(report):
    print()
    print(f"checked {report['frames_checked']} frames, "
          f"{report['overlap_pixels_tested']} of {report['overlap_pixels_available']} "
          f"overlap pixels ({report['sampled_pct']}%), "
          f"{report['bake_seconds']}s bake / {report['total_seconds']}s total")
    if not report["errors"]:
        print("no occlusion errors found")
        return
    print(f"{report['wrong_pixels']} wrong ({report['rate_pct']}% of what was tested"
          f" -- about {report['wrong_pixels_estimated_total']} across the whole overlap), "
          f"{len(report['errors'])} distinct")
    print()
    print(f"  {'element':<26} {'kind':<7} {'over':<18} {'px':>5} {'gap':>7}  cause")
    for rec in report["errors"]:
        cause = (rec.get("diagnosis") or {}).get("cause", "-")
        print(f"  {rec['element']:<26} {rec['kind']:<7} {rec['occluder']:<18} "
              f"{rec['pixels']:>5} {rec['worst_gap']:>7.3f}  {cause}")
    # One line per thing to do, not one per pixel that revealed it: the same
    # oversized face turns up in a dozen errors and repeating it buries the
    # rest of the list.
    crossing, others = {}, []
    for rec in report["errors"]:
        diag = rec.get("diagnosis") or {}
        fix = diag.get("fix")
        if not fix:
            continue
        who = diag.get("crossing_element")
        if who:
            crossing[who] = max(crossing.get(who, 0.0), diag.get("its_depth_span", 0.0),
                                diag.get("my_depth_span", 0.0))
        elif fix not in others:
            others.append(fix)
    if crossing or others:
        print()
        for fix in others:
            print(f"  * {fix}")
        if crossing:
            worst = sorted(crossing.items(), key=lambda kv: -kv[1])
            print(f"  * {len(worst)} faces cross something they are ordered against. "
                  f"Splitting them in Blender is what fixes these:")
            for name, span in worst:
                print(f"      {name:<24} spans {span:.2f} units of depth")


