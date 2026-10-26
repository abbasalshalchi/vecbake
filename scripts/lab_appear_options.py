# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""Where should a piece that is not there yet be parked? Measured, not built.

    blender -b --factory-startup <blend> --python scripts/lab_appear_options.py -- \
        --object <name> [--frames A B] [--camera <name>]

A merged outline's subpath slot that has no piece in a frame is parked, and the
player interpolates from the parked ring to the next frame's real one. Today the
whole ring is parked on one mesh vertex (`bake._settle_absent_pieces`), so a hole
that opens along a line grows out of one end of it.

This bakes the object once, as the scorecard would, then re-scores every gap where
a slot is parked at one end -- with Merge False Outline's own measure
(`bake._false_outline`, samples 0.5 px apart, farther than tol x 1.25 from the true
pieces) -- under each parking rule:

  now       as written: the whole ring on one vertex
  centroid  the whole ring on its own centroid
  vertices  every point on its own surface point, projected in the empty frame
  ideal     whatever makes the halfway ring exact: 2 x truth(half) - ring(present)

and, because a parked ring is drawn at its keyframe, what each rule draws there
against that keyframe's truth. A point draws nothing; the other two do. The object
is alone, so every false sample is visible -- as the bake's own count would find.
"""

import argparse
import os
import sys

import addon_utils
import bpy
import numpy as np
from mathutils import Vector

addon_utils.enable("bl_ext.user_default.vecbake", default_set=False, persistent=False)
from bl_ext.user_default.vecbake import api, bake, merge, outline as outline_mod  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
_defs = {"__file__": os.path.join(HERE, "scorecard.py"), "__name__": "scorecard_defs"}
_src = open(_defs["__file__"], encoding="utf-8").read()
exec(compile(_src.split("\ndef main():")[0], "scorecard_defs", "exec"), _defs)
W, H = 1920, 1080


def silhouette(scene, obj, cam, frame, subframe=0.0):
    """True pieces as (ids, verts, xy ring) and the projected vertex positions."""
    scene.frame_set(frame, subframe=subframe)
    dg = bpy.context.evaluated_depsgraph_get()
    view, mvp = bake.camera_matrices(scene, cam, dg, W, H)
    ce = cam.evaluated_get(dg)
    cp = np.array(ce.matrix_world.translation, dtype=np.float64)
    cf = np.array((ce.matrix_world.to_3x3() @ Vector((0, 0, -1))).normalized())
    ev = obj.evaluated_get(dg)
    me = ev.to_mesh()
    try:
        plan = bake.ObjectPlan(obj, me, bake.BakeSettings())
        seen = bake.project_object(plan, me, ev, view, mvp, W, H, cp, cf,
                                   cam.data.type == "ORTHO")
        facing = seen.face_ok & seen.face_front
        got = merge.walk_pieces(plan, merge.silhouette_edges(plan, facing), seen.xy, facing) or []
        return [(ids, verts, seen.xy[verts].copy()) for ids, verts, _ccw in got], seen.xy.copy()
    finally:
        ev.to_mesh_clear()


def false_px(rings, truth_rings, allow):
    truth = [(r, np.zeros(len(r))) for r in truth_rings]
    pts, _ = bake._false_outline(rings, truth, allow, W, H)
    return 0.5 * len(pts)


def degenerate(ring):
    return len(ring) < 2 or np.ptp(ring, axis=0).max() <= 0.0


def anchors(ring_pts, loop_verts, xy):
    """Each drawn point as (vertex a, vertex b, t) on the closed vertex loop it lies on."""
    a = xy[loop_verts]
    b = np.roll(a, -1, axis=0)
    seg = b - a
    den = np.maximum(np.einsum("ij,ij->i", seg, seg), 1e-12)
    out = []
    for p in ring_pts:
        t = np.clip(((p - a) * seg).sum(1) / den, 0, 1)
        d = np.linalg.norm(a + seg * t[:, None] - p, axis=1)
        k = int(d.argmin())
        out.append((int(loop_verts[k]), int(loop_verts[(k + 1) % len(loop_verts)]), float(t[k])))
    return out


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("--object", required=True)
    ap.add_argument("--camera")
    ap.add_argument("--frames", nargs=2, type=int)
    args = ap.parse_args(argv)
    scene = bpy.context.scene
    if args.frames:
        scene.frame_start, scene.frame_end = args.frames
    if args.camera:
        scene.camera = bpy.data.objects[args.camera]
    _defs["setup_camera"](scene)
    _defs["reset_settings"](scene.vecbake)
    for k, v in _defs["SETTINGS"].items():
        setattr(scene.vecbake, k, v)
    settings = scene.vecbake.to_bake_settings(bpy.context)
    obj = bpy.data.objects[args.object]
    settings.objects = [obj]
    # Measuring, not deciding: with the limit on, the outline being studied is
    # refused and there is nothing to re-score (as section 14 measured at 1e9).
    settings.merge_false_px = 0.0
    cam = scene.camera
    vec = api.bake_scene(bpy.context, settings)
    el = next((e for e in vec.elements if e.key == f"{obj.name}/m"), None)
    if el is None:
        print("[appear] not merged as several subpaths; nothing to re-score")
        return
    nframes = vec.meta["frames"]
    times = [vec.duration * i / (nframes - 1) for i in range(nframes)]
    sizes = getattr(el, "subpaths", None) or getattr(el, "sp", None)
    rows = [np.asarray(el.geo.sample(t)).reshape(-1, 2) for t in times]
    if not sizes:
        print("[appear] element has no subpath sizes:", [a for a in dir(el) if not a.startswith("_")])
        return
    cuts = np.cumsum(sizes)[:-1]
    allow = settings.tol_px * 1.25
    frames = list(range(scene.frame_start, scene.frame_end + 1))
    fractions = (0.25, 0.5, 0.75)
    names = ("now", "centroid", "vertices", "birth")
    totals = {k: [0.0, 0.0] for k in names}

    def count(scene_frame, s_frac):
        return len(silhouette(scene, obj, cam, scene_frame, s_frac)[0])

    def event_time(f0, n0, n1):
        """Fraction of the gap where the piece count stops being n0 (bisection)."""
        lo, hi = 0.0, 1.0
        for _ in range(12):
            mid = 0.5 * (lo + hi)
            if count(f0, mid) == n0:
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)

    for i in range(len(frames) - 1):
        a_rings = np.split(rows[i], cuts)
        b_rings = np.split(rows[i + 1], cuts)
        parked = [k for k in range(len(sizes)) if degenerate(a_rings[k]) != degenerate(b_rings[k])]
        if not parked:
            continue
        f0 = frames[i]
        truths = {sf: [r for _i, _v, r in silhouette(scene, obj, cam, f0, sf)[0]] for sf in fractions}
        n0, n1 = count(f0, 0.0), count(frames[i + 1], 0.0)
        line = f"[appear] gap {f0}->{frames[i + 1]} pieces {n0}->{n1}"
        for k in parked:
            birth = degenerate(a_rings[k])            # empty at the start of the gap
            empty_i, full_i = (i, i + 1) if birth else (i + 1, i)
            full = (b_rings if birth else a_rings)[k]
            truth_full, xy_full = silhouette(scene, obj, cam, frames[full_i])
            truth_empty, xy_empty = silhouette(scene, obj, cam, frames[empty_i])
            empty_rings = [r for _i, _v, r in truth_empty]
            best = min(truth_full, key=lambda p: np.linalg.norm(
                p[2][:, None, :] - full[None, :, :], axis=2).min(axis=0).mean())
            anc = anchors(full, best[1], xy_full)

            def own_points(sf):
                xy = silhouette(scene, obj, cam, f0, sf)[1]
                return np.array([xy[va] + (xy[vb] - xy[va]) * t for va, vb, t in anc])

            s_event = event_time(f0, n0, n1) if n0 != n1 else (0.0 if birth else 1.0)
            # The ring at the event, as its own surface points then, carried back
            # (or forward) to the empty keyframe along the line through the full
            # one -- never drawn on the empty side of the event.
            at_event = own_points(s_event)
            if birth:
                span = max(1.0 - s_event, 1e-6)
                carried = full + (at_event - full) / span
            else:
                span = max(s_event, 1e-6)
                carried = full + (at_event - full) / span
            parked_rings = {
                "now": (a_rings if birth else b_rings)[k],
                "centroid": np.repeat(full.mean(axis=0)[None, :], len(full), axis=0),
                "vertices": own_points(0.0 if birth else 1.0),
                "birth": carried,
            }
            for name in names:
                ring = parked_rings[name]
                ra = ring if birth else a_rings[k]
                rb = b_rings[k] if birth else ring
                half_cost = 0.0
                for sf in fractions:
                    drawn = [a_rings[j] + (b_rings[j] - a_rings[j]) * sf
                             for j in range(len(sizes)) if j != k]
                    shown = (sf >= s_event) if birth else (sf <= s_event)
                    if name != "birth" or shown:
                        drawn.append(ra + (rb - ra) * sf)
                    half_cost += false_px(drawn, truths[sf], allow)
                key_cost = 0.0 if (name == "birth" or degenerate(ring)) else false_px([ring], empty_rings, allow)
                totals[name][0] += half_cost
                totals[name][1] += key_cost
                line += f"  {name}: {half_cost:.0f}/{key_cost:.0f}"
            line += f"  (event at {s_event:.3f} of the gap)"

        # Bound: the whole element keyed at the event, every ring placed by its
        # own surface points on each side of it -- the best a representation
        # that knows when topology changes could do with linear interpolation.
        if n0 != n1:
            s_e = event_time(f0, n0, n1)
            key_cost = 0.0
            for sf in fractions:
                drawn = []
                for j in range(len(sizes)):
                    ra, rb = a_rings[j], b_rings[j]
                    if sf < s_e:
                        if degenerate(ra):
                            continue
                        tf, xy_k = silhouette(scene, obj, cam, frames[i])
                        best = min(tf, key=lambda p: np.linalg.norm(
                            p[2][:, None, :] - ra[None, :, :], axis=2).min(axis=0).mean())
                        anc_j = anchors(ra, best[1], xy_k)
                        xy_e = silhouette(scene, obj, cam, f0, s_e)[1]
                        ev = np.array([xy_e[va] + (xy_e[vb] - xy_e[va]) * t for va, vb, t in anc_j])
                        drawn.append(ra + (ev - ra) * (sf / s_e))
                    else:
                        if degenerate(rb):
                            continue
                        tf, xy_k = silhouette(scene, obj, cam, frames[i + 1])
                        best = min(tf, key=lambda p: np.linalg.norm(
                            p[2][:, None, :] - rb[None, :, :], axis=2).min(axis=0).mean())
                        anc_j = anchors(rb, best[1], xy_k)
                        xy_e = silhouette(scene, obj, cam, f0, s_e)[1]
                        ev = np.array([xy_e[va] + (xy_e[vb] - xy_e[va]) * t for va, vb, t in anc_j])
                        drawn.append(ev + (rb - ev) * ((sf - s_e) / max(1.0 - s_e, 1e-6)))
                key_cost += false_px(drawn, truths[sf], allow)
            totals.setdefault("event-keyed", [0.0, 0.0])[0] += key_cost
            print(f"[appear]   event-keyed bound for this gap: {key_cost:.0f}", flush=True)
        print(line, flush=True)
    print("[appear] totals over the quarter, half and three-quarter points / at the empty keyframes:",
          {k: (round(v[0], 1), round(v[1], 1)) for k, v in totals.items()}, flush=True)


try:
    main()
except Exception:
    import traceback
    traceback.print_exc()
sys.stdout.flush()
os._exit(0)
