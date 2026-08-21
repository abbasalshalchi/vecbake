"""The pool must return exactly what resolving in Blender would have.

    "<blender>/5.1/python/bin/python.exe" scripts/test_parallel.py [frames.pkl]

Ordering is 93% of a dense bake -- 3719s of the Voyager probe's 4012s -- and
`order.resolve()` is per-frame, pure, and bpy-free, so it belongs in worker
processes. What must not change is the answer. A rank that depends on how many
cores were free is a bake that cannot be reproduced, and the difference would
show up as elements swapping paint order between one run and the next.

That the answer *can* be identical is not obvious. `resolve` iterates sets
while breaking cycles (`for src in before[node]`, `max(blocked, ...)`), and set
iteration order is only fixed if the hashes are -- which for small ints they
are, since they hash to themselves and no salt applies. So the guarantee is
real, but it rests on those sets holding ints rather than the element keys they
stand for. Keep it that way, and this test keeps passing.

Pass a pickle of `[(items, ortho), ...]` captured from a real bake to run it
against real geometry; without one it builds frames whose costs differ enough
that workers finish out of order, which is the case worth covering anyway.
"""

import copy
import importlib.util
import os
import pickle
import sys
import time
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
parallel = _load("parallel")
fails = []


def check(label, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {label}{'  ' + detail if detail else ''}")
    if not cond:
        fails.append(label)


def synthetic(nframes=8, seed=7):
    """Frames of overlapping quads and lines, deliberately uneven in cost.

    Every frame holds a different number of elements, so the expensive ones
    finish long after the cheap ones and results arrive out of order -- which
    is the ordering the parent has to be indifferent to.
    """
    rng = np.random.default_rng(seed)
    frames = []
    for fi in range(nframes):
        count = 40 + (fi * 37) % 160
        items = []
        for k in range(count):
            x, y = rng.uniform(0, 900, 2)
            d = float(rng.uniform(5.0, 15.0))
            if k % 3:
                w, h = rng.uniform(20, 160, 2)
                pts = np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]])
                items.append({"key": f"f{fi}_{k}", "kind": order.FILL_KIND,
                              "seed": d, "pts": pts,
                              "depths": d + rng.uniform(-0.4, 0.4, 4)})
            else:
                pts = np.array([[x, y], [x + rng.uniform(-200, 200),
                                         y + rng.uniform(-200, 200)]])
                items.append({"key": f"s{fi}_{k}", "kind": 0, "seed": d,
                              "pts": pts, "depths": d + rng.uniform(-0.2, 0.2, 2),
                              "on_faces": ()})
        frames.append((items, True))
    return frames


path = sys.argv[1] if len(sys.argv) > 1 else ""
if path and os.path.exists(path):
    with open(path, "rb") as fh:
        frames = pickle.load(fh)
    print(f"real frames from {os.path.basename(path)}: {len(frames)}, "
          f"{sum(len(f[0]) for f in frames)} items")
else:
    frames = synthetic()
    print(f"synthetic frames: {len(frames)}, {sum(len(f[0]) for f in frames)} items")

frame_items = [f[0] for f in frames]
frame_ortho = [f[1] for f in frames]

# --- the reference: exactly what bake.py used to do ------------------------
t0 = time.time()
serial = {}
serial_stats = []
for fi, items in enumerate(frame_items):
    if items:
        serial[fi] = order.resolve(copy.deepcopy(items), frame_ortho[fi])
        serial_stats.append(dict(order.LAST_STATS))
t_serial = time.time() - t0
print(f"serial: {t_serial:.1f}s")

# --- the pool --------------------------------------------------------------
# A fixed worker count, so the split is the same on every machine this runs on
# and a failure means the pool is wrong rather than the hardware differing.
t0 = time.time()
got = {}
pool_stats = []
arrival = []
for fi, ranks, stats in parallel.imap_resolve(
        copy.deepcopy(frame_items), frame_ortho, workers=4,
        report=lambda msg: print(f"  pool: {msg}")):
    got[fi] = ranks
    pool_stats.append(stats)
    arrival.append(fi)
t_pool = time.time() - t0
print(f"pool:   {t_pool:.1f}s   ({t_serial / max(t_pool, 1e-9):.1f}x)")

check("every frame comes back exactly once",
      sorted(got) == sorted(serial) and len(arrival) == len(set(arrival)),
      f"{len(got)} of {len(serial)}")

worst = None
for fi in sorted(serial):
    if got.get(fi) != serial[fi]:
        worst = fi
        break
check("every rank is identical to resolving in-process", worst is None,
      "" if worst is None else f"frame {worst} differs")

# One frame is always kept back for the parent to resolve while the workers
# boot, so it leads. Everything after it lands as it finishes, and the parent
# must not care in what order.
check("the frame kept back for the parent comes back first",
      arrival[0] == min(serial),
      f"arrived {arrival[:6]}...")
check("the rest are allowed to arrive out of order, and did",
      arrival[1:] != sorted(arrival[1:]) or len(arrival) < 4,
      f"arrived {arrival[:6]}...")

# --- auto must reach the same answer, however it decides to get there ------
notes = []
auto = {fi: r for fi, r, _ in parallel.imap_resolve(
    copy.deepcopy(frame_items), frame_ortho, workers=0, report=notes.append)}
check("auto agrees with resolving in-process", auto == serial,
      "; ".join(notes) or f"{parallel.worker_count(len(frames) - 1)} workers")

# A clip with one frame in it has nothing to hand out: the pool would be all
# startup and no work, and the frame the parent keeps back is the whole job.
alone_frames = [frame_items[0]]
solo = list(parallel.imap_resolve(copy.deepcopy(alone_frames), [frame_ortho[0]],
                                  workers=0, report=notes.append))
check("a one-frame clip never starts a pool",
      any("in-process" in n for n in notes[-1:]), notes[-1] if notes else "(silent)")
check("...and still resolves that frame", len(solo) == 1 and solo[0][1] == serial[0])

# --- one worker means no pool at all, and must still agree -----------------
alone = {fi: r for fi, r, _ in parallel.imap_resolve(
    copy.deepcopy(frame_items), frame_ortho, workers=1)}
check("a single worker resolves in-process and matches", alone == serial)

# --- empty frames are the caller's to count, not the pool's ----------------
holes = copy.deepcopy(frame_items)
holes[0] = []
seen = [fi for fi, _, _ in parallel.imap_resolve(holes, frame_ortho, workers=2)]
check("an empty frame is never dispatched and never returned", 0 not in seen,
      f"{len(seen)} results for {len(holes)} frames")

# --- the summary the panel reports ----------------------------------------
merged = parallel.merge_stats(pool_stats)
ref = parallel.merge_stats(serial_stats)
check("the merged stats match the serial run", merged == ref,
      f"{merged.get('constraints')} constraints, "
      f"{merged.get('constraints_dropped')} dropped")
check("elements reports the busiest frame, not the sum",
      merged.get("elements") == max(s["elements"] for s in serial_stats),
      f"{merged.get('elements')}")

# --- culling: the pool must cull exactly the same fills --------------------
# The union is what makes this safe to split: a fill is removed only if it
# showed nothing in *every* frame, so no frame can decide alone and the order
# the frames report in cannot matter. Build a scene where the answer depends on
# a frame in the middle -- one fill buried for all but one frame, which must
# survive, and one buried throughout, which must not.
ir = _load("ir")
occlude = _load("occlude")
CANVAS = 100


class _Settings:
    occlusion_px = 5.0


def _quad(x0, y0, x1, y1):
    return [x0, y0, x1, y0, x1, y1, x0, y1]


def cull_scene(nframes=9):
    full = _quad(0, 0, CANVAS, CANVAS)
    specs = {"front": [(full, 1.0, 1.0)] * nframes,
             "buried": [(full, 2.0, 1.0)] * nframes,
             # in front of the covering fill in exactly one frame, and so
             # visible in exactly one frame -- which is enough to keep it
             "peeks": [(full, 2.0 if fi != nframes // 2 else 0.5, 1.0)
                       for fi in range(nframes)]}
    elements, slots = [], {}
    for key, rows in specs.items():
        elements.append(ir.Element(key=key, kind=ir.FILL, npts=4, group=key,
                                   color=(1, 1, 1)))
        slots[key] = {"geo": [r[0] for r in rows],
                      "depth": np.array([r[1] for r in rows]),
                      "vis": np.array([r[2] for r in rows])}
    items = [[{"key": k} for k in specs] for _ in range(nframes)]
    return elements, slots, items


els_a, slots_a, items_a = cull_scene()
ref_report = occlude.apply(els_a, slots_a, items_a, _Settings(), 9, CANVAS, CANVAS)

els_b, slots_b, items_b = cull_scene()
cull_notes = []
pool_report = parallel.cull_hidden(els_b, slots_b, items_b, _Settings(), 9,
                                   CANVAS, CANVAS, workers=3,
                                   report=cull_notes.append)

check("culling through the pool reports the same counts",
      pool_report == ref_report, f"{pool_report} vs {ref_report}")
check("...and zeroes the same visibility tracks",
      all(list(slots_a[k]["vis"]) == list(slots_b[k]["vis"]) for k in slots_a),
      "; ".join(f"{k}={slots_b[k]['vis'].max()}" for k in slots_b))
check("...and prunes the same items from the ordering pass",
      [sorted(i["key"] for i in f) for f in items_a]
      == [sorted(i["key"] for i in f) for f in items_b])
check("a fill visible in one frame only survives the split",
      slots_b["peeks"]["vis"].max() > 0.0)
check("a fill buried in every frame is still culled",
      slots_b["buried"]["vis"].max() == 0.0)
check("culling actually used workers", any("worker" in n for n in cull_notes),
      "; ".join(cull_notes))

# --- the pieces the pool is built on --------------------------------------
check("the worker script ships beside the module",
      os.path.exists(os.path.join(ADDON, "_worker.py")))
check("a bundled interpreter is found", parallel.worker_python() is not None,
      str(parallel.worker_python()))
check("worker_python is a python, never blender",
      "blender" not in os.path.basename(parallel.worker_python() or "").lower())
check("auto leaves a core for the parent",
      parallel.worker_count(100) == max(1, (os.cpu_count() or 2) - 1),
      f"{parallel.worker_count(100)} of {os.cpu_count()}")
check("never more workers than frames", parallel.worker_count(3) <= 3)
check("an explicit count is honoured", parallel.worker_count(100, 4) == 4)

# order.py has to stay importable without Blender, or every worker dies at
# startup and the pool quietly degrades to serial.
src = open(os.path.join(ADDON, "order.py"), encoding="utf-8").read()
check("order.py still imports no bpy", "import bpy" not in src)

print()
print(f"{len(fails)} failure(s)" + (": " + ", ".join(fails) if fails else ""))
raise SystemExit(1 if fails else 0)
