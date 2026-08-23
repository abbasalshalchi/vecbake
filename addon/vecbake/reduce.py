# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""Keyframe reduction.

Densely-sampled tracks go in, sparse ones come out.  We use Ramer-Douglas-
Peucker with the error measured in the same unit the viewer sees -- pixels for
geometry -- so the tolerance is a direct, honest bound on the visual error of
the interpolated playback.  At 0.35 px nobody can see the difference between
the baked curve and the true projection, and a slow camera move collapses from
30 samples to a handful of keys.
"""

from __future__ import annotations

import math

from .ir import Track


def _rdp(times, values, tol, err_fn):
    n = len(times)
    if n <= 2:
        return list(range(n))
    keep = [False] * n
    keep[0] = keep[n - 1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        ta, tb = times[a], times[b]
        dt = tb - ta
        va, vb = values[a], values[b]
        worst = -1.0
        worst_i = -1
        for i in range(a + 1, b):
            u = 0.0 if dt <= 0.0 else (times[i] - ta) / dt
            e = err_fn(va, vb, values[i], u)
            if e > worst:
                worst = e
                worst_i = i
        if worst > tol:
            keep[worst_i] = True
            stack.append((a, worst_i))
            stack.append((worst_i, b))
    return [i for i, k in enumerate(keep) if k]


def _rdp_bounded(times, values, tols, err_fn):
    """RDP with a *per-sample* error budget.

    Depth tracks cannot use a single global tolerance. Their absolute values
    barely matter -- only the order they induce across elements does -- so the
    budget at each sample is the room that element has before it would swap
    with its nearest neighbour in depth. Elements far from anything else
    compress hard; elements packed together keep their keys.
    """
    n = len(times)
    if n <= 2:
        return list(range(n))
    keep = [False] * n
    keep[0] = keep[n - 1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        ta, tb = times[a], times[b]
        dt = tb - ta
        va, vb = values[a], values[b]
        worst = 0.0
        worst_i = -1
        for i in range(a + 1, b):
            u = 0.0 if dt <= 0.0 else (times[i] - ta) / dt
            e = err_fn(va, vb, values[i], u)
            budget = tols[i]
            if budget > 1e-12:
                ratio = e / budget
            else:
                ratio = 0.0 if e <= 1e-12 else float("inf")
            if ratio > worst:
                worst = ratio
                worst_i = i
        if worst > 1.0 and worst_i >= 0:
            keep[worst_i] = True
            stack.append((a, worst_i))
            stack.append((worst_i, b))
    return [i for i, k in enumerate(keep) if k]


def reduce_scalar_bounded(times, values, tols) -> Track:
    """Reduce a scalar track against per-sample tolerances."""
    if all(t == float("inf") for t in tols):
        return _collapse_constant(times, values, float("inf"), False) or Track(
            [times[0]], [values[0]]
        )
    const = _collapse_constant(times, values, min(tols), False)
    if const is not None:
        return const
    idx = _rdp_bounded(times, values, tols, _scalar_err)
    return Track([times[i] for i in idx], [values[i] for i in idx])


def _scalar_err(a, b, v, u):
    return abs(v - (a + (b - a) * u))


def _points_err(a, b, v, u):
    """Worst per-point deviation, in pixels."""
    worst = 0.0
    for j in range(0, len(v), 2):
        dx = v[j] - (a[j] + (b[j] - a[j]) * u)
        dy = v[j + 1] - (a[j + 1] + (b[j + 1] - a[j + 1]) * u)
        d = dx * dx + dy * dy
        if d > worst:
            worst = d
    return math.sqrt(worst)


def _collapse_constant(times, values, eps, is_vector):
    first = values[0]
    for v in values[1:]:
        if is_vector:
            if any(abs(a - b) > eps for a, b in zip(first, v)):
                return None
        elif abs(v - first) > eps:
            return None
    return Track([times[0]], [first])


def reduce_scalar(times, values, tol) -> Track:
    const = _collapse_constant(times, values, tol, False)
    if const is not None:
        return const
    idx = _rdp(times, values, tol, _scalar_err)
    return Track([times[i] for i in idx], [values[i] for i in idx])


def reduce_points(times, values, tol) -> Track:
    const = _collapse_constant(times, values, tol, True)
    if const is not None:
        return const
    idx = _rdp(times, values, tol, _points_err)
    return Track([times[i] for i in idx], [values[i] for i in idx])


def freeze_hidden(geo_samples, vis_samples):
    """Give every hidden frame the pose of the nearest frame that is not.

    A hidden run contributes nothing visually, so pinning it lets RDP collapse
    the stretch to nothing. What matters is the two ends of the run: opacity
    ramps across one frame and the player interpolates geometry over the same
    span, so whatever pose sits at the last hidden frame is a pose the viewer
    watches the element travel from.

    Frames beside a flip used to be left at their own pose, on the reasoning
    that it is the honest thing to ramp against. It is not honest when the
    element had no pose there: a merged fill writes nothing in a frame whose
    silhouette is not one ring, and the frame then borrowed the nearest pose
    it could find, which can be from long before. part_001 is the magnetometer
    boom, eleven units long, projecting to a sliver that reaches 2400px off
    screen. It went hidden for four frames, came back 2900px away, and faded
    in while travelling the whole distance -- a line sweeping across the frame,
    from geometry that was correct at both ends.

    Nearest visible pose instead, so an element fades in and out where it
    belongs. Inside a long run the borrowed pose switches from one end to the
    other, and nobody sees that: it happens where opacity is zero.
    """
    n = len(geo_samples)
    if n == 0:
        return geo_samples
    seen = [i for i in range(n)
            if vis_samples[i] > 0.0 and geo_samples[i] is not None]
    if not seen:
        # Never visible, or never drawn. The caller drops these; hand back what
        # came in rather than inventing a pose for it.
        return list(geo_samples)
    out = list(geo_samples)
    at = 0
    for i in range(n):
        if vis_samples[i] > 0.0 and geo_samples[i] is not None:
            continue
        while at + 1 < len(seen) and abs(seen[at + 1] - i) <= abs(seen[at] - i):
            at += 1
        out[i] = geo_samples[seen[at]]
    return out
