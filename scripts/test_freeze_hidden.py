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

# --- the ordinary path must not change ------------------------------------
# A hidden run between two visible ones still pins to the last visible pose,
# and the frames either side of a flip are still left alone for the opacity
# ramp to interpolate against.
A = [0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0]
B = [9.0, 9.0, 8.0, 9.0, 8.0, 8.0, 9.0, 8.0]
seq = [A, A, B, B, B, A]
vis2 = [1.0, 1.0, 0.0, 0.0, 0.0, 1.0]
held = reduce.freeze_hidden(seq, vis2)
check("a hidden run still pins to the last visible pose", held[3] == A,
      f"got {held[3]}")
check("...and the frames beside a flip keep their own", held[2] == B and held[4] == B)

print()
print(f"{len(fails)} failure(s)" + (": " + ", ".join(fails) if fails else ""))
raise SystemExit(1 if fails else 0)
