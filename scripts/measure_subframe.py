"""How far is what the player draws from what the object really looks like --
at times between the keyframes, which is all that slow playback shows.

Every metric so far has been a proxy: area dips, slot displacement, winding.
This is the thing itself. Blender can evaluate the scene at a subframe, so the
true silhouette is available at the same instants the player is interpolating
through, and the two can simply be compared.
"""
import json
import os
import re
import numpy as np
import bpy
try:
    from bl_ext.user_default.vecbake import bake as bake_mod, merge as merge_mod, \
        outline as outline_mod
except ImportError:
    from vecbake import bake as bake_mod, merge as merge_mod, outline as outline_mod

TAG = os.environ.get("T_TAG", "r18-windonly")
STEPS = int(os.environ.get("T_STEPS", "4"))
path = rf"C:\Program Files (x86)\Steam\steamapps\common\Blender\vecbake\out\{TAG}.lerpa_cam.svg"
text = open(path, encoding="utf-8").read()
doc = json.loads(re.search(r"<!\[CDATA\[([\s\S]*?)\]\]>", text).group(1))
fps, dur, samples = doc["fps"], doc["dur"], doc["samples"]


def decode(enc, comps):
    if enc is None:
        return None, None
    if "c" in enc:
        v = enc["c"]
        return [0.0], [v if isinstance(v, list) else [v]]
    q, d = enc["q"], enc["d"]
    rows, acc = [], None
    for k in range(len(d) // comps):
        chunk = d[k*comps:(k+1)*comps]
        acc = list(chunk) if acc is None else [a+b for a, b in zip(acc, chunk)]
        rows.append([c / q for c in acc])
    if "t" in enc:
        return list(enc["t"]), rows
    n = len(rows)
    return ([dur*i/(n-1) for i in range(n)] if n > 1 else [0.0]), rows


def sample(times, rows, t):
    if times is None: return None
    if len(times) == 1 or t <= times[0]: return rows[0]
    if t >= times[-1]: return rows[-1]
    lo = 0
    for i in range(1, len(times)):
        if times[i] >= t:
            lo = i-1; break
    hi = lo+1
    span = times[hi]-times[lo] or 1e-9
    u = (t-times[lo])/span
    return [a+(b-a)*u for a, b in zip(rows[lo], rows[hi])]


tracks = {}
for e in doc["els"]:
    eid = e.get("id") or ""
    if eid.endswith("/m"):
        tracks[eid.split("/")[0]] = (decode(e.get("G"), 2*e["n"]), decode(e.get("O"), 1), e["n"])

sc = bpy.context.scene
st = sc.vecbake.to_bake_settings(bpy.context)
W, H = sc.render.resolution_x, sc.render.resolution_y


def true_ring(obj):
    dg = bpy.context.evaluated_depsgraph_get()
    view, mvp = bake_mod.camera_matrices(sc, sc.camera, dg, W, H)
    cam = np.array(sc.camera.evaluated_get(dg).matrix_world.translation[:], float)
    ev = obj.evaluated_get(dg); me = ev.to_mesh()
    try:
        plan = bake_mod.ObjectPlan(obj, me, st)
        world = np.array([(ev.matrix_world @ v.co)[:] for v in me.vertices])
        nrm = np.array([(ev.matrix_world.to_3x3() @ f.normal)[:] for f in me.polygons])
        ctr = np.array([(ev.matrix_world @ f.center)[:] for f in me.polygons])
        xy, depth, in_front = bake_mod.project(world, view, mvp, W, H)
        tf = ctr - cam
        cosf = np.einsum("ij,ij->i", nrm, tf)/np.maximum(np.linalg.norm(tf, axis=1), 1e-12)
        lv, starts = plan.loop_verts, plan.loop_start
        totals = plan.loop_total.astype(np.float64)
        okf = np.add.reduceat(in_front[lv].astype(np.float64), starts) == totals
        facing = (cosf < 0.0) & okf
        loop = merge_mod.walk_loop(plan, merge_mod.silhouette_edges(plan, facing)) \
            if facing.any() else None
        return None if loop is None else outline_mod.polyline(loop, xy)
    finally:
        ev.to_mesh_clear()


def hausdorff(a, b):
    """Worst distance from any point of a to the closed polyline b."""
    worst = 0.0
    bb = np.roll(b, -1, axis=0)
    for q in a:
        ab = bb - b
        den = np.einsum("ij,ij->i", ab, ab)
        t = np.clip(np.einsum("ij,ij->i", q - b, ab) / np.where(den > 0, den, 1e-12), 0, 1)
        d = np.linalg.norm(q - (b + ab * t[:, None]), axis=1).min()
        worst = max(worst, float(d))
    return worst


names = os.environ.get("T_NAMES", "part_042").split(",")
print(f"[truth] {TAG}, {STEPS} steps per frame", flush=True)
for name in names:
    if name not in tracks:
        print(f"[truth] {name}: not merged", flush=True); continue
    (gt, gr), (ot, orr), npts = tracks[name]
    obj = bpy.data.objects[name]
    at_key = at_mid = 0.0
    worst = (0.0, 0, 0.0)
    for f in range(sc.frame_start, sc.frame_end):
        for k in range(STEPS):
            u = k / STEPS
            sc.frame_set(f, subframe=u)
            t = ((f - sc.frame_start) + u) / fps
            if (sample(ot, orr, t) or [1])[0] <= 0.5:
                continue
            ring = true_ring(obj)
            if ring is None or len(ring) < 3:
                continue
            p = sample(gt, gr, t)
            drawn = np.array(p, dtype=np.float64).reshape(-1, 2)
            d = max(hausdorff(drawn, ring), hausdorff(ring, drawn))
            if k == 0:
                at_key = max(at_key, d)
            else:
                at_mid = max(at_mid, d)
                if d > worst[0]:
                    worst = (d, f, u)
    print(f"[truth] {name:<12} worst error at a keyframe {at_key:7.2f}px   "
          f"between keyframes {at_mid:7.2f}px   (at frame {worst[1]}+{worst[2]:.2f})",
          flush=True)
