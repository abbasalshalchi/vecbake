"""Silhouette corners must not cross object boundaries. Run inside Blender:

    exec(open('scripts/test_silhouette_objects.py').read())

Mesh vertex indices restart at zero in every object, so a bare index is only
meaningful next to the object it belongs to. `silhouette._movers` used to sweep
up any element whose endpoint index matched the silhouette edge's, from
anywhere in the scene, and hand it a corner position computed for a different
object entirely.

The symptom was two parts a thousand pixels apart swapping corners: a stroke
sitting at x=1017 pulled to x=-115, and the part at x=-110 pulled to x=1010.
On screen that is a line leaping across the model, and it only appears with
Curves on, because none of this runs otherwise.

The scene below is the minimum that reproduces it: two identical low-poly
cylinders, far apart, so their vertex numbering collides on every index while
their screen positions could not be further apart.
"""

import math

import bpy
from bpy_extras.object_utils import world_to_camera_view

from bl_ext.user_default import vecbake as vb

NAME = "vecbake_sil_objtest"
old = bpy.data.scenes.get(NAME)
if old:
    bpy.data.scenes.remove(old)
sc = bpy.data.scenes.new(NAME)
prev = bpy.context.window.scene
bpy.context.window.scene = sc

for i, x in enumerate((-4.0, 4.0)):
    bpy.ops.mesh.primitive_cylinder_add(vertices=8, radius=0.7, depth=1.6,
                                        location=(x, 0, 0))
    bpy.context.active_object.name = f"part{i}"

cam_data = bpy.data.cameras.new("c")
cam = bpy.data.objects.new("c", cam_data)
sc.collection.objects.link(cam)
cam.location = (0, -12, 2.5)
cam.rotation_euler = (math.radians(78), 0, 0)
sc.camera = cam
sc.render.resolution_x, sc.render.resolution_y = 1200, 700
sc.frame_start = sc.frame_end = 1
sc.frame_set(1)

W, H = 1200, 700
dg = bpy.context.evaluated_depsgraph_get()
box = {}
for ob in [o for o in sc.objects if o.type == "MESH"]:
    ev = ob.evaluated_get(dg)
    me = ev.to_mesh()
    xs = []
    ys = []
    for v in me.vertices:
        co = world_to_camera_view(sc, cam, ob.matrix_world @ v.co)
        xs.append(co.x * W)
        ys.append((1 - co.y) * H)
    ev.to_mesh_clear()
    box[ob.name] = (min(xs), min(ys), max(xs), max(ys))

fails = []
for mode in ("SMOOTH", "ANGLE"):
    s = vb.BakeSettings()
    s.frame_start = s.frame_end = 1
    s.width, s.height = W, H
    s.strokes = True
    s.fills = True
    s.curve_mode = mode
    s.curve_angle = 40.0
    s.accurate_order = False
    s.tol_px = 0.35
    vec = vb.api.bake_scene(bpy.context, s)

    # An element may bow past its own vertices -- that is what curving means --
    # but never by a share of the object it belongs to. A cross-object swap
    # lands it in the other cylinder, eight world units away.
    worst = 0.0
    culprit = None
    for el in vec.elements:
        b = box.get(el.key.split("/")[0])
        if not b:
            continue
        diag = math.hypot(b[2] - b[0], b[3] - b[1])
        g = el.geo.sample(0.0)
        for i in range(0, len(g), 2):
            d = max(b[0] - g[i], g[i] - b[2], b[1] - g[i + 1], g[i + 1] - b[3], 0.0)
            if d / diag > worst:
                worst = d / diag
                culprit = el.key
    ok = worst < 0.35
    print(f"{'PASS' if ok else 'FAIL'}  {mode}: worst point {worst * 100:.1f}% "
          f"of its object outside it ({culprit})")
    if not ok:
        fails.append(mode)

bpy.context.window.scene = prev
bpy.data.scenes.remove(sc)
print(f"\n{len(fails)} failure(s)" + (": " + ", ".join(fails) if fails else ""))
