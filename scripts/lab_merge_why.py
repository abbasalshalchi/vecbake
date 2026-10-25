# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""Why one object's merged outline is refused, gap by gap.

    blender -b --factory-startup <blend> --python scripts/lab_merge_why.py -- \
        --object <name> [--camera <name>] [--out <dir>]

Bakes that one object with the scorecard's settings and camera handling
(`scorecard.setup_camera`, so an orbit where the camera does not move), and
watches the two tests that can refuse a merged outline while they run:

- the gap test (`judge_carry`): every `outline.stray` it asks, with the gap;
- Merge False Outline: every `_false_outline` and `_unhidden` call, with the gap,
  how many rings were drawn and how many true pieces there were.

Then, separately, how many silhouette pieces the object has at every keyframe
and every half-frame -- which is what says whether a gap holds a piece
appearing or vanishing (docs/audits.md section 12) or only a ring drifting.
Prints one line per gap and the bake's own merge record.
"""

import argparse
import json
import math
import os
import sys

import addon_utils
import bpy
import numpy as np
from mathutils import Vector

addon_utils.enable("bl_ext.user_default.vecbake", default_set=False, persistent=False)
from bl_ext.user_default.vecbake import api, bake, merge  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
_src = open(os.path.join(HERE, "scorecard.py"), encoding="utf-8").read()
_defs = {"__file__": os.path.join(HERE, "scorecard.py"), "__name__": "scorecard_defs"}
exec(compile(_src.split("\ndef main():")[0], "scorecard_defs", "exec"), _defs)


def pieces_at(scene, obj, cam, frame, subframe=0.0):
    scene.frame_set(frame, subframe=subframe)
    dg = bpy.context.evaluated_depsgraph_get()
    view, mvp = bake.camera_matrices(scene, cam, dg, 1920, 1080)
    ce = cam.evaluated_get(dg)
    cp = np.array(ce.matrix_world.translation, dtype=np.float64)
    cf = np.array((ce.matrix_world.to_3x3() @ Vector((0, 0, -1))).normalized())
    ev = obj.evaluated_get(dg)
    me = ev.to_mesh()
    try:
        plan = bake.ObjectPlan(obj, me, bake.BakeSettings())
        seen = bake.project_object(plan, me, ev, view, mvp, 1920, 1080, cp, cf,
                                   cam.data.type == "ORTHO")
        facing = seen.face_ok & seen.face_front
        got = merge.walk_pieces(plan, merge.silhouette_edges(plan, facing), seen.xy, facing)
        return -1 if got is None else len(got)
    finally:
        ev.to_mesh_clear()


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("--object", required=True)
    ap.add_argument("--camera")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    scene = bpy.context.scene
    if args.camera:
        scene.camera = bpy.data.objects[args.camera]
    cam_rec = _defs["setup_camera"](scene)
    _defs["reset_settings"](scene.vecbake)
    for k, v in _defs["SETTINGS"].items():
        setattr(scene.vecbake, k, v)
    settings = scene.vecbake.to_bake_settings(bpy.context)
    obj = bpy.data.objects[args.object]
    settings.objects = [obj]
    cam = scene.camera

    log = {"stray": [], "false": [], "seen": []}
    om = bake.outline_mod
    real = (om.stray, bake._false_outline, bake._unhidden)

    def stray_spy(a, b):
        v = real[0](a, b)
        log["stray"].append((scene.frame_current, round(float(v), 2), len(a), len(b)))
        return v

    dumps = {}

    def false_spy(drawn, truth, allow, w, h, *rest, **kw):
        pts, dep = real[1](drawn, truth, allow, w, h, *rest, **kw)
        dumps[scene.frame_current - 1] = ([np.asarray(r) for r in drawn],
                                          [np.asarray(r) for r, _d in truth],
                                          np.asarray(pts))
        log["false"].append((scene.frame_current, len(drawn),
                             sum(1 for r in drawn if len(r) >= 2 and np.ptp(r, axis=0).max() > 0),
                             len(truth), len(pts)))
        return pts, dep

    def unhidden_spy(sc, dg, m, w, h, pts, dep, own, names):
        n = real[2](sc, dg, m, w, h, pts, dep, own, names)
        log["seen"].append((sc.frame_current, own, len(pts), int(n)))
        return n

    om.stray, bake._false_outline, bake._unhidden = stray_spy, false_spy, unhidden_spy
    try:
        vec = api.bake_scene(bpy.context, settings)
    finally:
        om.stray, bake._false_outline, bake._unhidden = real

    frames = list(range(scene.frame_start, scene.frame_end + 1))
    at_key = [pieces_at(scene, obj, cam, f) for f in frames]
    at_mid = [pieces_at(scene, obj, cam, f, 0.5) for f in frames[:-1]]
    m = vec.meta
    name = obj.name
    record = {
        "object": name, "camera": cam_rec, "elements": len(vec.elements),
        "merged": any(el.key == f"{name}/m" for el in vec.elements),
        "uncarried": name in (m.get("merged_uncarried") or []),
        "fallback": name in (m.get("merged_fallback") or []),
        "gap_carry_face": (m.get("merged_gap_worst") or {}).get(name),
        "false_px": (m.get("merged_false_line") or {}).get(name),
        "slots": (m.get("merged_slots") or {}).get(name),
        "pieces_key": at_key, "pieces_mid": at_mid,
        "stray": log["stray"], "false": log["false"], "seen": log["seen"],
    }
    # `stray` and `_false_outline` run while the scene sits on keyframe fi and
    # judge the gap that ends there; `_unhidden` runs with the scene set to the
    # half-frame after keyframe fi - 1. Key every gap by the frame it starts on.
    gaps = {}
    for f, v, _na, _nb in log["stray"]:
        gaps.setdefault(f - 1, {})["carry"] = v
    for f, _nd, live, nt, nfalse in log["false"]:
        gaps.setdefault(f - 1, {}).update(live_rings=live, true_pieces=nt, false_samples=nfalse)
    for f, _own, _npts, n in log["seen"]:
        gaps.setdefault(f, {})["seen"] = n
    rows = []
    for g in sorted(gaps):
        i = g - scene.frame_start
        if not 0 <= i < len(at_mid):
            continue
        a, mid_, b = at_key[i], at_mid[i], at_key[i + 1]
        rows.append(dict(gap=[g, g + 1], pieces=[a, mid_, b],
                         kind="stable" if a == mid_ == b else "change", **gaps[g]))
    record["gaps"] = rows
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        with open(os.path.join(args.out, f"why_{name}.json"), "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=1, default=str)
        # The drawn rings at each half-frame, the true pieces there and the
        # samples judged false, for drawing: gap -> {drawn, truth, false}.
        with open(os.path.join(args.out, f"why_{name}_rings.json"), "w", encoding="utf-8") as fh:
            json.dump({str(g): {"drawn": [r.tolist() for r in d], "truth": [r.tolist() for r in t],
                                "false": f.tolist()} for g, (d, t, f) in dumps.items()}, fh)
    print("[why]", {k: record[k] for k in ("object", "elements", "merged", "uncarried",
                                           "fallback", "gap_carry_face", "false_px", "slots")})
    by_kind = {}
    for row in rows:
        by_kind[row["kind"]] = by_kind.get(row["kind"], 0.0) + row.get("seen", 0) * 0.5
        print(f"[why] gap {row['gap'][0]}->{row['gap'][1]} pieces {row['pieces']} {row['kind']:6s} "
              f"live {row.get('live_rings')} true {row.get('true_pieces')} "
              f"seen {row.get('seen')} carry {row.get('carry')}")
    print("[why] visible false outline by gap kind (px, as false_seen counts it):", by_kind)


try:
    main()
except Exception:
    import traceback
    traceback.print_exc()
sys.stdout.flush()
os._exit(0)
