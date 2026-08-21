"""A fill thinner than the sample step still has to be ordered against.

    "<blender>/5.1/python/bin/python.exe" scripts/test_thin_overlap.py

order.resolve() only constrains a pair it can find shared ground for, and for a
polyline against a fill it looked for that ground by walking the line at a fixed
4px step and asking which points landed inside. That works until the fill is
narrower than the step.

An edge-on face is exactly that: a sliver. feed-horn/f0 on the Voyager probe is
3.8px wide, and the 125px chain segment crossing it -- sampled 32 times, every
4px -- put not one point on it. No shared samples means no constraint, and with
nothing to say otherwise a bus edge was painted straight over a shaft standing
0.95 units in front of it.

The fix is to stop sampling and start solving: clip the segment to the polygon
and take points from the part that is provably inside. The fill-fill branch
already had this shape of fallback, for the same reason, on the grid rather
than the step.
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


def prep(items, ortho=True):
    for it in items:
        p = it["pts"]
        it["bbox"] = (p[:, 0].min(), p[:, 0].max(), p[:, 1].min(), p[:, 1].max())
        it["dmin"] = float(it["depths"].min())
        it["dmax"] = float(it["depths"].max())
        it["field"] = (order._fit_field(p, it["depths"], ortho)
                       if it["kind"] == order.FILL_KIND else None)
    return items


# --- the probe's numbers: a 3.8px sliver crossed by a 125px line ----------
SLIVER_W = 3.8
shaft = dict(key="shaft", kind=order.FILL_KIND, seed=9.6,
             pts=np.array([[775.4, 249.5], [775.4 + SLIVER_W, 249.5],
                           [775.4 + SLIVER_W, 518.8], [775.4, 518.8]]),
             depths=np.array([9.62, 9.62, 10.03, 10.03]))
# a chain segment sweeping across it, well behind
line = dict(key="edge", kind=0, seed=10.5,
            pts=np.array([[700.0, 400.0], [825.0, 400.0]]),
            depths=np.array([10.5, 10.5]),
            on_faces=())
prep([shaft, line])

check("the sliver really is thinner than the old 4px step", SLIVER_W < 4.0)

shared = order._shared_samples(shaft, line)
check("shared ground is found at all", shared is not None,
      "None means no constraint, which is the bug")
if shared is not None:
    qx, qy = shared
    check("every point it returns is genuinely inside the fill",
          bool(order._inside(shaft, qx, qy).all()), f"{len(qx)} points")
    check("...and they are on the line, not near it",
          bool(np.allclose(qy, 400.0)), f"y={np.unique(np.round(qy, 6))}")

res = order.resolve([dict(shaft), dict(line)], is_ortho=True)
# larger value = painted earlier = further back
check("the shaft in front is drawn over the line behind it",
      res[1] > res[0], f"line={res[1]} shaft={res[0]}")

# --- the exact solver on its own ------------------------------------------
square = np.array([[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]])
spans = order._inside_spans(np.array([-5.0, 5.0]), np.array([15.0, 5.0]), square)
check("a line crossing a square enters and leaves once", len(spans) == 1, f"{spans}")
if spans:
    lo, hi = spans[0]
    check("...at the right places", abs(lo - 0.25) < 1e-9 and abs(hi - 0.75) < 1e-9,
          f"({lo:.4f}, {hi:.4f})")

# A concave outline must be clipped against its real boundary, not its box. At
# y=5 this C-shape is solid only from x=0 to x=4 -- the notch is open from x=4
# out to x=10 -- so a line swept right-to-left across it is inside for the last
# stretch only. A bounding-box test would have called the whole crossing inside
# and derived the constraint from depths sampled over thin air.
c_shape = np.array([[0.0, 0.0], [10.0, 0.0], [10.0, 4.0], [4.0, 4.0],
                    [4.0, 6.0], [10.0, 6.0], [10.0, 10.0], [0.0, 10.0]])
notched = order._inside_spans(np.array([12.0, 5.0]), np.array([-2.0, 5.0]), c_shape)
check("a notch is excluded, not swallowed", len(notched) == 1, f"{notched}")
if notched:
    lo, hi = notched[0]
    # enters at x=4 (t=8/14) and leaves at x=0 (t=12/14)
    check("...with the span ending where the solid part does",
          abs(lo - 8 / 14) < 1e-9 and abs(hi - 12 / 14) < 1e-9, f"({lo:.4f}, {hi:.4f})")

miss = order._inside_spans(np.array([-5.0, 50.0]), np.array([15.0, 50.0]), square)
check("a line that misses reports nothing", miss == [], f"{miss}")

along = order._inside_spans(np.array([0.0, 0.0]), np.array([0.0, 0.0]), square)
check("a zero-length segment is not divided by zero", along == [])

# --- a closed chain draws one more segment than it lists ------------------
# The player calls closePath(), so the wrap from the last point back to the
# first is stroked like any other segment. resolve() walked range(len(pts)-1)
# and never saw it, so a chain overlapping a fill *only* along that segment got
# no constraint at all and was ordered by seed alone. On the Voyager probe that
# was the bus rim over the feed-horn shaft: 126px of line, a full unit of depth
# on the wrong side of it, and every audit calling the frame clean.
ring = dict(key="ring", kind=0, seed=5.0,
            pts=np.array([[0.0, 0.0], [100.0, 0.0], [100.0, 100.0], [0.0, 100.0]]),
            depths=np.array([9.0, 9.0, 9.0, 9.0]), closed=True, on_faces=())
# a fill sitting only on the closing edge, x=0 -> the segment [0,100]->[0,0]
onClosing = dict(key="onClosing", kind=order.FILL_KIND, seed=4.0,
                 pts=np.array([[-6.0, 40.0], [6.0, 40.0], [6.0, 60.0], [-6.0, 60.0]]),
                 depths=np.array([4.0, 4.0, 4.0, 4.0]))
prep([ring, onClosing])
check("a closed chain's wrap segment is sampled",
      order._shared_samples(onClosing, ring) is not None,
      "None means the pair is never constrained")

open_ring = dict(ring)
open_ring["closed"] = False
open_ring["key"] = "open"
check("...and an open one still is not, since it does not draw that segment",
      order._shared_samples(onClosing, dict(open_ring)) is None)

res = order.resolve([dict(ring), dict(onClosing)], is_ortho=True)
check("the nearer fill is drawn over the ring's closing segment",
      res[0] > res[1], f"ring={res[0]} fill={res[1]}")

# depths must interpolate along the wrap too, not snap to the nearest listed leg
ramp = dict(key="ramp", kind=0, seed=5.0,
            pts=np.array([[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]]),
            depths=np.array([1.0, 1.0, 1.0, 9.0]), closed=True, on_faces=())
prep([ramp])
mid = order._depths_at(ramp, np.array([0.0]), np.array([5.0]), True)[0]
check("depth is interpolated along the closing segment",
      4.0 < mid < 6.0, f"got {mid:.2f} halfway between 9.0 and 1.0")

print()
print(f"{len(fails)} failure(s)" + (": " + ", ".join(fails) if fails else ""))
raise SystemExit(1 if fails else 0)
