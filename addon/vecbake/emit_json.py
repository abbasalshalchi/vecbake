# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""Primary emitter: the compact player format.

Design notes
------------
* Keys are short because this is machine-read, never hand-edited.
* A track is ``{"t": [...], "v": [...]}``; a *constant* track omits ``t`` and
  carries a single sample.  The player branches on ``t`` being absent, which
  is both the smallest encoding and the fastest check.
* Colours are deduped into a palette so the player can retheme by index --
  ``player.setColor(2, '#f00')`` recolours everything sharing that swatch
  without touching geometry.
* ``D`` (depth) is what makes this format able to do something neither Lottie
  nor SMIL can: the player re-sorts paint order every frame, so occlusion can
  legitimately flip mid-animation.
"""

from __future__ import annotations

import json

from .ir import FILL, VecScene

FORMAT_VERSION = 2
KIND_CODE = {"stroke": 0, "fill": 1}


def _hex(color):
    r, g, b = (max(0.0, min(1.0, c)) for c in color[:3])
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def _round(value, decimals):
    r = round(value, decimals)
    return int(r) if decimals <= 0 or r == int(r) else r


def _flat_ints(values, scale):
    """Quantise a track's samples to integers, flattened."""
    rows = []
    for v in values:
        if isinstance(v, (int, float)):
            rows.append([int(round(v * scale))])
        else:
            rows.append([int(round(c * scale)) for c in v])
    return rows


def _delta(rows):
    """Difference each sample against the previous one, component by component.

    This is where correlated motion pays off. Neighbouring elements that travel
    together produce near-identical delta streams, and quantised deltas are
    small integers with few digits -- short to write and highly repetitive, so
    the generic compressor on the wire can match them across elements. Storing
    absolute coordinates throws that structure away.
    """
    out = list(rows[0])
    prev = rows[0]
    for row in rows[1:]:
        out.extend(a - b for a, b in zip(row, prev))
        prev = row
    return out


def _has_off_grid_key(times, grid):
    """Does this track carry a key that is not on the sample grid?

    Dense encoding throws the timeline away and rebuilds it by dividing the
    duration evenly, so it can only carry keys the grid can express. Opacity is
    now allowed keys *between* frames -- a fade with no geometry behind it is
    closed off a sliver past the last frame the pose was real on -- and
    resampling that onto the grid reads the track at the frames either side and
    hands back exactly the frame-long fade it replaced. Silently, and only on
    the elements that needed it.
    """
    if len(grid) < 2:
        return False
    step = grid[1] - grid[0]
    if step <= 0.0:
        return False
    eps = step * 1e-6
    last = len(grid) - 1
    for t in times:
        i = min(last, max(0, int(round(t / step))))
        if abs(grid[i] - t) > eps:
            return True
    return False


def _encode_track(track, decimals, grid):
    """Pick the cheaper of a dense (implicit-timeline) or sparse encoding."""
    if track is None:
        return None
    scale = 10 ** decimals
    if track.constant:
        v = track.values[0]
        return {"c": _round(v, decimals) if isinstance(v, (int, float))
                else [_round(c, decimals) for c in v]}

    sparse = {
        "t": [_round(t, 4) for t in track.times],
        "q": scale,
        "d": _delta(_flat_ints(track.values, scale)),
    }
    if not grid or _has_off_grid_key(track.times, grid):
        return sparse

    # Dense: resample onto the sample grid and drop the times entirely. Worth
    # it whenever a track already carries a key at nearly every frame, which is
    # the normal case for anything actually moving.
    dense = {"q": scale, "d": _delta(_flat_ints([track.sample(t) for t in grid], scale))}
    return dense if len(json.dumps(dense, separators=(",", ":"))) < \
        len(json.dumps(sparse, separators=(",", ":"))) else sparse


def _encode_mask(mask):
    """Per-vertex flags: 1 for all, else a hex bitmask, else absent.

    Bit i is vertex i, so the *last* hex digit carries vertices 0-3. A bitmask
    beats a list of indices here because it is the same size whether the path
    is mostly corners or mostly curve, and a cube is as common as a circle.
    """
    if mask is None:
        return None
    flags = [bool(v) for v in mask]
    if not any(flags):
        return None
    if all(flags):
        return 1
    bits = 0
    for i, on in enumerate(flags):
        if on:
            bits |= 1 << i
    return format(bits, "x")


def to_dict(vec: VecScene, include_ids=True, coord_decimals=2) -> dict:
    palette: list[str] = []
    index: dict[str, int] = {}

    # The sample grid every dense track is implicitly keyed against. Dense
    # encoding throws the timeline away and lets the reader rebuild it by
    # dividing the duration evenly, so it is only correct when the bake really
    # did sample evenly. A Step that does not divide the frame range leaves a
    # short final gap -- 1..60 by 8 ends 57, 60 -- and resampling those keys
    # onto an even grid interpolates every track at the wrong instants. On
    # geometry that is a small drift; on opacity it invents fades, and on a
    # resolved paint order it blends integer ranks into fractions and shuffles
    # what is in front of what.
    depth_decimals = int(vec.meta.get("depth_decimals", 4))
    samples = int(vec.meta.get("frames", 0) or 0)
    # Absent means "unknown", and unknown must not be read as "even". A bake
    # that forgets to say -- or a VecScene assembled by hand -- then quietly
    # gets the dense encoding and the resampling error that comes with it,
    # which shows up as invented fades and blended paint ranks rather than as
    # anything that looks like a missing flag. Sparse costs bytes; guessing
    # costs correctness.
    uniform = bool(vec.meta.get("uniform_frames", False))
    if uniform and samples > 1 and vec.duration > 0:
        grid = [vec.duration * i / (samples - 1) for i in range(samples)]
    else:
        grid = None       # sparse carries its own times, so it stays exact

    def color_index(color):
        h = _hex(color)
        if h not in index:
            index[h] = len(palette)
            palette.append(h)
        return index[h]

    group_index = {g: i for i, g in enumerate(vec.groups)}

    els = []
    for el in vec.elements:
        item = {
            "k": KIND_CODE[el.kind],
            "n": el.npts,
            "g": group_index.get(el.group, 0),
            "c": color_index(el.color),
            "G": _encode_track(el.geo, coord_decimals, grid),
            "O": _encode_track(el.opacity, 3, grid),
            "D": _encode_track(el.depth, depth_decimals, grid),
        }
        if el.kind != FILL:
            item["w"] = round(el.width, 2)
            if el.closed:
                item["cl"] = 1        # a stroke that joins back to its start
        elif el.edge_color is not None and el.width > 0.0:
            # A fill that draws its own outline -- the merged silhouette. Both
            # fields are needed: the width says to draw it at all, and the
            # colour is not the fill's, so it cannot be inferred. A reader that
            # knows neither field still gets the fill, without its line, which
            # is what every older player will do.
            item["w"] = round(el.width, 2)
            item["oc"] = color_index(el.edge_color)
        if el.shade is not None:
            item["S"] = _encode_track(el.shade, 3, grid)
        smooth = _encode_mask(el.smooth)
        if smooth is not None:
            item["sm"] = smooth
        # Spans of a merged outline that are a weld: filled, not
        # stroked. Same encoding as `sm`, and absent unless the
        # object was cut off another. See bake._seam_spans.
        seam = _encode_mask(getattr(el, "seam", None))
        if seam is not None:
            item["sw"] = seam
        # How a merged fill's points divide into subpaths, when it carries more
        # than the one ring. Counts rather than offsets: they sum to `n`, so a
        # reader that ignores this draws the pieces joined end to end instead of
        # separately -- wrong, but not a crash.
        subs = getattr(el, "subpaths", None)
        if subs and len(subs) > 1:
            item["sp"] = [int(v) for v in subs]
        if include_ids:
            item["id"] = el.key
        els.append(item)

    doc = {
        "format": "vecbake",
        "version": FORMAT_VERSION,
        "w": vec.width,
        "h": vec.height,
        "fps": round(vec.fps, 6),
        "dur": round(vec.duration, 6),
        "samples": samples,
        "groups": list(vec.groups),
        "colors": palette,
        "els": els,
        "motion": vec.motion(),
        "stats": vec.stats(),
        "meta": vec.meta,
    }
    if vec.background:
        doc["bg"] = _hex(vec.background)
    return doc


def to_json(vec: VecScene, include_ids=True, coord_decimals=2, pretty=False) -> str:
    doc = to_dict(vec, include_ids, coord_decimals)
    if pretty:
        return json.dumps(doc, indent=1)
    return json.dumps(doc, separators=(",", ":"))


def write(vec: VecScene, path, include_ids=True, coord_decimals=2, pretty=False):
    text = to_json(vec, include_ids, coord_decimals, pretty)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return len(text.encode("utf-8"))
