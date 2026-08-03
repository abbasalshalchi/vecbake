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
    """Stop tracking geometry that nobody can see.

    A run of frames where the element is hidden *and* stays hidden into the
    next frame contributes nothing visually, so we pin its geometry to the last
    visible pose.  RDP then collapses the whole run to nothing.  Frames on
    either side of a visibility flip are left alone -- their geometry is what
    the one-frame opacity ramp interpolates against.
    """
    n = len(geo_samples)
    if n == 0:
        return geo_samples
    out = list(geo_samples)
    last_seen = None
    for i in range(n):
        visible = vis_samples[i] > 0.0
        neighbour_visible = (i > 0 and vis_samples[i - 1] > 0.0) or (
            i + 1 < n and vis_samples[i + 1] > 0.0
        )
        if visible:
            last_seen = geo_samples[i]
        elif not neighbour_visible and last_seen is not None:
            out[i] = last_seen
    return out
