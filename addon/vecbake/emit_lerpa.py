# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""The .lerpa container -- one file per transition.

A .lerpa **is a valid SVG file** with the animation data embedded in it. That
single decision is what makes the format pleasant to hand to someone who knows
nothing about this project:

* Double-click it, drop it in an ``<img>``, open it in Illustrator or Figma --
  it renders, because it is just an SVG showing the resting state.
* Give it to the player and it becomes an animation: the JSON in ``<metadata>``
  carries the keyframes.
* No JavaScript, no build step and no second file are needed for the static
  case, so it degrades gracefully instead of showing a broken image.

Both resting states travel inside it, `start` visible and `end` hidden, so the
transition and the two states it sits between can never drift apart or go
missing separately.

What is deliberately *not* in here: the player runtime. It is a few KB shared
across every animation on a page, and inlining it per asset would multiply it
by the number of transitions. Nor is there a reverse clip -- reversal is a time
transform the player applies for free, so storing it would double the file to
encode something already present.
"""

from __future__ import annotations

import json

from . import emit_json, emit_svg
from .ir import VecScene

CONTAINER_VERSION = 1
DATA_ID = "lerpa-data"


def _cdata(text: str) -> str:
    # a CDATA section cannot contain "]]>", so split any occurrence across two
    # sections; JSON is reassembled unchanged by any conforming XML parser
    return "<![CDATA[" + text.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def to_svg(vec: VecScene, include_ids=True, coord_decimals=2, states=True,
           name="", source="", pretty=False) -> str:
    doc = emit_json.to_dict(vec, include_ids=include_ids, coord_decimals=coord_decimals)
    doc["format"] = "lerpa"
    doc["version"] = CONTAINER_VERSION
    doc["track_format"] = emit_json.FORMAT_VERSION
    if name:
        doc["name"] = name
    meta = dict(doc.get("meta") or {})
    meta["generator"] = "VecBake"
    if source:
        meta["source"] = source
    doc["meta"] = meta
    doc["has_states"] = bool(states)

    payload = json.dumps(doc, indent=1) if pretty else json.dumps(doc, separators=(",", ":"))

    parts = [emit_svg.svg_open_tag(vec, extra=' data-lerpa="1"')]
    parts.append(f'<title>{name or "Lerpa animation"}</title>')
    parts.append(f'<metadata id="{DATA_ID}" data-format="lerpa">{_cdata(payload)}</metadata>')

    if states:
        parts.append('<g id="lerpa-state-start">')
        parts.append(emit_svg.render_inner(vec, time=0.0, decimals=coord_decimals))
        parts.append("</g>")
        parts.append('<g id="lerpa-state-end" style="display:none">')
        parts.append(emit_svg.render_inner(vec, time=vec.duration, decimals=coord_decimals))
        parts.append("</g>")
    else:
        # still render *something* so the file is never a blank image
        parts.append('<g id="lerpa-state-start">')
        parts.append(emit_svg.render_inner(vec, time=0.0, decimals=coord_decimals))
        parts.append("</g>")

    parts.append("</svg>")
    return "\n".join(parts)


def write(vec: VecScene, path, **kwargs):
    text = to_svg(vec, **kwargs)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return len(text.encode("utf-8"))
