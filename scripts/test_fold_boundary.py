# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""A merged outline must cover what the object covers, where its surface folds.

Found on the Apollo-Soyuz docking module (dock_001): a body, a narrow neck, and a
flange. The neck's silhouette comes back as its own loop, but along 40 of its 42
segments the part covers both sides -- it runs round the walls -- and only its
two long silhouette edges are a real boundary. Two things threw it away:

  * the fold test sampled nine evenly spaced segments, missed both, and called
    the whole loop a fold (1,043 px missing at frame 21 of the hero);
  * where the neck is under 1% of the part's loop area, a size cut meant for
    bridged single rings dropped it first (113-122 px at frames 11 and 26).

Either way the part drew with a band missing straight through it, where ray-cast
truth has dock_001 solid. The profile below is dock_001's own.

The shape is built exactly, projected orthographically at several angles, and
the rings `walk_pieces` returns are filled with the nonzero rule on a pixel
grid and compared with the union of the front faces -- which, for a closed
solid, is precisely what the object covers on screen.

    python scripts/test_fold_boundary.py
"""
import math
from types import SimpleNamespace

import numpy as np

import _addon

_addon.stub_blender()
merge = _addon.load("merge")

fails = []


def check(label, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {label}  {detail}")
    if not cond:
        fails.append(label)


def grooved_cylinder(seg=40):
    """dock_001's own profile (axial, radius), read off the mesh: a body tapering to a lip,
    a wall down into a narrow neck, a wall up to a flange, and an end cap."""
    profile = [(-0.742, 0.0), (-0.742, 0.257), (-0.634, 0.257), (-0.137, 0.618),
               (1.374, 0.618), (1.485, 0.578), (1.485, 0.542), (1.729, 0.542),
               (1.729, 0.578), (1.729, 0.592), (1.784, 0.592), (1.784, 0.0)]
    verts, rings = [], []
    for x, r in profile:
        if r == 0.0:
            rings.append([len(verts)])
            verts.append((x, 0.0, 0.0))
            continue
        ring = []
        for k in range(seg):
            a = 2 * math.pi * k / seg
            ring.append(len(verts))
            verts.append((x, r * math.cos(a), r * math.sin(a)))
        rings.append(ring)
    faces = []
    for ra, rb in zip(rings, rings[1:]):
        if len(ra) == 1:
            faces += [[ra[0], rb[(k + 1) % seg], rb[k]] for k in range(seg)]
        elif len(rb) == 1:
            faces += [[ra[k], ra[(k + 1) % seg], rb[0]] for k in range(seg)]
        else:
            faces += [[ra[k], ra[(k + 1) % seg], rb[(k + 1) % seg], rb[k]] for k in range(seg)]
    return np.asarray(verts, dtype=float), faces


def plan_for(verts, faces):
    key, ea, eb, ev = {}, [], [], []
    for f, poly in enumerate(faces):
        for a, b in zip(poly, poly[1:] + poly[:1]):
            k = (min(a, b), max(a, b))
            if k not in key:
                key[k] = len(ev)
                ev.append(k)
                ea.append(f)
                eb.append(-1)
            else:
                eb[key[k]] = f
    ls = np.cumsum([0] + [len(p) for p in faces[:-1]])
    return SimpleNamespace(
        edge_verts=np.asarray(ev), edge_face_a=np.asarray(ea), edge_face_b=np.asarray(eb),
        nedges=len(ev), nfaces=len(faces), loop_start=ls,
        loop_total=np.asarray([len(p) for p in faces]),
        loop_verts=np.asarray([v for p in faces for v in p]))


def view(verts, faces, yaw, pitch, scale=140.0, centre=(200.0, 150.0)):
    """Orthographic camera looking along -d; returns screen xy and the front-face mask."""
    cy, sy, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
    d = np.array([cp * cy, cp * sy, sp])             # towards the camera
    right = np.cross([0.0, 0.0, 1.0], d)
    right /= np.linalg.norm(right)
    up = np.cross(d, right)
    c = verts - verts.mean(axis=0)
    xy = np.column_stack([c @ right * scale + centre[0], -(c @ up) * scale + centre[1]])
    front = []
    for poly in faces:
        p = verts[poly]
        n = np.cross(p[1] - p[0], p[2] - p[0])
        front.append(float(n @ d) > 0.0)
    return xy, np.asarray(front)


def raster(polys, w=400, h=300):
    """Nonzero-winding fill of closed polygons at pixel centres."""
    ys, xs = np.mgrid[0:h, 0:w] + 0.5
    wind = np.zeros((h, w), dtype=np.int32)
    for poly in polys:
        x1, y1 = poly[:, 0], poly[:, 1]
        x2, y2 = np.roll(x1, -1), np.roll(y1, -1)
        for a, b, c, e in zip(x1, y1, x2, y2):
            if b == e:
                continue
            up = (b <= ys) & (e > ys)
            down = (e <= ys) & (b > ys)
            xi = a + (ys - b) * (c - a) / (e - b)
            left = xs < xi
            wind += (up & left).astype(np.int32) - (down & left).astype(np.int32)
    return wind != 0


def run_case(yaw, pitch):
    verts, faces = grooved_cylinder()
    plan = plan_for(verts, faces)
    xy, front = view(verts, faces, yaw, pitch)
    truth = raster([xy[p] for p, f in zip(faces, front) if f])
    edges = merge.silhouette_edges(plan, front, xy) if _takes_xy() else merge.silhouette_edges(plan, front)
    pieces = merge.walk_pieces(plan, edges, xy, front)
    if not pieces:
        return truth.sum(), truth.sum(), 0, 0
    polys = []
    for _ids, vs, ccw in pieces:
        p = xy[vs]
        a = 0.5 * float(np.dot(p[:, 0], np.roll(p[:, 1], -1)) - np.dot(p[:, 1], np.roll(p[:, 0], -1)))
        polys.append(p if (a > 0) == bool(ccw) else p[::-1])
    fill = raster(polys)
    gap = int((truth & ~fill).sum())
    spill = int((fill & ~truth).sum())
    return int(truth.sum()), gap, spill, len(pieces)


def _takes_xy():
    import inspect
    return len(inspect.signature(merge.silhouette_edges).parameters) >= 3


# The allowance covers pixel-centre rounding along the outline only; the gaps this
# exists for were 65 to 1,043 px.
cases = [(0.45, 0.35), (0.7, 0.25), (1.0, 0.3), (1.2, 0.15), (0.35, 0.55)]
for yaw, pitch in cases:
    area, gap, spill, n = run_case(yaw, pitch)
    edge_allow = 0.02 * area ** 0.5 * 4
    check(f"yaw {yaw} pitch {pitch}: the fill covers the part", gap <= edge_allow,
          f"truth {area}px, uncovered {gap}px, spill {spill}px, {n} piece(s)")
    check(f"yaw {yaw} pitch {pitch}: the fill stays on the part", spill <= edge_allow,
          f"spill {spill}px")

print(f"\n{len(fails)} failure(s)")
raise SystemExit(1 if fails else 0)
