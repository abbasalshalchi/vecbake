"""Flat elements following their neighbours' curves. Run inside Blender:

    exec(open('scripts/test_follow.py').read())

The thing worth guarding is the *agreement*: a follower's inserted points have
to land on the very curve the owner draws, in every frame, or the fix trades a
wedge-shaped gap for a wobbly one.
"""

import math

import numpy as np

from vecbake import follow
from vecbake.ir import FILL, STROKE, Element


class Settings:
    dissolve_px = 0.35


def _slot(frames):
    return {"geo": [list(f) if f is not None else None for f in frames],
            "npts": len(frames[0]) // 2 if frames[0] else 0}


def _ring(npts, cx, cy, r, squash=1.0):
    out = []
    for i in range(npts):
        a = 2 * math.pi * i / npts
        out += [cx + r * math.cos(a), cy + r * squash * math.sin(a)]
    return out


# The reference curve is itself walked as a fine polyline, so distances to it
# bottom out around a hundred-thousandth of a pixel. ON_CURVE sits above that
# floor and far below anything a wrong parameter or a flipped direction could
# produce -- those miss by pixels.
ON_CURVE = 1e-3


def _dist_to_curve(px, py, curve, steps=2000):
    """Distance to the curve itself, not to the nearest place we sampled it.

    Measuring to sample points instead reports the sampling interval as error,
    which is a fine way to fail a correct implementation.
    """
    pts = [follow._at(curve, k / steps) for k in range(steps + 1)]
    return min(_point_to_segment(px, py, pts[i], pts[i + 1]) for i in range(steps))


def test_flat_neighbour_lands_on_the_curve():
    """A quad sharing one edge with a curved ring must sit on that curve."""
    n = 10
    ring_pts = _ring(n, 0.0, 0.0, 200.0, 0.35)
    cap = Element(key="o/f0", kind=FILL, npts=n, group="g", color=(1, 1, 1))
    cap.smooth = np.ones(n, dtype=bool)
    # the side quad below rim vertices 0 and 1
    ax, ay = ring_pts[0], ring_pts[1]
    bx, by = ring_pts[2], ring_pts[3]
    quad = Element(key="o/f1", kind=FILL, npts=4, group="g", color=(1, 1, 1))

    slots = {
        "o/f0": _slot([ring_pts]),
        "o/f1": _slot([[ax, ay + 300, ax, ay, bx, by, bx, by + 300]]),
    }
    verts = {"o/f0": list(range(n)), "o/f1": [100, 0, 1, 101]}
    report = follow.apply([cap, quad], slots, verts, Settings(), 1)

    assert report["followed"] == 1, report
    assert quad.npts > 4, "the quad should have gained points"
    assert cap.npts == n, "the owner is never rewritten"

    # every inserted point must be on the cap's curve
    pts = slots["o/f1"]["geo"][0]
    inserted = [(pts[i * 2], pts[i * 2 + 1]) for i in range(2, quad.npts - 1)]
    assert inserted, "expected points between the two shared vertices"
    curve = follow._cubic(ring_pts, n, cap.smooth, True, 0, 1)
    for px, py in inserted:
        d = _dist_to_curve(px, py, curve)
        assert d < ON_CURVE, f"inserted point {px, py} is {d:.6f}px off the curve"


def test_gap_closes_to_within_tolerance():
    """The point of the exercise: the straight chord's error must go away."""
    n = 10
    ring_pts = _ring(n, 0.0, 0.0, 200.0, 0.35)
    cap = Element(key="o/f0", kind=FILL, npts=n, group="g", color=(1, 1, 1))
    cap.smooth = np.ones(n, dtype=bool)
    ax, ay, bx, by = ring_pts[0], ring_pts[1], ring_pts[2], ring_pts[3]
    quad = Element(key="o/f1", kind=FILL, npts=4, group="g", color=(1, 1, 1))
    slots = {"o/f0": _slot([ring_pts]),
             "o/f1": _slot([[ax, ay + 300, ax, ay, bx, by, bx, by + 300]])}
    verts = {"o/f0": list(range(n)), "o/f1": [100, 0, 1, 101]}

    curve = follow._cubic(ring_pts, n, cap.smooth, True, 0, 1)
    before = follow._bow(curve)
    assert before > 1.0, f"the test case has to have a visible bow, got {before}"

    follow.apply([cap, quad], slots, verts, Settings(), 1)
    pts = slots["o/f1"]["geo"][0]
    poly = [(pts[i * 2], pts[i * 2 + 1]) for i in range(1, quad.npts - 1)]

    worst = 0.0
    for k in range(401):
        cx, cy = follow._at(curve, k / 400.0)
        d = min(_point_to_segment(cx, cy, poly[i], poly[i + 1])
                for i in range(len(poly) - 1))
        worst = max(worst, d)
    assert worst <= Settings.dissolve_px, \
        f"followed edge still {worst:.3f}px from the curve (was {before:.2f}px)"


def _point_to_segment(px, py, a, b):
    ax, ay = a
    bx, by = b
    vx, vy = bx - ax, by - ay
    den = vx * vx + vy * vy
    t = 0.0 if den < 1e-12 else max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / den))
    return math.hypot(px - (ax + vx * t), py - (ay + vy * t))


def test_direction_is_respected():
    """A follower walking the edge backwards must not mirror the bow."""
    n = 10
    ring_pts = _ring(n, 0.0, 0.0, 200.0, 0.35)
    cap = Element(key="o/f0", kind=FILL, npts=n, group="g", color=(1, 1, 1))
    cap.smooth = np.ones(n, dtype=bool)
    ax, ay, bx, by = ring_pts[0], ring_pts[1], ring_pts[2], ring_pts[3]
    # this one lists vertex 1 before vertex 0
    quad = Element(key="o/f1", kind=FILL, npts=4, group="g", color=(1, 1, 1))
    slots = {"o/f0": _slot([ring_pts]),
             "o/f1": _slot([[bx, by + 300, bx, by, ax, ay, ax, ay + 300]])}
    verts = {"o/f0": list(range(n)), "o/f1": [101, 1, 0, 100]}
    follow.apply([cap, quad], slots, verts, Settings(), 1)

    pts = slots["o/f1"]["geo"][0]
    curve = follow._cubic(ring_pts, n, cap.smooth, True, 0, 1)
    inserted = [(pts[i * 2], pts[i * 2 + 1]) for i in range(2, quad.npts - 1)]
    for px, py in inserted:
        d = _dist_to_curve(px, py, curve)
        assert d < ON_CURVE, f"reversed follower is {d:.6f}px off the curve"

    # on the curve is not enough -- they have to be in the order this element
    # walks it, or the outline doubles back on itself
    start = (pts[2], pts[3])                    # the follower's first shared vertex
    runs = [math.hypot(px - start[0], py - start[1]) for px, py in inserted]
    assert runs == sorted(runs), f"inserted points are out of order: {runs}"


def test_many_edges_of_one_owner_each_get_their_own_curve():
    """A follower spanning several of an owner's edges, all at once.

    The case a per-element cache gets wrong: cache the owner's curve by owner
    rather than by *edge* and every split point after the first lands on the
    first edge's curve, which puts a whole arc's worth of points in a heap at
    one corner. Only shows up once something follows one owner more than once.
    """
    n = 12
    ring_pts = _ring(n, 0.0, 0.0, 220.0, 0.4)
    ring = Element(key="o/f0", kind=FILL, npts=n, group="g", color=(1, 1, 1))
    ring.smooth = np.ones(n, dtype=bool)

    # an open chain running along four consecutive edges of that ring
    span = [0, 1, 2, 3, 4]
    chain = Element(key="o/c0", kind=STROKE, npts=len(span), group="g", color=(0, 0, 0))
    flat = []
    for v in span:
        flat += [ring_pts[v * 2], ring_pts[v * 2 + 1]]
    slots = {"o/f0": _slot([ring_pts]), "o/c0": _slot([flat])}
    verts = {"o/f0": list(range(n)), "o/c0": span}

    report = follow.apply([ring, chain], slots, verts, Settings(), 1)
    assert report["followed"] == 1 and chain.npts > len(span), report

    pts = slots["o/c0"]["geo"][0]
    P = [(pts[i * 2], pts[i * 2 + 1]) for i in range(chain.npts)]
    # every point must sit on the ring's curve, and they must stay in order
    for k, (px, py) in enumerate(P):
        best = min(_dist_to_curve(px, py,
                                  follow._cubic(ring_pts, n, ring.smooth, True, s, s + 1))
                   for s in range(4))
        assert best < ON_CURVE, f"point {k} at {px:.1f},{py:.1f} is {best:.3f}px off"
    run = [math.hypot(P[i + 1][0] - P[i][0], P[i + 1][1] - P[i][1])
           for i in range(len(P) - 1)]
    assert max(run) < 3 * (sum(run) / len(run)), \
        f"a jump this big means points landed on the wrong edge: {max(run):.1f}px"


def test_nothing_happens_without_a_curve():
    quad = Element(key="o/f1", kind=FILL, npts=4, group="g", color=(1, 1, 1))
    other = Element(key="o/f2", kind=FILL, npts=4, group="g", color=(1, 1, 1))
    slots = {"o/f1": _slot([[0, 0, 100, 0, 100, 100, 0, 100]]),
             "o/f2": _slot([[0, 0, 100, 0, 100, -100, 0, -100]])}
    verts = {"o/f1": [0, 1, 2, 3], "o/f2": [0, 1, 4, 5]}
    report = follow.apply([quad, other], slots, verts, Settings(), 1)
    assert report["followed"] == 0 and quad.npts == 4 and other.npts == 4


def test_a_straight_edge_is_left_alone():
    """Smooth mask or not, an edge with no bow costs no points."""
    n = 4
    line = [0, 0, 100, 0, 200, 0, 300, 0]          # collinear: tangents bow nothing
    owner = Element(key="o/c0", kind=STROKE, npts=n, group="g", color=(0, 0, 0))
    owner.smooth = np.ones(n, dtype=bool)
    quad = Element(key="o/f1", kind=FILL, npts=4, group="g", color=(1, 1, 1))
    slots = {"o/c0": _slot([line]),
             "o/f1": _slot([[100, 50, 100, 0, 200, 0, 200, 50]])}
    verts = {"o/c0": [0, 1, 2, 3], "o/f1": [50, 1, 2, 51]}
    report = follow.apply([owner, quad], slots, verts, Settings(), 1)
    assert report["followed"] == 0, "a flat curve needs no following"
    assert quad.npts == 4


def test_point_count_is_the_same_in_every_frame():
    """Element identity depends on it: a count that moved would be a new shape."""
    n = 10
    frames = [_ring(n, 0.0, 0.0, 200.0 + 60 * k, 0.35) for k in range(4)]
    cap = Element(key="o/f0", kind=FILL, npts=n, group="g", color=(1, 1, 1))
    cap.smooth = np.ones(n, dtype=bool)
    quads = []
    for f in frames:
        quads.append([f[0], f[1] + 300, f[0], f[1], f[2], f[3], f[2], f[3] + 300])
    quad = Element(key="o/f1", kind=FILL, npts=4, group="g", color=(1, 1, 1))
    slots = {"o/f0": _slot(frames), "o/f1": _slot(quads)}
    verts = {"o/f0": list(range(n)), "o/f1": [100, 0, 1, 101]}
    follow.apply([cap, quad], slots, verts, Settings(), len(frames))
    for fi, g in enumerate(slots["o/f1"]["geo"]):
        assert len(g) // 2 == quad.npts, f"frame {fi} has {len(g)//2}, npts is {quad.npts}"


def test_hidden_owner_falls_back_to_the_chord():
    n = 10
    ring_pts = _ring(n, 0.0, 0.0, 200.0, 0.35)
    cap = Element(key="o/f0", kind=FILL, npts=n, group="g", color=(1, 1, 1))
    cap.smooth = np.ones(n, dtype=bool)
    ax, ay, bx, by = ring_pts[0], ring_pts[1], ring_pts[2], ring_pts[3]
    quad_pts = [ax, ay + 300, ax, ay, bx, by, bx, by + 300]
    slots = {"o/f0": _slot([ring_pts, ring_pts]), "o/f1": _slot([quad_pts, quad_pts])}
    slots["o/f0"]["geo"][1] = None                 # owner off screen in frame 1
    quad = Element(key="o/f1", kind=FILL, npts=4, group="g", color=(1, 1, 1))
    verts = {"o/f0": list(range(n)), "o/f1": [100, 0, 1, 101]}
    follow.apply([cap, quad], slots, verts, Settings(), 2)

    g = slots["o/f1"]["geo"][1]
    assert len(g) // 2 == quad.npts
    for i in range(2, quad.npts - 1):              # all on the straight chord
        px, py = g[i * 2], g[i * 2 + 1]
        assert _point_to_segment(px, py, (ax, ay), (bx, by)) < 1e-9


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print("ok  ", fn.__name__)
    print(f"\n{len(tests)} passed")


main()
