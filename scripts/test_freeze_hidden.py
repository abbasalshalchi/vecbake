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
# Opacity ramps across the frame between the last visible frame and the first
# hidden one, so the element is *drawn* for that whole frame -- 83% down to 17%
# -- and its pose over that span is interpolated between the two. Pinning the
# hidden end to the visible pose therefore holds the element still while the
# object it belongs to moves off, and what the viewer sees is a ghost of the
# stroke a frame of motion behind the model. Measured on a seven-primitive
# scene: ten elements drawn up to 21px outside their own object's true bounds,
# every one of them between frames and none on a boundary.
#
# So a hidden frame that touches a visible one keeps its own pose when the
# sampler wrote one, and only the interior of a run is pinned.
A = [0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0]
B = [9.0, 9.0, 8.0, 9.0, 8.0, 8.0, 9.0, 8.0]
C = [4.0, 4.0, 5.0, 4.0, 5.0, 5.0, 4.0, 5.0]
seq = [A, A, B, C, B, A]
vis2 = [1.0, 1.0, 0.0, 0.0, 0.0, 1.0]
held = reduce.freeze_hidden(seq, vis2)
check("a hidden frame beside a visible one keeps its own pose", held[2] == B,
      f"got {held[2]}")
check("...at the far end of the run too", held[4] == B, f"got {held[4]}")
check("...and the interior is still pinned, so the run collapses",
      held[3] == A, f"got {held[3]}")

# The fault this used to guard is a frame with *no* pose, which is what a
# merged fill writes when its outline is not one ring. There is nothing of its
# own to keep, so it must still borrow -- and borrow the nearer end, or an
# element reappearing far away fades in while travelling the whole distance.
# That was part_001, the magnetometer boom, sweeping 2900px across the frame.
seq3 = [A, None, None, None, None, A]
vis3 = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
h3 = reduce.freeze_hidden(seq3, vis3)
check("a frame with no pose still borrows", all(g is not None for g in h3))
check("early empty frames take the pose before them", h3[1] == A, f"got {h3[1]}")
check("late empty frames take the pose after them", h3[4] == A, f"got {h3[4]}")

# And a boundary frame with no pose of its own borrows rather than staying None
h4 = reduce.freeze_hidden([A, None, A], [1.0, 0.0, 1.0])
check("a boundary frame with nothing of its own borrows too", h4[1] == A,
      f"got {h4[1]}")


# ---- blind fades ----------------------------------------------------------
# A boundary frame with no pose is where freeze_hidden runs out of road: it has
# to borrow, and whatever it borrows is a pose the fade is then drawn across.
# blind_fade_samples takes the fade away instead of inventing the pose.
T = [0.0, 1.0, 2.0, 3.0]

ot, ov = reduce.blind_fade_samples(T, [1.0, 1.0, 0.0, 0.0], [A, A, None, None])
check("a fade off a missing pose gains a key just past the last real frame",
      len(ot) == 5 and abs(ot[2] - 1.02) < 1e-9, f"got {ot}")
check("...and that key is zero, so nothing is drawn across the gap",
      ov[2] == 0.0, f"got {ov}")

ot, ov = reduce.blind_fade_samples(T, [0.0, 0.0, 1.0, 1.0], [None, None, A, A])
check("a fade *in* off a missing pose is collapsed the same way",
      len(ot) == 5 and abs(ot[2] - 1.98) < 1e-9, f"got {ot}")
check("...to zero as well", ov[2] == 0.0, f"got {ov}")

ot, ov = reduce.blind_fade_samples(T, [1.0, 1.0, 0.0, 0.0], [A, A, B, B])
check("a hidden frame that has its own pose keeps its honest fade",
      (ot, ov) == (T, [1.0, 1.0, 0.0, 0.0]), f"got {ot} {ov}")

ot, ov = reduce.blind_fade_samples(T, [1.0, 1.0, 1.0, 1.0], [A, A, A, A])
check("an element that is never hidden is left alone",
      (ot, ov) == (T, [1.0, 1.0, 1.0, 1.0]), f"got {ot} {ov}")

ot, ov = reduce.blind_fade_samples(T, [0.0, 1.0, 0.0, 0.0], [None, A, None, None])
check("a lone visible frame is closed off on both sides",
      len(ot) == 6, f"got {ot}")
check("...and its own frame still carries the opacity it had",
      ov[ot.index(1.0)] == 1.0, f"got {ov}")

check("the samples stay sorted in time", all(a <= b for a, b in zip(ot, ot[1:])),
      f"got {ot}")

print()
print(f"{len(fails)} failure(s)" + (": " + ", ".join(fails) if fails else ""))
raise SystemExit(1 if fails else 0)
