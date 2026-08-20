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

Both choices are wrong, because the cycle is real. They are not equally wrong.
Violating the assertion lets a face cover the inner half of its own edge --
measured at 47-50% of the edge's pixels, so the line reads thinner. Violating
the occlusion constraint draws a whole line where there should be none. So the
assertion is now the *first* thing spent, not the last. This file pins that
answer, and pins the other half of it: a cycle is still needed before anything
is given up at all.
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
check("the disk hides the edge that is behind it",
      rank["edge"] > rank["disk"],
      f"edge={rank['edge']} disk={rank['disk']}"
      + ("" if rank["edge"] > rank["disk"] else "  <- stray line across the disk"))
check("the face stays in front of the disk",
      rank["face"] < rank["disk"], f"face={rank['face']} disk={rank['disk']}")
check("something had to give, and it was the assertion",
      stats["assertions_lost"] == 1, f"assertions_lost={stats['assertions_lost']}")

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

# --- the policy has to be stated, not inferred ----------------------------
# Assertions keep the full depth span as their weight. That no longer decides
# assertion-versus-measured -- the explicit sort key does -- but it still orders
# assertions among themselves, so the best-supported coincidence is spent last.
src = open(os.path.join(ADDON, "order.py"), encoding="utf-8").read()
check("assertions are still weighted at the full span",
      "add_edge(i, j, span)" in src)
check("...and are no longer excluded from the drop pool outright",
      "not in asserted] or live" not in src)
# Weight alone does not express the preference: assertions weigh `span`, so
# they sort last and the drop slice only reaches one when a tenth of the
# component runs past every measured edge. That is component size, not policy,
# and it leaves the three-element cycle above broken. The ordering has to be
# explicit.
check("the drop order is explicit, not left to the weight sort",
      "sorted(live, key=lambda e: ((e[1], e[2]) not in asserted" in src)

print()
print(f"{len(fails)} failure(s)" + (": " + ", ".join(fails) if fails else ""))
raise SystemExit(1 if fails else 0)
