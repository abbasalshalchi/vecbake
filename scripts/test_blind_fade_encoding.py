"""A fade closed off between two frames has to survive the encoder.

    "<blender>/5.2/python/bin/python.exe" scripts/test_blind_fade_encoding.py

Dense encoding drops the timeline and rebuilds it from the duration, so a key
that does not sit on the sample grid cannot be expressed. Opacity now has such
keys, and the failure is silent: the track comes back as the frame-long fade it
was written to replace, on exactly the elements that needed it.
"""
import importlib.util
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON = os.path.join(ROOT, "addon", "vecbake")


def _load(name):
    """Load one module without the package __init__, which wants bpy."""
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


emit_json = _load("emit_json")
Track = _load("ir").Track

fails = []


def check(label, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + label + ("  " + detail if detail else ""))
    if not ok:
        fails.append(label)


DUR, N = 1.0, 5
grid = [DUR * i / (N - 1) for i in range(N)]        # 0, .25, .5, .75, 1

check("a key on the grid is not off it",
      not emit_json._has_off_grid_key(grid, grid))
check("a key between two frames is off it",
      emit_json._has_off_grid_key([0.0, 0.255, 0.5], grid))
check("a grid of one instant is never off it",
      not emit_json._has_off_grid_key([0.0], [0.0]))
check("float noise on a grid time is still on the grid",
      not emit_json._has_off_grid_key([0.25 + 1e-13], grid))

on = Track(list(grid), [1.0, 1.0, 1.0, 0.0, 0.0])
enc = emit_json._encode_track(on, 3, grid)
check("an on-grid track may still take the dense encoding",
      "t" not in enc, f"got {sorted(enc)}")

# 1 at frame 2, gone a sliver later: the pop that replaces a blind fade
off = Track([0.0, 0.25, 0.5, 0.505, 0.75, 1.0], [1.0, 1.0, 1.0, 0.0, 0.0, 0.0])
enc = emit_json._encode_track(off, 3, grid)
check("a track with a key between frames is forced sparse",
      "t" in enc, f"got {sorted(enc)}")
check("...and keeps the between-frames time", 0.505 in enc["t"], f"got {enc['t']}")

# and the encoded track still reads as a pop, not a fade
rt = Track(enc["t"], off.values)
check("...so it still reads as off at the next frame",
      rt.sample(0.75) == 0.0, f"got {rt.sample(0.75)}")
check("...and as on at the frame it was last correct on",
      rt.sample(0.5) == 1.0, f"got {rt.sample(0.5)}")

print()
print(f"{len(fails)} failure(s)" + (": " + ", ".join(fails) if fails else ""))
raise SystemExit(1 if fails else 0)
