# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""Secondary emitter: Lottie, for previewing in standard tools.

Nearly free because the IR already holds exactly what Lottie wants -- sparse 2D
point keyframes plus opacity.  Useful for dropping a bake into LottieFiles,
After Effects (via importers), or any of the mobile/web Lottie players.

Known and deliberate limitation: **Lottie layer order is static.**  Depth
becomes a fixed layer stack (sorted by average depth), so occlusion cannot flip
mid-animation the way it can in the native player.  For a modest camera move
that is invisible; for a full orbit around a see-through lattice it is not.
This is the reason the player format exists and Lottie is the fallback.
"""

from __future__ import annotations

import json

from .ir import FILL, VecScene

LOTTIE_VERSION = "5.7.4"
# Bezier control points that make a keyframe pair interpolate linearly.
LINEAR_IN = {"x": [1.0], "y": [1.0]}
LINEAR_OUT = {"x": [0.0], "y": [0.0]}


def _rgba(color, shade=1.0):
    return [max(0.0, min(1.0, c * shade)) for c in color[:3]] + [1.0]


def _avg(track, fallback=0.0):
    if track is None or not track.values:
        return fallback
    return sum(track.values) / len(track.values)


def _handles(verts, mask, closed):
    """Lottie's per-vertex in/out handles, from the same tangents as everyone.

    The format already wants exactly what `curves.py` computes: a handle is the
    Catmull-Rom tangent over three, stated relative to its own vertex, and a
    corner is a pair of zeros -- which collapses its segment to the straight
    line it replaced, the same way it does in the SVG and the player.

    Leaving these at zero was survivable while the mask only ever *added* a bow
    that Lottie declined to draw. It stopped being survivable once flat faces
    began following their neighbours' curves: their bulge is real geometry, so
    a Lottie file without the handles has the sides overhanging a cap still
    drawn as a polygon -- the original gap, inside out.
    """
    n = len(verts)
    inn = [[0.0, 0.0] for _ in range(n)]
    out = [[0.0, 0.0] for _ in range(n)]
    if mask is None:
        return inn, out
    for i in range(n):
        if not mask[i]:
            continue
        if closed:
            a, b = (i - 1) % n, (i + 1) % n
        elif 0 < i < n - 1:
            a, b = i - 1, i + 1
        else:
            continue                       # an end has nothing to bend through
        tx = (verts[b][0] - verts[a][0]) / 6.0
        ty = (verts[b][1] - verts[a][1]) / 6.0
        out[i] = [tx, ty]
        inn[i] = [-tx, -ty]
    return inn, out


def _path_keyframes(geo, npts, fps, closed, mask=None):
    def shape(values):
        verts = [[values[i], values[i + 1]] for i in range(0, len(values), 2)]
        inn, out = _handles(verts, mask, closed)
        return [{"i": inn, "o": out, "v": verts, "c": closed}]

    if geo.constant:
        return {"a": 0, "k": shape(geo.values[0])[0]}

    keys = []
    for t, v in zip(geo.times, geo.values):
        keys.append({"i": LINEAR_IN, "o": LINEAR_OUT, "t": round(t * fps, 4), "s": shape(v)})
    keys.append({"t": round(geo.times[-1] * fps, 4)})
    return {"a": 1, "k": keys}


def _scalar_keyframes(track, fps, scale=1.0, fallback=100.0):
    if track is None:
        return {"a": 0, "k": fallback}
    if track.constant:
        return {"a": 0, "k": track.values[0] * scale}
    keys = [
        {"i": LINEAR_IN, "o": LINEAR_OUT, "t": round(t * fps, 4), "s": [v * scale]}
        for t, v in zip(track.times, track.values)
    ]
    keys.append({"t": round(track.times[-1] * fps, 4)})
    return {"a": 1, "k": keys}


def _color_property(el, fps):
    """Static colour, or an animated one when the fill is shaded."""
    if el.shade is None or el.shade.constant:
        shade = el.shade.values[0] if el.shade else 1.0
        return {"a": 0, "k": _rgba(el.color, shade)}
    keys = [
        {"i": LINEAR_IN, "o": LINEAR_OUT, "t": round(t * fps, 4), "s": _rgba(el.color, s)}
        for t, s in zip(el.shade.times, el.shade.values)
    ]
    keys.append({"t": round(el.shade.times[-1] * fps, 4)})
    return {"a": 1, "k": keys}


def to_dict(vec: VecScene, name="vecbake") -> dict:
    fps = vec.fps
    # `op` is exclusive in Lottie: a layer is drawn while ip <= t < op. Setting
    # it to exactly duration*fps makes the final pose unrenderable, which breaks
    # the whole point of cutting from the animation to the matching resting SVG.
    total = max(1.0, round(vec.duration * fps, 4)) + 1.0

    # Lottie paints the *first* layer in the array on top, so ordering the array
    # near-to-far reproduces a painter's algorithm.
    ordered = sorted(vec.elements, key=lambda e: _avg(e.depth))

    layers = []
    for i, el in enumerate(ordered):
        # what closes the path and what paints it are different questions: a
        # chained stroke can be a ring without being a fill
        filled = el.kind == FILL
        path = {"ty": "sh", "ind": 0, "nm": el.key,
                "ks": _path_keyframes(el.geo, el.npts, fps, el.is_closed(), el.smooth)}
        if filled:
            paint = {"ty": "fl", "nm": "fill", "c": _color_property(el, fps), "o": {"a": 0, "k": 100}, "r": 1}
        else:
            paint = {
                "ty": "st",
                "nm": "stroke",
                "c": {"a": 0, "k": _rgba(el.color)},
                "o": {"a": 0, "k": 100},
                "w": {"a": 0, "k": el.width},
                "lc": 2,
                "lj": 2,
            }
        group = {
            "ty": "gr",
            "nm": el.key,
            "it": [
                path,
                paint,
                {
                    "ty": "tr",
                    "p": {"a": 0, "k": [0, 0]},
                    "a": {"a": 0, "k": [0, 0]},
                    "s": {"a": 0, "k": [100, 100]},
                    "r": {"a": 0, "k": 0},
                    "o": {"a": 0, "k": 100},
                },
            ],
        }
        layers.append(
            {
                "ddd": 0,
                "ind": i + 1,
                "ty": 4,
                "nm": el.key,
                "sr": 1,
                "ks": {
                    "o": _scalar_keyframes(el.opacity, fps, scale=100.0, fallback=100.0),
                    "r": {"a": 0, "k": 0},
                    "p": {"a": 0, "k": [0, 0, 0]},
                    "a": {"a": 0, "k": [0, 0, 0]},
                    "s": {"a": 0, "k": [100, 100, 100]},
                },
                "ao": 0,
                "shapes": [group],
                "ip": 0,
                "op": total,
                "st": 0,
                "bm": 0,
            }
        )

    return {
        "v": LOTTIE_VERSION,
        "fr": fps,
        "ip": 0,
        "op": total,
        "w": vec.width,
        "h": vec.height,
        "nm": name,
        "ddd": 0,
        "assets": [],
        "layers": layers,
    }


def to_json(vec: VecScene, name="vecbake", pretty=False) -> str:
    doc = to_dict(vec, name)
    return json.dumps(doc, indent=1) if pretty else json.dumps(doc, separators=(",", ":"))


def write(vec: VecScene, path, name="vecbake", pretty=False):
    text = to_json(vec, name, pretty)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return len(text.encode("utf-8"))
