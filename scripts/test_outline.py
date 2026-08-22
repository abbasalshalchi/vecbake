"""A merged outline must hold its shape through a full turn, and never cross.

    "<blender>/5.1/python/bin/python.exe" scripts/test_outline.py

`merge.MergedPath` binds a slot to a mesh vertex and freezes the slot order on
the first ring. A solid turning 360 degrees presents a different silhouette set
in a different cyclic order a quarter turn later, so the order check fails and
the object is re-sampled unmerged. Five of the seven primitives in the lab
scene find a clean ring in every single frame and are rejected on order alone:
cone, cube, cylinder, sphere, tube.

`outline.py` binds a slot to a position around the loop instead. The assertions
here are the two properties that makes possible, plus the one that pays for it:

  * points stay monotone around the loop, in every frame of a full turn, so the
    polygon cannot self-intersect however far it spins;
  * the resampled outline stays inside the pixel tolerance it was given;
  * the point count comes from that tolerance rather than from counting mesh
    vertices -- the lab sphere's outline takes 33 points at 1px and 59 at
    0.35px, against the 482 slots a vertex-bound map has to reserve.

The ground truth throughout is the mesh silhouette polygon, not the smooth
surface behind it. That distinction sets the counts: a 59-gon of radius 113 is
already within 0.16px of a true circle, so at a 0.35px budget there is nothing
left to remove -- skipping one of its vertices moves the outline 0.64px.
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


outline = _load("outline")
fails = []


def check(label, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {label}{'  ' + detail if detail else ''}")
    if not cond:
        fails.append(label)


def circle(r, n, cx=0.0, cy=0.0, phase=0.0):
    a = np.linspace(0, 2 * math.pi, n, endpoint=False) + phase
    return np.stack([cx + r * np.cos(a), cy + r * np.sin(a)], axis=1)


def rect(w, h, per_side=1):
    xs, ys = w / 2, h / 2
    corners = [(-xs, -ys), (xs, -ys), (xs, ys), (-xs, ys)]
    out = []
    for i in range(4):
        a = np.array(corners[i])
        b = np.array(corners[(i + 1) % 4])
        for k in range(per_side):
            out.append(a + (b - a) * (k / per_side))
    return np.asarray(out)


def monotone(positions, m):
    """Do the slots run once around the loop without ever turning back?"""
    s = positions[:, 0] + positions[:, 1]
    d = np.diff(s)
    wraps = int((d < 0).sum())
    return wraps <= 1 and bool((s >= 0).all()) and bool((s < m).all())


# --- the count comes from the tolerance, measured against the real outline -
# The ground truth is the mesh silhouette polygon, not the smooth surface it
# stands for. That matters: a 59-gon of radius 113 is already within 0.16px of
# a true circle, so at 0.35px there is nothing to remove -- dropping a single
# vertex moves the outline 0.64px, which is over budget. Give it a budget it
# can spend and it spends it.
TOL = 0.35
sphere_ring = circle(113.0, 59)                 # the lab sphere's silhouette
tight = outline.choose(sphere_ring, TOL)
loose = outline.choose(sphere_ring, 1.0)
check("a tolerance finer than the mesh keeps every ring vertex",
      len(tight) == 59, f"{len(tight)} points at {TOL}px")
check("...and a coarser one genuinely reduces", len(loose) < 40,
      f"{len(loose)} points at 1.0px")
check("either way it is one element, not the 482 slots a vertex map needs",
      max(len(tight), len(loose)) < 482)

for label, pts, tol in (("tight", tight, TOL), ("loose", loose, 1.0)):
    pos = outline.pad_to(pts, sphere_ring, len(pts))
    check(f"the {label} outline is inside its tolerance",
          outline.max_error(pos, sphere_ring) <= tol,
          f"{outline.max_error(pos, sphere_ring):.3f}px")

# radius drives the count, because radius drives the error
small = outline.choose(circle(20.0, 59), 1.0)
big = outline.choose(circle(500.0, 240), 1.0)
check("a smaller circle needs fewer points", len(small) < len(loose),
      f"{len(small)} at r=20 vs {len(loose)} at r=113")
check("a bigger one needs more", len(big) > len(loose), f"{len(big)} at r=500")

# --- corners are exact, which uniform spacing could not manage ------------
box = rect(400.0, 260.0, per_side=8)             # 32 vertices, 4 real corners
kb = outline.choose(box, TOL)
check("a rectangle collapses to its four corners", len(kb) == 4, f"{len(kb)} points")
pb = outline.pad_to(kb, box, 4)
check("...with no corner cut", outline.max_error(pb, box) <= 1e-9,
      f"{outline.max_error(pb, box):.6f}px")

# The reason to place points by error rather than by arc length.
uni = outline.pad_to([0], box, 12)
check("uniform spacing would have failed the same shape",
      outline.max_error(uni, box) > TOL,
      f"{outline.max_error(uni, box):.0f}px off at 12 uniform points")

# --- padding adds points without changing the shape ----------------------
padded = outline.pad_to(kb, box, 24)
check("padding hits the requested count exactly", len(padded) == 24)
check("...and every padded point lies on the outline",
      outline.max_error(padded, box) <= 1e-9,
      f"{outline.max_error(padded, box):.6f}px")
check("...and stays monotone", monotone(padded, len(box)))

# --- a full turn: the case that breaks MergedPath -------------------------
# A cylinder tumbling end over end. Its outline runs from a tall rectangle
# through every intermediate to a flat circle and back, which is exactly the
# sweep that reorders a vertex-bound slot map.
FRAMES = 48
CAP = 16
polys = []
for f in range(FRAMES):
    a = 2 * math.pi * f / FRAMES
    half_h = abs(math.cos(a)) * 120.0 + 4.0        # foreshortens as it turns
    rx = 100.0
    ry = abs(math.sin(a)) * 100.0 + 2.0            # cap opens from edge-on
    pts = []
    for k in range(CAP + 1):                       # top cap, left to right
        th = math.pi - math.pi * k / CAP
        pts.append((rx * math.cos(th), half_h + ry * math.sin(th)))
    for k in range(CAP + 1):                       # bottom cap, right to left
        th = -math.pi * k / CAP
        pts.append((rx * math.cos(th), -half_h + ry * math.sin(th)))
    polys.append(np.asarray(pts))

n = outline.slots_needed(polys, TOL)
check("one point count covers every frame of a full turn", 4 <= n <= 64,
      f"{n} slots")

prev = None
worst_err = 0.0
bad_frame = None
for f, poly in enumerate(polys):
    p = outline.pad_to(outline.choose(poly, TOL), poly, n)
    p = outline.align(p, poly, prev)
    if not monotone(p, len(poly)):
        bad_frame = f
        break
    worst_err = max(worst_err, outline.max_error(p, poly))
    prev = outline.evaluate(p, poly)

check("slots stay monotone through all 48 frames of the turn",
      bad_frame is None, "" if bad_frame is None else f"broke at frame {bad_frame}")
check("...and the shape stays inside tolerance the whole way",
      worst_err <= TOL, f"worst {worst_err:.3f}px")


# --- self-intersection, stated directly ----------------------------------
def crosses(pts):
    """Does this closed polygon intersect itself anywhere?"""
    n = len(pts)

    def seg(p, q, r):
        return (r[1] - p[1]) * (q[0] - p[0]) - (q[1] - p[1]) * (r[0] - p[0])

    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            c, d = pts[j], pts[(j + 1) % n]
            d1, d2 = seg(a, b, c), seg(a, b, d)
            d3, d4 = seg(c, d, a), seg(c, d, b)
            if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
                return True
    return False


prev = None
crossed = None
for f, poly in enumerate(polys):
    p = outline.align(outline.pad_to(outline.choose(poly, TOL), poly, n), poly, prev)
    pts = outline.evaluate(p, poly)
    if crosses(pts):
        crossed = f
        break
    prev = pts
check("the outline never crosses itself, in any frame of the turn",
      crossed is None, "" if crossed is None else f"crossed at frame {crossed}")

# --- alignment keeps points from touring the outline ---------------------
# Same shape, rotated: without alignment slot 0 chases whichever vertex the
# refinement happened to pick, and every point takes the long way round.
ring_a = circle(113.0, 59)
ring_b = circle(113.0, 59, phase=0.11)
pa = outline.pad_to(outline.choose(ring_a, TOL), ring_a, 24)
raw = outline.pad_to(outline.choose(ring_b, TOL), ring_b, 24)
pts_a = outline.evaluate(pa, ring_a)
drift_raw = float(np.linalg.norm(outline.evaluate(raw, ring_b) - pts_a, axis=1).max())
aligned = outline.align(raw, ring_b, pts_a)
drift_aligned = float(np.linalg.norm(outline.evaluate(aligned, ring_b) - pts_a, axis=1).max())
check("alignment cuts how far a slot moves between frames",
      drift_aligned <= drift_raw,
      f"{drift_raw:.1f}px unaligned -> {drift_aligned:.1f}px aligned")

# --- degenerate input must not raise -------------------------------------
tiny = np.array([[0.0, 0.0], [1.0, 0.0], [0.5, 1.0]])
check("a triangle survives", len(outline.pad_to(outline.choose(tiny, TOL), tiny, 3)) == 3)
check("a triangle can still be padded", len(outline.pad_to([0, 1, 2], tiny, 9)) == 9)
flat = np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0], [1.0, 0.0]])
check("a zero-area outline does not divide by zero",
      outline.choose(flat, TOL) is not None)

print()
print(f"{len(fails)} failure(s)" + (": " + ", ".join(fails) if fails else ""))
raise SystemExit(1 if fails else 0)
