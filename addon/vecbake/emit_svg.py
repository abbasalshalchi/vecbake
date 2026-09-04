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


def _tangents(x, y, el, n):
    """Catmull-Rom half-tangents at every point, zero at a corner."""
    mask = getattr(el, "smooth", None)
    tx, ty = [0.0] * n, [0.0] * n
    if mask is None:
        return tx, ty
    flags = [bool(v) for v in mask]
    if not any(flags) or len(flags) != n:
        return tx, ty
    closed = el.is_closed()
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
    return tx, ty


def _run_data(pts, el, decimals, mask):
    """`d` for the spans a merged outline's seam mask leaves stroked.

    The fill keeps every point -- dropping the cut would change the shape --
    so the mask is applied here and only here. Same tangents as the filled
    path, so the line sits exactly on the boundary it came from, and one `M`
    wherever a run of kept spans begins.
    """
    n = len(pts) // 2
    if n < 3 or mask is None or len(mask) != n:
        return ""
    skip = [bool(v) for v in mask]
    if all(skip):
        return ""
    x = [pts[i * 2] for i in range(n)]
    y = [pts[i * 2 + 1] for i in range(n)]
    tx, ty = _tangents(x, y, el, n)
    f = lambda v: _fmt(v, decimals)
    # Begin where a run begins, so the run that wraps past the last span stays
    # one subpath. Starting at span 0 regardless breaks it at that corner and
    # leaves a zero-length subpath behind, which round caps paint as a dot.
    # 0 when nothing is skipped: there is only one run and it starts anywhere.
    start = next(((i + 1) % n for i in range(n)
                  if skip[i] and not skip[(i + 1) % n]), 0)
    out, pen = [], False
    for k in range(n):
        i = (start + k) % n
        j = (i + 1) % n
        if skip[i]:
            pen = False
            continue
        if not pen:
            out.append(f"M {f(x[i])},{f(y[i])}")
            pen = True
        if tx[i] == 0.0 and ty[i] == 0.0 and tx[j] == 0.0 and ty[j] == 0.0:
            out.append(f"L {f(x[j])},{f(y[j])}")
        else:
            out.append(
                f"C {f(x[i] + tx[i] / 3)},{f(y[i] + ty[i] / 3)}"
                f" {f(x[j] - tx[j] / 3)},{f(y[j] - ty[j] / 3)}"
                f" {f(x[j])},{f(y[j])}"
            )
    return " ".join(out)


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
            # Spans of this outline that are a weld: kept by the fill,
            # dropped by the line, and drawn as a separate path afterwards
            # because one SVG shape cannot stroke part of itself.
            seam = getattr(el, "seam", None)
            outlined = (el.edge_color is not None and el.width > 0.0
                        and opacity > 0.999)
            runs = cut = ""
            if outlined and seam is not None:
                runs = _run_data(pts, el, decimals, seam)
                # The complement: the cut itself, which takes the hairline
                # treatment rather than the line. Only along the cut -- round
                # the whole ring it would sit under the outline everywhere
                # else, and two strokes blend differently from one.
                cut = _run_data(pts, el, decimals,
                                [0 if v else 1 for v in seam])
            if outlined and seam is None:
                # A merged outline draws its own silhouette, so the resting
                # states have to draw it too. Without this the static SVG that
                # every .lerpa embeds would show these shapes unoutlined while
                # the animation shows them outlined -- and the player hands off
                # between the two, so the line would blink at the ends of a
                # clip. Wider than the seam hairline it replaces, so it closes
                # that as well. See outline.py.
                attrs.append(f'stroke="{_hex(el.edge_color)}"')
                attrs.append(f'stroke-width="{_fmt(el.width, 2)}"')
            elif outlined:
                pass          # both lines are emitted below, as their own paths
            elif seam_fix and opacity > 0.999:
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
            if cut:
                # The cut: filled by the shape, and closed against the
                # abutting half by a hairline in the fill's own colour.
                out.append(
                    f'<path d="{cut}" fill="none" stroke="{fill}"'
                    f' stroke-width="{_fmt(seam_width, 2)}"'
                    f' data-group="{escape(el.group)}"/>'
                )
            if runs:
                out.append(
                    f'<path d="{runs}" fill="none"'
                    f' stroke="{_hex(el.edge_color)}"'
                    f' stroke-width="{_fmt(el.width, 2)}"'
                    f' data-group="{escape(el.group)}"/>'
                )
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
