# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""Static SVG emitter -- the resting states.

Emitted from the *same IR* the player consumes, evaluated at one instant.  That
shared source is the whole point: the last frame the player draws and the SVG
you hard-cut to are computed from identical numbers, so the swap is seamless
instead of a one-pixel jump.

Output is real DOM: every element carries `data-group` and `id`, so the resting
state is hoverable, clickable and CSS-themeable.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from .ir import FILL, VecScene


def _hex(color, shade=1.0):
    r, g, b = (max(0.0, min(1.0, c * shade)) for c in color[:3])
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def _fmt(v, decimals):
    r = round(v, decimals)
    return str(int(r)) if r == int(r) else str(r)


def render_inner(vec: VecScene, time=None, decimals=2, class_prefix="vb", seam_fix=True,
                 seam_width=0.7) -> str:
    """The drawing itself, without the <svg> wrapper.

    Split out so a container can hold several states in one document.
    """
    t = vec.duration if time is None else time

    drawable = []
    for el in vec.elements:
        opacity = el.opacity.sample(t) if el.opacity else 1.0
        if opacity <= 0.004:
            continue
        pts = el.geo.sample(t)
        depth = el.depth.sample(t) if el.depth else 0.0
        shade = el.shade.sample(t) if el.shade else 1.0
        drawable.append((depth, el, pts, opacity, shade))

    # painter's algorithm: far to near
    drawable.sort(key=lambda item: -item[0])

    out = []
    if vec.background:
        out.append(f'<rect width="100%" height="100%" fill="{_hex(vec.background)}"/>')
    out.append('<g stroke-linecap="round" stroke-linejoin="round">')

    for depth, el, pts, opacity, shade in drawable:
        coords = " ".join(
            f"{_fmt(pts[i], decimals)},{_fmt(pts[i + 1], decimals)}"
            for i in range(0, len(pts), 2)
        )
        attrs = [
            f'id="{class_prefix}-{escape(el.key)}"',
            f'data-group="{escape(el.group)}"',
        ]
        if opacity < 0.999:
            attrs.append(f'opacity="{_fmt(opacity, 3)}"')
        if el.kind == FILL:
            fill = _hex(el.color, shade)
            attrs.append(f'fill="{fill}"')
            if seam_fix:
                # close the antialiasing hairline between abutting polygons,
                # exactly as the Canvas player does
                attrs.append(f'stroke="{fill}"')
                attrs.append(f'stroke-width="{_fmt(seam_width, 2)}"')
            out.append(f'<polygon points="{coords}" {" ".join(attrs)}/>')
        else:
            attrs.append(f'fill="none"')
            attrs.append(f'stroke="{_hex(el.color)}"')
            attrs.append(f'stroke-width="{_fmt(el.width, 2)}"')
            out.append(f'<polyline points="{coords}" {" ".join(attrs)}/>')

    out.append("</g>")
    return "\n".join(out)


def svg_open_tag(vec: VecScene, extra="") -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {vec.width} {vec.height}" '
        f'width="{vec.width}" height="{vec.height}" '
        f'shape-rendering="geometricPrecision"{extra}>'
    )


def render(vec: VecScene, time=None, decimals=2, **kwargs) -> str:
    return svg_open_tag(vec) + "\n" + \
        render_inner(vec, time=time, decimals=decimals, **kwargs) + "\n</svg>"


def write(vec: VecScene, path, time=None, decimals=2):
    text = render(vec, time=time, decimals=decimals)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return len(text.encode("utf-8"))
