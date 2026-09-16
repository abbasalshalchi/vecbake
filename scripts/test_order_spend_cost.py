# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""A cycle gives up the pair that paints the least wrong, not the one with the smallest gap.

Found on Apollo-Soyuz by the ray-cast audit: parts that look see-through. The
service-module face apollo_003/f5 is painted over the mount apollo_013 in every
frame (210 sampled px), apollo_003/f1 over apollo_002 (195), apollo_002/f10 over
apollo_003 (183). Each time the ordering had the right constraint and the cycle
breaker spent it, and each cycle has the same shape:

    fill A  under its own edge e     asserted
    edge e  behind fill B            measured where the line is
    fill B  behind fill A            measured where the fills overlap   <- spent

Both measured facts are true, in different places: the geometry crosses, and
one scalar per element cannot say so. What differs is the price of breaking each, which is the screen the pair shares:
area for two fills, length x width for a line inside a fill. Breaking the fill
pair paints a whole surface over a part. Breaking the line pair draws a line over
a surface that should hide it -- usually cheaper, but not when the line is a long
chain, which is why the rule is the price and not the kind.

    python scripts/test_order_spend_cost.py
"""
import numpy as np

import _addon

_addon.stub_blender()
order = _addon.load("order")

fails = []


def check(label, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {label}  {detail}")
    if not cond:
        fails.append(label)


def quad(key, x0, y0, x1, y1, depths, seed):
    return dict(key=key, kind=order.FILL_KIND, seed=seed,
                pts=np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=float),
                depths=np.array(depths, dtype=float))


# The mount and the hull, reduced. `hull` is raked (depth 9 at the top, 11 at the
# bottom). `mount` sits on it at 9.95 -- in front of the hull where they overlap
# (the hull is 10.0 at the mount's centre) -- but its base rim `rim` runs along
# y = 60, where the hull is at 10.2 and the rim, sunk into it, at 10.4: the
# line pair is 0.2 apart, the fill pair 0.05, as on apollo_018.013 (0.133, 0.060).
hull = quad("hull", 0, 0, 100, 100, [9.0, 9.0, 11.0, 11.0], seed=10.0)
mount = quad("mount", 40, 40, 60, 60, [9.95, 9.95, 9.95, 9.95], seed=9.95)
# A merged part is read off its front surface, and its depth range comes from
# every vertex -- the sunk rim included, as bake._surface_probe keeps them.
mount["tris"] = (np.array([[40., 60., 60.], [40., 60., 40.]]),
                 np.array([[40., 40., 60.], [40., 60., 60.]]),
                 np.array([[9.95, 9.95, 9.95], [9.95, 9.95, 9.95]]))
mount["probe"] = (np.array([40., 60., 60., 40., 50., 50.]),
                  np.array([40., 40., 60., 60., 50., 60.]),
                  np.array([9.95, 9.95, 9.95, 9.95, 9.9, 10.45]))
rim = dict(key="rim", kind=0, seed=9.95, pts=np.array([[40.0, 60.0], [60.0, 60.0]]),
           depths=np.array([10.4, 10.4]), on_faces=("mount",))

items = [hull, mount, rim]
res = order.resolve([dict(it) for it in items], is_ortho=True)
rank = {it["key"]: res[i] for i, it in enumerate(items)}
stats = dict(order.LAST_STATS)

# Larger rank = painted earlier = further back.
check("the cycle is there", stats["cycle_breaks"] >= 1, f"breaks {stats['cycle_breaks']}")
check("the mount is painted over the hull, not the hull over the mount",
      rank["mount"] < rank["hull"], f"mount {rank['mount']:.0f} hull {rank['hull']:.0f}")
check("the rim stays on its own fill", rank["rim"] < rank["mount"],
      f"rim {rank['rim']:.0f} mount {rank['mount']:.0f}")
check("no assertion spent", stats["assertions_lost"] == 0)

# A line is not always the cheap side. On the Voyager, part_043.001's chained
# outline c3_0 runs a long way across part_003.001/f623; spending that pair drew
# the chain over the face in all 60 frames (335 sampled px), where the fill pair
# the old breaker spent instead overlapped by a sliver. What a break costs is the
# screen the pair shares: area for two fills, length x width for a line.
# Same cycle shape, but here the rim is a long chain and the mount and hull
# barely touch on screen.
hull2 = quad("hull", 0, 0, 400, 100, [9.0, 9.0, 11.0, 11.0], seed=10.0)
tab = quad("tab", 396, 46, 402, 52, [9.95, 9.95, 9.95, 9.95], seed=9.95)
tab["tris"] = (np.array([[396., 402., 402.], [396., 402., 396.]]),
               np.array([[46., 46., 52.], [46., 52., 52.]]),
               np.array([[9.95, 9.95, 9.95], [9.95, 9.95, 9.95]]))
tab["probe"] = (np.array([396., 402., 402., 396., 0., 399.]),
                np.array([46., 46., 52., 52., 60., 49.]),
                np.array([9.95, 9.95, 9.95, 9.95, 10.4, 9.9]))
chain = dict(key="chain", kind=0, seed=9.95, width=1.6,
             pts=np.array([[0.0, 60.0], [399.0, 60.0], [399.0, 49.0]]),
             depths=np.array([10.4, 10.4, 9.95]), on_faces=("tab",))
items2 = [hull2, tab, chain]
res = order.resolve([dict(it) for it in items2], is_ortho=True)
rank2 = {it["key"]: res[i] for i, it in enumerate(items2)}
check("a long chain is kept under the hull; the sliver of fill pair is spent",
      rank2["chain"] > rank2["hull"],
      f"chain {rank2['chain']:.0f} hull {rank2['hull']:.0f} tab {rank2['tab']:.0f}; "
      f"breaks {order.LAST_STATS['cycle_breaks']}")

# With no stroke in the cycle, fill pairs are still spent weakest first.
a = quad("a", 0, 0, 20, 10, [0.7, 1.0, 1.0, 0.7], seed=0.85)
b = quad("b", 10, 0, 30, 10, [1.02, 1.32, 1.32, 1.02], seed=1.17)
c = quad("c", 0, 2, 30, 8, [0.24, 1.77, 1.77, 0.24], seed=1.0)
res = order.resolve([dict(it) for it in (a, b, c)], is_ortho=True)
check("fills-only cycle still resolves", len(set(res)) == 3)

print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILED: ' + ', '.join(fails)}")
raise SystemExit(1 if fails else 0)
