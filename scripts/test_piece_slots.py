# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""A merged fill's subpath slots: a piece keeps its slot across a gap, and an
absent piece waits where it is, not across the part.

Found on the Apollo-Soyuz docking module (dock_001/m, three rings, two pieces in
most frames): a ring swapped slots and was collapsed onto ring 1's far end, and
between keyframes it swept a long wedge across the part.

    python scripts/test_piece_slots.py
"""
import numpy as np

import _addon

_addon.stub_blender()
bake = _addon.load("bake")

fails = []


def check(label, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {label}  {detail}")
    if not cond:
        fails.append(label)


def piece(verts):
    v = np.asarray(verts)
    return (v.copy(), v, True)


BODY = list(range(0, 10))
LEFT = list(range(100, 106))
RIGHT = list(range(200, 206))

# Frame 0: body + left + right fill three slots.
slots, held = bake._match_pieces([], [piece(BODY), piece(LEFT), piece(RIGHT)], 3)
check("first frame fills slots in order", slots == [0, 1, 2], str(slots))

# Frame 1: the left piece goes behind the body.
slots, held = bake._match_pieces(held, [piece(BODY), piece(RIGHT)], 3)
check("right piece stays in its own slot while left is gone", slots == [0, None, 1], str(slots))

# Frame 2: the left piece comes back -- listed first this time.
slots, held = bake._match_pieces(held, [piece(LEFT), piece(BODY), piece(RIGHT)], 3)
check("a returning piece returns to the slot it left", slots == [1, 0, 2], str(slots))

# Frame 3: right goes; then frame 4 a brand-new piece (no shared vertices).
slots, held = bake._match_pieces(held, [piece(BODY), piece(LEFT)], 3)
check("second disappearance keeps both others put", slots == [0, 1, None], str(slots))

# --- where an absent slot waits -------------------------------------------
parts = [3, 2, 2]


def frame(body, a, b):
    return [c for p in (body + a + b) for c in p]


B = [(0, 0), (10, 0), (10, 10)]
A0 = [(50, 50), (51, 50)]
far = [(0, 0), (0, 0)]            # placeholder the sampler writes today
geo = {
    0: frame(B, A0, [(90, 90), (91, 90)]),
    1: frame(B, far, [(90, 90), (91, 90)]),      # slot 1 absent
    2: frame(B, far, [(90, 90), (91, 90)]),      # slot 1 absent
    3: frame(B, [(60, 60), (61, 60)], far),      # slot 1 back; slot 2 absent
}
present = {0: [True, True, True], 1: [True, False, True],
           2: [True, False, True], 3: [True, True, False]}
geo_list = [geo[i] for i in range(4)]
moved = bake._settle_absent_pieces(geo_list, parts, present)
check("absent slots are placed", moved == 3, str(moved))
check("absent frame 1 waits where slot 1 was (frame 0, nearer)",
      geo_list[1][6:10] == [50, 50, 50, 50], str(geo_list[1][6:10]))
check("absent frame 2 waits where slot 1 comes back (frame 3, nearer)",
      geo_list[2][6:10] == [60, 60, 60, 60], str(geo_list[2][6:10]))
check("absent slot 2 at frame 3 waits where it was at frame 2",
      geo_list[3][10:14] == [90, 90, 90, 90], str(geo_list[3][10:14]))
check("present rings untouched", geo_list[0] == frame(B, A0, [(90, 90), (91, 90)]))

# The old placeholder would have swept the ring across the part: frame 1 -> 3.
old_sweep = np.hypot(60 - 0, 60 - 0)
new_sweep = np.hypot(60 - geo_list[2][6], 60 - geo_list[2][7])
check("the ring no longer travels across the part between keyframes",
      new_sweep < 1e-9 < old_sweep, f"old {old_sweep:.1f}px, new {new_sweep:.1f}px")

tie = [frame(B, A0, far), frame(B, far, far), frame(B, [(70, 70), (71, 70)], far)]
bake._settle_absent_pieces(tie, [3, 2, 2], {0: [1, 1, 0], 1: [1, 0, 0], 2: [1, 1, 0]})
check("a tie takes the earlier frame: a vanishing piece shrinks where it was",
      tie[1][6:8] == [50, 50], str(tie[1][6:8]))

print(f"\n{len(fails)} failure(s)")
raise SystemExit(1 if fails else 0)
