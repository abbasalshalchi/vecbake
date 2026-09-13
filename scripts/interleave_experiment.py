"""Experimental clip-wide split selector; kept outside the shipping add-on.

Evidence is exhaustive at output pixel centres, not a grid fitted to each pair.
Depth at each covered pixel is camera-correct barycentric interpolation and
overlapping front triangles reduce to the nearest surface. No off-surface
nearest-vertex fallback can manufacture a contradiction. Counts on both sides
are retained so that the any-frame rule can be judged against its pixel cost.

The probe collects during sampling. Blender's tessellation supplies the actual
faces, including concave n-gons. Selection waits until outline feasibility is
settled: a refused merge is several independently ranked faces, not one part.
It then regenerates the clip with one fixed representation before ordering.
This is a representation experiment: it never changes a mesh or order.resolve.
"""

from collections import defaultdict
import math
import time

import numpy as np


def raster(tris, width, height, order, is_ortho):
    """Front depth at every covered output pixel centre, in a cropped array."""
    tx, ty, td = tris
    if not len(tx):
        return None
    x0 = max(0, math.ceil(float(tx.min()) - .5))
    x1 = min(width, math.floor(float(tx.max()) - .5) + 1)
    y0 = max(0, math.ceil(float(ty.min()) - .5))
    y1 = min(height, math.floor(float(ty.max()) - .5) + 1)
    if x1 <= x0 or y1 <= y0:
        return None
    depth = np.full((y1 - y0, x1 - x0), np.inf)
    # Each triangle covers its own box. Dispatching a whole object's box to
    # _surface_read would test every pixel against every unrelated triangle.
    for k in range(len(tx)):
        ax0 = max(x0, math.ceil(float(tx[k].min()) - .5))
        ax1 = min(x1, math.floor(float(tx[k].max()) - .5) + 1)
        ay0 = max(y0, math.ceil(float(ty[k].min()) - .5))
        ay1 = min(y1, math.floor(float(ty[k].max()) - .5) + 1)
        if ax1 <= ax0 or ay1 <= ay0:
            continue
        qx, qy = np.meshgrid(np.arange(ax0, ax1) + .5,
                             np.arange(ay0, ay1) + .5)
        item = {"tris": (tx[k:k+1], ty[k:k+1], td[k:k+1])}
        d = order._surface_read(item, qx.ravel(), qy.ravel(), is_ortho)
        d = np.where(np.isfinite(d), d, np.inf).reshape(qx.shape)
        target = depth[ay0-y0:ay1-y0, ax0-x0:ax1-x0]
        np.minimum(target, d, out=target)
    return (x0, x1, y0, y1), depth


def frame_evidence(items, width, height, order, is_ortho=False):
    """Return measured two-sided overlap for every pair with a merged member.

    `items` carry key, owner, merged, tris. Nonmerged faces remain separate
    elements: opposite signs against two independently ranked faces are not a
    contradiction against one element. No temporal aggregation of signs either:
    both directions must exist in the SAME frame.
    """
    if not items:
        return []
    lo = min(float(it["tris"][2].min()) for it in items)
    hi = max(float(it["tris"][2].max()) for it in items)
    tol = max(1e-4, (hi - lo) * 1e-4)  # same depth units/definition as resolve
    boxes = np.array([(t[0].min(), t[0].max(), t[1].min(), t[1].max())
                      for t in (it["tris"] for it in items)])
    ranges = np.array([(it["tris"][2].min(), it["tris"][2].max()) for it in items])
    cache = {}
    result = []
    for i, body in enumerate(items):
        if not body["merged"]:
            continue
        overlap = ((boxes[:, 0] < boxes[i, 1]) & (boxes[:, 1] > boxes[i, 0])
                   & (boxes[:, 2] < boxes[i, 3]) & (boxes[:, 3] > boxes[i, 2])
                   & (ranges[:, 0] <= ranges[i, 1]) & (ranges[:, 1] >= ranges[i, 0]))
        for j in np.flatnonzero(overlap):
            part = items[j]
            if part["owner"] == body["owner"] or (part["merged"] and j <= i):
                continue
            for n in (i, int(j)):
                if n not in cache:
                    cache[n] = raster(items[n]["tris"], width, height, order, is_ortho)
            if cache[i] is None or cache[j] is None:
                continue
            a, ad = cache[i]; b, bd = cache[j]
            x0, x1 = max(a[0], b[0]), min(a[1], b[1])
            y0, y1 = max(a[2], b[2]), min(a[3], b[3])
            if x1 <= x0 or y1 <= y0:
                continue
            da = ad[y0-a[2]:y1-a[2], x0-a[0]:x1-a[0]]
            db = bd[y0-b[2]:y1-b[2], x0-b[0]:x1-b[0]]
            valid = np.isfinite(da) & np.isfinite(db)
            with np.errstate(invalid="ignore"):
                delta = da - db
            front = valid & (delta < -tol)
            back = valid & (delta > tol)
            nf, nb = int(front.sum()), int(back.sum())
            if not nf or not nb:
                continue
            def witness(mask):
                y, x = np.argwhere(mask)[0]
                return [float(x+x0+.5), float(y+y0+.5), float(da[y,x]), float(db[y,x])]
            result.append(dict(body=body["owner"], part=part["key"],
                               part_owner=part["owner"], part_merged=part["merged"],
                               front_pixels=nf, back_pixels=nb, tolerance=tol,
                               front=witness(front), back=witness(back)))
    return result


def selected(evidence):
    out = set()
    for row in evidence:
        out.add(row["body"])
        if row["part_merged"]:
            out.add(row["part_owner"])
    return sorted(out)


class SamplingProbe:
    """Scoped instrumentation; callbacks replace the old proxy for one bake."""
    def __init__(self, bake, order, width, height, report=None):
        self.bake, self.order = bake, order
        self.width, self.height, self.report = width, height, report
        self.plans, self.topology = {}, {}
        self.rows, self.picks = [], []
        self.cpu_seconds = 0.0
        self.calls = 0

    def sample(self, original, plan, me, *args, **kwargs):
        self.plans[plan.name] = plan
        if plan.name not in self.topology:
            me.calc_loop_triangles()
            tv = np.empty(len(me.loop_triangles)*3, dtype=np.int32)
            tf = np.empty(len(me.loop_triangles), dtype=np.int32)
            me.loop_triangles.foreach_get("vertices", tv)
            me.loop_triangles.foreach_get("polygon_index", tf)
            self.topology[plan.name] = tv.reshape(-1,3), tf
        return original(plan, me, *args, **kwargs)

    def surface(self, plan, facing, xy, depth):
        if not plan.fills:
            return None
        tv, tf = self.topology[plan.name]
        keep = facing[tf]
        tv, tf = tv[keep], tf[keep]
        if not len(tv):
            return None
        tx, ty, td = xy[tv,0], xy[tv,1], depth[tv]
        valid = np.isfinite(tx).all(axis=1) & np.isfinite(ty).all(axis=1) & np.isfinite(td).all(axis=1)
        if not valid.any():
            return None
        return dict(tris=(tx[valid],ty[valid],td[valid]), faces=tf[valid])

    def choose(self, extent, unused_pct, frame_ortho):
        started = time.process_time()
        self.calls += 1
        candidates = {n for n,p in self.plans.items() if p.merge_fill and p.outline_n
                      and p.outline_n >= 3}
        for fi in range(len(frame_ortho)):
            items = []
            for name in sorted(extent):
                row = extent[name][fi]
                if row is None:
                    continue
                if name in candidates:
                    items.append(dict(key=name+"/m", owner=name, merged=True, tris=row["tris"]))
                else:
                    for face in np.unique(row["faces"]):
                        hit = row["faces"] == face
                        items.append(dict(key=f"{name}/f{face}", owner=name, merged=False,
                                          tris=tuple(t[hit] for t in row["tris"])))
            rows = frame_evidence(items, self.width, self.height, self.order, frame_ortho[fi])
            self.rows.extend(dict(frame=fi, **row) for row in rows)
            if self.report:
                self.report(fi, selected(rows), len(rows))
        self.picks = selected(self.rows)
        self.cpu_seconds += time.process_time()-started
        return self.picks
