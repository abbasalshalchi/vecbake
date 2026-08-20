# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""Audit a bake for occlusion errors, against the 3D scene as ground truth.

Run it inside Blender, on the scene you are about to ship:

    exec(open(r'.../scripts/check_occlusion.py').read())
    report = run()                       # the panel's settings, every frame
    report = run(frames=[0, 12, 24])     # or a few
    report = run(diagnose=False)          # faster: find, do not explain

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

import time

import numpy as np

import bpy

try:                                   # installed as an extension
    from bl_ext.user_default.vecbake import api, bake as bake_mod, order as order_mod
    from bl_ext.user_default.vecbake.settings import BakeSettings
except ImportError:                    # loaded from the add-on folder
    from vecbake import api, bake as bake_mod, order as order_mod
    from vecbake.settings import BakeSettings


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


def _stroke_mask(g, width, gx, gy):
    """Pixels within half a stroke width of the polyline."""
    mask = np.zeros(gx.shape, dtype=bool)
    half = max(width, 1.0) / 2.0 + 0.35        # +half a pixel for coverage
    for k in range(0, len(g) - 2, 2):
        ax, ay, bx, by = g[k], g[k + 1], g[k + 2], g[k + 3]
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
    return _stroke_mask(g, getattr(el, "width", 1.6) or 1.6, gx, gy)


def _bbox(g):
    return (g[0::2].min(), g[0::2].max(), g[1::2].min(), g[1::2].max())


def _rects_worth_testing(live, width, height, pad=2.0):
    """Screen rectangles where two different objects overlap.

    Everything else is a waste of a ray cast: an element can only be painted
    over the wrong thing where there is another object to be wrong about.

    Clipped to the canvas, which is not pedantry. Geometry is deliberately kept
    past the frame edge so it still draws when it crosses back in, so element
    boxes run well outside it -- and a pixel nobody will ever see reported as
    the worst error in the scene is worse than no report at all. The first run
    of this ranked one at y=1040 on a 1000px canvas.
    """
    boxes = {}
    for el, g, _ in live:
        b = _bbox(g)
        cur = boxes.get(el.group)
        boxes[el.group] = b if cur is None else (min(cur[0], b[0]), max(cur[1], b[1]),
                                                 min(cur[2], b[2]), max(cur[3], b[3]))
    names = sorted(boxes)
    rects = []
    for i, a in enumerate(names):
        ax0, ax1, ay0, ay1 = boxes[a]
        for b in names[i + 1:]:
            bx0, bx1, by0, by1 = boxes[b]
            x0 = max(max(ax0, bx0) - pad, 0.0)
            x1 = min(min(ax1, bx1) + pad, float(width))
            y0 = max(max(ay0, by0) - pad, 0.0)
            y1 = min(min(ay1, by1) + pad, float(height))
            if x1 > x0 and y1 > y0:
                rects.append((x0, x1, y0, y1))
    return rects


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
            rects = _rects_worth_testing(live, vec.width, vec.height)
            if not rects:
                continue

            scene.frame_set(start + fi * step)
            truth = _Truth(scene, vec.width, vec.height)

            seen = set()
            candidates = []
            for x0, x1, y0, y1 in rects:
                gx, gy = np.meshgrid(np.arange(int(x0), int(x1)) + 0.5,
                                     np.arange(int(y0), int(y1)) + 0.5)
                gx, gy = gx.ravel(), gy.ravel()
                if gx.size == 0:
                    continue
                near = [(el, g, d) for el, g, d in live
                        if not (_bbox(g)[0] > x1 or _bbox(g)[1] < x0
                                or _bbox(g)[2] > y1 or _bbox(g)[3] < y0)]
                top = np.full(gx.shape, -1, dtype=np.int32)
                coverage = {}
                # far to near, so the last write is what the player draws
                for i in sorted(range(len(near)), key=lambda i: -near[i][2]):
                    el, g, _d = near[i]
                    mask = _element_mask(el, g, gx, gy)
                    top[mask] = i
                    held = coverage.get(el.group)
                    coverage[el.group] = mask if held is None else (held | mask)

                # A rectangle where two objects' *boxes* meet is mostly pixels
                # where the objects themselves do not, and spending the budget
                # evenly over it dilutes the real overlap until nothing is hit:
                # this found zero on a bake with four known errors in it. Count
                # the objects actually covering each pixel and keep the ones
                # with more than one, which is the only place order can be wrong.
                objects_here = np.zeros(gx.shape, dtype=np.int16)
                for mask in coverage.values():
                    objects_here += mask
                for q in np.nonzero((objects_here >= 2) & (top >= 0))[0]:
                    px, py = float(gx[q]), float(gy[q])
                    ident = (round(px, 1), round(py, 1))
                    if ident in seen:
                        continue
                    seen.add(ident)
                    candidates.append((px, py, near[int(top[q])][0]))

            available += len(candidates)
            if len(candidates) > budget:
                keep = np.linspace(0, len(candidates) - 1, budget).astype(int)
                candidates = [candidates[i] for i in keep]

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


if __name__ == "__main__":
    run()
