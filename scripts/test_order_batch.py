"""Batching changes scheduling, not the surface reads or the paint graph.

    "<blender>/5.2/python/bin/python.exe" scripts/test_order_batch.py
    ... scripts/test_order_batch.py --reference out/surface-batch/order_scalar.py

The old per-pair triangle implementation passes the geometric checks but fails
the call-budget check: a merged body is read again for every fitting on it.
Use --order PATH to demonstrate that regression against a saved old module.
The optional reference also compares the entire resolver's ranks and graph
statistics, including shuffled input order. No ignored fixture is required.

The numerical oracle below is the pre-batching scalar triangle reader. It pins
arithmetic independently of the new reader, and the known scenes pin which
query belongs to which pair. These are controlled geometry and equivalence
checks; they do not replace the real model's ray-cast audit.
"""

import argparse
from collections import Counter
from copy import deepcopy
import importlib.util
import os

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS = []


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check(label, condition, detail=""):
    print(f"{'PASS' if condition else 'FAIL'}  {label}"
          + (f"  {detail}" if detail else ""))
    if not condition:
        FAILS.append(label)


def rectangle(x0, y0, x1, y1):
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=float)


def fill(key, pts, depths, seed=None, triangles=None):
    pts = np.asarray(pts, dtype=float)
    depths = np.broadcast_to(depths, (len(pts),)).astype(float).copy()
    item = dict(key=key, kind=1, pts=pts, depths=depths,
                seed=float(depths.mean()) if seed is None else seed)
    if triangles is not None:
        t = np.asarray(triangles)
        item.update(probe=(pts[:, 0].copy(), pts[:, 1].copy(), depths.copy()),
                    tris=(pts[t, 0], pts[t, 1], depths[t]))
    return item


def ramp_depth(x, is_ortho):
    t = np.asarray(x) / 400.0
    return 10.0 + 20.0 * t if is_ortho else 1.0 / ((1 - t) / 10 + t / 30)


def ramp_scene(is_ortho):
    """The body has four fittings, each known to be in front or behind it."""
    body = fill("body", rectangle(0, 0, 400, 120), [10, 30, 30, 10],
                triangles=[(0, 1, 2), (0, 2, 3)])
    front = fill("front", rectangle(30, 40, 70, 80),
                 ramp_depth(50, is_ortho) - 0.2, seed=100)
    behind = fill("behind", rectangle(180, 40, 220, 80),
                  ramp_depth(200, is_ortho) + 0.2, seed=-100)
    merged = fill("merged_fitting", rectangle(280, 10, 320, 30),
                  ramp_depth(300, is_ortho) - 0.4, seed=100,
                  triangles=[(0, 1, 2), (0, 2, 3)])
    line = dict(key="front_line", kind=0, pts=np.array([[340., 60.], [360., 60.]]),
                depths=np.full(2, ramp_depth(350, is_ortho) - 0.2), seed=100)
    own = dict(key="own_line", kind=0, pts=np.array([[80., 100.], [320., 100.]]),
               depths=ramp_depth([80, 320], is_ortho), seed=100, on_faces=("body",))
    # These overlap on screen but disjoint depth ranges must settle their
    # order even though the deliberately wrong seeds say the opposite.
    back = fill("back", rectangle(0, 0, 400, 120), 100, seed=-200,
                triangles=[(0, 1, 2), (0, 2, 3)])
    isolated = fill("isolated", rectangle(600, 0, 700, 100), [10, 30, 30, 10],
                    triangles=[(0, 1, 2), (0, 2, 3)])
    return [body, front, behind, merged, line, own, back, isolated]


def gap_scene(is_ortho):
    """A bar meets both arms of a U; their shared centre lies in its hole."""
    pts = np.array([[0, 0], [400, 0], [400, 100], [300, 100],
                    [300, 20], [100, 20], [100, 100], [0, 100]], dtype=float)
    body = fill("bracket", pts, ramp_depth(pts[:, 0], is_ortho),
                triangles=[(0, 1, 4), (0, 4, 5), (0, 5, 6), (0, 6, 7),
                           (1, 2, 3), (1, 3, 4)])
    bar = fill("bar", rectangle(0, 50, 400, 70), 14.5)
    left = fill("left", rectangle(30, 40, 70, 80), ramp_depth(50, is_ortho) - .2)
    right = fill("right", rectangle(330, 40, 370, 80), ramp_depth(350, is_ortho) + .2)
    return [body, bar, left, right]


def no_shared_scene():
    # Their boxes overlap, their filled triangles do not, and their depth
    # intervals overlap. No surface request should be generated for this pair.
    return [fill("low", [(0, 0), (100, 0), (0, 100)], [10, 20, 30],
                 triangles=[(0, 1, 2)]),
            fill("high", [(100, 100), (100, 60), (60, 100)], [10, 20, 30],
                 triangles=[(0, 1, 2)])]


def cycle_scene():
    # The scalar orderer's pinned cycle, with the sloping face represented by
    # front triangles. Reading pairs first must preserve edge insertion order
    # as well as arithmetic, since equal-weight edges tie by insertion order.
    face = fill("face", rectangle(0, 0, 300, 300), [9.2, 9.2, 10.9, 10.9],
                triangles=[(0, 1, 2), (0, 2, 3)])
    disk = fill("disk", rectangle(80, 200, 260, 320), [10.7, 10.7, 10.85, 10.85])
    edge = dict(key="edge", kind=0, seed=10.05,
                pts=np.array([[0., 300.], [300., 300.]]),
                depths=np.full(2, 10.9), on_faces=("face",))
    return [face, disk, edge]


def scalar_surface(item, qx, qy, is_ortho):
    """The original per-query triangle reader, retained only as an oracle."""
    tx, ty, td = item["tris"]
    ax, ay = tx[:, 0], ty[:, 0]
    e1x, e1y = tx[:, 1] - ax, ty[:, 1] - ay
    e2x, e2y = tx[:, 2] - ax, ty[:, 2] - ay
    area = e1x * e2y - e1y * e2x
    keep = np.abs(area) > 1e-12
    inv = np.zeros_like(area)
    inv[keep] = 1.0 / area[keep]
    out = np.full(len(qx), np.nan)
    for n, (x, y) in enumerate(zip(qx, qy)):
        cand = np.nonzero(keep & (tx.min(axis=1) <= x) & (x <= tx.max(axis=1))
                          & (ty.min(axis=1) <= y) & (y <= ty.max(axis=1)))[0]
        if not len(cand):
            continue
        vx, vy = x - ax[cand], y - ay[cand]
        w1 = (vx * e2y[cand] - vy * e2x[cand]) * inv[cand]
        w2 = (e1x[cand] * vy - e1y[cand] * vx) * inv[cand]
        w0 = 1.0 - w1 - w2
        inside = (w0 >= -1e-9) & (w1 >= -1e-9) & (w2 >= -1e-9)
        if not inside.any():
            continue
        d = td[cand[inside]]
        w = np.stack([w0[inside], w1[inside], w2[inside]], axis=1)
        values = ((w * d).sum(axis=1) if is_ortho else
                  1.0 / np.maximum((w / np.maximum(d, 1e-9)).sum(axis=1), 1e-12))
        out[n] = values.min()
    return out


def resolve_observed(module, items, is_ortho):
    calls, points, values = Counter(), Counter(), {}
    original = module._surface_read

    def observed(item, qx, qy, ortho):
        calls[item["key"]] += 1
        points[item["key"]] += len(qx)
        actual = original(item, qx, qy, ortho)
        expected = scalar_surface(item, qx, qy, ortho)
        values[item["key"]] = values.get(item["key"], True) and np.allclose(
            actual, expected, rtol=0, atol=1e-11, equal_nan=True)
        return actual

    module._surface_read = observed
    try:
        ranks = module.resolve(deepcopy(items), is_ortho=is_ortho)
        stats = dict(module.LAST_STATS)
    finally:
        module._surface_read = original
    return dict(zip((it["key"] for it in items), ranks)), stats, calls, points, values


def numerical_batch(module, is_ortho):
    rng = np.random.default_rng(20260913)
    # A mosaic plus a nearer copy gives misses, edges and overlapping front
    # faces in one batch. A large query batch exercises bounded allocation.
    tx, ty, td = [], [], []
    for y in range(8):
        for x in range(8):
            pts = rectangle(x * 20, y * 20, x * 20 + 20, y * 20 + 20)
            for t in [(0, 1, 2), (0, 2, 3)]:
                tx.append(pts[list(t), 0])
                ty.append(pts[list(t), 1])
                td.append(rng.uniform(1, 30, 3))
    tx, ty, td = np.array(tx), np.array(ty), np.array(td)
    tx = np.concatenate([tx, tx])
    ty = np.concatenate([ty, ty])
    td = np.concatenate([td, td * .8])
    item = dict(tris=(tx, ty, td))
    q = np.concatenate([rng.uniform(-20, 180, (4097, 2)),
                        np.array([[0, 0], [160, 160], [20, 20], [20, 20 + 1e-10]])])
    expected = scalar_surface(item, q[:, 0], q[:, 1], is_ortho)
    actual = module._surface_read(item, q[:, 0], q[:, 1], is_ortho)
    check(f"{'ortho' if is_ortho else 'perspective'} large read matches scalar oracle",
          np.allclose(actual, expected, rtol=0, atol=1e-11, equal_nan=True),
          f"{len(q)} points x {len(tx)} triangles; "
          f"max error {np.nanmax(np.abs(actual - expected)):.3g}")
    check("the numerical fixture includes both misses and hits",
          np.isnan(actual).any() and np.isfinite(actual).any())
    empty = module._surface_read(item, np.array([]), np.array([]), is_ortho)
    check("empty query batch is empty", empty.shape == (0,))


def numerical_secondary_batches(module, is_ortho, reference):
    camera = "ortho" if is_ortho else "perspective"
    # 4,113 queries against 256 samples exceed the allocation boundary even
    # after removing five triangle hits. Midpoints must still pick the first
    # of two equally close probe samples, on both sides of that boundary.
    px = np.arange(256.) * 2
    pd = np.arange(256.) + 7
    rows = np.arange(4113)
    sample = rows % 255
    offset = np.array([.25, 1., 1.75])[rows % 3]
    qx, qy = sample * 2 + offset, np.full(len(rows), 5.)
    expected = pd[sample + (offset > 1)]
    hits = rows % 1024 == 0
    qx[hits], qy[hits], expected[hits] = -97., -97., 999.
    item = dict(probe=(px, np.zeros(256), pd),
                tris=(np.array([[-100., -90., -100.]]),
                      np.array([[-100., -100., -90.]]), np.full((1, 3), 999.)))
    actual = module._depths_at(deepcopy(item), qx, qy, is_ortho)
    check(camera + " chunked fallback keeps hits, misses and first-nearest ties",
          np.allclose(actual, expected, rtol=0, atol=1e-11),
          f"{int((~hits).sum())} misses x {len(px)} samples")

    # The first 255 points follow the bottom of a right triangle; the last
    # goes up its side. Every query below lies on its closing diagonal, which
    # must be included in both chunks. The two endpoint depths give the exact
    # answer for either camera independently of the segment search.
    bottom = np.column_stack([np.linspace(0, 100, 255), np.zeros(255)])
    pts = np.vstack([bottom, [[100., 100.]]])
    depths = np.concatenate([np.linspace(5, 15, 255), [25.]])
    line = dict(kind=0, pts=pts, depths=depths, closed=True)
    fraction = np.linspace(.1, .9, 4097)
    q = fraction * 100
    expected_line = (5 * (1 - fraction) + 25 * fraction if is_ortho else
                     1.0 / ((1 - fraction) / 5 + fraction / 25))
    actual_line = module._depths_at(deepcopy(line), q, q, is_ortho)
    check(camera + " chunked closed stroke reads its final segment",
          np.allclose(actual_line, expected_line, rtol=0, atol=1e-11),
          f"{len(q)} queries x {len(pts)} points")
    if reference is not None:
        # Include both neighbours of the chunk boundary and the final point;
        # scalar reads suffice without allocating the old full chain matrix.
        chosen = [0, 1, 1023, 1024, 4094, 4095, 4096, 4112]
        old = np.array([reference._depths_at(deepcopy(item), [qx[k]], [qy[k]],
                                           is_ortho)[0] for k in chosen])
        check(camera + " fallback boundary matches old scalar reads",
              np.allclose(actual[chosen], old, rtol=0, atol=1e-11))
        chosen = [0, 1, 1024, 2048, 4094, 4095, 4096]
        old = np.array([reference._depths_at(deepcopy(line), [q[k]], [q[k]],
                                           is_ortho)[0] for k in chosen])
        check(camera + " closing-segment boundary matches old scalar reads",
              np.allclose(actual_line[chosen], old, rtol=0, atol=1e-11))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--order", default=os.path.join(ROOT, "addon", "vecbake", "order.py"))
    parser.add_argument("--reference", help="optional unbatched order.py for full graph comparison")
    args = parser.parse_args()
    order = load(args.order, "tested_order")
    reference = load(args.reference, "reference_order") if args.reference else None
    rng = np.random.default_rng(4)

    for ortho in (True, False):
        camera = "ortho" if ortho else "perspective"
        numerical_batch(order, ortho)
        numerical_secondary_batches(order, ortho, reference)
        for name, items in [("ramp", ramp_scene(ortho)), ("gap", gap_scene(ortho)),
                            ("no_shared", no_shared_scene()), ("cycle", cycle_scene())]:
            for permutation in range(3):
                if permutation:
                    items = [items[i] for i in rng.permutation(len(items))]
                ranks, stats, calls, points, values = resolve_observed(order, items, ortho)
                label = f"{camera}/{name}/permutation {permutation}"
                check(label + " surface values match scalar oracle", all(values.values()))
                check(label + " at most one surface read per queried item",
                      all(n == 1 for n in calls.values()), str(dict(calls)))
                check(label + " ranks are injective", len(set(ranks.values())) == len(items))
                if name == "ramp":
                    check(label + " fittings preserve known front/behind relations",
                          all(ranks[k] < ranks["body"] for k in
                              ("front", "front_line", "own_line", "merged_fitting"))
                          and ranks["behind"] > ranks["body"])
                    check(label + " disjoint depths override wrong seeds",
                          all(ranks["back"] > ranks[k] for k in
                              ("body", "front", "behind", "merged_fitting", "front_line", "own_line")))
                    check(label + " unrelated or disjoint surfaces are never read",
                          calls["back"] == 0 and calls["isolated"] == 0)
                    check(label + " body has multiple requests; spy really ran", points["body"] >= 5)
                    check(label + " own-edge assertion survives", stats["assertions"] == 1
                          and stats["assertions_lost"] == 0)
                elif name == "gap":
                    check(label + " bracket receives hit and gap queries", points["bracket"] >= 3)
                    body = deepcopy(next(it for it in items if it["key"] == "bracket"))
                    surface = order._surface_read(body, np.array([50., 200., 350.]),
                                                  np.array([60., 60., 60.]), ortho)
                    read = order._depths_at(body, [50., 200., 350.], [60., 60., 60.], ortho)
                    check(label + " mixed batch falls back only off the surface",
                          np.isfinite(surface[[0, 2]]).all() and np.isnan(surface[1])
                          and np.array_equal(read[[0, 2]], surface[[0, 2]])
                          # Four vertices tie at the gap centre. The first in
                          # probe order is (300, 100), as in the scalar read.
                          and read[1] == ramp_depth(300, ortho))
                elif name == "no_shared":
                    check(label + " box overlap without shared area reads no surface", not calls)
                else:
                    check(label + " geometric cycle is exercised", stats["cycle_breaks"] > 0
                          and stats["assertions_lost"] == 0)
                if reference is not None:
                    old = reference.resolve(deepcopy(items), is_ortho=ortho)
                    old_ranks = dict(zip((it["key"] for it in items), old))
                    check(label + " full ranks and graph match unbatched resolver",
                          ranks == old_ranks and stats == reference.LAST_STATS,
                          "" if ranks == old_ranks else f"new={ranks}, old={old_ranks}")
    print(f"\n{len(FAILS)} failure(s)")
    return bool(FAILS)


if __name__ == "__main__":
    raise SystemExit(main())
