"""A cycle has to give something up; give up the cheapest thing on screen.

    "<blender>/5.1/python/bin/python.exe" scripts/test_order_cycle.py

resolve() asserts that an edge lies on the faces it borders, so a face can
never paint over its own edge. The assertion is true, and it used to be
unbreakable: cycle breaking drew only from constraints that sampling had
measured, on the reasoning that every cycle through an assertion also runs
through a stroke->fill edge that could go instead.

It does. The trouble is what that edge is holding up.

A face raked steeply away from the camera spans a lot of depth. It can measure
as being well in front of something that its own far edge is behind. Then:

    edge   -> disk    the disk hides the edge          (measured, small gap)
    disk   -> face    the face is in front of the disk (measured, large gap)
    face   -> edge    the edge lies on the face        (asserted, unbreakable)

is a cycle, and with the assertion protected the breaker takes the weakest
measured constraint -- the first one -- so the edge is painted across a disk
that covers it. That is the Voyager probe's stray line: bus/f8 spans 1.71 units
of depth, measures 0.778 in front of the golden record over 274 samples, and
drags its far edge bus/e14 (0.046 *behind* the disk) in front along with it.

Spending the assertion first was tried, and reverted. In a still frame it looks
like the cheaper error: the face covers the inner half of its own edge, 47-50%
of its pixels, so the line just reads thinner. In motion it is worse than what
it fixes. Whether the cycle exists depends on the frame, so the same edge loses
the assertion in some frames and keeps it in others, and its width alternates --
18 edges of 196 on the probe across 14 frames. Unprotecting only the assertions
the measured graph already contradicts halves that, to 11; nothing reaches zero,
because an assertion broken per frame is a width that changes per frame.

So the assertion stays protected and the stray line stays with it. This file
pins that, and pins why: not because the ordering is right, but because both
answers are wrong and this is the one that holds still. The real fix is to stop
a crossing line running through the middle of an element at all -- an element
whose depth range spans the thing crossing it cannot be ordered against it by
one scalar, whichever way the tie is broken.
"""

import importlib.util
import os
import sys
import types

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON = os.path.join(ROOT, "addon", "vecbake")


def _load(name):
    if "_vb" not in sys.modules:
        pkg = types.ModuleType("_vb")
        pkg.__path__ = [ADDON]
        sys.modules["_vb"] = pkg
    spec = importlib.util.spec_from_file_location(
        f"_vb.{name}", os.path.join(ADDON, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"_vb.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


order = _load("order")
fails = []


def check(label, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {label}{'  ' + detail if detail else ''}")
    if not cond:
        fails.append(label)


def quad(x0, y0, x1, y1, depths):
    return dict(pts=np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=float),
                depths=np.array(depths, dtype=float))


# --- the probe's geometry, reduced to the three elements that matter -------
# `face` is raked away from the camera: its near edge is at depth 9.2 and its
# far edge at 10.9, so it sweeps 1.7 units the way bus/f8 does. `disk` lies
# across the far part of it at 10.70-10.85. Over their shared region the face
# averages nearer (10.33 at the top of the overlap against the disk's 10.70),
# so it measures as being in front -- but along y=300 the disk at 10.82 is
# nearer than the face at 10.9. `edge` is the face's far boundary, and it is
# behind the disk there.
face = quad(0, 0, 300, 300, [9.2, 9.2, 10.9, 10.9])
face.update(key="face", kind=order.FILL_KIND, seed=10.05)

disk = quad(80, 200, 260, 320, [10.70, 10.70, 10.85, 10.85])
disk.update(key="disk", kind=order.FILL_KIND, seed=10.775)

# seeds an edge inherits from the nearest face it borders, so it is well in
# front of where the edge itself actually is -- exactly as bake.py builds it
edge = dict(key="edge", kind=0, seed=10.05,
            pts=np.array([[0.0, 300.0], [300.0, 300.0]]),
            depths=np.array([10.9, 10.9]),
            on_faces=("face",))

items = [face, disk, edge]
res = order.resolve([dict(it) for it in items], is_ortho=True)
rank = {it["key"]: res[i] for i, it in enumerate(items)}
stats = dict(order.LAST_STATS)

# larger value = painted earlier = further back
check("the cycle is detected at all", stats["cycle_breaks"] >= 1,
      f"cycle_breaks={stats['cycle_breaks']}")
check("the assertion survives the break, so the edge keeps its full width",
      stats["assertions_lost"] == 0, f"assertions_lost={stats['assertions_lost']}")
check("the edge stays with its own face",
      rank["edge"] < rank["face"], f"edge={rank['edge']} face={rank['face']}")
# And the cost of that, stated rather than hidden: the edge is painted over the
# disk that covers it. Pinned so the trade is visible if anyone revisits it.
check("...at the price of the stray line, which is the known trade",
      rank["edge"] < rank["disk"],
      f"edge={rank['edge']} disk={rank['disk']} -- the artifact this cannot fix")

# --- nothing is spent without a cycle -------------------------------------
# Assertions go first *within a cycle*. Outside one they are untouched, which
# is the whole reason the edge-on-face guarantee still holds for the 299 of 306
# assertions on the probe that are not caught in a contradiction. Two fills that
# plainly overlap, plus an edge on one of them, and no contradiction anywhere:
# nothing should be dropped.
a = quad(0, 0, 100, 100, [5.0, 5.0, 5.0, 5.0])
a.update(key="a", kind=order.FILL_KIND, seed=5.0)
b = quad(50, 50, 150, 150, [8.0, 8.0, 8.0, 8.0])
b.update(key="b", kind=order.FILL_KIND, seed=8.0)
ea = dict(key="ea", kind=0, seed=5.0, pts=np.array([[0.0, 100.0], [100.0, 100.0]]),
          depths=np.array([5.0, 5.0]), on_faces=("a",))
clean = [a, b, ea]
res2 = order.resolve([dict(it) for it in clean], is_ortho=True)
r2 = {it["key"]: res2[i] for i, it in enumerate(clean)}
s2 = dict(order.LAST_STATS)

check("a scene with no contradiction drops nothing",
      s2["constraints_dropped"] == 0, f"dropped={s2['constraints_dropped']}")
check("...and keeps every assertion",
      s2["assertions_lost"] == 0, f"lost={s2['assertions_lost']}")
check("...with the edge still over its own face",
      r2["ea"] < r2["a"], f"ea={r2['ea']} a={r2['a']}")
check("...and the nearer fill still in front",
      r2["a"] < r2["b"], f"a={r2['a']} b={r2['b']}")

# --- the policy has to stay stated ----------------------------------------
src = open(os.path.join(ADDON, "order.py"), encoding="utf-8").read()
check("assertions are weighted at the full span", "add_edge(i, j, span)" in src)
check("...and are drawn from last, so measured evidence is spent first",
      "not in asserted] or live" in src)

print()
print(f"{len(fails)} failure(s)" + (": " + ", ".join(fails) if fails else ""))
raise SystemExit(1 if fails else 0)
