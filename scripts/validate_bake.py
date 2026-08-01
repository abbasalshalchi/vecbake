"""Audit a bake for occlusion errors.

Checks the two things that can silently go wrong between "the baker knows the
right answer" and "the player draws it":

1. **Occlusion violations** -- for every screen-overlapping pair, is the
   painted order actually consistent with which one is nearer where they
   overlap? This is ground truth, recomputed independently of the ordering
   code, so it catches the ordering code being wrong.
2. **Order flips after reduction** -- keyframe reduction runs per element, so
   two elements a hair apart in depth can each drift within tolerance and swap.
   Compares the reduced tracks against the pre-reduction resolved order.

Both should be zero. Anything else shows up as slicing or flicker on playback.

Run inside Blender:
    exec(open('scripts/validate_bake.py').read())
"""

import sys

import numpy as np

import bpy


def validate(settings=None, verbose=True):
    import vecbake
    from vecbake import order as order_mod
    import vecbake.bake as bake_mod
    from vecbake.settings import BakeSettings

    settings = settings or BakeSettings()
    resolved_per_frame = []
    violations = []
    cycle_breaks = []
    real = order_mod.resolve

    def spy(items, is_ortho=False, tol=1e-4):
        out = real(items, is_ortho, tol)
        cycle_breaks.append(order_mod.LAST_STATS.get("cycle_breaks", 0))
        resolved_per_frame.append({it["key"]: v for it, v in zip(items, out)})

        found = []
        n = len(items)
        # Same screen-bounds prefilter resolve() uses. This is not sampling:
        # pairs whose boxes miss cannot share any point, so they cannot be
        # violations. Without it this sweep is O(n^2) and unusable on a real
        # scene of a few thousand elements.
        bbox = np.array([it["bbox"] for it in items], dtype=np.float64)
        kinds = np.array([it["kind"] for it in items], dtype=np.int32)
        for i in range(n):
            if kinds[i] != 1:
                continue                      # only fills can occlude
            overlap = (
                (bbox[:, 0] <= bbox[i, 1]) & (bbox[:, 1] >= bbox[i, 0])
                & (bbox[:, 2] <= bbox[i, 3]) & (bbox[:, 3] >= bbox[i, 2])
            )
            overlap[i] = False
            overlap[:i + 1] &= kinds[:i + 1] != 1
            for j in np.nonzero(overlap)[0]:
                j = int(j)
                shared = order_mod._shared_samples(items[i], items[j])
                if shared is None:
                    continue
                qx, qy = shared
                da = float(np.mean(order_mod._depths_at(items[i], qx, qy, is_ortho)))
                db = float(np.mean(order_mod._depths_at(items[j], qx, qy, is_ortho)))
                if abs(da - db) <= tol:
                    continue
                painted_i_behind = out[i] > out[j]
                if painted_i_behind != (da > db):
                    found.append((items[i]["key"], items[j]["key"], round(da - db, 4)))
        violations.append(found)
        return out

    bake_mod.order_mod.resolve = spy
    try:
        vec = vecbake.bake_scene(bpy.context, settings)
    finally:
        bake_mod.order_mod.resolve = real

    by_key = {el.key: el for el in vec.elements}
    flips = []
    for fi, truth in enumerate(resolved_per_frame):
        t = fi / vec.fps
        keys = [k for k in truth if k in by_key]
        rank = {k: i for i, k in enumerate(sorted(keys, key=lambda k: -truth[k]))}
        seq = [rank[k] for k in sorted(keys, key=lambda k: -by_key[k].depth.sample(t))]
        flips.append(sum(1 for a in range(len(seq)) for b in range(a + 1, len(seq))
                         if seq[a] > seq[b]))

    report = {
        "frames": len(resolved_per_frame),
        "elements": len(vec.elements),
        "occlusion_violations": sum(len(v) for v in violations),
        "order_flips_after_reduction": sum(flips),
        "cycle_breaks": sum(cycle_breaks),
        "worst_frame": max(range(len(violations)), key=lambda i: len(violations[i]))
                       if violations else None,
        "examples": [v for v in violations if v][:1],
    }
    if verbose:
        ok = report["occlusion_violations"] == 0 and report["order_flips_after_reduction"] == 0
        print(("PASS" if ok else "FAIL"), report)
    return report


if __name__ == "__main__":
    validate()
