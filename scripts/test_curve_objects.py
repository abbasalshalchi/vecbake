# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""A curve, a text and a surface bake exactly as their own evaluated meshes.

    blender -b --factory-startup --python scripts/test_curve_objects.py

Until 25 October `collect_objects` took meshes only, so a bevelled curve, a
title or a NURBS surface was silently absent from the bake -- 3,072 of the
flashbang scene's 5,221 faces -- and the ray cast saw straight through where
it should have been.

Built here, nothing loaded: a bevelled Bezier circle, an extruded text, a
NURBS surface patch, a wire curve with no bevel, and a curve with no
splines at all, in front of a wall, under a camera that orbits. Baked once as
they are, then again with every one of them replaced by
`bpy.data.meshes.new_from_object` of its evaluated self, same name, same
place, same order. The two bakes must agree element for element and sample for
sample. The empty curve must draw nothing and not stop the bake.

Then the graders: the audit's own-surface ray (`_Truth.surface_of`) must find a
curve's surface, and `check_geometry.py` must grade the curve elements rather
than skip them.
"""

import math
import os
import sys
import tempfile

import addon_utils
import bpy
import numpy as np

addon_utils.enable("bl_ext.user_default.vecbake", default_set=False, persistent=False)
from bl_ext.user_default.vecbake import api, audit, emit_lerpa  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SHAPES = ["Ring", "Title", "Patch", "Wire", "Empty"]
FRAMES = 9


def _clear():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)


def build():
    _clear()
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, FRAMES
    scene.render.resolution_x, scene.render.resolution_y = 1920, 1080

    bpy.ops.mesh.primitive_plane_add(size=30.0, location=(0, 4, 0), rotation=(math.radians(90), 0, 0))
    bpy.context.active_object.name = "Wall"

    bpy.ops.curve.primitive_bezier_circle_add(radius=1.2, location=(-2.6, 0, 0.6),
                                              rotation=(math.radians(70), 0, 0))
    ring = bpy.context.active_object
    ring.name = "Ring"
    ring.data.bevel_depth, ring.data.bevel_resolution, ring.data.resolution_u = 0.18, 2, 10

    bpy.ops.object.text_add(location=(0.0, 0.0, 0.0), rotation=(math.radians(90), 0, 0))
    title = bpy.context.active_object
    title.name = "Title"
    title.data.body = "Ob"
    title.data.extrude = 0.12
    title.data.size = 1.4

    bpy.ops.surface.primitive_nurbs_surface_surface_add(location=(2.8, 0.5, 0.6),
                                                      rotation=(math.radians(60), 0, 0.4))
    patch = bpy.context.active_object
    patch.name = "Patch"
    patch.data.resolution_u = patch.data.resolution_v = 6

    bpy.ops.curve.primitive_bezier_curve_add(location=(0.0, -0.5, -1.4))
    bpy.context.active_object.name = "Wire"           # no bevel: edges, no faces

    empty = bpy.data.curves.new("Empty", "CURVE")      # no splines: no geometry
    obj = bpy.data.objects.new("Empty", empty)
    scene.collection.objects.link(obj)

    pivot = bpy.data.objects.new("pivot", None)
    scene.collection.objects.link(pivot)
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    scene.collection.objects.link(cam)
    cam.location = (0.0, -11.0, 2.5)
    cam.rotation_euler = (math.radians(80), 0.0, 0.0)
    cam.parent = pivot
    scene.camera = cam
    bpy.context.preferences.edit.keyframe_new_interpolation_type = "LINEAR"
    for frame, angle in ((1, -0.35), (FRAMES, 0.35)):
        pivot.rotation_euler = (0.0, 0.0, angle)
        pivot.keyframe_insert("rotation_euler", index=2, frame=frame)
    scene.frame_set(1)
    return scene


def to_meshes(scene):
    """Replace each shape by a mesh object made from its evaluated geometry."""
    dg = bpy.context.evaluated_depsgraph_get()
    for name in SHAPES:
        obj = bpy.data.objects[name]
        # The transform's own channels, not matrix_world: assigning a matrix
        # decomposes it and comes back a few bits off, which moves every point.
        basis = (obj.location.copy(), obj.rotation_mode, obj.rotation_euler.copy(),
                 obj.scale.copy())
        mats = [s.material for s in obj.material_slots]
        ev = obj.evaluated_get(dg)
        mesh = bpy.data.meshes.new_from_object(ev) if ev.to_mesh() is not None else None
        ev.to_mesh_clear()
        bpy.data.objects.remove(obj)
        if mesh is None:
            continue
        twin = bpy.data.objects.new(name, mesh)
        scene.collection.objects.link(twin)
        twin.location, twin.rotation_mode, twin.rotation_euler, twin.scale = basis
        for m in mats:
            mesh.materials.append(m)
    bpy.context.view_layer.update()


def bake(scene):
    p = scene.vecbake
    p.edge_mode, p.crease_angle = "SHARP", math.radians(30.0)
    p.merge_fills, p.weld_seams, p.chain_edges = True, True, True
    p.use_scene_res, p.width, p.height = False, 1920, 1080
    s = p.to_bake_settings(bpy.context)
    # One fixed order for both bakes: the twins are linked last, and object
    # order can decide ties in the ordering pass.
    s.objects = [o for n in ["Wall"] + SHAPES for o in [bpy.data.objects.get(n)] if o]
    return api.bake_scene(bpy.context, s), s


def tracks(vec):
    out = {}
    times = np.linspace(0.0, vec.duration, FRAMES * 4 - 3)
    for el in vec.elements:
        out[el.key] = (el.kind, len(el.geo.sample(0.0)),
                       np.array([el.geo.sample(t) for t in times]),
                       np.array([el.depth.sample(t) for t in times]),
                       np.array([el.opacity.sample(t) for t in times]))
    return out


def main():
    failures = []
    scene = build()
    curves, settings = bake(scene)
    got = tracks(curves)
    per = {n: sum(1 for k in got if k.split("/")[0] == n) for n in SHAPES}
    print("[curves] elements per shape:", per)
    for name in ("Ring", "Title", "Patch"):
        if not per[name]:
            failures.append(f"{name} drew nothing")
    if per["Empty"]:
        failures.append("the empty curve drew something")

    # the graders, while the curves are still curves
    scene.frame_set(1)
    truth = audit._Truth(scene, 1920, 1080)
    ring_el = next((el for el in curves.elements if el.key.startswith("Ring/")
                    and el.kind == "fill" and el.opacity.sample(0.0) > 0.5), None)
    if ring_el is None:
        failures.append("no drawn Ring fill to aim the audit at")
    else:
        xy = np.asarray(ring_el.geo.sample(0.0)).reshape(-1, 2).mean(axis=0)
        if truth.surface_of("Ring", float(xy[0]), float(xy[1])) is None:
            failures.append("audit cannot see the Ring's own surface")
    path = os.path.join(tempfile.mkdtemp(), "curves.svg")
    emit_lerpa.write(curves, path, include_ids=True,
                     coord_decimals=settings.effective_coord_decimals(),
                     states=False, name="curves", source="")
    ns = {"__name__": "check_geometry", "__file__": os.path.join(HERE, "check_geometry.py")}
    exec(open(ns["__file__"], encoding="utf-8").read(), ns)
    geo = ns["run"](path, verbose=False)
    print(f"[curves] check_geometry: {geo['checked']} checked, {geo['skipped']} skipped, "
          f"{geo['offenders']} offenders, worst {geo['worst']:.3f} px")
    if geo["skipped"] or geo["offenders"] or not geo["checked"]:
        failures.append("check_geometry skipped or failed curve elements")

    # the same geometry as meshes
    to_meshes(scene)
    meshes, _ = bake(scene)
    want = tracks(meshes)
    if set(got) != set(want):
        failures.append(f"element sets differ: {sorted(set(got) ^ set(want))[:8]}")
    differ = []
    for key in sorted(set(got) & set(want)):
        a, b = got[key], want[key]
        if a[0] != b[0] or a[1] != b[1] or any(not np.array_equal(x, y) for x, y in zip(a[2:], b[2:])):
            differ.append(key)
    print(f"[curves] {len(got)} elements as curves, {len(want)} as meshes, "
          f"{len(differ)} differ")
    if differ:
        failures.append(f"{len(differ)} elements differ, e.g. {differ[:5]}")

    print("[curves]", "PASS" if not failures else "FAIL: " + "; ".join(failures))
    return 1 if failures else 0


code = main()
sys.stdout.flush()
os._exit(code)
