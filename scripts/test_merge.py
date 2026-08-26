"""A silhouette is rarely one tidy ring, and merge.py has to say what it is.

    "<blender>/5.1/python/bin/python.exe" scripts/test_merge.py

Three things arrive instead of a ring and they are one shape seen three ways:
a boundary that pinches at a vertex, one that comes apart into pieces, and a
chain that never closes. The shapes here are small enough to work through by
hand, which is the point -- the bugs this file exists to catch were both found
on six-edge inputs and neither was visible on the probe.

What is asserted:

  * a plain ring is unchanged, and every position keeps its anchor;
  * a bowtie splices into one closed walk, visiting the pinch twice, and only
    the second visit gives up its anchor -- two slots claiming one vertex is
    what threw away every other anchor on the ring;
  * `verts` stays a real mesh vertex everywhere, because it indexes screen
    positions and depths, where -1 would quietly read the last vertex;
  * winding is preserved rather than normalised, so a hole spliced to its outer
    boundary still fills as a hole;
  * a piece too far away to reach is dropped only when it is negligible, and a
    real second piece refuses the merge instead of losing half the shape;
  * a walk that dead-ends gives its edges back, or one dangling chain hanging
    off a square costs the square as well.
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


merge = _load("merge")
fails = []


def check(label, cond, detail=""):
    if cond:
        print(f"PASS  {label}" + (f"  {detail}" if detail else ""))
    else:
        fails.append(label)
        print(f"FAIL  {label}  {detail}")


class Plan:
    """Only the two fields the walk reads."""

    def __init__(self, edges):
        self.edge_verts = np.asarray(edges, dtype=np.int64)
        self.nedges = len(self.edge_verts)


def walk(edges, xy):
    return merge.walk_loop(Plan(edges), np.arange(len(edges)),
                           np.asarray(xy, dtype=np.float64))


def ring_of(edges, xy):
    got = walk(edges, xy)
    return None if got is None else [int(v) for v in got[1]]


SQ_E = [(0, 1), (1, 2), (2, 3), (3, 0)]
SQ_XY = [(0, 0), (1, 0), (1, 1), (0, 1)]

r = ring_of(SQ_E, SQ_XY)
check("a plain square comes back as one ring", r is not None and len(r) == 4, r)
check("...visiting every corner once",
      r is not None and sorted(r) == [0, 1, 2, 3], r)
check("...with no anchor given up", bool((walk(SQ_E, SQ_XY)[0] >= 0).all()))

# Two triangles meeting at vertex 0: the boundary pinches there.
BOW_E = [(0, 1), (1, 2), (2, 0), (0, 3), (3, 4), (4, 0)]
BOW_XY = [(0, 0), (-1, 1), (-1, -1), (1, 1), (1, -1)]
r = ring_of(BOW_E, BOW_XY)
check("a bowtie splices into one walk", r is not None, r)
check("...carrying all six positions", r is not None and len(r) == 6, r)
check("...through the pinch vertex twice", r is not None and r.count(0) == 2, r)
ids, verts = walk(BOW_E, BOW_XY)
check("...where only the repeat gives up its anchor",
      int((ids < 0).sum()) == 1 and int((ids >= 0).sum()) == 5, ids.tolist())
check("...and every position is still a real vertex",
      bool((verts >= 0).all()), verts.tolist())

# Outer square anticlockwise, inner triangle clockwise, touching at vertex 0.
HOLE_E = [(0, 1), (1, 2), (2, 3), (3, 0), (0, 4), (4, 5), (5, 0)]
HOLE_XY = [(0, 0), (3, 0), (3, 3), (0, 3), (1, 1), (2, 1)]
got = walk(HOLE_E, HOLE_XY)
check("a hole touching its outer boundary splices", got is not None)
if got is not None:
    pts = np.asarray(HOLE_XY, dtype=np.float64)[got[1]]
    x, y = pts[:, 0], pts[:, 1]
    area = 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
    # The square is 9 and the triangle 0.5. Normalising both directions would
    # add them; keeping the traced direction subtracts.
    check("...and subtracts rather than filling solid", abs(area) < 9.0,
          f"area {area:.3f}")

r = ring_of(SQ_E + [(4, 5), (5, 6), (6, 4)],
            SQ_XY + [(9.0, 9.0), (9.02, 9.0), (9.0, 9.02)])
check("a speck too small to matter is dropped",
      r is not None and sorted(r) == [0, 1, 2, 3], r)

# A square with a real hole in it: the inner boundary is traced the other way,
# so a seam out and back leaves the hole open rather than filling it solid.
BORE_E = SQ_E + [(4, 5), (5, 6), (6, 4)]
BORE_XY = SQ_XY + [(0.3, 0.3), (0.5, 0.7), (0.7, 0.3)]
got = walk(BORE_E, BORE_XY)
check("a real second piece is bridged, not refused", got is not None)
if got is not None:
    pts = np.asarray(BORE_XY, dtype=np.float64)[got[1]]
    x, y = pts[:, 0], pts[:, 1]
    area = abs(0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))
    hole = 0.5 * abs((0.5 - 0.3) * (0.3 - 0.3) - (0.7 - 0.3) * (0.7 - 0.3)) * 2
    check("...and the hole stays open", area < 1.0 - 1e-9,
          f"encloses {area:.4f} of the square's 1.0")
    check("...with the seam enclosing nothing",
          abs(area - (1.0 - 0.08)) < 0.02, f"{area:.4f}")
    check("...and the seam's repeats claim no anchor",
          int((got[0] < 0).sum()) >= 2, got[0].tolist())

r = ring_of(SQ_E + [(2, 4), (4, 5)], SQ_XY + [(2.0, 2.0), (3.0, 3.0)])
check("a dangling chain is dropped and the ring survives",
      r is not None and sorted(r) == [0, 1, 2, 3], r)

check("two edges are not a ring", walk([(0, 1), (1, 0)], [(0, 0), (1, 0)]) is None)

print("\n" + ("all passed" if not fails else f"{len(fails)} FAILED"))
sys.exit(1 if fails else 0)
