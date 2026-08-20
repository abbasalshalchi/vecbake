"""Edges > Blender Smooth by Angle draws what the viewport shades hard.

    "<blender>" --background --factory-startup --python scripts/test_edge_shading.py

The same idea as Curves > Blender Shading, applied to which strokes exist
rather than to whether they bend: take the threshold from the object's own
shading instead of from a number in the panel.

Worth being precise about what is new, because part of it already worked. The
baker reads the *evaluated* mesh, and a Smooth by Angle modifier writes
`sharp_edge` onto its output, so Sharp Edges has always picked those up through
`edge_marked_sharp` -- which are kept whatever the angle says. Where the modes
genuinely part company is everything the modifier is not:

  * shade-flat, no modifier -- the viewport facets the object, but Sharp Edges
    only draws seams steeper than the panel angle, so a 32-sided cylinder loses
    all 32 of its barrel seams at any threshold above 11.25 degrees;
  * shade-smooth -- the viewport draws no hard edge anywhere, but Sharp Edges
    still creases anything over the panel angle, so a smooth-shaded cube comes
    out fully wireframed;
  * and with a modifier, the panel angle stops mattering at all, so the two
    cannot drift apart when someone edits one and forgets the other.

The 0-degree case needs care rather than trust: two faces of a flat panel dot
to 1 - 1e-16, which is below cos(0), so a bare comparison puts a crease down
the middle of every flat surface in the scene. That is pinned below.
"""

import os
import sys

import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "addon"))

from vecbake import bake as bake_mod          # noqa: E402
from vecbake.settings import BakeSettings     # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {label}{'  ' + detail if detail else ''}")
    if not cond:
        fails.append(label)


def fresh():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    scene.collection.objects.link(cam)
    cam.location = (6.0, -6.0, 4.5)
    cam.rotation_euler = (1.05, 0.0, 0.785)
    scene.camera = cam
    scene.frame_start = scene.frame_end = 1
    scene.render.resolution_x, scene.render.resolution_y = 640, 400
    return scene


def activate(obj):
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return obj


def strokes(obj, mode, crease=30.0):
    s = BakeSettings()
    s.objects = [obj]
    s.camera = bpy.context.scene.camera
    s.edge_mode = mode
    s.crease_angle = crease
    s.fills = False
    s.chain_edges = False
    s.accurate_order = False
    s.cull_offscreen = False
    return {el.key for el in bake_mod.bake(bpy.context, s).elements}


# --- the modifier, which is the case that already half-worked --------------
# `shade_auto_smooth` adds the real "Smooth by Angle" node modifier.
# `shade_smooth_by_angle`, confusingly, adds no modifier at all -- it writes
# sharp_edge straight onto the mesh, which is a different feature.
fresh()
bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=1.0, depth=2.0)
cyl = activate(bpy.context.active_object)
bpy.ops.object.shade_auto_smooth(angle=np.radians(30.0))

check("the modifier is what got added",
      [m.type for m in cyl.modifiers] == ["NODES"], f"{[m.name for m in cyl.modifiers]}")
reported = bake_mod._smooth_by_angle(cyl)
check("its angle is read back",
      reported is not None and abs(reported - 30.0) < 0.01, f"got {reported}")
check("the mode agrees with Sharp Edges typed to the same number",
      strokes(cyl, "SHADING") == strokes(cyl, "SHARP", crease=30.0))

# Move the modifier, leave the panel alone. The mode has to follow.
before = strokes(cyl, "SHADING")
bpy.ops.object.shade_auto_smooth(angle=np.radians(5.0))
after = strokes(cyl, "SHADING")
check("changing the modifier changes the strokes",
      after != before, f"{len(before)} -> {len(after)}")
check("...to what Sharp Edges would give if retyped by hand",
      strokes(cyl, "SHADING") == strokes(cyl, "SHARP", crease=5.0))

# --- shade-flat with no modifier: the viewport facets it -------------------
fresh()
bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=1.0, depth=2.0)
flat_cyl = activate(bpy.context.active_object)
bpy.ops.object.shade_flat()
check("a shade-flat object reports 0 degrees",
      bake_mod._shading(flat_cyl, flat_cyl.data, len(flat_cyl.data.polygons))[2] == 0.0)

sh = strokes(flat_cyl, "SHADING")
sharp30 = strokes(flat_cyl, "SHARP", crease=30.0)
check("faceting is drawn, and Sharp Edges at 30 misses it",
      len(sh) > len(sharp30), f"SHADING {len(sh)} vs SHARP30 {len(sharp30)}")
check("...and it keeps everything hidden-line removal keeps",
      sh == strokes(flat_cyl, "HIDDEN"), f"{len(sh)} vs {len(strokes(flat_cyl, 'HIDDEN'))}")

# --- shade-smooth: no hard edge anywhere ----------------------------------
fresh()
bpy.ops.mesh.primitive_cube_add(size=2.0)
smooth_cube = activate(bpy.context.active_object)
bpy.ops.object.shade_smooth()
check("a shade-smooth object reports 180 degrees",
      bake_mod._shading(smooth_cube, smooth_cube.data,
                        len(smooth_cube.data.polygons))[2] == 180.0)
sm = strokes(smooth_cube, "SHADING")
check("a smooth-shaded cube keeps only its outline",
      sm == strokes(smooth_cube, "BOUNDARY"),
      f"{len(sm)} vs {len(strokes(smooth_cube, 'BOUNDARY'))} for silhouette alone")
check("...where Sharp Edges would wireframe it",
      len(strokes(smooth_cube, "SHARP", crease=30.0)) > len(sm))

# --- the epsilon: a flat plane has no creases in it -----------------------
fresh()
bpy.ops.mesh.primitive_grid_add(x_subdivisions=6, y_subdivisions=6, size=2.0)
grid = activate(bpy.context.active_object)
bpy.ops.object.shade_flat()
gr = strokes(grid, "SHADING")
edge_only = strokes(grid, "BOUNDARY")
check("a flat plane gets no crease down its middle",
      gr == edge_only, f"{len(gr)} strokes vs {len(edge_only)} for its border alone")

print()
print(f"{len(fails)} failure(s)" + (": " + ", ".join(fails) if fails else ""))
sys.exit(1 if fails else 0)
