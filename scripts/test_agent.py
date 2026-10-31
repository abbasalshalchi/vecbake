# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""The agent interface: what it refuses, and that it bakes what the panel bakes.

    blender -b --factory-startup --python scripts/test_agent.py

Built here, nothing loaded: a cube and a cone under a camera that moves. The bake
that matters most is the comparison. The same scene goes through the operator a
person's button runs and through `window_manager.vecbake.start`, and every file must
come out byte for byte the same, or an agent would be shipping something nobody saw.

What runs off a timer needs a window, which `-b` has not got:
scripts/agent_window_check.py covers that half.
"""

import json
import os
import shutil
import sys
import tempfile

import addon_utils
import bpy

addon_utils.enable("bl_ext.user_default.vecbake", default_set=False, persistent=False)
from bl_ext.user_default.vecbake import agent  # noqa: E402

FAILS = []


def check(label, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + label + (f"  {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        FAILS.append(label)


def build():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 12
    scene.frame_set(5)
    scene.render.resolution_x, scene.render.resolution_y = 640, 360
    bpy.ops.mesh.primitive_cube_add(size=2, location=(-1.5, 0, 0))
    bpy.context.active_object.name = "Box"
    bpy.ops.mesh.primitive_cone_add(location=(1.5, 0, 0))
    bpy.context.active_object.name = "Cone"
    bpy.ops.object.camera_add(location=(0, -9, 3), rotation=(1.25, 0, 0))
    cam = bpy.context.active_object
    cam.name = "Shot"
    scene.camera = cam
    cam.keyframe_insert("location", frame=1)
    cam.location.x += 3
    cam.keyframe_insert("location", frame=12)
    return cam


def read_tree(folder):
    return {name: open(os.path.join(folder, name), "rb").read() for name in sorted(os.listdir(folder))}


def main():
    wm = bpy.context.window_manager
    tmp = tempfile.mkdtemp(prefix="vecbake_agent_")
    # An unsaved file's "//vecbake/" resolves against the working directory, and run
    # from the Blender folder that is this repository.
    os.chdir(tmp)
    scene = bpy.context.scene
    build()

    # --- describe ------------------------------------------------------------
    info = wm.vecbake.describe()
    json.dumps(info)
    check("describe() is plain data", True)
    check("describe() finds the camera", info["scene"]["cameras"] == ["Shot"], info["scene"])
    check("describe() counts what would be drawn", info["scene"]["drawable_objects"] == 2, info["scene"])
    check("describe() says the file is unsaved", info["blend"]["saved"] is False, info["blend"])
    check("describe() names every format", set(info["formats"]) >= {"lerpa", "json", "lottie", "svg", "still", "preview"})
    check("describe() starts idle", info["job"]["state"] == "idle", info["job"])

    # --- the panel's enums and this module's cannot drift apart ----------------
    props = scene.vecbake
    for name, choices in agent.CHOICES.items():
        items = {e.identifier for e in props.bl_rna.properties[name].enum_items}
        check(f"{name} choices match the panel", items == set(choices), f"{items} vs {choices}")

    # --- refusals: nothing runs, nothing is written, the error names the problem --
    target = os.path.join(tmp, "refused")
    for label, request, needle in (
        ("an unknown key", {"formts": ["lerpa"]}, "formts"),
        ("an unknown setting", {"settings": {"stroke_widht": 2}}, "stroke_widht"),
        ("an unknown format", {"directory": target, "formats": ["gif"]}, "gif"),
        ("a bad choice", {"directory": target, "settings": {"edge_mode": "Hidden"}}, "edge_mode"),
        ("a string for a number", {"directory": target, "settings": {"stroke_width": "2"}}, "stroke_width"),
        ("a number for a flag", {"directory": target, "settings": {"fills": 1}}, "fills"),
        ("a flag for a number", {"directory": target, "settings": {"frame_step": True}}, "frame_step"),
        ("a short colour", {"directory": target, "settings": {"stroke_color": [1, 0]}}, "stroke_color"),
        ("a missing object", {"directory": target, "settings": {"objects": ["Nope"]}}, "Nope"),
        ("a mesh as the camera", {"directory": target, "settings": {"camera": "Box"}}, "Box"),
        ("a request that is not a dict", ["lerpa"], "dict"),
    ):
        got = wm.vecbake.start(request)
        check(f"refuses {label}", got["state"] == "failed" and needle in (got["error"] or ""), got["error"])
    check("a refusal writes nothing", not os.path.exists(target))
    check("a refusal leaves no bake running", not props.baking)

    # --- an unsaved file: the relative folder fails loudly, an analysis needs none --
    got = wm.vecbake.start({"formats": ["lerpa"]})
    check("a // folder on an unsaved file fails", got["state"] == "failed" and "saved" in (got["error"] or ""), got)
    got = wm.vecbake.start({"formats": []})
    check("no formats measures without writing", got["state"] == "done" and got["files"] == [], got)
    check("the measurement counts elements", got["stats"].get("elements", 0) > 0, got["stats"])
    check("the measurement leaves no folder", not os.path.exists(bpy.path.abspath(props.out_dir)))
    check("the result is plain data", bool(json.dumps(got)))
    clip = got["clips"][0]
    check("the clip names its camera", clip["camera"] == "Shot", clip)
    check("the clip says it moved", clip["motion"] is not None, clip)

    # --- the point of it: the same bake as the button -------------------------
    all_formats = ["lerpa", "json", "lottie", "svg", "still", "preview"]
    for name in ("emit_lerpa", "emit_json", "emit_lottie", "emit_svg", "emit_still", "emit_preview"):
        setattr(props, name, True)
    props.basename = "same"
    props.out_dir = os.path.join(tmp, "panel")
    check("the panel's bake runs", bpy.ops.vecbake.export() == {"FINISHED"})
    panel = read_tree(props.out_dir)

    got = wm.vecbake.start({"directory": os.path.join(tmp, "agent"), "basename": "same", "formats": all_formats})
    check("the agent's bake finishes", got["state"] == "done", got["error"])
    mine = read_tree(os.path.join(tmp, "agent"))
    check("the same files", sorted(panel) == sorted(mine), f"{sorted(panel)} vs {sorted(mine)}")
    differ = [n for n in panel if panel[n] != mine.get(n)]
    check("byte for byte", not differ, differ)
    check("reports every file with its size",
          {os.path.basename(f["path"]): f["bytes"] for f in got["files"]} == {n: len(b) for n, b in mine.items()},
          got["files"])

    # --- a request borrows the panel's settings and does not change them --------
    before = (props.stroke_width, props.basename, props.out_dir, props.emit_json)
    got = wm.vecbake.start({"directory": os.path.join(tmp, "wide"), "basename": "wide", "formats": ["json"],
                            "settings": {"stroke_width": 9.0, "objects": ["Cone"]}})
    check("an override bakes", got["state"] == "done", got["error"])
    check("an override leaves the panel alone", before == (props.stroke_width, props.basename, props.out_dir, props.emit_json))
    wide = open(os.path.join(tmp, "wide", "wide.Shot.json"), "rb").read()
    check("an override reaches the file", wide != mine["same.Shot.json"])
    check("an object list narrows the bake", got["stats"]["elements"] < wm.vecbake.start({"formats": []})["stats"]["elements"])

    # --- leaves the scene as it found it ---------------------------------------
    check("the frame is restored", scene.frame_current == 5, scene.frame_current)
    check("nothing still marked as baking", not props.baking)
    check("cancel with nothing running is harmless", wm.vecbake.cancel()["state"] == "done")

    # --- the bake's own error, from inside the job ------------------------------
    bpy.data.objects.remove(bpy.data.objects["Shot"])
    got = wm.vecbake.start({"formats": []})
    check("no camera comes back as an error", got["state"] == "failed" and "camera" in (got["error"] or ""), got)
    check("and clears the running flag", not props.baking)

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"{'FAILED ' + str(len(FAILS)) if FAILS else 'all passed'}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
