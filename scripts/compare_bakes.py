# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""Are two bakes the same drawing? Element by element, field by field.

    python scripts/compare_bakes.py old.svg new.svg

For a change that should not touch a scene -- the anchors after the curve fix --
the answer has to be "identical", and a byte comparison is too strict to be
useful: a run that reset the panel paints the same geometry in other colours.
So colour fields are reported apart from the rest. Everything else -- ids,
kinds, point counts, the geometry, depth and opacity tracks, flags -- must
match exactly.
"""

import json
import sys

COLOUR = {"c", "oc"}


def payload(path):
    text = open(path, encoding="utf-8", errors="replace").read()
    i = text.index("<![CDATA[") + 9
    return json.loads(text[i:text.index("]]>", i)])


def main(old_path, new_path):
    old, new = payload(old_path), payload(new_path)
    a = {e.get("id", i): e for i, e in enumerate(old["els"])}
    b = {e.get("id", i): e for i, e in enumerate(new["els"])}
    only_old, only_new = sorted(set(a) - set(b), key=str), sorted(set(b) - set(a), key=str)
    shape, colour = [], []
    for key in sorted(set(a) & set(b), key=str):
        ea, eb = a[key], b[key]
        if old["groups"][ea["g"]] != new["groups"][eb["g"]]:
            shape.append((key, "group"))
            continue
        fields = (set(ea) | set(eb)) - {"g"}
        bad = sorted(f for f in fields - COLOUR if ea.get(f) != eb.get(f))
        if bad:
            shape.append((key, ",".join(bad)))
        if any(ea.get(f) != eb.get(f) for f in COLOUR):
            colour.append(key)
    same_order = [e.get("id") for e in old["els"]] == [e.get("id") for e in new["els"]]
    print(f"[compare] {len(a)} -> {len(b)} elements; only before {len(only_old)}, "
          f"only after {len(only_new)}; geometry/depth/flags differ on {len(shape)}; "
          f"colour differs on {len(colour)}; file order {'same' if same_order else 'DIFFERENT'}")
    for key, why in shape[:10]:
        print(f"    {key}: {why}")
    for key in (only_old[:5] + only_new[:5]):
        print(f"    unmatched {key}")
    identical = not (only_old or only_new or shape) and same_order
    print("[compare]", "IDENTICAL DRAWING" if identical else "DIFFERENT")
    return 0 if identical else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
