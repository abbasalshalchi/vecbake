"""freeze_hidden must never hand back a frame with no geometry in it.

    "<blender>/5.1/python/bin/python.exe" scripts/test_freeze_hidden.py

Most samplers write geometry every frame whether the element is visible or not,
so the only thing freeze_hidden ever had to do was pin hidden runs to the last
visible pose and let RDP collapse them.

Merged fills break that assumption. `_sample_object` writes nothing at all in a
frame whose outline is not one simple loop -- the slot keeps its initial None --
and an object that only resolves partway into the clip has None in every frame
before that. There is no earlier pose to hold, so the run came back unfilled and
reduce_points was handed a sample it cannot subtract:

    TypeError: 'NoneType' object is not iterable

which fails the entire bake, not one frame of it. Reproduced on the Voyager
probe with Merge Fills on: golden-record found a ring in only 3 frames of 14.
"""

import importlib.util
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON = os.path.join(ROOT, "addon", "vecbake")


def _load(name):
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


reduce = _load("reduce")
fails = []


def check(label, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {label}{'  ' + detail if detail else ''}")
    if not cond:
        fails.append(label)


SQ = [0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0]

# --- the bake-killer: visible only at the end -----------------------------
geo = [None] * 11 + [SQ, SQ, SQ]
vis = [0.0] * 11 + [1.0, 1.0, 1.0]
out = reduce.freeze_hidden(geo, vis)
check("no frame is left without geometry", all(g is not None for g in out),
      f"{sum(g is None for g in out)} of {len(out)} still empty")
check("the real poses are untouched", out[11:] == [SQ, SQ, SQ])
check("and reduce_points survives it",
      reduce.reduce_points([i / 24 for i in range(14)], out, 0.35) is not None)

# --- visible only at the start, and only in the middle --------------------
lead = reduce.freeze_hidden([SQ, SQ] + [None] * 4, [1.0, 1.0, 0.0, 0.0, 0.0, 0.0])
check("a trailing empty run borrows backwards", all(g is not None for g in lead))
mid = reduce.freeze_hidden([None, None, SQ, None, None], [0.0, 0.0, 1.0, 0.0, 0.0])
check("an empty run on both sides is filled", all(g is not None for g in mid))

# --- never visible at all: the caller drops it, so leave it alone ---------
ghost = reduce.freeze_hidden([None] * 4, [0.0] * 4)
check("an element with nothing to borrow is returned as-is, not crashed on",
      ghost == [None] * 4)

# --- an element must fade in and out where it belongs ----------------------
# Opacity ramps over one frame and the player interpolates geometry across the
# same span, so the pose at the last hidden frame is one the viewer watches the
# element travel from. Every hidden frame therefore takes the nearest visible
# pose: the run still collapses for the reducer, and both ends of it already
# match the frame they ramp into.
A = [0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0]
B = [9.0, 9.0, 8.0, 9.0, 8.0, 8.0, 9.0, 8.0]
seq = [A, A, B, B, B, A]
vis2 = [1.0, 1.0, 0.0, 0.0, 0.0, 1.0]
held = reduce.freeze_hidden(seq, vis2)
check("a hidden frame beside a visible one matches it", held[2] == A,
      f"got {held[2]}")
check("...at the far end of the run too", held[4] == A, f"got {held[4]}")
check("...and the run in between is one pose, so it collapses",
      held[2] == held[3] == held[4], f"got {held[2:5]}")

# the pose it borrows really is the nearer one, not simply the last seen
seq3 = [A, B, B, B, B, A]
vis3 = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
h3 = reduce.freeze_hidden(seq3, vis3)
check("early hidden frames take the pose before them", h3[1] == A, f"got {h3[1]}")
check("late hidden frames take the pose after them", h3[4] == A, f"got {h3[4]}")

print()
print(f"{len(fails)} failure(s)" + (": " + ", ".join(fails) if fails else ""))
raise SystemExit(1 if fails else 0)
