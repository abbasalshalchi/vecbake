"""A merged outline must hold its shape through a full turn, and never cross.

    "<blender>/5.1/python/bin/python.exe" scripts/test_outline.py

A frame has two questions to answer about its outline, and they have different
answers. *Where do the points go* is settled by a measure -- arc length with
each vertex widening it by what that vertex is worth. *Which slot is which* is
settled by the mesh, a slot staying on the vertex it was on for as long as that
vertex is on the silhouette.

The assertions here are the properties that buys:

  * points stay monotone around the loop, in every frame of a full turn, so the
    polygon cannot self-intersect however far it spins;
  * the resampled outline stays inside the pixel tolerance it was given, and a
    frame handed *more* points than it asked for does not draw worse;
  * the point count comes from that tolerance rather than from counting mesh
    vertices -- the lab sphere's outline takes a few dozen points against the
    482 slots a vertex-bound map has to reserve;
  * a corner is drawn exactly, and a vertex arriving on a straight edge moves
    nothing until it is worth moving something;
  * a slot keeps its vertex when the ring around it changes.

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
TOL = 0.35


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
sphere_ring = circle(113.0, 59)
tight = outline.need_for(sphere_ring, TOL)
loose = outline.need_for(sphere_ring, 1.0)
check("a tolerance finer than the mesh keeps every ring vertex",
      tight >= len(sphere_ring), f"{tight} points for a 59-gon")
check("...and a coarser one genuinely reduces", loose < 40, f"{loose} at 1px")
check("either way it is one element, not the 482 slots a vertex map needs",
      max(tight, loose) < 120, f"{tight} / {loose}")
check("...and each meets the tolerance it was given",
      outline.max_error(outline.spread(sphere_ring, tight, TOL), sphere_ring) <= TOL
      and outline.max_error(outline.spread(sphere_ring, loose, 1.0),
                            sphere_ring) <= 1.0)

small = outline.need_for(circle(20.0, 59), 1.0)
big = outline.need_for(circle(500.0, 240), 1.0)
check("a smaller circle needs fewer points", small < loose, f"{small} at r=20")
check("a bigger one needs more", big > loose, f"{big} at r=500")


# --- corners are exact, which uniform spacing could not manage ------------
box = rect(400.0, 90.0)
nb = outline.need_for(box, TOL)
check("a rectangle collapses to its four corners", nb == 4, f"{nb} points")
check("...with no corner cut",
      outline.max_error(outline.spread(box, nb, TOL), box) <= 1e-9,
      f"{outline.max_error(outline.spread(box, nb, TOL), box):.2e}px")

uniform = outline.at_arc(np.arange(4) / 4.0, box)
check("uniform spacing would have failed the same shape",
      outline.max_error(uniform, box) > 1.0,
      f"{outline.max_error(uniform, box):.1f}px off")


# --- more points than a frame asked for must never draw worse ------------
# The count is settled once for the whole clip from the busiest frame, so a
# quieter frame is routinely handed a bigger budget than it needs. A vertex is
# only guaranteed a point once its share of the measure is a full step, so
# below that the corners are caught by arithmetic luck -- a four-corner box
# once drew exactly at four points, 8.3px wrong at six, and exactly again at
# eight. `need_for` returns a count that holds when it is exceeded.
worst_over = 0.0
for shape, tol in ((box, TOL), (rect(60.0, 90.0), TOL), (sphere_ring, 1.0)):
    base = outline.need_for(shape, tol)
    for extra in range(0, 13):
        e = outline.max_error(outline.spread(shape, base + extra, tol), shape)
        worst_over = max(worst_over, e - tol)
check("a bigger budget never draws worse than the tolerance", worst_over <= 1e-9,
      f"worst excess {worst_over:.2e}px")


# --- a vertex arriving on a straight edge disturbs nothing ----------------
# The failure this replaced: one span of the silhouette swaps to a different
# chain of edges while the rest stands still, and every slot after it shifts a
# place round the loop.
# What counts is not how far a point travels but where the polygon *between*
# two frames lands: a point sliding along a straight edge covers ground and
# changes nothing, while a point crossing a corner barely moves and cuts it.
flat = rect(400.0, 90.0, per_side=3)
grown = np.insert(flat, 1, [[-100.0, -45.0 - 0.05]], axis=0)
a_pts = outline.evaluate(outline.spread(flat, 12, TOL), flat)
b_pts = outline.evaluate(outline.place(grown, 12, TOL, a_pts), grown)
half = 0.5 * (a_pts + b_pts)
edge = flat[np.array([0, 3, 6, 9])]          # the four corners it must keep
stray = 0.0
for q in half:
    seg = np.roll(edge, -1, axis=0) - edge
    t = np.clip(np.einsum("ij,ij->i", q - edge, seg)
                / np.einsum("ij,ij->i", seg, seg), 0.0, 1.0)
    stray = max(stray, float(
        np.linalg.norm(q - (edge + seg * t[:, None]), axis=1).min()))
check("a vertex arriving collinear leaves the shape between frames alone",
      stray < 0.2, f"midpoint strays {stray:.3f}px")


# --- a full turn: the case that broke binding slots to vertices -----------
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

n = max(outline.need_for(p, TOL) for p in polys)
check("one point count covers every frame of a full turn", 4 <= n <= 96,
      f"{n} slots")

prev = None
worst_err = 0.0
bad_frame = None
for f, poly in enumerate(polys):
    ids = np.arange(len(poly), dtype=np.int64)
    pos, anchors = outline.carry(poly, ids, n, TOL, prev)
    if not monotone(pos, len(poly)):
        bad_frame = f
        break
    worst_err = max(worst_err, outline.max_error(pos, poly))
    prev = (anchors, outline.evaluate(pos, poly))

check("slots stay monotone through all 48 frames of the turn",
      bad_frame is None, "" if bad_frame is None else f"broke at frame {bad_frame}")
check("...and the shape stays inside tolerance the whole way",
      worst_err <= TOL, f"worst {worst_err:.3f}px")


# --- a slot keeps its vertex when the ring around it changes --------------
# What a measure alone cannot do. Its budget divides between the corners and
# the edges in proportion to what the corners are worth, so a vertex joining or
# leaving the ring moves that division by one and shifts every slot after it.
ring_a = rect(300.0, 120.0, per_side=4)
ids_a = np.arange(len(ring_a), dtype=np.int64)
pos_a, anch_a = outline.carry(ring_a, ids_a, 14, TOL, None)
pts_a = outline.evaluate(pos_a, ring_a)
check("the first frame anchors most of its slots to vertices",
      int((anch_a >= 0).sum()) >= 4, f"{int((anch_a >= 0).sum())} of 14")

# The ring gains a vertex partway along, as a fold rotating into view does.
ring_b = np.insert(ring_a, 3, [[ring_a[2, 0] * 0.5 + ring_a[3, 0] * 0.5,
                               ring_a[2, 1] * 0.5 + ring_a[3, 1] * 0.5]], axis=0)
ids_b = np.concatenate([ids_a[:3], [99], ids_a[3:]]).astype(np.int64)
pos_b, anch_b = outline.carry(ring_b, ids_b, 14, TOL, (anch_a, pts_a))
kept = sum(1 for j in range(14)
           if anch_a[j] >= 0 and anch_b[j] == anch_a[j])
alive = sum(1 for v in anch_a if v >= 0 and v in set(ids_b.tolist()))
check("every anchor that survived the change kept its own slot",
      kept == alive and alive > 0, f"{kept} of {alive} kept")
check("...and the outline is still inside tolerance",
      outline.max_error(pos_b, ring_b) <= TOL,
      f"{outline.max_error(pos_b, ring_b):.3f}px")
check("...and still monotone", monotone(pos_b, len(ring_b)))

# A span that acquires a real corner has to be able to take a slot from a
# neighbour, or the error creeps up frame after frame -- it climbed 0.41px,
# 0.55, 0.69 ... 1.22px against a 0.35px tolerance before spans could borrow.
spike = ring_a.copy()
spike[6] = spike[6] + np.array([0.0, -40.0])
ids_s = ids_a.copy()
pos_s, _ = outline.carry(spike, ids_s, 14, TOL, (anch_a, pts_a))
check("a span that gains a corner borrows a slot for it",
      outline.max_error(pos_s, spike) <= TOL,
      f"{outline.max_error(pos_s, spike):.3f}px")


# --- self-intersection, stated directly ----------------------------------
def crosses(pts):
    m = len(pts)
    for i in range(m):
        a, b = pts[i], pts[(i + 1) % m]
        for j in range(i + 1, m):
            if j == i or (j + 1) % m == i:
                continue
            c, d = pts[j], pts[(j + 1) % m]

            def side(o, e, p):
                return ((e[0] - o[0]) * (p[1] - o[1])
                        - (e[1] - o[1]) * (p[0] - o[0]))

            # Both sides must genuinely straddle. A zero means the segments
            # touch or graze, which is not a crossing -- and with more slots
            # than ring vertices, two slots coincide and touching is normal.
            def straddles(u, v):
                return (u > 0 > v) or (u < 0 < v)

            if (straddles(side(a, b, c), side(a, b, d))
                    and straddles(side(c, d, a), side(c, d, b))):
                return True
    return False


tangled = None
prev = None
for f, poly in enumerate(polys):
    ids = np.arange(len(poly), dtype=np.int64)
    pos, anchors = outline.carry(poly, ids, n, TOL, prev)
    pts = outline.evaluate(pos, poly)
    if crosses(pts):
        tangled = f
        break
    prev = (anchors, pts)
check("the outline never crosses itself, in any frame of the turn",
      tangled is None, "" if tangled is None else f"tangled at frame {tangled}")


# --- a loop that crosses itself cannot be merged at all -------------------
check("a circle is simple", outline.is_simple(circle(50.0, 24)))
check("a rectangle is simple", outline.is_simple(rect(120.0, 60.0, 4)))
check("a triangle is simple",
      outline.is_simple(np.array([[0., 0.], [9., 0.], [4., 8.]])))
bowtie = np.array([[0., 0.], [10., 10.], [10., 0.], [0., 10.]])
check("a bowtie is caught", not outline.is_simple(bowtie))
eight = list(circle(30.0, 12, cx=-30.0)) + list(circle(30.0, 12, cx=30.0))
check("a figure-eight is caught", not outline.is_simple(np.asarray(eight)))
ell = np.array([[0., 0.], [60., 0.], [60., 20.], [20., 20.],
                [20., 60.], [0., 60.]])
check("a concave L is still simple", outline.is_simple(ell))
check("...and its own resampling stays inside tolerance",
      outline.max_error(outline.spread(ell, outline.need_for(ell, TOL), TOL),
                        ell) <= TOL)


# --- winding is settled once, where the ring becomes geometry -------------
cw = circle(40.0, 16)[::-1]
ids_cw = np.arange(16, dtype=np.int64)[::-1]
w_ids, w_pts = outline.wound(ids_cw, circle(40.0, 16)[np.argsort(ids_cw)])
area = float((w_pts[:, 0] * np.roll(w_pts[:, 1], -1)
              - w_pts[:, 1] * np.roll(w_pts[:, 0], -1)).sum())
check("a ring comes back wound one way whichever way it was walked", area >= 0.0,
      f"signed area {area:.1f}")
check("...and its vertex ids travel with it", len(w_ids) == len(w_pts))


# --- degenerate input must not raise -------------------------------------
tiny = np.array([[0., 0.], [10., 0.], [5., 9.]])
check("a triangle survives", len(outline.spread(tiny, 3, TOL)) == 3)
check("a triangle can still be padded", len(outline.spread(tiny, 9, TOL)) == 9)
check("a degenerate ring returns something drawable",
      outline.measure(np.zeros((3, 2)), TOL, 8) is None)
check("...and placing on one does not raise",
      len(outline.place(np.zeros((3, 2)), 5, TOL)) == 5)

print()
print(f"{len(fails)} failed" if fails else "all passed")
sys.exit(1 if fails else 0)
