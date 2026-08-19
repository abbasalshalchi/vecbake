"""A paint rank and a camera distance are not the same number.

    "<blender>/5.1/python/bin/python.exe" scripts/test_order_scale.py

order.resolve() ranks only what is drawn in a frame, because ranking is the
expensive pass. Every other element keeps whatever was already in its depth
slot, which is the raw distance from the camera. So any element that is culled
for part of the clip -- and with Backface Cull on that is most of them -- leaves
the bake carrying integer ranks in some frames and metres in others.

The player interpolates between keys. Sweeping from a rank to a distance walks
through every rank in between, so a face at the back of the scene slides to the
front over the course of one frame gap, at partial opacity because its
visibility track is fading across the same gap.

Measured on the Voyager probe bake before the fix: 382 of 424 elements carried
both scales. Ranks spanned 9..208; every culled frame held 10.0 or 11.0, and
smaller means nearer, so the whole scene's hidden geometry dived to the front
together. The user's report was "the shafts that are inside the body of the
ship flip into view", "things become translucent for a moment even though
nothing is translucent in the scene", and -- the clue that pinned it -- "turning
off backface culling removes all the shaft occlusion errors", because with
nothing culled per frame every element is ranked in every frame and the two
scales never meet.
"""

import importlib.util
import os
import sys
import types

import numpy as np

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


order = _load("order")

fails = []


def check(label, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {label}{'  ' + detail if detail else ''}")
    if not cond:
        fails.append(label)


def lerp(a, b, u):
    return a + (b - a) * u


# --- the scene ------------------------------------------------------------
# Six frames. `hull` faces the camera throughout and is ranked every frame.
# `shaft` sits behind it and turns away at frame 3, so it is ranked in frames
# 0-2 and culled in 3-5. CAM is the raw camera distance left in the slot.
NFRAMES = 6
CAM = 10.0
HULL_RANK = 100.0
SHAFT_RANK = 180.0          # larger = further away = painted first

slots = {
    "hull": {"depth": np.full(NFRAMES, HULL_RANK),
             "vis": np.ones(NFRAMES)},
    "shaft": {"depth": np.array([SHAFT_RANK] * 3 + [CAM] * 3),
              "vis": np.array([1.0, 1.0, 1.0, 0.0, 0.0, 0.0])},
}
ranked = {
    "hull": np.ones(NFRAMES, dtype=bool),
    "shaft": np.array([True, True, True, False, False, False]),
}

# --- what the bug looks like ---------------------------------------------
# Frame 2 -> 3 is the gap where the shaft is fading out. Sample across it.
before = slots["shaft"]["depth"].copy()
flipped = [u for u in np.linspace(0, 1, 21)
           if lerp(before[2], before[3], u) < HULL_RANK]
check("the mixed scale really does reorder mid-gap",
      len(flipped) > 0,
      f"shaft passes in front of the hull for {len(flipped)}/21 of the gap")

# ...while it is part-transparent, which is the second half of the report
o = [lerp(1.0, 0.0, u) for u in np.linspace(0, 1, 21)]
check("...and it is translucent while it does it",
      any(0.01 < v < 0.99 for v in o))

# --- the fix --------------------------------------------------------------
held, never = order.hold_unranked(slots, ranked)
after = slots["shaft"]["depth"]

check("unranked frames are repaired", held == 3, f"held={held}")
check("nothing was left on the distance scale", never == 0, f"never={never}")
check("no culled frame still holds a camera distance",
      not np.any(np.isclose(after, CAM)), f"got {after.tolist()}")
check("the rank is carried across the culled run",
      np.allclose(after, SHAFT_RANK), f"got {after.tolist()}")

still = [u for u in np.linspace(0, 1, 21)
         if lerp(after[2], after[3], u) < HULL_RANK]
check("the shaft stays behind the hull for the whole gap",
      len(still) == 0, f"{len(still)}/21 frames still wrong")

# The hull was ranked everywhere, so it must come back untouched.
check("an always-drawn element is not modified",
      np.allclose(slots["hull"]["depth"], HULL_RANK))

# --- edges ----------------------------------------------------------------
# Culled at the *start*: there is no earlier rank to hold, so the first one
# has to reach backwards. Leaving those frames alone would keep the bug for
# anything that enters the shot after frame 0.
lead = {"x": {"depth": np.array([CAM, CAM, 42.0, 43.0]),
              "vis": np.array([0.0, 0.0, 1.0, 1.0])}}
order.hold_unranked(lead, {"x": np.array([False, False, True, True])})
check("a leading culled run reaches back to the first rank",
      np.allclose(lead["x"]["depth"], [42.0, 42.0, 42.0, 43.0]),
      f"got {lead['x']['depth'].tolist()}")

# Culled in the *middle*, with a different rank on the far side. Filling only
# forwards leaves the last culled frame holding the stale rank, so the element
# slides across the difference over the gap where it fades back in -- measured
# at up to 14 ranks on the probe. Both ends of the run have to be flat, because
# both ends are where a fade happens; the step belongs in the middle, where
# nothing is drawn.
GONE, BACK = 40.0, 54.0
mid = {"m": {"depth": np.array([GONE, GONE, CAM, CAM, CAM, CAM, BACK, BACK]),
             "vis": np.array([1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0])}}
order.hold_unranked(mid, {"m": np.array([1, 1, 0, 0, 0, 0, 1, 1], dtype=bool)})
got = mid["m"]["depth"]
check("the fade-out gap is flat", got[1] == got[2], f"got {got.tolist()}")
check("the fade-in gap is flat too", got[5] == got[6], f"got {got.tolist()}")
check("the step lands in the middle, where nothing is drawn",
      got.tolist() == [GONE, GONE, GONE, GONE, BACK, BACK, BACK, BACK],
      f"got {got.tolist()}")

# Ranked nowhere but visible somewhere: every frame is on the distance scale,
# so at least it is self-consistent -- but it must be counted, not silently
# passed through, because it would mean an element reached the emitter in the
# wrong units.
orphan = {"y": {"depth": np.full(3, CAM), "vis": np.ones(3)}}
_, never2 = order.hold_unranked(orphan, {"y": np.zeros(3, dtype=bool)})
check("an element that never got a rank is reported", never2 == 1)

# Never visible at all is normal -- _finish drops it -- and is not a fault.
ghost = {"z": {"depth": np.full(3, CAM), "vis": np.zeros(3)}}
_, never3 = order.hold_unranked(ghost, {"z": np.zeros(3, dtype=bool)})
check("an element nobody ever sees is not reported", never3 == 0)

# --- the producer has to call it -----------------------------------------
# The fix lives in order.py but only works if bake.py tracks which elements got
# a rank and hands that over. A resolve loop that forgets leaves the bug fully
# intact with every test here still passing.
bake_src = open(os.path.join(ADDON, "bake.py"), encoding="utf-8").read()
check("bake.py records which elements were ranked", "ranked[item[\"key\"]][fi]" in bake_src)
check("bake.py calls hold_unranked", "hold_unranked" in bake_src)

print()
print(f"{len(fails)} failure(s)" + (": " + ", ".join(fails) if fails else ""))
raise SystemExit(1 if fails else 0)
