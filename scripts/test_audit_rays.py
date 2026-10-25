# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""The audit's rays go through the pixels the bake drew.

    blender -b --factory-startup --python scripts/test_audit_rays.py

The occlusion audit ray casts the scene at a pixel of the *bake* and compares
with what the bake painted there, so its ray has to be the bake's ray. The bake
projects through `calc_matrix_camera` at its own width and height; the audit
used to build rays from `cam.data.view_frame(scene=scene)`, which follows the
scene's render resolution, and fanned them out from the camera centre even for
an orthographic camera. On 25 October that graded a 1080 x 1080 flashbang baked
at 1920 x 1080 as 9.4% wrong -- rays through the wrong pixels -- and an
orthographic lab scene as clean, because a ray that misses is skipped, not
scored.

Built here, nothing loaded: a cube in front of a wall, under a perspective and
an orthographic camera, with the scene's render size square, 16:9 and very wide,
and the bake always 1920 x 1080. Every face centre of the cube that faces the
camera is projected with the bake's own projection; the audit's ray at that
pixel must hit the cube, at that centre's depth.
"""

import math
import sys

import addon_utils
import bpy

addon_utils.enable("bl_ext.user_default.vecbake", default_set=False, persistent=False)
from bl_ext.user_default.vecbake import audit, bake  # noqa: E402

import numpy as np  # noqa: E402

W, H = 1920, 1080


def build():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    scene = bpy.context.scene
    bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0.4, 0.2, 0.3))
    cube = bpy.context.active_object
    cube.name = "Cube"
    cube.rotation_euler = (0.3, 0.2, 0.5)
    bpy.ops.mesh.primitive_plane_add(size=40.0, location=(0, 0, -6), rotation=(0, 0, 0))
    bpy.context.active_object.name = "Wall"
    data = bpy.data.cameras.new("cam")
    cam = bpy.data.objects.new("cam", data)
    scene.collection.objects.link(cam)
    cam.location = (3.0, -4.0, 9.0)
    cam.rotation_euler = (math.radians(30), 0.0, math.radians(35))
    scene.camera = cam
    return scene, cube, cam


def check(scene, cube, cam, kind, res):
    cam.data.type = kind
    cam.data.lens, cam.data.ortho_scale = 50.0, 9.0
    scene.render.resolution_x, scene.render.resolution_y = res
    scene.render.resolution_percentage = 100
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    view, mvp = bake.camera_matrices(scene, cam, dg, W, H)
    eye = np.array(cam.matrix_world.translation)
    fwd = -np.array(cam.matrix_world.to_3x3().col[2])
    truth = audit._Truth(scene, W, H)
    span = float(np.linalg.norm(eye))
    tried = bad = 0
    for poly in cube.data.polygons:
        centre = np.array(cube.matrix_world @ poly.center)
        normal = np.array((cube.matrix_world.to_3x3() @ poly.normal).normalized())
        towards = (eye - centre) if kind == "PERSP" else -fwd
        if normal.dot(towards) <= 0.0:
            continue                       # faces away: another face is in front
        corners = [np.array(cube.matrix_world @ cube.data.vertices[v].co) for v in poly.vertices]
        points = [centre] + [(centre + c) / 2.0 for c in corners]
        xy, depths, _ = bake.project(np.array(points), view, mvp, W, H)
        for (px, py), depth in zip(xy, depths):
            tried += 1
            name, got = truth.nearest(float(px), float(py))
            if name != "Cube" or got is None or abs(got - depth) > 1e-4 * span:
                bad += 1
    return tried, bad


def main():
    scene, cube, cam = build()
    failures = []
    for kind in ("PERSP", "ORTHO"):
        for res in ((1080, 1080), (1920, 1080), (1560, 320)):
            tried, bad = check(scene, cube, cam, kind, res)
            ok = tried and not bad
            print(f"[rays] {kind:5s} scene {res[0]}x{res[1]}, bake {W}x{H}: "
                  f"{tried - bad}/{tried} face centres hit {'ok' if ok else 'FAIL'}")
            if not ok:
                failures.append((kind, res))
    print("[rays]", "PASS" if not failures else f"FAIL {failures}")
    return 1 if failures else 0


code = main()
sys.stdout.flush()
import os  # noqa: E402
os._exit(code)
