# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""The visible false outline behind `merge_false_px`: line a merged fill draws
between keyframes where the part has no outline, and that nothing nearer covers.

Found on the Apollo-Soyuz docking module (dock_001/m): 214 px of it over the
clip, against 119 px on the next worst part. See docs/audits.md section 13.

    python scripts/test_false_outline.py
"""
import numpy as np

import _addon

_addon.stub_blender()
bake = _addon.load("bake")
bake.Vector = lambda v: tuple(float(c) for c in v)   # the stub has no mathutils

fails = []


def check(label, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {label}  {detail}")
    if not cond:
        fails.append(label)


def circle(cx, cy, r, n=32):
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return np.stack([cx + r * np.cos(t), cy + r * np.sin(t)], axis=1)


W, H = 400, 300
body = circle(200, 150, 80)
nozzle = circle(320, 150, 20)
truth = [(body, np.full(len(body), 10.0)), (nozzle, np.full(len(nozzle), 10.0))]

# An outline drawn exactly on the silhouette draws no false line.
pts, dep = bake._false_outline([body, nozzle], truth, 0.44, W, H)
check("outline on the silhouette is not false", len(pts) == 0, f"{len(pts)} samples")

# A ring half grown between keyframes: the nozzle at half size is false all round.
half = circle(320, 150, 10)
pts, dep = bake._false_outline([body, half], truth, 0.44, W, H)
expect = 2 * np.pi * 10
got = len(pts) * 0.5
check("a half-grown ring is false line along its length",
      abs(got - expect) < 0.05 * expect, f"{got:.1f} px, ring {expect:.1f} px")
check("false samples take the outline's depth", np.allclose(dep, 10.0))

# A collapsed ring (every point equal) draws nothing.
pts, _ = bake._false_outline([body, np.repeat([[320.0, 150.0]], 32, axis=0)], truth, 0.44, W, H)
check("a collapsed ring draws no line", len(pts) == 0)

# Off the canvas does not count.
off = circle(-100, 150, 10)
pts, _ = bake._false_outline([body, off], truth, 0.44, W, H)
check("a ring off the canvas is not counted", len(pts) == 0)

# Points sliding across a corner: a square whose one side bows inward.
square = np.array([[100, 100], [200, 100], [200, 200], [100, 200]], dtype=float)
sq_truth = [(square, np.full(4, 5.0))]
cut = np.array([[100, 100], [200, 100], [150, 150], [100, 200]], dtype=float)
pts, _ = bake._false_outline([cut], sq_truth, 0.44, W, H)
check("a corner cut is false line", len(pts) * 0.5 > 100, f"{len(pts) * 0.5:.1f} px")


# --- what a viewer can see -------------------------------------------------
class Hit:
    def __init__(self, name):
        self.name = name


class Scene:
    """Orthographic stand-in: an occluder covers x < 320 at depth `near`."""

    def __init__(self, near, name="cover"):
        self.near, self.cover = near, name
        self.camera = None

    def ray_cast(self, dg, origin, direction):
        x = origin[0]
        if x < 320:
            return True, (x, origin[1], -self.near), None, 0, Hit(self.cover), None
        return False, None, None, 0, None, None


class Eye:
    def inverted(self):
        return np.eye(4)


class Cam:
    def __init__(self):
        self.matrix_world = Eye()

    def evaluated_get(self, dg):
        return self


# mvp for an orthographic camera looking down -z, pixel x == world x.
mvp = np.array([[2.0 / W, 0, 0, -1], [0, 2.0 / H, 0, -1], [0, 0, -0.01, 0], [0, 0, 0, 1]])
mvp[1] = [0, -2.0 / H, 0, 1]
pts = np.array([[300.0, 150.0], [340.0, 150.0]])
dep = np.array([10.0, 10.0])

sc = Scene(near=4.0)
sc.camera = Cam()
seen = bake._unhidden(sc, None, mvp, W, H, pts, dep, "part", {"part", "cover"})
check("a nearer drawn object hides the line behind it", seen == 1, f"{seen} of 2 seen")

sc = Scene(near=20.0)
sc.camera = Cam()
seen = bake._unhidden(sc, None, mvp, W, H, pts, dep, "part", {"part", "cover"})
check("an object behind the line does not hide it", seen == 2, f"{seen} of 2 seen")

sc = Scene(near=4.0, name="part")
sc.camera = Cam()
seen = bake._unhidden(sc, None, mvp, W, H, pts, dep, "part", {"part", "cover"})
check("the part's own surface does not hide its outline", seen == 2, f"{seen} of 2 seen")

sc = Scene(near=4.0)
sc.camera = Cam()
seen = bake._unhidden(sc, None, mvp, W, H, pts, dep, "part", {"part"})
check("an object the bake does not draw hides nothing", seen == 2, f"{seen} of 2 seen")

print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILED: ' + ', '.join(fails)}")
raise SystemExit(1 if fails else 0)
