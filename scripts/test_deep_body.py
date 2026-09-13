"""A body deep enough to span the scene is drawn face by face, not merged.

    "<blender>/5.2/python/bin/python.exe" scripts/test_deep_body.py

One merged fill carries one depth per frame. That is enough for a fitting and
not enough for the body it is bolted to: a service module reaching from the
front of the scene to the back overlaps its own thrusters, and no single scalar
orders those pairs without contradicting itself. The graph fills with cycles and
breaking them spends constraints that were right -- a part that is always in
front ending up behind, which is not a fault in how the breaker chooses.

Drawn face by face the same body orders cleanly, because each face spans little
depth. `bake._deep_bodies` decides which bodies those are, and this pins the
decision -- including the two ways of getting it wrong that the measurement
ruled out.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import _addon                                              # noqa: E402

_addon.stub_blender()
bake = _addon.load("bake")
fails = []


def check(label, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {label}{'  ' + detail if detail else ''}")
    if not cond:
        fails.append(label)


# --- the scene the fix was measured on, in miniature ----------------------
# `body` reaches across most of the scene; `bolt` and `tip` are fittings on it.
# (dmin, dmax, x0, x1, y0, y1). The fittings sit on the body, on screen and in
# depth; `far` is clear of it in both.
BOX = (0.0, 10.0, 0.0, 10.0)
scene = {
    "body": [(0.0, 7.0) + BOX, (0.5, 7.5) + BOX, (1.0, 8.0) + BOX],
    "bolt": [(2.0, 2.6) + BOX, (2.5, 3.1) + BOX, (3.0, 3.6) + BOX],
    "tip":  [(6.0, 6.4) + BOX, (6.5, 6.9) + BOX, (7.0, 7.4) + BOX],
    "far":  [(9.0, 10.0, 40.0, 50.0, 40.0, 50.0),
             (9.5, 10.5, 40.0, 50.0, 40.0, 50.0),
             (10.0, 11.0, 40.0, 50.0, 40.0, 50.0)],
}
deep = bake._deep_bodies(scene, 50.0)
check("the body that spans the scene is split", deep == ["body"], f"got {deep}")

# --- the threshold is a threshold -----------------------------------------
# Zero is handled by the caller -- `depth_extent` is never even collected --
# so what this pins is that the bar is read, not that zero means "off" here.
check("a bar above every object splits nothing",
      bake._deep_bodies(scene, 99.9) == [],
      f"got {bake._deep_bodies(scene, 99.9)}")
# Only `body` has anything inside its range, so lowering the bar cannot reach
# the others -- depth is a necessary condition, not a sufficient one.
check("a low bar still only splits what holds something",
      bake._deep_bodies(scene, 1.0) == ["body"],
      f"got {bake._deep_bodies(scene, 1.0)}")

# --- the case that made the second condition necessary --------------------
# One object is the whole scene by definition: its depth range *is* the scene's
# range, so a depth-only rule splits it every time, for nothing -- there is no
# other element it could be ordered against.
lone = {"only": [(2.0, 9.0, 0.0, 10.0, 0.0, 10.0)]}
check("a scene of one object is left merged",
      bake._deep_bodies(lone, 50.0) == [], f"got {bake._deep_bodies(lone, 50.0)}")

# Two deep objects that never meet on screen have no pair to contradict either.
apart = {
    "left":  [(0.0, 9.0, 0.0, 10.0, 0.0, 10.0)],
    "right": [(0.0, 9.0, 90.0, 99.0, 0.0, 10.0)],
}
check("two deep objects that do not overlap on screen are left merged",
      bake._deep_bodies(apart, 50.0) == [], f"got {bake._deep_bodies(apart, 50.0)}")

# --- traffic is not the criterion -----------------------------------------
# The probe has a boom straddled by 59 objects that spans 8.9% of the scene and
# orders perfectly well by one scalar. A rule counting overlaps would split it;
# this one must not, however much sits inside its range.
crowd = {"boom": [(4.9, 5.1) + BOX], "near": [(0.0, 0.1) + BOX],
         "far": [(9.9, 10.0) + BOX]}
crowd.update({f"fitting{i}": [(4.95, 5.05) + BOX] for i in range(59)})
check("a shallow object with heavy traffic is left merged",
      "boom" not in bake._deep_bodies(crowd, 50.0))

# --- the scene's depth is a per-frame fact --------------------------------
# A camera orbiting a rigid model sees it deepen and flatten. An object deep
# only in the frames where everything is deep has not earned the split, so the
# fractions are per frame and averaged -- not measured against one global range.
# `steady` holds a constant 5-unit slab; the scene is 10 deep, then 100.
swing = {
    "steady": [(0.0, 5.0) + BOX, (0.0, 5.0) + BOX],
    "rest":   [(-1.0, 10.0) + BOX, (-1.0, 100.0) + BOX],
}
# frame 0: 5/10 = 50%.  frame 1: 5/100 = 5%.  mean 27.5%, below the bar.
check("a body deep in one frame only is not split on that frame alone",
      bake._deep_bodies(swing, 50.0) == ["rest"],
      f"got {bake._deep_bodies(swing, 50.0)}")

# --- a frame an object is missing from -----------------------------------
# Extents are gathered per frame, and an object with nothing finite in a frame
# (entirely behind the camera) has no row there. It has to leave a gap. When it
# was appended instead, every later frame of that object was compared against
# the scene one frame early.
#
# `gone` is behind the camera in frame 0. Aligned, frame 1 is where it and
# `body` meet, and `body` holds it. Shifted, gone's frame-1 row would be
# compared against frame 0, where `body` sits somewhere else entirely.
late = {
    "body": [(0.0, 8.0, 0.0, 10.0, 0.0, 10.0), (0.0, 8.0, 0.0, 10.0, 0.0, 10.0)],
    "gone": [None, (3.0, 4.0, 0.0, 10.0, 0.0, 10.0)],
    "edge": [(0.0, 10.0, 90.0, 99.0, 0.0, 10.0), (0.0, 10.0, 90.0, 99.0, 0.0, 10.0)],
}
check("a frame an object is missing from is a gap, not a shift",
      bake._deep_bodies(late, 50.0) == ["body"], f"got {bake._deep_bodies(late, 50.0)}")

# And a missing frame is not a shallow one: an object's share is its mean over
# the frames it is in. `blink` is 80% of the scene whenever it is there at all.
blink = {
    "blink": [(0.0, 8.0) + BOX, None, None, (0.0, 8.0) + BOX],
    "bolt":  [(2.0, 3.0) + BOX] * 4,
    "wall":  [(0.0, 10.0, 90.0, 99.0, 0.0, 10.0)] * 4,
}
check("frames an object is absent from do not dilute its depth share",
      "blink" in bake._deep_bodies(blink, 50.0), f"got {bake._deep_bodies(blink, 50.0)}")

# --- degenerate input -----------------------------------------------------
check("no objects is not a crash", bake._deep_bodies({}, 50.0) == [])
check("a scene with no depth at all is not a division by zero",
      bake._deep_bodies({"a": [(3.0, 3.0) + BOX], "b": [(3.0, 3.0) + BOX]},
                        50.0) == [])

print()
print(f"{len(fails)} failure(s)" + (": " + ", ".join(fails) if fails else ""))
raise SystemExit(1 if fails else 0)
