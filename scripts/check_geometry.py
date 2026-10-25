# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""Check every drawn point of a finished bake against the geometry it came from.

`check_occlusion.py` grades what is drawn *in front of* what. This grades where
things are drawn at all, which is the failure that looks like strokes flying
around the model when it moves. It is exhaustive rather than sampled -- a stray
is a few dozen pixels in a few hundred thousand, so sampling misses it, which is
the same reason the occlusion audit restricts itself to overlaps.

Correspondence is free, and that is what makes this cheap and exact:

- `<obj>/f<n>` is polygon n's loop vertices, in order
- `<obj>/e<n>` is edge n's two vertices, in order

so those are compared point for point against the true projection of those same
vertices -- no matching, no nearest neighbour, and a stroke joining the wrong
vertices cannot pass. That covers ~95% of a typical bake's elements.

Chains and merged outlines are a path of edges and a resampled silhouette ring,
so neither is a vertex list to index into. What is still true of both is that
every point lies **on a projected edge of its own object**, so they are graded
by point-to-segment distance against the object's projected edges.

Both are checked at the frames the bake sampled, where `tol_px` is a real bound
on the interpolation error. The allowance is a quarter over that -- see `run`
for why it is empirical, and why being loose costs nothing here.

    exec(open(r'.../scripts/check_geometry.py').read())
    report = run(r'.../out/thing.lerpa_cam.svg')
    report = run(path, frames=[1, 24, 48])

Gated on opacity: an element that is not drawn at a frame has no pose worth
grading, and grading it anyway reports every correctly-hidden element in the
file as an offender. On the Voyager hero that gate is the difference between
zero offenders and 57,402 of them.
"""

from __future__ import annotations

import json

import bpy
import numpy as np

try:                                   # installed as an extension
    from bl_ext.user_default.vecbake import bake as _bake
except ImportError:                    # loaded straight from the add-on folder
    from vecbake import bake as _bake

# What the baker draws. An install from before curves were baked has no
# `drawable`, and drew meshes only, so that is the right answer for its files.
_drawable = getattr(_bake, "drawable", lambda obj: obj is not None and obj.type == "MESH")


def load(path):
    """The scene payload, out of an .svg, a .preview.html or a .json."""
    text = open(path, encoding="utf-8", errors="replace").read()
    if "<![CDATA[" in text:                       # animated SVG
        i = text.index("<![CDATA[") + 9
        return json.loads(text[i:text.index("]]>", i)])
    marker = "const doc = "
    if marker in text:                            # preview page
        i = text.index(marker) + len(marker)
        return json.JSONDecoder().raw_decode(text[i:])[0]
    return json.loads(text)


def _decode(enc, comps, dur, samples):
    """(times, rows) from one encoded track; rows are lists of `comps` floats."""
    if enc is None:
        return None, None
    if "c" in enc:
        v = enc["c"]
        return [0.0], [v if isinstance(v, list) else [v]]
    q, d = enc["q"], enc["d"]
    rows, acc = [], None
    for k in range(len(d) // comps):
        chunk = d[k * comps:(k + 1) * comps]
        acc = list(chunk) if acc is None else [a + b for a, b in zip(acc, chunk)]
        rows.append([c / q for c in acc])
    if "t" in enc:
        return list(enc["t"]), rows
    n = len(rows)
    # Dense tracks carry no times: they are keyed against the even sample grid.
    return ([dur * i / (samples - 1) for i in range(n)] if n > 1 else [0.0]), rows


def _sample(times, rows, t):
    if times is None:
        return None
    if len(times) == 1 or t <= times[0]:
        return rows[0]
    if t >= times[-1]:
        return rows[-1]
    lo = 0
    for i in range(1, len(times)):
        if times[i] >= t:
            lo = i - 1
            break
    span = times[lo + 1] - times[lo] or 1e-9
    u = (t - times[lo]) / span
    return [a + (b - a) * u for a, b in zip(rows[lo], rows[lo + 1])]


def _seg_dist(p, a, b):
    """Distance from each point in p (n,2) to the nearest segment a->b (m,2)."""
    d = b - a
    ll = (d * d).sum(1)
    ll[ll == 0] = 1e-12
    u = ((p[:, None, 0] - a[None, :, 0]) * d[None, :, 0]
         + (p[:, None, 1] - a[None, :, 1]) * d[None, :, 1]) / ll[None, :]
    np.clip(u, 0.0, 1.0, out=u)
    return np.hypot(p[:, None, 0] - (a[None, :, 0] + u * d[None, :, 0]),
                    p[:, None, 1] - (a[None, :, 1] + u * d[None, :, 1])).min(1)


def _projected(obj, dg, view, mvp, w, h):
    """Projected points plus the index arrays the element ids refer to."""
    # to_mesh, not `.data`: a curve's evaluated data is the Curve, and its
    # drawn geometry -- what the element ids index -- is the mesh it makes.
    ev = obj.evaluated_get(dg)
    me = ev.to_mesh()
    try:
        return _arrays(obj, me, view, mvp, w, h)
    finally:
        ev.to_mesh_clear()


def _arrays(obj, me, view, mvp, w, h):
    nv = len(me.vertices)
    co = np.empty(nv * 3, dtype=np.float64)
    me.vertices.foreach_get("co", co)
    world = np.asarray(obj.matrix_world, dtype=np.float64) @ np.vstack(
        [co.reshape(nv, 3).T, np.ones(nv)])
    px, _, _ = _bake.project(world[:3].T.copy(), view, mvp, w, h)
    edges = np.empty(len(me.edges) * 2, dtype=np.int64)
    me.edges.foreach_get("vertices", edges)
    ls = np.empty(len(me.polygons), dtype=np.int64)
    lt = np.empty(len(me.polygons), dtype=np.int64)
    me.polygons.foreach_get("loop_start", ls)
    me.polygons.foreach_get("loop_total", lt)
    lv = np.empty(len(me.loops), dtype=np.int64)
    me.loops.foreach_get("vertex_index", lv)
    return px, edges.reshape(-1, 2), ls, lt, lv


def run(path, frames=None, tol=None, camera=None, verbose=True):
    doc = load(path)
    w, h, dur = doc["w"], doc["h"], doc["dur"]
    samples, fps, groups = doc["samples"], doc["fps"], doc["groups"]
    tol_px = float(doc.get("meta", {}).get("tol_px", 1.0)) if tol is None else tol
    # Reduction bounds the deviation at `tol_px` at the sample times, and
    # coordinate rounding adds up to about sqrt(2)/2 of the quantum on top --
    # 0.09 px on a 1 px bake. That predicts 1.09 and the worst actually seen on
    # the Voyager hero is 1.10, so the last hundredth is not accounted for and
    # the slack here is empirical rather than derived. A quarter of the
    # tolerance covers it with room to spare, and is still two orders of
    # magnitude under anything that reads as a stray, which is tens of pixels.
    allow = tol_px * 1.25

    by_obj = {}
    for e in doc["els"]:
        eid = e.get("id") or ""
        tail = eid.rsplit("/", 1)[-1]
        if tail[:1] == "f" and tail[1:].isdigit():
            kind, idx = "f", int(tail[1:])
        elif tail[:1] == "e" and tail[1:].isdigit():
            kind, idx = "e", int(tail[1:])
        elif tail == "m" or tail[:1] == "c":
            kind, idx = "on-edge", -1
        else:
            continue
        by_obj.setdefault(groups[e["g"]], []).append({
            "id": eid, "kind": kind, "idx": idx, "n": e["n"],
            "geo": _decode(e["G"], e["n"] * 2, dur, samples),
            "op": _decode(e["O"], 1, dur, samples) if e.get("O") else None,
        })

    scene = bpy.context.scene
    cam = camera or scene.camera
    if cam is None:
        raise RuntimeError("no camera to project through")
    # The sampled frames, which are not one per frame when Step is set: the
    # duration spans samples-1 steps, so the step in frames falls out of it.
    # A Step that does not divide the frame range leaves a short final gap --
    # 1..60 by 8 ends 57, 60 -- and those samples are not evenly spaced at all,
    # so pass `frames` yourself for one of those. Everything else is even.
    step = (dur * fps / (samples - 1)) if samples > 1 else 1.0
    # `dur` is stored to six decimals, so a step that is really 1 comes back as
    # 0.99999986 and every derived frame lands a hair before the frame the bake
    # sampled. Snap it: a Step is a whole number of frames or the sample times
    # were not even in the first place.
    if abs(step - round(step)) < 1e-4:
        step = float(round(step))
    if frames is None:
        frames = [scene.frame_start + i * step for i in range(samples)]

    worst, worst_at, bad = 0.0, None, []
    checked = skipped = 0
    for f in frames:
        # Step can put a sample on a fraction of a frame, and the time has to
        # come from the frame rather than the loop index -- an explicit `frames`
        # list is the whole point of the argument.
        whole = int(f // 1)
        scene.frame_set(whole, subframe=float(f) - whole)
        t = (float(f) - scene.frame_start) / fps
        dg = bpy.context.evaluated_depsgraph_get()
        view, mvp = _bake.camera_matrices(scene, cam, dg, w, h)
        for name, items in by_obj.items():
            obj = bpy.data.objects.get(name)
            if not _drawable(obj):
                skipped += len(items)
                continue
            px, edges, ls, lt, lv = _projected(obj, dg, view, mvp, w, h)
            for it in items:
                if it["op"] is not None:
                    a = _sample(*it["op"], t)
                    if a is None or a[0] <= 1.0 / 255.0:
                        continue      # not drawn: its pose is nobody's business
                got = _sample(*it["geo"], t)
                if got is None:
                    skipped += 1
                    continue
                p = np.column_stack([np.asarray(got[0::2]), np.asarray(got[1::2])])
                if it["kind"] == "f":
                    if it["idx"] >= len(ls) or int(lt[it["idx"]]) != it["n"]:
                        skipped += 1    # thinned outline: not that loop any more
                        continue
                    s = int(ls[it["idx"]])
                    truth = px[lv[s:s + it["n"]]]
                    d = float(np.hypot(p[:, 0] - truth[:, 0],
                                       p[:, 1] - truth[:, 1]).max())
                elif it["kind"] == "e":
                    if it["idx"] >= len(edges) or it["n"] != 2:
                        skipped += 1
                        continue
                    truth = px[edges[it["idx"]]]
                    d = float(np.hypot(p[:, 0] - truth[:, 0],
                                       p[:, 1] - truth[:, 1]).max())
                else:
                    if not len(edges):
                        skipped += 1
                        continue
                    d = float(_seg_dist(p, px[edges[:, 0]], px[edges[:, 1]]).max())
                checked += 1
                if d > worst:
                    worst, worst_at = d, f"{it['id']} frame {f}"
                if d > allow:
                    bad.append({"d": d, "id": it["id"], "frame": int(f),
                                "kind": it["kind"]})
    if not checked and by_obj == {} and doc["els"]:
        # A bake without element ids gives this nothing to match, and zero
        # offenders over zero point-sets read as a clean pass: celltower.blend
        # saves include_ids off, and graded clean that way on 25 October.
        raise RuntimeError(f"{path}: no element carries an id, so nothing can be "
                           "checked -- bake with include_ids on")
    bad.sort(key=lambda r: -r["d"])
    report = {"checked": checked, "skipped": skipped, "offenders": len(bad),
              "worst": worst, "worst_at": worst_at, "tol_px": tol_px,
              "allow": allow, "frames": len(frames), "bad": bad}
    if verbose:
        print(f"[geometry] {checked} point-sets over {len(frames)} frames, "
              f"{skipped} skipped")
        print(f"[geometry] worst {worst:.4f} px at {worst_at} "
              f"(tolerance {tol_px}, allowing {allow:.4f})")
        print(f"[geometry] offenders: {len(bad)}")
        for r in bad[:10]:
            print(f"    {r['d']:9.2f} px  {r['id']}  frame {r['frame']}")
    return report


if __name__ == "__main__":
    import sys

    run(sys.argv[-1])
