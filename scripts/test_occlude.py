"""Occlusion culling must be all-or-nothing per element.

    python scripts/test_occlude.py

Runs outside Blender -- occlude.py needs numpy and ir, and neither touches bpy.

The invariant under test is not "does it cull the right things". It is the one
whose violation shipped: a fill may be removed from the *whole clip* or kept
for the whole clip, and nothing in between. Hiding an element for some frames
only is what produced the artefacts a user reported as flickering and
momentary transparency:

  * visibility becomes an opacity track and the player interpolates it, so an
    element hidden at one keyframe and shown at the next fades in across the
    gap instead of appearing -- translucent while it is the frontmost thing;

  * an element pruned from the ordering pass for some frames carries an
    integer paint rank at one end of a segment and a raw camera distance at
    the other, and interpolating between those is meaningless.

Both are invisible in a single-frame check, which is why the original testing
missed them. Every assertion below spans at least two frames.
"""

import importlib.util
import os
import sys
import types

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON = os.path.join(ROOT, "addon", "vecbake")


def _load(name):
    """Import an add-on module without running its bpy-importing __init__."""
    if "_vb" not in sys.modules:
        pkg = types.ModuleType("_vb")
        pkg.__path__ = [ADDON]
        sys.modules["_vb"] = pkg
    spec = importlib.util.spec_from_file_location(
        f"_vb.{name}", os.path.join(ADDON, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"_vb.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


ir = _load("ir")
occlude = _load("occlude")

W = H = 100


class Settings:
    occlusion_px = 5.0


def quad(x0, y0, x1, y1):
    return [x0, y0, x1, y0, x1, y1, x0, y1]


def scene(specs, nframes):
    """specs: {key: [(geo, depth, vis) per frame]}"""
    elements, slots = [], {}
    for key, frames in specs.items():
        elements.append(ir.Element(key=key, kind=ir.FILL, npts=4, group=key,
                                   color=(1, 1, 1)))
        slots[key] = {
            "geo": [f[0] for f in frames],
            "depth": np.array([f[1] for f in frames], dtype=np.float64),
            "vis": np.array([f[2] for f in frames], dtype=np.float64),
        }
    items = [[{"key": k} for k in specs] for _ in range(nframes)]
    return elements, slots, items


FULL = quad(0, 0, W, H)
CORNER = quad(0, 0, 10, 10)

fails = []


def check(label, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {label}{'  ' + detail if detail else ''}")
    if not cond:
        fails.append(label)


# --- 1. hidden in every frame -> gone everywhere -------------------------
els, slots, items = scene({
    "front": [(FULL, 1.0, 1.0), (FULL, 1.0, 1.0)],
    "back":  [(FULL, 2.0, 1.0), (FULL, 2.0, 1.0)],
}, 2)
rep = occlude.apply(els, slots, items, Settings(), 2, W, H)
check("a fill behind an opaque fill in every frame is culled",
      slots["back"]["vis"].max() == 0.0, f"culled={rep['culled']}")
check("the fill in front is untouched",
      list(slots["front"]["vis"]) == [1.0, 1.0])
check("only the culled key leaves the ordering pass",
      [[i["key"] for i in f] for f in items] == [["front"]] * 2)

# --- 2. hidden in ONE frame only -> kept, and kept OPAQUE ----------------
# This is the regression. 'back' is covered at frame 0 and clear at frame 1.
els, slots, items = scene({
    "front": [(FULL, 1.0, 1.0), (CORNER, 1.0, 1.0)],
    "back":  [(FULL, 2.0, 1.0), (FULL,   2.0, 1.0)],
}, 2)
occlude.apply(els, slots, items, Settings(), 2, W, H)
check("a fill hidden in only some frames survives",
      slots["back"]["vis"].max() > 0.0)
check("...and its visibility is NOT zeroed for the frame it was hidden in",
      list(slots["back"]["vis"]) == [1.0, 1.0],
      f"got {list(slots['back']['vis'])}")
check("...and it is never pruned from the ordering pass",
      all("back" in [i["key"] for i in f] for f in items))

# --- 3. a translucent fill cannot hide anything --------------------------
els, slots, items = scene({
    "glass": [(FULL, 1.0, 0.5), (FULL, 1.0, 0.5)],
    "back":  [(FULL, 2.0, 1.0), (FULL, 2.0, 1.0)],
}, 2)
occlude.apply(els, slots, items, Settings(), 2, W, H)
check("a half-opaque fill does not occlude what is behind it",
      slots["back"]["vis"].max() > 0.0)

# --- 4. offscreen geometry is left to Cull Offscreen ---------------------
OFF = quad(-400, -400, -300, -300)
els, slots, items = scene({
    "front":   [(FULL, 1.0, 1.0), (FULL, 1.0, 1.0)],
    "offside": [(OFF, 2.0, 1.0), (OFF, 2.0, 1.0)],
}, 2)
occlude.apply(els, slots, items, Settings(), 2, W, H)
check("geometry outside the canvas is not this pass's decision",
      slots["offside"]["vis"].max() > 0.0)

print()
print(f"{len(fails)} failure(s)" + (": " + ", ".join(fails) if fails else ""))
raise SystemExit(1 if fails else 0)
