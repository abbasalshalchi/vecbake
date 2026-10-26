# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""Sizing a merged outline's subpaths does not depend on which frame comes last.

    blender -b --factory-startup --python scripts/test_piece_sizing.py

A tilted torus under a 20 mm orbit (lab_torus.py: 10 degrees up, 15 degrees of
tilt) shows its hole from frame 8 to 24 of 31: one silhouette piece, then two,
then one. Until 26 October the measuring pass sent one-piece frames down the
single-ring path, which set `piece_needs` to one number and dropped the hole's,
so the torus was sized as one ring when the clip ended on one piece and as two
when it ended on two (docs/audits.md section 23).

Must hold: the full clip, the clip cut to end with the hole open, and the clip
cut to start with it open all size the torus as the same two subpaths.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
src = open(os.path.join(HERE, "lab_torus.py"), encoding="utf-8").read()
ns = {"__name__": "lab_torus", "__file__": os.path.join(HERE, "lab_torus.py")}
exec(compile(src.split('\nif __name__ == "__main__":')[0], "lab_torus", "exec"), ns)
bpy, api, SETTINGS = ns["bpy"], ns["api"], ns["SETTINGS"]

sizes = {}
for first, last in ((1, 31), (1, 20), (8, 31)):
    scene, torus, cam = ns["build"](10.0, tilt=15.0, lens=20.0)
    scene.frame_start, scene.frame_end = first, last
    for key, value in SETTINGS.items():
        setattr(scene.vecbake, key, value)
    settings = scene.vecbake.to_bake_settings(bpy.context)
    settings.merge_false_px = 0.0     # measure the sizing, do not refuse on it
    vec = api.bake_scene(bpy.context, settings)
    el = next((e for e in vec.elements if e.key == "Torus/m"), None)
    sizes[(first, last)] = list(el.subpaths) if el is not None and el.subpaths else None
    print(f"[sizing] frames {first}-{last}: subpaths {sizes[(first, last)]}", flush=True)

failures = []
if any(v is None or len(v) != 2 for v in sizes.values()):
    failures.append(f"not two subpaths in every clip: {sizes}")
print("[sizing]", "PASS" if not failures else "FAIL: " + "; ".join(failures))
sys.stdout.flush()
os._exit(1 if failures else 0)
