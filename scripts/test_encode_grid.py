"""Dense track encoding is only legal on an evenly sampled bake.

    "<blender>/python/bin/python.exe" scripts/test_encode_grid.py

The dense form drops the timeline and lets the reader rebuild it by dividing
the duration into equal steps. That is a real saving and it is only correct
when the bake sampled evenly. Always including the end frame means a Step that
does not divide the range leaves one short gap at the end -- 1..60 by 8 gives
57 then 60 -- and encoding those keys densely resamples every track at instants
that never existed.

The damage is graded. Geometry drifts a little. Opacity, which is only ever 0
or 1 in an opaque scene, acquires fractional values, and the player renders
those as a fade -- a fill going see-through for the length of a gap. A resolved
paint order stores integer ranks, and blending two ranks yields a fraction that
sorts somewhere neither of them was, which reorders what is drawn in front.

Both faults reached a user as "flickering, and things become translucent even
though nothing is translucent in the scene".
"""

import importlib.util
import json
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


ir = _load("ir")
emit_json = _load("emit_json")

fails = []


def check(label, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {label}{'  ' + detail if detail else ''}")
    if not cond:
        fails.append(label)


def decode(enc):
    """Undo _delta + quantisation. Mirrors what a reader has to do."""
    if enc is None:
        return None
    if "c" in enc:
        return [enc["c"]]
    q = enc["q"]
    out, acc = [], None
    for d in enc["d"]:
        acc = d if acc is None else acc + d
        out.append(acc / q)
    return out


# Frames 1..60 by 8 -> 1,9,17,25,33,41,49,57,60. The last gap is 3, not 8.
FRAMES = [1, 9, 17, 25, 33, 41, 49, 57, 60]
FPS = 60.0
times = [(f - FRAMES[0]) / FPS for f in FRAMES]
duration = times[-1]

# An opaque fill that switches off partway and never comes back.
vis = [1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0]
# A resolved paint order: integer ranks, one of which changes late.
rank = [12.0, 12.0, 12.0, 12.0, 12.0, 12.0, 12.0, 12.0, 5.0]

el = ir.Element(key="f", kind=ir.FILL, npts=4, group="g", color=(1, 1, 1))
el.geo = ir.Track(list(times), [[0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0]] * len(times))
el.opacity = ir.Track(list(times), list(vis))
el.depth = ir.Track(list(times), list(rank))

base = dict(width=100, height=100, fps=FPS, duration=duration,
            elements=[el], groups=["g"])

uneven = ir.VecScene(**base, meta={"frames": len(FRAMES), "depth_decimals": 0,
                                   "uniform_frames": False})
doc = emit_json.to_dict(uneven)
o = decode(doc["els"][0]["O"])
d = decode(doc["els"][0]["D"])

check("uneven sampling keeps its explicit timeline",
      "t" in doc["els"][0]["O"], "sparse, not dense")
check("opacity survives as 0 or 1 only",
      all(v < 0.004 or v > 0.996 for v in o), f"got {o}")
check("paint ranks survive as integers",
      all(abs(v - round(v)) < 1e-6 for v in d), f"got {d}")

# An evenly sampled bake -- same count, no short final gap -- may still encode
# densely, and must stay exact when it does. The times have to be genuinely
# even here: claiming uniform_frames on uneven keys reproduces the very bug
# under test, which is a useful thing to know and a useless thing to assert.
EVEN = [1, 9, 17, 25, 33, 41, 49, 57]
t_even = [(f - EVEN[0]) / FPS for f in EVEN]
el2 = ir.Element(key="f", kind=ir.FILL, npts=4, group="g", color=(1, 1, 1))
el2.geo = ir.Track(list(t_even), [[0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0]] * len(EVEN))
el2.opacity = ir.Track(list(t_even), [1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0])
el2.depth = ir.Track(list(t_even), [12.0] * 7 + [5.0])

even = ir.VecScene(width=100, height=100, fps=FPS, duration=t_even[-1],
                   elements=[el2], groups=["g"],
                   meta={"frames": len(EVEN), "depth_decimals": 0,
                         "uniform_frames": True})
doc2 = emit_json.to_dict(even)
o2 = decode(doc2["els"][0]["O"])
d2 = decode(doc2["els"][0]["D"])
check("even sampling still gets the compact encoding",
      "t" not in doc2["els"][0]["O"], "dense")
check("...and is exact there",
      all(v < 0.004 or v > 0.996 for v in o2), f"got {o2}")
check("...ranks intact there too",
      all(abs(v - round(v)) < 1e-6 for v in d2), f"got {d2}")

# --- the flag must be REQUIRED, not assumed -------------------------------
# A revert once removed the line in bake.py that sets uniform_frames while
# leaving the reader's default at True. Every bake silently went back to dense
# encoding and the bug came with it, and this file still passed, because it
# builds the metadata by hand and so tested only the consumer. An absent flag
# now means "unknown", and unknown has to encode sparsely.
mute = ir.VecScene(width=100, height=100, fps=FPS, duration=t_even[-1],
                   elements=[el2], groups=["g"],
                   meta={"frames": len(EVEN), "depth_decimals": 0})
doc3 = emit_json.to_dict(mute)
check("a scene that does not say whether it sampled evenly is encoded sparsely",
      "t" in doc3["els"][0]["O"], "no flag -> explicit timeline")

# and the producer has to actually say it, which is the half that broke
import os
bake_src = open(os.path.join(ROOT, "addon", "vecbake", "bake.py"), encoding="utf-8").read()
check("bake.py still records uniform_frames in the scene metadata",
      '"uniform_frames"' in bake_src)

print()
print(f"{len(fails)} failure(s)" + (": " + ", ".join(fails) if fails else ""))
raise SystemExit(1 if fails else 0)
