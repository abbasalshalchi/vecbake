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


def _path_data(pts, el, decimals):
    """`d` for a curved element, or "" when it is a plain polyline.

    Same Catmull-Rom tangents the web player uses -- see curves.py. A corner
    carries a zero tangent, which collapses its cubic to the straight segment
    it replaced, so corners and curves mix inside one path without special
    cases at the boundary between them.
    """
    mask = getattr(el, "smooth", None)
    if mask is None:
        return ""
    flags = [bool(v) for v in mask]
    if not any(flags):
        return ""

    n = len(pts) // 2
    if n < 3 or len(flags) != n:
        return ""
    closed = el.is_closed()
    x = [pts[i * 2] for i in range(n)]
    y = [pts[i * 2 + 1] for i in range(n)]

    tx, ty = [0.0] * n, [0.0] * n
    for i in range(n):
        if not flags[i]:
            continue
        if closed:
            a, b = (i - 1) % n, (i + 1) % n
        elif 0 < i < n - 1:
            a, b = i - 1, i + 1
        else:
            continue
        tx[i] = (x[b] - x[a]) * 0.5
        ty[i] = (y[b] - y[a]) * 0.5

    f = lambda v: _fmt(v, decimals)
    out = [f"M {f(x[0])},{f(y[0])}"]
    last = n if closed else n - 1
    for i in range(last):
        j = (i + 1) % n
        if tx[i] == 0.0 and ty[i] == 0.0 and tx[j] == 0.0 and ty[j] == 0.0:
            out.append(f"L {f(x[j])},{f(y[j])}")
        else:
            out.append(
                f"C {f(x[i] + tx[i] / 3)},{f(y[i] + ty[i] / 3)}"
                f" {f(x[j] - tx[j] / 3)},{f(y[j] - ty[j] / 3)}"
                f" {f(x[j])},{f(y[j])}"
            )
    return " ".join(out)


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
        curved = _path_data(pts, el, decimals)
        coords = "" if curved else " ".join(
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
            if seam_fix and opacity > 0.999:
                # close the antialiasing hairline between abutting polygons,
                # exactly as the Canvas player does
                #
                # Not on a translucent fill. Canvas applies its alpha per draw
                # call, so a stroke laid over its own fill blends twice along
                # the boundary and rings the shape in a darker outline. SVG
                # composites the element first and would not, but it follows
                # anyway: the two renderers matching matters more than a
                # hairline nobody can see through a translucent surface.
                attrs.append(f'stroke="{fill}"')
                attrs.append(f'stroke-width="{_fmt(seam_width, 2)}"')
            if curved:
                out.append(f'<path d="{curved} Z" {" ".join(attrs)}/>')
            else:
                out.append(f'<polygon points="{coords}" {" ".join(attrs)}/>')
        else:
            attrs.append(f'fill="none"')
            attrs.append(f'stroke="{_hex(el.color)}"')
            attrs.append(f'stroke-width="{_fmt(el.width, 2)}"')
            if curved:
                shut = " Z" if el.closed else ""
                out.append(f'<path d="{curved}{shut}" {" ".join(attrs)}/>')
            elif el.closed:
                # a ring, so it needs the join drawn -- polyline would leave it
                out.append(f'<polygon points="{coords}" {" ".join(attrs)}/>')
            else:
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
