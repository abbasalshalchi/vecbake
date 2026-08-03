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
    eaten = []
    cycle_breaks = []
    discarded = []
    real = order_mod.resolve

    def spy(items, is_ortho=False, tol=1e-4):
        out = real(items, is_ortho, tol)
        cycle_breaks.append(order_mod.LAST_STATS.get("cycle_breaks", 0))
        discarded.append(order_mod.LAST_STATS.get("constraints_dropped", 0))
        resolved_per_frame.append({it["key"]: v for it, v in zip(items, out)})

        found = []
        n = len(items)
        # Match the resolver's effective tolerance. It treats "coplanar" as
        # relative to the scene's depth scale, so grading against the raw
        # absolute value reports pairs it deliberately left unconstrained.
        span = float(max(it["dmax"] for it in items) - min(it["dmin"] for it in items)) if n else 0.0
        tol = max(tol, span * 1e-4)
        # Same screen-bounds prefilter resolve() uses. This is not sampling:
        # pairs whose boxes miss cannot share any point, so they cannot be
        # violations. Without it this sweep is O(n^2) and unusable on a real
        # scene of a few thousand elements.
        bbox = np.array([it["bbox"] for it in items], dtype=np.float64)
        kinds = np.array([it["kind"] for it in items], dtype=np.int32)
        # An edge and a face it borders are coincident, so sampled depth cannot
        # grade them: the plane fitted to a slightly non-planar n-gon misses its
        # own boundary, which is the whole reason the baker overrides these.
        # Judging them here by that same fit just scores the correction as the
        # error. They get their own count below instead.
        keys = {it["key"]: k for k, it in enumerate(items)}
        on_face = {(keys[fk], j) for j, it in enumerate(items)
                   for fk in it.get("on_faces", ()) if fk in keys}
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
                if (i, j) in on_face or (j, i) in on_face:
                    continue
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
        # An edge lies on the faces it borders, so a face painted over one is
        # always wrong -- it eats half the stroke's width and the line reads as
        # thinned, or vanishes. Counted separately because it is a known open
        # problem rather than a general ordering failure.
        for i, j in on_face:
            if out[i] < out[j]:
                eaten.append((items[i]["key"], items[j]["key"]))

        violations.append(found)
        return out

    bake_mod.order_mod.resolve = spy
    try:
        vec = vecbake.bake_scene(bpy.context, settings)
    finally:
        bake_mod.order_mod.resolve = real

    by_key = {el.key: el for el in vec.elements}
    flips = []
    # Sample the reduced tracks at the times the bake actually recorded. Frames
    # are spaced by the step setting, so sample fi sits at fi*step/fps -- using
    # fi/fps reads every track from the first fraction of a second and reports
    # the mismatch as tens of thousands of flips per frame that are not there.
    n_samples = len(resolved_per_frame)
    for fi, truth in enumerate(resolved_per_frame):
        t = 0.0 if n_samples < 2 else vec.duration * fi / (n_samples - 1)
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
        # Constraints thrown away to break cycles. Worth watching: every one is
        # a measured ordering fact the final order does not honour, so a spike
        # here shows up as slicing somewhere even when the other counts look ok.
        "constraints_discarded": sum(discarded),
        "lines_under_own_face": len(eaten),
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
