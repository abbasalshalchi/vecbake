# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""Spread the two per-frame passes of a bake across worker processes.

`order.resolve()` is the whole cost of a dense bake. On the Voyager probe --
303 parts, 8544 elements drawn per frame -- it took 3719s of a 4012s run: 93%
of the wall clock, against 82s for sampling every frame and 50s for writing
both files. Culling hidden fills is the only other phase worth a pool, at
146s; sampling has to stay in Blender, since it evaluates the depsgraph a
frame at a time, and writing the files is 50s of string building.

It parallelises about as cleanly as anything ever does. Each frame is resolved
from that frame's items alone (`bake.py`), the function reads no module state
and writes none but `LAST_STATS`, and it hands back one float per item. So a
worker needs the frame in and a list of floats out, and nothing has to be
merged.

Threads would not do it. The pair loop is Python-level -- set inserts, dict
writes, `_shared_samples` -- over numpy arrays of a few dozen points, so the
per-call overhead dominates and the GIL is held essentially throughout.

**Why not multiprocessing.** Windows has no fork, so its children are spawned,
and a spawned child rebuilds `__main__` by running the parent's main script
again under the name `__mp_main__`. Under `blender --python bake_it.py` that
script *is* the bake, so every worker would start its own -- recursively. The
guard is a `if __name__ == "__main__":` block in a file the add-on does not
own and cannot fix. A pool of plain subprocesses has no such contract: the
worker is named explicitly, so nothing else can ever be run by accident.

Determinism is a requirement, not a hope: the ranks a bake emits must not
depend on how many cores ran it. `resolve` is pure and its only unordered
containers are sets of small ints, whose iteration order is fixed by value, so
the same frame yields identical ranks anywhere. `scripts/test_parallel.py`
asserts it against real frames.

Every failure here is recoverable, and recovers by doing the work in-process.
A missing interpreter, a worker that dies, a frame that raises -- all of them
end in the same place as if the pool had never been asked for.
"""

from __future__ import annotations

import glob
import os
import pickle
import shutil
import subprocess
import sys
import tempfile
import time

from . import occlude
from . import order

# One frame's results, written by the worker and picked up here. The worker
# writes to a scratch name and renames, so a file that exists is a file that
# is complete -- the parent never has to guess whether a write is still
# running.
JOB = "job_%04d.pkl"
RESULT = "res_%04d.pkl"

# Windows opens a console for every child unless told not to. Eleven of them
# flashing up in front of an artist mid-bake is not acceptable.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def worker_python() -> str | None:
    """Blender's bundled interpreter, or None if it cannot be found.

    Not `sys.executable`: that is the bundled Python in Blender 5.x but
    blender.exe in earlier versions, and launching *that* starts a second
    Blender rather than a worker. Deriving it from `sys.prefix` is what makes
    the difference explicit rather than version-dependent.
    """
    binary = os.path.join(sys.prefix, "bin")
    names = ["python.exe", "python3.exe"] if os.name == "nt" else ["python3", "python"]
    for name in names:
        cand = os.path.join(binary, name)
        if os.path.exists(cand):
            return cand
    for cand in sorted(glob.glob(os.path.join(binary, "python3.*"))):
        if os.path.isfile(cand) and not cand.endswith((".dll", ".lib", ".a")):
            return cand
    # A last resort worth taking: if sys.executable is a Python rather than a
    # Blender, it will do. Checked by name, since that is all there is to go on.
    exe = sys.executable or ""
    if os.path.basename(exe).lower().startswith("python"):
        return exe
    return None


def worker_count(jobs: int, requested: int = 0) -> int:
    """How many workers to run. `requested` of 0 means decide from the machine.

    One core is left for the parent, which is not idle while this runs -- it
    polls, unpickles every result and writes it into the slots.
    """
    if requested > 0:
        return max(1, min(jobs, requested))
    cores = os.cpu_count() or 2
    return max(1, min(jobs, cores - 1))


def _spawn(python, script, folder, count):
    procs = []
    for wid in range(count):
        log = open(os.path.join(folder, f"worker_{wid}.log"), "wb")
        procs.append((subprocess.Popen(
            [python, script, folder, str(wid)],
            stdout=log, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
        ), log))
    return procs


def _read_logs(folder, limit=2000):
    out = []
    for path in sorted(glob.glob(os.path.join(folder, "worker_*.log"))):
        try:
            with open(path, "rb") as fh:
                text = fh.read().decode("utf-8", "replace").strip()
        except OSError:
            continue
        if text:
            out.append(f"{os.path.basename(path)}: {text[-limit:]}")
    return "\n".join(out)


def _drive(keys, build_job, do_here, workers=0, report=None, tag="work"):
    """Run one job per key across a pool, yielding `(key, payload)` as they land.

    `build_job(key)` returns a picklable dict for a worker; `do_here(key)`
    computes the same answer in this process. Both must agree exactly -- one is
    used for the frame the parent keeps, and for anything the pool could not
    finish -- which is why every caller derives them from the same function.

    Keys are frame indices, and the order they come back in is the order the
    workers happen to finish. Callers must not care: on the Voyager probe a
    frame ranges from 18s to 76s, and letting the cheap ones land early is
    exactly what keeps the slow ones from being the whole clip's tail.
    """
    keys = list(keys)
    if not keys:
        return
    head, rest = keys[0], keys[1:]

    python = worker_python()
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_worker.py")
    count = worker_count(len(rest), workers)

    # Nothing to run in, nothing to run on, or nothing left to hand out.
    if python is None or count < 2 or not os.path.exists(script):
        why = ("no bundled interpreter" if python is None else
               "worker script missing" if not os.path.exists(script) else
               "single worker")
        if report:
            report(f"{tag} in-process ({why})")
        for key in keys:
            yield key, do_here(key)
        return

    folder = tempfile.mkdtemp(prefix=f"vecbake_{tag}_")
    done = set()
    try:
        # Workers first, so their interpreters boot -- about 0.7s each, mostly
        # importing numpy -- while the jobs are still being written. They block
        # on `ready`, which is written last precisely so none of them can see a
        # half-written job list.
        try:
            procs = _spawn(python, script, folder, count)
        except OSError as exc:
            if report:
                report(f"{tag} in-process (workers would not start: {exc})")
            for key in keys:
                yield key, do_here(key)
            return
        try:
            for key in rest:
                with open(os.path.join(folder, JOB % key), "wb") as fh:
                    pickle.dump(build_job(key), fh, protocol=pickle.HIGHEST_PROTOCOL)
            open(os.path.join(folder, "ready"), "wb").close()
            if report:
                report(f"{tag}: {count} workers")

            # One job is kept back and done right here, while the workers start
            # up and claim the rest. It costs nothing -- the parent has only
            # polling to do otherwise -- and on a dense scene it is a minute of
            # work that would have been spent waiting.
            #
            # This is also what makes the pool safe to start unconditionally.
            # Deciding first -- timing one frame in-process and only then
            # spawning -- sounds cheaper and is not: it puts a full frame of
            # pure serial time in front of every large bake, which is the exact
            # case the pool exists for. Here a bake too small to need workers
            # has merely started a few interpreters that find the work already
            # gone, and one too large gets its first frame free.
            yield head, do_here(head)

            while len(done) < len(rest):
                progressed = False
                for key in rest:
                    if key in done:
                        continue
                    path = os.path.join(folder, RESULT % key)
                    if not os.path.exists(path):
                        continue
                    # The name appearing does not mean the handle is free.
                    # Windows can still be finishing the worker's os.replace
                    # onto it, and an open in that window fails with
                    # PermissionError rather than blocking. It is transient by
                    # construction -- the writer is on its way out -- so leave
                    # the file for the next poll instead of failing the bake.
                    try:
                        with open(path, "rb") as fh:
                            payload = pickle.load(fh)
                    except (PermissionError, OSError, EOFError):
                        continue
                    try:
                        os.remove(path)
                    except OSError:
                        pass              # read already; a stray file is swept
                    done.add(key)
                    progressed = True
                    # A frame that raised in a worker is done here instead.
                    # Losing one is not an option: a frame missing from either
                    # pass is a visible fault in the clip, and a slow bake is
                    # not.
                    yield key, (do_here(key) if payload.get("error") else payload)
                if len(done) == len(rest):
                    break
                if not progressed:
                    # Every worker gone with jobs outstanding: they crashed, or
                    # could not import. Whatever the reason, what is left is
                    # this process's problem now.
                    if all(p.poll() is not None for p, _ in procs):
                        break
                    time.sleep(0.02)
        finally:
            for proc, log in procs:
                if proc.poll() is None:
                    proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                log.close()

        left = [key for key in rest if key not in done]
        if left:
            if report:
                report(f"{tag}: {len(left)} frame(s) in-process after worker "
                       f"failure\n{_read_logs(folder)}")
            for key in left:
                yield key, do_here(key)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def imap_resolve(frame_items, frame_ortho, workers=0, report=None):
    """Yield `(fi, ranks, stats)` per frame, in whatever order they finish.

    The caller writes each frame's ranks into that frame's own slot, so
    completion order does not matter.
    """
    todo = [fi for fi, items in enumerate(frame_items) if items]

    def build(fi):
        return {"kind": "order", "items": frame_items[fi],
                "ortho": bool(frame_ortho[fi])}

    def here(fi):
        ranks = order.resolve(frame_items[fi], frame_ortho[fi])
        return {"ranks": ranks, "stats": dict(order.LAST_STATS)}

    for fi, payload in _drive(todo, build, here, workers, report, "ordering"):
        yield fi, payload["ranks"], payload["stats"]


def cull_hidden(elements, slots, frame_items, settings, nframes, width, height,
                workers=0, report=None):
    """`occlude.apply`, with the per-frame coverage walk spread across processes.

    Second in cost after ordering -- 146s of the Voyager probe's 4012s, which
    is 3.6% of that bake but a fifth of what is left once the ordering pass is
    no longer the whole run.

    The result cannot depend on the split. An element is culled only if it showed
    nothing in *every* frame, so the frames combine as a union of the keys that
    showed and a sum of the ones that did not; both are order-independent, and
    neither is decided until every frame has reported.
    """
    grid = occlude.frame_grid(settings, width, height)
    items = occlude.cullable(elements, settings)
    if not items:
        return {"culled": 0, "hidden": 0, "cells": 0}

    # Built here, not in the workers: sorting a frame near-to-far needs the depth
    # slots, which are the baker's and do not travel.
    live = {fi: occlude.live_elements(items, slots, fi) for fi in range(nframes)}
    todo = [fi for fi in range(nframes) if live[fi]]

    def build(fi):
        return {"kind": "occlude", "live": live[fi], "grid": grid}

    def here(fi):
        showed, count = occlude.frame_coverage(live[fi], grid)
        return {"seen": showed, "hidden": count}

    seen: set = set()
    hidden = 0
    for _fi, payload in _drive(todo, build, here, workers, report, "culling"):
        seen.update(payload["seen"])
        hidden += payload["hidden"]
    return occlude.finish(items, seen, hidden, slots, frame_items, grid)


def merge_stats(rows):
    """Sum the per-frame ordering counters into one summary for the bake.

    Counts add across frames; `elements` is the busiest frame rather than a
    sum, because there is no frame with that many elements in it and reporting
    one would misrepresent the scene.
    """
    total: dict = {}
    for row in rows:
        for key, value in (row or {}).items():
            if key == "elements":
                total[key] = max(total.get(key, 0), value)
            else:
                total[key] = total.get(key, 0) + value
    return total
