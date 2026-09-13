# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""Check the *segments* of merged outlines and chains, not only their points.

`check_geometry.py` proves every drawn point lies on a projected edge of its own
object. A ring can satisfy that and still be wrong: join two correct points in
the wrong order and the segment between them cuts straight across the part,
which reads as a triangle stretched over it. So grade each segment by points
along it -- a quarter, half and three quarters -- against the same projected
edges. A segment that follows the silhouette stays within tolerance all along;
one that cuts across does not. Rings close (`sp` gives each ring's length);
chains close only when flagged.

    blender -b scene.blend --python scripts/check_outline_segments.py -- <bake.preview.html|.svg>
"""

from __future__ import annotations

import os
import sys

import bpy
import numpy as np

_here = os.path.dirname(os.path.abspath(__file__))
_ns = {"__name__": "check_geometry"}
exec(open(os.path.join(_here, "check_geometry.py"), encoding="utf-8").read(), _ns)
load, _decode, _sample = _ns["load"], _ns["_decode"], _ns["_sample"]
_projected, _seg_dist, _bake = _ns["_projected"], _ns["_seg_dist"], _ns["_bake"]

FRACTIONS = np.array([0.25, 0.5, 0.75])


def _rings(e, n):
    """Index ranges of each closed ring of a merged fill, or the open chain."""
    if e["id"].endswith("/m"):
        out, s = [], 0
        for size in (e.get("sp") or [n]):
            out.append((s, s + size, True))
            s += size
        return out
    return [(0, n, bool(e.get("cl") or e.get("closed")))]


def run(path, verbose=True, limit=15):
    doc = load(path)
    w, h, dur = doc["w"], doc["h"], doc["dur"]
    samples, fps, groups = doc["samples"], doc["fps"], doc["groups"]
    allow = float(doc.get("meta", {}).get("tol_px", 1.0)) * 1.25

    by_obj = {}
    for e in doc["els"]:
        tail = (e.get("id") or "").rsplit("/", 1)[-1]
        if not (tail == "m" or tail[:1] == "c"):
            continue
        by_obj.setdefault(groups[e["g"]], []).append({
            "id": e["id"], "e": e, "n": e["n"],
            "geo": _decode(e["G"], e["n"] * 2, dur, samples),
            "op": _decode(e["O"], 1, dur, samples) if e.get("O") else None})

    scene = bpy.context.scene
    cam = scene.camera
    step = (dur * fps / (samples - 1)) if samples > 1 else 1.0
    if abs(step - round(step)) < 1e-4:
        step = float(round(step))
    frames = [scene.frame_start + i * step for i in range(samples)]

    bad, checked = [], 0
    for f in frames:
        whole = int(f // 1)
        scene.frame_set(whole, subframe=float(f) - whole)
        t = (float(f) - scene.frame_start) / fps
        dg = bpy.context.evaluated_depsgraph_get()
        view, mvp = _bake.camera_matrices(scene, cam, dg, w, h)
        for name, items in by_obj.items():
            obj = bpy.data.objects.get(name)
            if obj is None or obj.type != "MESH":
                continue
            px, edges, *_ = _projected(obj, dg, view, mvp, w, h)
            if not len(edges):
                continue
            ea, eb = px[edges[:, 0]], px[edges[:, 1]]
            for it in items:
                if it["op"] is not None:
                    a = _sample(*it["op"], t)
                    if a is None or a[0] <= 1.0 / 255.0:
                        continue
                got = _sample(*it["geo"], t)
                if got is None:
                    continue
                p = np.column_stack([np.asarray(got[0::2]), np.asarray(got[1::2])])
                for s0, s1, closed in _rings(it["e"], it["n"]):
                    ring = p[s0:s1]
                    if len(ring) < 2:
                        continue
                    a_ = ring if closed else ring[:-1]
                    b_ = np.roll(ring, -1, axis=0) if closed else ring[1:]
                    q = (a_[:, None, :] + (b_ - a_)[:, None, :] * FRACTIONS[None, :, None])
                    d = _seg_dist(q.reshape(-1, 2), ea, eb).reshape(len(a_), -1).max(axis=1)
                    checked += len(d)
                    k = int(d.argmax())
                    if d[k] > allow:
                        bad.append({"d": float(d[k]), "id": it["id"], "frame": int(f),
                                    "segment": int(s0 + k),
                                    "from": [round(float(v), 1) for v in a_[k]],
                                    "to": [round(float(v), 1) for v in b_[k]],
                                    "length": round(float(np.hypot(*(b_[k] - a_[k]))), 1)})
    bad.sort(key=lambda r: -r["d"])
    if verbose:
        print(f"[segments] {checked} segments over {len(frames)} frames; "
              f"offending (element, frame) pairs: {len(bad)}")
        worst = {}
        for r in bad:
            worst.setdefault(r["id"], r)
        for r in list(worst.values())[:limit]:
            print(f"    {r['d']:8.2f} px  {r['id']:18s} frame {r['frame']:3d}  "
                  f"segment {r['segment']} {r['from']} -> {r['to']} ({r['length']} px)")
        counts = {}
        for r in bad:
            counts[r["id"]] = counts.get(r["id"], 0) + 1
        print("[segments] frames offending per element:",
              dict(sorted(counts.items(), key=lambda kv: -kv[1])[:limit]))
    return bad


if __name__ == "__main__":
    run(sys.argv[-1])
