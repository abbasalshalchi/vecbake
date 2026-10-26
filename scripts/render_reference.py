# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""Blender's own picture of a scorecard frame, to put beside the bake.

    blender -b --factory-startup <blend> --python scripts/render_reference.py -- \
        --out <png> [--camera <name>] [--frame-index i] [--scale 50]

The camera is set up exactly as `scorecard.py` does it (its own move, or the 60
degree orbit), and the picture is rendered at the bake's own 1920 x 1080, so a
bake drawn with `fit="contain"` in a 16:9 box lands on it pixel for pixel. The
look is chosen to compare shapes, not to be pretty: Workbench, flat light, one
fill colour and the object outline on the page's ground -- what a merged fill and its
outline are trying to be. Creases are not outlined by Workbench; the bake draws
them, so expect lines on the bake the render does not have.

The default frame is the middle one, the scorecard audit's second frame.
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_defs = {"__file__": os.path.join(HERE, "scorecard.py"), "__name__": "scorecard_defs"}
_src = open(_defs["__file__"], encoding="utf-8").read()
exec(compile(_src.split("\ndef main():")[0], "scorecard_defs", "exec"), _defs)

import bpy  # noqa: E402



def linear(hex_colour):
    """'#rrggbb' as linear RGB, which is what Workbench's colours are in."""
    out = []
    for i in (1, 3, 5):
        c = int(hex_colour[i:i + 2], 16) / 255.0
        out.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return tuple(out)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--camera")
    ap.add_argument("--frame-index", type=int)
    ap.add_argument("--scale", type=int, default=50)
    # The bake's own default palette (fills, lines) and the ground to show it on.
    ap.add_argument("--fill", default="#c7ccd6")
    ap.add_argument("--ground", default="#6a707a")
    args = ap.parse_args(argv)
    scene = bpy.context.scene
    if args.camera:
        scene.camera = bpy.data.objects[args.camera]
    camera = _defs["setup_camera"](scene)
    n = scene.frame_end - scene.frame_start + 1
    index = n // 2 if args.frame_index is None else args.frame_index
    scene.frame_set(scene.frame_start + index)

    r = scene.render
    r.engine = "BLENDER_WORKBENCH"
    r.resolution_x, r.resolution_y, r.resolution_percentage = 1920, 1080, args.scale
    r.pixel_aspect_x = r.pixel_aspect_y = 1.0
    r.film_transparent = False
    # A scene set to render video only offers FFMPEG until its media type is an
    # image again (Blender 5.x keeps the two apart).
    if hasattr(r.image_settings, "media_type"):
        r.image_settings.media_type = "IMAGE"
    r.image_settings.file_format = "PNG"
    # Plain colour, whatever the scene's own grade: the doughnut tutorial's
    # exposure turned this whole picture white.
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    shading = scene.display.shading
    shading.light = "FLAT"
    shading.color_type = "SINGLE"
    shading.single_color = linear(args.fill)
    shading.show_object_outline = True
    shading.object_outline_color = (0.0, 0.0, 0.0)
    shading.show_cavity = False
    shading.show_shadows = False
    if scene.world is None:
        scene.world = bpy.data.worlds.new("reference")
    scene.world.color = linear(args.ground)
    r.filepath = os.path.abspath(args.out)
    bpy.ops.render.render(write_still=True)
    print("[reference]", os.path.abspath(args.out), "frame", scene.frame_current,
          "index", index, "of", n, camera["mode"], flush=True)


try:
    main()
except Exception:
    import traceback
    traceback.print_exc()
sys.stdout.flush()
os._exit(0)
