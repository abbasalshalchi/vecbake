# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""A chain is ordered by the vertices it runs through, not by the points it is drawn with.

Found on Apollo-Soyuz once dock_001 was drawn face by face: the rings round the
docking module came out dashed, one gap per mesh vertex (dock_001/c12_0,
dock_001/c310_0, and soyuz_001/c142_0, which was always faces and always dashed).

A chain is thinned for drawing (`dissolve_px`), which drops most of a curved
ring's vertices. The ordering pass was given the same thinned points, so between
two kept points it read the ring's depth off a straight line -- and a ring that
curves toward the camera sags behind its own chord. The ring's own faces that
meet it at a dropped vertex read their true depth there, so the ring measured
0.007-0.05 behind them: more than `tol`, so a real constraint. Each such face was
painted after the ring, and the hairline stroke the player lays round every fill
to close cracks between faces nicked the ring at that vertex.

The scene below is that shape: an arc bowing toward the camera, and triangles
that touch it at single vertices from the inside.

    python scripts/test_chain_depth.py
"""
import math
import os

import numpy as np

import _addon

_addon.stub_blender()
order = _addon.load("order")

fails = []


def check(label, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {label}  {detail}")
    if not cond:
        fails.append(label)


N = 25
theta = np.linspace(0.15 * math.pi, 0.85 * math.pi, N)
R, CX, CY = 300.0, 400.0, 400.0
arc = np.stack([CX + R * np.cos(theta), CY - R * np.sin(theta)], axis=1)
# Nearer the camera in the middle: depth 10 at the ends, 8 at the top.
arc_depth = 10.0 - 2.0 * (np.sin(theta) - np.sin(theta[0])) / (1 - np.sin(theta[0]))


def touching_faces():
    """One triangle per interior vertex, meeting the arc at that vertex only."""
    out = []
    for i in range(2, N - 2, 2):
        inward = np.array([CX, CY]) - arc[i]
        inward /= np.linalg.norm(inward)
        side = np.array([-inward[1], inward[0]])
        a = arc[i] + inward * 40 + side * 12
        b = arc[i] + inward * 40 - side * 12
        pts = np.array([arc[i], a, b])
        # The face leans a little toward the camera away from the ring, as the
        # module's end faces do, so its seed sorts it in front.
        d = np.array([arc_depth[i], arc_depth[i] - 0.05, arc_depth[i] - 0.05])
        out.append({"key": f"part/f{i}", "kind": order.FILL_KIND, "pts": pts,
                    "depths": d, "seed": float(d.mean())})
    return out


def run(line_idx):
    line = {"key": "part/c0_0", "kind": 0, "width": 1.6,
            "pts": arc[line_idx], "depths": arc_depth[line_idx],
            "seed": float(arc_depth.min()), "on_faces": ()}
    faces = touching_faces()
    items = [line] + faces
    ranks = order.resolve([dict(it) for it in items], is_ortho=True)
    over = [f["key"] for f, r in zip(faces, ranks[1:]) if r < ranks[0]]
    return over, len(faces)


thinned = [0, N // 2, N - 1]            # what _dissolve keeps of a smooth arc
over, n = run(thinned)
check("measured by its thinned points, the ring reads behind faces it only touches",
      len(over) > 0, f"{len(over)} of {n} touching faces painted over it")

over, n = run(list(range(N)))
check("measured along every vertex, no face that only touches it is painted over it",
      not over, f"{len(over)} of {n}: {over[:4]}")

# Touching is not covering. A face of the same part that shares a vertex with
# the line *and* lies across its length, in front, is measured, and must still
# be painted over it.
line = {"key": "part/c0_0", "kind": 0, "width": 1.6, "pts": arc.copy(),
        "depths": arc_depth.copy(), "seed": float(arc_depth.min()), "on_faces": ()}
i = N // 2
lid = np.array([arc[i], arc[i] + [-60.0, -30.0], arc[i] + [60.0, -30.0], arc[i] + [60.0, 30.0],
                arc[i] + [-60.0, 30.0]])
cover = {"key": "part/f99", "kind": order.FILL_KIND, "pts": lid,
         "depths": np.full(5, arc_depth.min() - 1.0), "seed": float(arc_depth.min() - 1.0)}
ranks = order.resolve([dict(line), dict(cover)], is_ortho=True)
check("a face sharing a vertex that really lies across the line in front still covers it",
      ranks[1] < ranks[0], f"line {ranks[0]:.0f} face {ranks[1]:.0f}")

# ...and parts are separate: a vertex of another part landing on the line is not
# topology, so it decides nothing.
other = [dict(f, key=f["key"].replace("part/", "other/")) for f in touching_faces()]
line_all = {"key": "part/c0_0", "kind": 0, "width": 1.6, "pts": arc.copy(),
            "depths": arc_depth.copy(), "seed": float(arc_depth.min()), "on_faces": ()}
calls = []
real = order._touches
order._touches = lambda *a: (calls.append(a[1:3]), real(*a))[1]
order.resolve([dict(line_all)] + [dict(f) for f in other], is_ortho=True)
order._touches = real
check("another part's faces are not treated as touching",
      calls and not any(real([dict(line_all)] + other, f, l,
                             [it["key"].split("/")[0] for it in [line_all] + other], {})
                        for f, l in calls), f"{len(calls)} pairs asked")

# The chain builder has to hand the ordering pass the vertex path, not the
# thinned one it draws.
src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "addon", "vecbake", "chain.py"), encoding="utf-8").read()
check("chain.py orders a chain by its unthinned vertex path",
      '"pts": xy[measure_path], "depths": depths[measure_path]' in src)

print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILED: ' + ', '.join(fails)}")
raise SystemExit(1 if fails else 0)
