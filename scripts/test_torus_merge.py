# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""A torus whose rim and hole stay put merges, and draws right between keyframes.

    blender -b --factory-startup --python scripts/test_torus_merge.py

A smooth torus under the scorecard's 60 degree orbit, seen from 30 and 60
degrees up, has two silhouette pieces -- rim and hole -- in every frame, and its
merged outline lands within 0.3 px of the truth halfway between keyframes. Until
26 October `judge_carry` still refused it: it scored the two subpaths joined end
to end as one loop against `walk_loop`'s bridged ring, which drops each ring's
closing segment and adds two chords across the body, and read 10.8 and 13.2 px
(docs/audits.md section 22). Multi-subpath outlines are now judged by Merge False
Outline, as sizing already treated them.

Must hold: both merge to one element; every drawn point at every keyframe within
the checker's allowance; no outline segment more than 3 px off at subframe 0.5.
Seen edge-on (15 degrees) the torus still does not merge -- its silhouette folds,
which is a different problem and not pinned here.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
src = open(os.path.join(HERE, "lab_torus.py"), encoding="utf-8").read()
ns = {"__name__": "lab_torus", "__file__": os.path.join(HERE, "lab_torus.py")}
exec(compile(src.split('\nif __name__ == "__main__":')[0], "lab_torus", "exec"), ns)

failures = []
for elevation in (30.0, 60.0):
    row = ns["run"](elevation)
    geo = row["checks"]["check_geometry"]
    seg = row["checks"]["check_outline_segments"]
    if not row["merged"] or row["elements"] != 1:
        failures.append(f"{elevation:.0f} deg: did not merge ({row['elements']} elements)")
    if geo["offenders"]:
        failures.append(f"{elevation:.0f} deg: {geo['offenders']} geometry offenders")
    if seg["over_3px"]:
        failures.append(f"{elevation:.0f} deg: {seg['over_3px']} segments over 3 px")
print("[torus-merge]", "PASS" if not failures else "FAIL: " + "; ".join(failures))
sys.stdout.flush()
os._exit(1 if failures else 0)
