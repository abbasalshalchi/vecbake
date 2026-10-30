# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""Film the VecBake panel in a real Blender window, starting a real bake.

    blender --factory-startup -p 60 60 1400 960 --python scripts/film_blender.py -- <blend> <out_dir> [--still]

The window screenshots itself, so nothing else on the desktop is in the clip. It
bakes into <out_dir>/bake, and leaves with os._exit: no save, no quit.blend, no
preferences written. --still takes one shot of the set-up and stops, for placing
the pointer.
"""
import json
import os
import sys
import time

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "addon"))
import vecbake  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:]
BLEND, OUT, STILL = args[0], os.path.abspath(args[1]), "--still" in args
os.makedirs(os.path.join(OUT, "shots"), exist_ok=True)
SECONDS_AFTER_CLICK = 18.0
vecbake.register()
state = {"n": 0, "phase": "open", "t": 0.0, "ticks": 0}


def view3d():
    win = bpy.context.window_manager.windows[0]
    for area in win.screen.areas:
        if area.type == "VIEW_3D":
            ui = next(r for r in area.regions if r.type == "UI")
            main = next(r for r in area.regions if r.type == "WINDOW")
            return win, area, ui, main
    raise RuntimeError("no 3D view")


def shot():
    win, *_ = view3d()
    state["n"] += 1
    state.setdefault("times", []).append(time.time())
    path = os.path.join(OUT, "shots", f"{state['n']:05d}.png")
    with bpy.context.temp_override(window=win):
        bpy.ops.screen.screenshot(filepath=path, check_existing=False)
    return path


def tick():
    state["ticks"] += 1
    try:
        if state["phase"] == "open":
            # Without the file's own window layout, whose panel fills the viewport.
            state["phase"] = "maximise"
            bpy.ops.wm.open_mainfile(filepath=BLEND, load_ui=False)
            return 1.0
        win, area, ui, main = view3d()
        if state["phase"] == "maximise":
            with bpy.context.temp_override(window=win, area=area):
                bpy.ops.screen.screen_full_area()
            state["phase"] = "setup"
            return 0.5
        if state["phase"] == "setup":
            bpy.context.preferences.view.ui_scale = 1.35
            space = area.spaces.active
            space.show_region_ui = True
            space.region_3d.view_perspective = "CAMERA"
            # Slide the frame out from under the panel.
            space.region_3d.view_camera_offset = (0.2, 0.0)
            space.region_3d.view_camera_zoom = -6
            space.shading.type = "SOLID"
            space.overlay.show_overlays = False
            space.show_gizmo = False
            space.show_region_toolbar = False
            props = bpy.context.scene.vecbake
            props.out_dir = os.path.join(OUT, "bake")
            props.basename = "apollo-soyuz"
            # What the last audit in this file said is not about this bake.
            props.audit_summary = ""
            props.audit_rows = ""
            props.last_stats = ""
            state["phase"] = "tab"
            return 0.5
        if state["phase"] == "tab":
            try:
                ui.active_panel_category = "VecBake"
            except Exception as err:
                print("[film] tab:", err, flush=True)
            state["phase"] = "scroll"
            return 0.5
        if state["phase"] == "scroll":
            # The button sits under a long panel: scroll till it is on screen.
            with bpy.context.temp_override(window=win, area=area, region=ui):
                bpy.ops.view2d.scroll_down(page=True)
            state["scrolls"] = state.get("scrolls", 0) + 1
            if state["scrolls"] >= int(os.environ.get("FILM_SCROLLS", "4")):
                state["phase"] = "still"
            return 0.5
        if state["phase"] == "still":
            print("[film] still", shot(), flush=True)
            if STILL:
                os._exit(0)
            state["phase"] = "before"
            state["t"] = time.time()
            return 0.1
        if state["phase"] == "before":
            shot()
            if time.time() - state["t"] > 1.5:
                with bpy.context.temp_override(window=win, area=area, region=ui):
                    bpy.ops.vecbake.export("INVOKE_DEFAULT")
                print("[film] clicked at shot", state["n"], flush=True)
                state["clicked"] = time.time()
                state["phase"] = "baking"
                state["t"] = time.time()
            return 0.05
        if state["phase"] == "baking":
            shot()
            if time.time() - state["t"] > SECONDS_AFTER_CLICK:
                print("[film] shots", state["n"], flush=True)
                json.dump({"click": state["clicked"] - state["times"][0],
                           "times": [t - state["times"][0] for t in state["times"]]},
                          open(os.path.join(OUT, "shots", "shots.json"), "w"))
                os._exit(0)
            return 0.05
    except Exception as err:
        print("[film] failed:", repr(err), flush=True)
        os._exit(1)
    return 0.1


bpy.app.timers.register(tick, first_interval=1.5, persistent=True)
