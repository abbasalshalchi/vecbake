"""A merged part is a surface, and it has to be ordered as one.

    "<blender>/5.1/python/bin/python.exe" scripts/test_order_surface.py

Every element hands `resolve()` a set of screen points with depths, and for a
fill `resolve()` fits a plane through them and evaluates it wherever the pair
overlaps. For a single face that fit is exact -- a planar polygon under
perspective has 1/depth affine in (x, y).

A merged element is not a face. It is a whole part, and the points it hands
over are its silhouette ring: the *rim*. Fitting a plane to the rim of a bowl
and then asking it about the middle of the bowl is not an approximation of the
part, it is a description of something else. On the Voyager dish's back shell
that came to 1.35 units on a shell 0.067 thick, which put the beams standing
inside the dish behind it.

The two failures below are the two ways it goes wrong, and the second is the
one with teeth: a depth range read off the rim does not cover the part, and
`resolve` settles non-overlapping ranges outright, without measuring. A pair
decided that way is not decided badly -- it is decided with no evidence at all.
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


# bake.py is the baker and imports Blender, but the two pieces under test here
# are plain arithmetic over numpy. Stub the import rather than leave them
# reachable only from inside Blender, where nothing exercises them short of a
# full twelve-minute bake.
for _name in ("bpy", "mathutils"):
    sys.modules.setdefault(_name, types.ModuleType(_name))
sys.modules["mathutils"].Vector = object

order = _load("order")
bake = _load("bake")
fails = []


def check(label, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {label}{'  ' + detail if detail else ''}")
    if not cond:
        fails.append(label)


def ring(x0, y0, x1, y1):
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=float)


# --- the bowl -------------------------------------------------------------
# A dish seen face on. Its rim runs all the way round at depth 10 and its
# middle sinks away from the camera to 12. A beam stands in the hollow at 11:
# nearer than the middle of the dish, further than its rim.
RIM = 10.0
MIDDLE = 12.0
BEAM = 11.0

_grid = np.linspace(60.0, 240.0, 6)
_gx, _gy = np.meshgrid(_grid, _grid)
_inner = np.hypot(_gx - 150.0, _gy - 150.0) < 95.0
_rim = ring(0, 0, 300, 300)
surface = (np.concatenate([_rim[:, 0], _gx[_inner]]),
           np.concatenate([_rim[:, 1], _gy[_inner]]),
           np.concatenate([np.full(4, RIM), np.full(int(_inner.sum()), MIDDLE)]))


def scene(with_probe):
    dish = {"key": "dish/m", "kind": order.FILL_KIND, "seed": RIM,
            "pts": ring(0, 0, 300, 300), "depths": np.full(4, RIM)}
    if with_probe:
        dish["probe"] = surface
    beam = {"key": "beam/f0", "kind": order.FILL_KIND, "seed": BEAM,
            "pts": ring(130, 130, 170, 170), "depths": np.full(4, BEAM)}
    return [dish, beam]


# Bigger rank = painted earlier = further away.
plain = scene(False)
r_plain = order.resolve(plain, is_ortho=True)
probed = scene(True)
r_probe = order.resolve(probed, is_ortho=True)

check("a rim-only fit reads the dish as flat at the depth of its rim",
      plain[0]["dmin"] == plain[0]["dmax"] == RIM,
      f"{plain[0]['dmin']}..{plain[0]['dmax']}")
check("...so the dish is wrongly painted over the beam standing inside it",
      r_plain[0] < r_plain[1], f"dish={r_plain[0]} beam={r_plain[1]}")

check("the surface samples give the dish its real depth range",
      (probed[0]["dmin"], probed[0]["dmax"]) == (RIM, MIDDLE),
      f"{probed[0]['dmin']}..{probed[0]['dmax']}")
check("...and nothing is fitted, so no plane is kept for it",
      probed[0]["field"] is None)
check("...so the beam is now correctly in front of the dish it sits in",
      r_probe[0] > r_probe[1], f"dish={r_probe[0]} beam={r_probe[1]}")

# The depth read back at a point is a real sample, not an interpolation
# between them -- which is what "nothing is approximated" has to mean.
at_middle = order._depths_at(probed[0], np.array([150.0]), np.array([150.0]), True)
check("depth at a query point is the nearest sample's, exactly",
      float(at_middle[0]) == MIDDLE, f"{float(at_middle[0])}")


# --- thinning keeps the extremes ------------------------------------------
# The cap is what makes the lookup cheap. It must not be what loses the
# nearest and furthest points, because those two are the whole depth range,
# and a range short at either end settles pairs that should have been measured.
class FakePlan:
    name = "part"
    fills = True
    merge_fill = True
    nverts = 4000
    nfaces = 1000
    loop_total = np.full(1000, 4, dtype=np.int64)
    loop_verts = np.arange(4000, dtype=np.int32)
    face_of_loop = None
    edge_face_a = np.array([0])
    edge_face_b = np.array([1])


plan = FakePlan()
rng = np.random.default_rng(7)
depth = rng.uniform(5.0, 9.0, 4000)
depth[1234] = 1.0                       # the nearest point on the part
depth[3777] = 40.0                      # the furthest
xy = rng.uniform(0.0, 500.0, (4000, 2))
probe = bake._surface_probe(plan, np.ones(1000, dtype=bool), xy, depth)

check("the probe is capped", len(probe[2]) <= bake.PROBE_CAP + 2,
      f"{len(probe[2])} samples")
check("...but the nearest point on the part survives the thinning",
      probe[2].min() == 1.0, f"min={probe[2].min()}")
check("...and so does the furthest", probe[2].max() == 40.0,
      f"max={probe[2].max()}")

nothing = bake._surface_probe(plan, np.zeros(1000, dtype=bool), xy, depth)
check("a part with nothing facing the camera hands over no surface",
      nothing is None)


# --- a merged fill is the surface its own strokes lie on ------------------
# The assertion "an edge lies on the faces it borders, so a face can never
# paint over its own edge" is named by element key. Merging replaces those
# faces with one fill, so the face keys stop matching anything and the
# assertion is dropped -- silently, because a key nothing answers to reads the
# same as an edge that borders nothing.
keys = bake.ObjectPlan.edge_face_keys(plan, 0)
check("a merged object asserts its edges against the fill that is drawn",
      keys == ("part/m",), f"{keys}")

plan.merge_fill = False
keys = bake.ObjectPlan.edge_face_keys(plan, 0)
check("...and an unmerged one still against its own faces",
      keys == ("part/f0", "part/f1"), f"{keys}")

plan.fills = False
check("...and an object drawing no fills asserts nothing",
      bake.ObjectPlan.edge_face_keys(plan, 0) == ())


# The stroke has to end up on top, which is the whole point of the assertion.
face = {"key": "part/m", "kind": order.FILL_KIND, "seed": 10.0,
        "pts": ring(0, 0, 300, 300), "depths": np.full(4, 10.0)}
line = {"key": "part/e3", "kind": 0, "seed": 10.0,
        "pts": np.array([[50.0, 150.0], [250.0, 150.0]]),
        "depths": np.array([10.0, 10.0]), "on_faces": ("part/m",)}
r = order.resolve([dict(face), dict(line)], is_ortho=True)
asserted = order.LAST_STATS["assertions"]
unmatched = order.LAST_STATS["assertions_face_not_drawn"]
check("a merged fill is held behind the stroke lying on it",
      r[0] > r[1], f"fill={r[0]} stroke={r[1]}")
check("...and the assertion is recorded, not merely implied",
      (asserted, unmatched) == (1, 0), f"{asserted} asserted, {unmatched} unmatched")

# Recorded even though sampling had already put the pair the right way round,
# which is not bookkeeping: `asserted` is the set the cycle breaker will not
# spend, so the record is what stops it eating this pair later. Dropping it was
# measured and cost 1853 lines painted over by their own face.

# The other half: when sampling gets it wrong, the assertion has to correct it.
# A fill measuring a whole unit in front of the edge lying on it is the
# non-planar n-gon case the mechanism was written for.
sunk = dict(face, depths=np.full(4, 9.0))
r = order.resolve([sunk, dict(line)], is_ortho=True)
check("a fill measured in front of its own stroke is put back behind it",
      r[0] > r[1], f"fill={r[0]} stroke={r[1]}")
check("...having retracted the measured edge rather than leaving a 2-cycle",
      order.LAST_STATS["cycle_breaks"] == 0,
      f"{order.LAST_STATS['cycle_breaks']} breaks")

line["on_faces"] = ("part/f7",)          # what merging used to leave behind
order.resolve([face, dict(line)], is_ortho=True)
asserted = order.LAST_STATS["assertions"]
unmatched = order.LAST_STATS["assertions_face_not_drawn"]
check("a key nothing answers to counts as unmatched, not as an assertion",
      (asserted, unmatched) == (0, 1), f"{asserted} asserted, {unmatched} unmatched")

print()
print(f"{len(fails)} failure(s)" + (": " + ", ".join(fails) if fails else ""))
raise SystemExit(1 if fails else 0)
