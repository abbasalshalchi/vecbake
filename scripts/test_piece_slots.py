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
# Three subpaths: body (3 points), slot 1 and slot 2 (2 points each). The mesh
# has vertices 0..9; slot 1's piece is anchored on vertex 5, slot 2's on 8.
parts = [3, 2, 2]


def positions_moving(fi):
    """Every vertex slides 100 px right per frame: the part moves."""
    xy = np.zeros((10, 2))
    xy[:, 0] = np.arange(10) * 10 + fi * 100
    xy[:, 1] = 7
    return xy


def flat(fi):
    return [0.0] * (sum(parts) * 2)


geo = [flat(fi) for fi in range(4)]
present = {0: [0, 5, 8], 1: [0, None, 8], 2: [0, None, 8], 3: [0, 5, None]}
moved = bake._settle_absent_pieces(geo, parts, present, positions_moving)
check("absent slots are placed", moved == 3, str(moved))
check("frame 1: slot 1 waits on its own vertex (5), where it is in frame 1",
      geo[1][6:10] == [150.0, 7.0, 150.0, 7.0], str(geo[1][6:10]))
check("frame 2: slot 1 waits on vertex 5 where it is in frame 2, not where it was",
      geo[2][6:10] == [250.0, 7.0, 250.0, 7.0], str(geo[2][6:10]))
check("frame 3: slot 2 waits on vertex 8 as projected in frame 3",
      geo[3][10:14] == [380.0, 7.0, 380.0, 7.0], str(geo[3][10:14]))
check("present slots untouched", geo[0] == flat(0) and geo[3][6:10] == [0.0] * 4)

# The first version parked on the nearest frame's screen point and so fell
# behind a moving part: fifty frames of that was 253 px on the Voyager.
lag = abs(geo[2][6] - (5 * 10 + 2 * 100))
check("the parked point moves with the part", lag < 1e-9, f"{lag:.1f}px behind")

tie = [flat(0), flat(1), flat(2)]
bake._settle_absent_pieces(tie, [3, 2, 2], {0: [0, 5, None], 1: [0, None, None], 2: [0, 6, None]},
                           positions_moving)
check("a tie takes the earlier frame's piece: a vanishing piece shrinks where it was",
      tie[1][6:8] == [150.0, 7.0], str(tie[1][6:8]))

check("a frame with no positions is left alone",
      bake._settle_absent_pieces([flat(0), flat(1)], parts,
                                 {0: [0, 5, 8], 1: [0, None, 8]}, lambda fi: None) == 0)

print(f"\n{len(fails)} failure(s)")
raise SystemExit(1 if fails else 0)
