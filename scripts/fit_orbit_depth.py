# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""Per-node camera distance for an orbit bake, as three numbers each.

A viewer that zooms into one part of an orbiting model has a problem the bake
cannot solve for it: the part's distance to the camera changes as the orbit
goes round, so its apparent size changes with it. On the Voyager hero a part
out on the boom runs 14 to 20 units away and is drawn 1.45x smaller at the far
end; the magnetometer boom swings 2.46x. A part on the spin axis does not move
at all, which is why the model as a whole looks stable and its parts do not.

The bake ships no distances -- depth is an integer paint rank, which is all the
painter's algorithm needs -- so this writes them alongside the zoom tree.

One camera orbiting a fixed model (or a fixed camera and an orbiting model --
only the relative motion matters) puts every point of it at

    d^2(theta) = A + B cos(theta) + C sin(theta)

*exactly*, so three numbers per node is not an approximation and the runtime
can evaluate them at the exact instant it is drawing rather than sampling and
interpolating. Measured on all 328 nodes of the Voyager hero, the worst
residual is 0.000%.

Two things that have to be right or the fit quietly degrades into a curve fit:

- **Read the camera inside the frame loop.** If the camera is the thing that
  orbits and you read its position once, every node fits a constant and reports
  a swing of exactly 1.00x with a perfect residual, which looks like success.
- **Use the period, not the duration.** A clip of N frames that closes on frame
  N+1 has a period of N frames, not N-1. Getting this wrong stretched every
  angle by 1.7% here and put 2.99% of residual on the worst node.

    blender -b model.blend --python scripts/fit_orbit_depth.py -- tree.json
"""

from __future__ import annotations

import json
import math
import sys

import bpy
import numpy as np
from mathutils import Vector


def node_parts(tree):
    """Every addressable node in the tree, as id -> the parts it draws."""
    out = {}
    for seg in tree.get("segments", []):
        out[seg["id"]] = list(seg["parts"])
        for p in seg["parts"]:
            out[p] = [p]
    for loose in tree.get("loose", []):
        out[loose["part"]] = [loose["part"]]
    out[""] = [p for seg in tree.get("segments", []) for p in seg["parts"]]
    return out


def world_box(obj, dg):
    ev = obj.evaluated_get(dg)
    pts = [obj.matrix_world @ Vector(v) for v in ev.bound_box]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return lo, hi


def run(tree_path, camera=None, samples=24, verbose=True):
    tree = json.load(open(tree_path, encoding="utf-8"))
    scene = bpy.context.scene
    cam = camera or scene.camera
    if cam is None:
        raise RuntimeError("no camera to measure from")

    nodes = node_parts(tree)
    every = {n for parts in nodes.values() for n in parts}
    # The clip's frames, and the period, which is one frame longer: the pose at
    # frame_end + 1 is the pose at frame_start again.
    first, last = scene.frame_start, scene.frame_end
    period = float(last - first + 1)

    thetas, campos = [], []
    centres = {k: [] for k in nodes}
    for i in range(samples):
        f = first + i * period / samples
        whole = int(f // 1)
        scene.frame_set(whole, subframe=float(f) - whole)
        dg = bpy.context.evaluated_depsgraph_get()
        thetas.append(2 * math.pi * (f - first) / period)
        campos.append(cam.matrix_world.translation.copy())   # inside the loop
        box = {}
        for name in every:
            obj = bpy.data.objects.get(name)
            if obj is not None and obj.type == "MESH":
                box[name] = world_box(obj, dg)
        for k, parts in nodes.items():
            got = [box[n] for n in parts if n in box]
            if not got:
                centres[k].append(None)
                continue
            lo = Vector((min(g[0].x for g in got), min(g[0].y for g in got),
                         min(g[0].z for g in got)))
            hi = Vector((max(g[1].x for g in got), max(g[1].y for g in got),
                         max(g[1].z for g in got)))
            centres[k].append((lo + hi) * 0.5)

    th = np.array(thetas)
    M = np.column_stack([np.ones_like(th), np.cos(th), np.sin(th)])
    fits, worst, worst_at = {}, 0.0, None
    for k, cs in centres.items():
        good = [(i, c) for i, c in enumerate(cs) if c is not None]
        if len(good) < 4:
            continue
        idx = [i for i, _ in good]
        d2 = np.array([(campos[i] - c).length_squared for i, c in good])
        coef, *_ = np.linalg.lstsq(M[idx], d2, rcond=None)
        pred = np.sqrt(np.maximum(M[idx] @ coef, 1e-9))
        rel = float(np.max(np.abs(pred - np.sqrt(d2)) / np.sqrt(d2)))
        fits[k] = [round(float(v), 5) for v in coef]
        if rel > worst:
            worst, worst_at = rel, k

    tree["depth"] = fits
    json.dump(tree, open(tree_path, "w", encoding="utf-8"), indent=1)
    if verbose:
        swing = {}
        for k, (A, B, C) in fits.items():
            amp = math.hypot(B, C)
            lo = math.sqrt(max(A - amp, 1e-9))
            swing[k] = math.sqrt(A + amp) / lo
        hot = sorted(swing.items(), key=lambda kv: -kv[1])[:5]
        print(f"[depth] fitted {len(fits)} nodes over {samples} samples, "
              f"period {period:g} frames")
        print(f"[depth] worst residual {worst * 100:.3f}% at {worst_at or '-'}")
        for k, v in hot:
            print(f"[depth]   {k or '(root)':<18} apparent size swings {v:.2f}x")
    return {"fits": fits, "worst": worst, "worst_at": worst_at}


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[-1:]
    run(args[0])
