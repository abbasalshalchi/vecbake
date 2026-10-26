# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""Why a torus will not merge: one torus, one orbit, several camera heights.

    blender -b --factory-startup --python scripts/lab_torus.py -- [elevations...]

The doughnut tutorial's nine bodies all fell back to faces on 25 October
(docs/audits.md section 20): refused by the carry test and by Merge False
Outline. A torus's outline is two rings -- the rim and the hole -- and the hole
changes with the view, so this asks the question with nothing else in the way:
a smooth primitive torus, the scorecard's settings and its 60 degree orbit, at
camera elevations from nearly edge-on to nearly overhead.

Per elevation it prints the bake's own merge record -- merged or not, the gap
test's two numbers (carry vs face), the visible false outline, the slots -- and,
per keyframe, how many silhouette pieces the torus has, which is what decides
whether the hole ring exists in that frame.
"""

import math
import os
import sys

import addon_utils
import bpy

addon_utils.enable("bl_ext.user_default.vecbake", default_set=False, persistent=False)
from bl_ext.user_default.vecbake import api, bake, emit_lerpa, merge  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
src = open(os.path.join(HERE, "scorecard.py"), encoding="utf-8").read()
SETTINGS = {}
exec(compile(src[src.index("SETTINGS = "):src.index("ORBIT_FRAMES")], "settings", "exec"),
     {"math": math}, ns := {})
SETTINGS = ns["SETTINGS"]
FRAMES = 31


def build(elevation, major=1.0, minor=0.4, segments=(64, 32), tilt=0.0, lens=50.0, fill=1.1):
    """`tilt` leans the torus about X, in degrees: a tilted torus under an orbit
    about Z is seen from a changing height, which is what makes its silhouette
    change topology during the clip (the doughnut's tori lean and are seen
    nearly edge-on). `fill` is the camera distance as a multiple of the one that
    just frames the torus."""
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, FRAMES
    scene.render.fps = 24
    scene.render.resolution_x, scene.render.resolution_y = 1920, 1080
    bpy.ops.mesh.primitive_torus_add(major_radius=major, minor_radius=minor,
                                     major_segments=segments[0], minor_segments=segments[1])
    torus = bpy.context.active_object
    torus.name = "Torus"
    torus.rotation_euler = (math.radians(tilt), 0.0, 0.0)
    bpy.ops.object.shade_smooth()

    pivot = bpy.data.objects.new("pivot", None)
    scene.collection.objects.link(pivot)
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    scene.collection.objects.link(cam)
    cam.data.lens = lens
    radius = major + minor
    dist = radius / math.sin(math.atan(36.0 * 9 / 16 / 2 / lens)) * fill
    up = math.radians(elevation)
    cam.location = (0.0, -dist * math.cos(up), dist * math.sin(up))
    cam.rotation_euler = (math.pi / 2 - up, 0.0, 0.0)
    cam.parent = pivot
    scene.camera = cam
    bpy.context.preferences.edit.keyframe_new_interpolation_type = "LINEAR"
    for frame, angle in ((1, -math.pi / 6), (FRAMES, math.pi / 6)):
        pivot.rotation_euler = (0.0, 0.0, angle)
        pivot.keyframe_insert("rotation_euler", index=2, frame=frame)
    scene.frame_set(1)
    return scene, torus, cam


def pieces_per_frame(scene, torus, cam):
    out = []
    for f in range(scene.frame_start, scene.frame_end + 1):
        scene.frame_set(f)
        dg = bpy.context.evaluated_depsgraph_get()
        view, mvp = bake.camera_matrices(scene, cam, dg, 1920, 1080)
        ev = torus.evaluated_get(dg)
        me = ev.to_mesh()
        try:
            plan = bake.ObjectPlan(torus, me, bake.BakeSettings())
            seen = bake.project_object(plan, me, ev, view, mvp, 1920, 1080,
                                       *_cam(cam, dg), cam.data.type == "ORTHO")
            facing = seen.face_ok & seen.face_front
            pieces = merge.walk_pieces(plan, merge.silhouette_edges(plan, facing),
                                       seen.xy, facing) or []
            out.append(len(pieces))
        finally:
            ev.to_mesh_clear()
    scene.frame_set(scene.frame_start)
    return out


def _cam(cam, dg):
    import numpy as np
    from mathutils import Vector
    ce = cam.evaluated_get(dg)
    return (np.array(ce.matrix_world.translation, dtype=np.float64),
            np.array((ce.matrix_world.to_3x3() @ Vector((0, 0, -1))).normalized()))


def run(elevation, **shape):
    scene, torus, cam = build(elevation, **shape)
    props = scene.vecbake
    for key, value in SETTINGS.items():
        setattr(props, key, value)
    settings = props.to_bake_settings(bpy.context)
    vec = api.bake_scene(bpy.context, settings)
    m = vec.meta
    merged = any(el.key == "Torus/m" for el in vec.elements)
    row = {
        "elevation": elevation, "merged": merged, "elements": len(vec.elements),
        "uncarried": "Torus" in (m.get("merged_uncarried") or []),
        "gap_carry_face": (m.get("merged_gap_worst") or {}).get("Torus"),
        "false_px": (m.get("merged_false_line") or {}).get("Torus"),
        "slots": (m.get("merged_slots") or {}).get("Torus"),
        "pieces": pieces_per_frame(scene, torus, cam),
    }
    # Grade what merged with the same checkers as the scorecard: every drawn
    # point at the keyframes, and every outline segment halfway between them.
    out = os.path.join(os.path.dirname(HERE), "out", "lab-torus")
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, f"torus_{int(elevation)}.svg")
    emit_lerpa.write(vec, path, include_ids=True, coord_decimals=settings.effective_coord_decimals(),
                     states=False, name="torus", source="")
    checks = {}
    for script, kw in (("check_geometry", {}), ("check_outline_segments", {"subframe": 0.5})):
        ns = {"__name__": script, "__file__": os.path.join(HERE, f"{script}.py")}
        exec(open(ns["__file__"], encoding="utf-8").read(), ns)
        got = ns["run"](path, verbose=False, **kw)
        checks[script] = ({"offenders": got["offenders"], "worst": round(got["worst"], 2)}
                          if isinstance(got, dict) else
                          {"offenders": len(got), "over_3px": sum(1 for r in got if r["d"] > 3),
                           "worst": round(max([r["d"] for r in got] or [0.0]), 2)})
    row["checks"] = checks
    print("[torus]", row, flush=True)
    return row


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    for e in [float(a) for a in argv] or [5.0, 15.0, 30.0, 60.0]:
        run(e)
    sys.stdout.flush()
    os._exit(0)
