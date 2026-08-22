"""The union boundary must be right on the shapes that broke the old outline.

    "<blender>/5.1/python/bin/python.exe" scripts/test_boundary.py

`merge.walk_loop` chains silhouette mesh edges, and three things about real
geometry defeat it -- on the Voyager probe in near enough equal thirds:

    branching      40   three silhouette edges at one vertex
    self-crossing  35   a fold crossing the outline on screen
    multi-loop     21   an open shell or a hole

`boundary.outline_of` asks a different question -- what does this object cover
on screen -- so all three stop being special cases. These assertions are the
ones the old suite could not have made, because every shape in it was convex.

Answers here are known by hand: two squares meeting at a corner enclose a known
area, an annulus has a known hole. Comparing against a hand-computed number is
what makes this a test rather than a snapshot of whatever the code does today.
"""

import importlib.util
import math
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


boundary = _load("boundary")
outline = _load("outline")
fails = []


def check(label, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {label}{'  ' + detail if detail else ''}")
    if not cond:
        fails.append(label)


def quad(x0, y0, x1, y1):
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=float)


def area(loop):
    x, y = loop[:, 0], loop[:, 1]
    return abs(float((x * np.roll(y, -1) - y * np.roll(x, -1)).sum())) / 2.0


def edges_of(faces):
    """Every face edge, with the face it came from. The caller's candidate set.

    In the baker only silhouette edges are offered -- an edge between two drawn
    faces has the union on both sides. Here every edge is offered instead,
    which is a strictly harder input: the interior ones all have to be dropped
    by the covering test rather than by never arriving.
    """
    segs, owners = [], []
    for fi, f in enumerate(faces):
        for k in range(len(f)):
            segs.append((f[k], f[(k + 1) % len(f)]))
            owners.append(fi)
    return np.asarray(segs, dtype=float), np.asarray(owners)


# --- one square: the boundary is the square -------------------------------
faces = [quad(0, 0, 10, 10)]
loops = boundary.outline_of(faces, *edges_of(faces))
check("a single face gives one loop", len(loops) == 1, f"{len(loops)} loops")
if loops:
    check("...with the right area", abs(area(loops[0]) - 100.0) < 1e-6,
          f"{area(loops[0]):.4f} vs 100")

# --- two overlapping squares: one loop, and the shared part is gone --------
faces = [quad(0, 0, 10, 10), quad(6, 0, 16, 10)]
loops = boundary.outline_of(faces, *edges_of(faces))
check("two overlapping faces merge into one loop", len(loops) == 1,
      f"{len(loops)} loops")
if loops:
    check("...with the union's area, not the sum",
          abs(area(loops[0]) - 160.0) < 1e-6, f"{area(loops[0]):.4f} vs 160")

# --- two disjoint squares: two loops --------------------------------------
faces = [quad(0, 0, 10, 10), quad(20, 0, 30, 10)]
loops = boundary.outline_of(faces, *edges_of(faces))
check("two disjoint faces give two loops", len(loops) == 2, f"{len(loops)} loops")
check("...each with its own area",
      len(loops) == 2 and all(abs(area(L) - 100.0) < 1e-6 for L in loops),
      ", ".join(f"{area(L):.2f}" for L in loops))

# --- THE FOLD: a bar crossing over another bar of the same object ----------
# This is part_019. Chaining silhouette edges walks straight through the
# crossing and ties a knot; the union has no crossing in it at all.
faces = [quad(0, 4, 20, 6), quad(9, 0, 11, 10)]
segs, owners = edges_of(faces)
loops = boundary.outline_of(faces, segs, owners)
check("a crossing pair of bars gives one simple loop", len(loops) == 1,
      f"{len(loops)} loops")
if loops:
    # 20x2 plus 10x2 less the 2x2 counted twice
    check("...with the union's area", abs(area(loops[0]) - 56.0) < 1e-6,
          f"{area(loops[0]):.4f} vs 56")
    check("...and it does not cross itself", outline.is_simple(loops[0]))

# --- THE HOLE: four bars round a square leave an annulus ------------------
faces = [quad(0, 0, 12, 3), quad(0, 9, 12, 12), quad(0, 0, 3, 12), quad(9, 0, 12, 12)]
loops = boundary.outline_of(faces, *edges_of(faces))
check("a ring of bars gives an outer loop and a hole", len(loops) == 2,
      f"{len(loops)} loops")
if len(loops) == 2:
    outer, inner = loops
    check("...the outer one is the full square",
          abs(area(outer) - 144.0) < 1e-6, f"{area(outer):.4f} vs 144")
    check("...and the inner one is the hole",
          abs(area(inner) - 36.0) < 1e-6, f"{area(inner):.4f} vs 36")
    check("...both simple", outline.is_simple(outer) and outline.is_simple(inner))

# --- BRANCHING: a fin on a box, three faces meeting on one edge -----------
# The fin sticks out of the top, so at each end of its root three boundary
# pieces meet. Walking mesh edges gives up here; the union does not.
faces = [quad(0, 0, 10, 6), quad(3, 6, 7, 11)]
loops = boundary.outline_of(faces, *edges_of(faces))
check("a fin on a box gives one loop", len(loops) == 1, f"{len(loops)} loops")
if loops:
    check("...with both parts' area", abs(area(loops[0]) - 80.0) < 1e-6,
          f"{area(loops[0]):.4f} vs 80")
    check("...and no crossing", outline.is_simple(loops[0]))

# --- a face wholly inside another contributes nothing ---------------------
faces = [quad(0, 0, 20, 20), quad(5, 5, 8, 8)]
loops = boundary.outline_of(faces, *edges_of(faces))
check("a face hidden inside another adds no loop", len(loops) == 1,
      f"{len(loops)} loops")
if loops:
    check("...and does not dent the outline",
          abs(area(loops[0]) - 400.0) < 1e-6, f"{area(loops[0]):.4f} vs 400")

# --- the split, on its own ------------------------------------------------
segs = np.array([[[0.0, 5.0], [10.0, 5.0]], [[5.0, 0.0], [5.0, 10.0]]])
pieces, src = boundary.split_at_crossings(segs)
check("a crossing pair splits into four pieces", len(pieces) == 4, f"{len(pieces)}")
check("...and each piece knows which segment it came from",
      sorted(src.tolist()) == [0, 0, 1, 1], str(src.tolist()))
touch = np.array([[[0.0, 0.0], [10.0, 0.0]], [[10.0, 0.0], [10.0, 10.0]]])
check("segments sharing an endpoint are not split",
      len(boundary.split_at_crossings(touch)[0]) == 2)
para = np.array([[[0.0, 0.0], [10.0, 0.0]], [[0.0, 5.0], [10.0, 5.0]]])
check("parallel segments on different lines are not split",
      len(boundary.split_at_crossings(para)[0]) == 2)
# two bars in a row share part of a line: a fin's root on a box's top edge
same = np.array([[[0.0, 0.0], [10.0, 0.0]], [[4.0, 0.0], [14.0, 0.0]]])
check("collinear overlapping segments are cut at each other's ends",
      len(boundary.split_at_crossings(same)[0]) == 4,
      f"{len(boundary.split_at_crossings(same)[0])} pieces")

# --- region or hole is containment, never winding -------------------------
# A loop's direction here comes from whichever segment the walk started on,
# and projected mesh edges are wound every which way, so orientation decides
# nothing. Hand-built faces happen to be consistent, which is exactly how a
# winding test passes here and fails on a real mesh.
faces = [quad(0, 0, 12, 3), quad(0, 9, 12, 12), quad(0, 0, 3, 12), quad(9, 0, 12, 12)]
loops = boundary.outline_of(faces, *edges_of(faces))
kinds = boundary.classify(loops)
check("the outer loop is a region", kinds and kinds[0] is True)
check("the loop inside it is a hole", len(kinds) == 2 and kinds[1] is False)

flipped = [L[::-1].copy() for L in loops]
check("reversing every loop changes nothing", boundary.classify(flipped) == kinds,
      f"{boundary.classify(flipped)} vs {kinds}")

faces = [quad(0, 0, 10, 10), quad(20, 0, 30, 10)]
side = boundary.classify(boundary.outline_of(faces, *edges_of(faces)))
check("two loops side by side are both regions", side == [True, True], str(side))
check("a single loop is always a region",
      boundary.classify(boundary.outline_of([quad(0, 0, 5, 5)],
                                            *edges_of([quad(0, 0, 5, 5)]))) == [True])

# --- two pieces meeting at one corner must meet at one node ---------------
# The failure this fixes: two faces contribute the same corner, and the same
# vertex projected along two paths differs in its last bits -- 1.7e-5 px,
# measured on test_ell. Keyed by rounding, that corner became two nodes of
# degree one, the walk could not close the loop, and the whole part vanished
# for that frame. It failed on 12 of 24 frames on a 16-vertex shape.
def nudge(poly, eps):
    """The same polygon, with every coordinate off by a hair."""
    return poly + np.array([[eps, -eps]] * len(poly))

a = quad(0, 0, 10, 10)
b = nudge(quad(10, 0, 20, 10), 1.7e-5)     # touches a's right edge, imprecisely
faces = [a, b]
loops = boundary.outline_of(faces, *edges_of(faces))
check("two faces touching along an imprecise edge still give one loop",
      len(loops) == 1, f"{len(loops)} loops")
if loops:
    check("...enclosing both of them", abs(area(loops[0]) - 200.0) < 0.01,
          f"{area(loops[0]):.3f} vs 200")

# and a corner join, which is what test_ell actually does
c = quad(0, 0, 10, 10)
e = nudge(quad(10, 10, 20, 20), 2e-5)
faces = [c, e]
loops = boundary.outline_of(faces, *edges_of(faces))
check("two faces meeting at one imprecise corner still walk",
      len(loops) >= 1, f"{len(loops)} loops")
check("...covering both squares",
      abs(sum(area(L) for L in loops) - 200.0) < 0.01,
      f"{sum(area(L) for L in loops):.3f} vs 200")

# a drift wider than the weld must NOT be merged -- these are different points
far = [quad(0, 0, 10, 10), quad(10.5, 0, 20, 10)]
loops = boundary.outline_of(far, *edges_of(far))
check("faces that genuinely do not touch stay two loops", len(loops) == 2,
      f"{len(loops)} loops")

# --- one missing piece must not lose the whole outline --------------------
# The boundary can come back a piece short where geometry is exactly tangent.
# part_012 produced 35 correct pieces over 36 nodes with 34 properly paired,
# and returning nothing for that frame made the part vanish. A chain that is
# nearly closed is an outline; a chain whose ends are far apart is not.
# one small piece of a long boundary, which is the real case: part_012 lost a
# piece of a few pixels from an outline about a hundred across
# a square whose sides are cut into eight pieces each
corners = [(0.0, 0.0), (80.0, 0.0), (80.0, 80.0), (0.0, 80.0)]
fine = np.asarray([
    (np.array(corners[i]) + (np.array(corners[(i + 1) % 4]) - np.array(corners[i])) * (k / 8))
    for i in range(4) for k in range(8)])
segs, owners = edges_of([fine])
pieces = boundary.split_at_crossings(segs)[0]
short = boundary.walk_loops(np.delete(pieces, 5, axis=0))
check("an outline missing one small piece is closed, not discarded",
      len(short) == 1, f"{len(short)} loops")
if short:
    check("...and encloses about the right area",
          abs(area(short[0]) - 6400.0) < 60.0, f"{area(short[0]):.0f} vs 6400")

# a gap of a quarter of the shape is not a missing piece, it is a missing side
gappy = boundary.walk_loops(np.delete(
    boundary.split_at_crossings(edges_of([quad(0, 0, 40, 40)])[0])[0], 0, axis=0))
check("a gap the size of a whole side is refused", len(gappy) == 0,
      f"{len(gappy)} loops")

# two far-apart fragments are not one outline and must not be joined
frag = np.array([[[0.0, 0.0], [10.0, 0.0]], [[10.0, 0.0], [10.0, 10.0]],
                 [[500.0, 500.0], [510.0, 500.0]]])
check("distant fragments are not welded into an invented loop",
      all(area(L) < 200.0 for L in boundary.walk_loops(frag)),
      str([round(area(L)) for L in boundary.walk_loops(frag)]))

# --- degenerate input must not raise --------------------------------------
check("no faces gives no loops", boundary.outline_of([], np.zeros((0, 2, 2)), []) == [])
check("a zero-area face does not hang",
      isinstance(boundary.outline_of([quad(0, 0, 0, 0)],
                                     *edges_of([quad(0, 0, 0, 0)])), list))

print()
print(f"{len(fails)} failure(s)" + (": " + ", ".join(fails) if fails else ""))
raise SystemExit(1 if fails else 0)
