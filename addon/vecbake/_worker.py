# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""One ordering worker. Run by `parallel.py`, never imported.

    python.exe _worker.py <job folder> [worker id]

Claims frames from the folder one at a time until none are left, and writes
each frame's ranks back beside them. Claiming rather than being assigned a
share up front is deliberate: the cost of a frame varies four-fold across an
orbit, so a fixed split leaves cores idle while one worker finishes the
expensive half of the clip alone.

This runs under plain Python, with no Blender in the process at all -- which
is possible because `order.py` imports nothing but `heapq` and `numpy`. Keep
it that way: a `bpy` import anywhere in that module's chain would strand every
worker here at startup, and the pool would silently degrade to doing the whole
pass in the parent.
"""

import os

# Before numpy is imported, and so before its backends read them. Each worker
# already owns a core; letting a BLAS pool open eleven more per process turns
# a full machine into a thrashing one. The arrays here are a few dozen points,
# so there is nothing to gain from threading them anyway.
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
             "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_var, "1")

import glob            # noqa: E402
import importlib       # noqa: E402
import pickle          # noqa: E402
import sys             # noqa: E402
import time            # noqa: E402
import traceback       # noqa: E402
import types           # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


PACKAGE = "vecbake_worker_pkg"


def load(name):
    """Import one add-on module without importing the add-on.

    It cannot be imported by its real name -- the extension is installed as
    `bl_ext.user_default.vecbake`, and that package's `__init__` pulls in bpy,
    which does not exist here. But the modules cannot be loaded loose either:
    `occlude` does `from .ir import FILL`, and a relative import needs a parent
    package to be relative *to*.

    So stand one up: an empty module whose `__path__` is this folder. Its
    submodules then import each other exactly as they do inside Blender, and
    nothing has to be rewritten to run out here.
    """
    if PACKAGE not in sys.modules:
        pkg = types.ModuleType(PACKAGE)
        pkg.__path__ = [HERE]
        sys.modules[PACKAGE] = pkg
    return importlib.import_module(f"{PACKAGE}.{name}")


def claim(folder, start=0):
    """Take ownership of one job file, or return None when none are left.

    The rename is the claim. It is atomic on every platform this runs on, so
    exactly one worker can succeed on a given file and the loser simply moves
    on -- no lock file, no directory to keep consistent, nothing to clean up
    if a worker dies holding one.
    """
    jobs = sorted(glob.glob(os.path.join(folder, "job_*.pkl")))
    if not jobs:
        return None
    # Start each worker at a different point in the list so they are not all
    # contending for the same file on every pass.
    order_ = jobs[start % len(jobs):] + jobs[:start % len(jobs)]
    for path in order_:
        mine = f"{path}.{os.getpid()}"
        try:
            os.rename(path, mine)
        except OSError:
            continue                      # claimed by someone else
        return mine
    return None


def run_order(job):
    order = load("order")
    ranks = order.resolve(job["items"], job["ortho"])
    return {"ranks": ranks, "stats": dict(order.LAST_STATS)}


def run_occlude(job):
    occlude = load("occlude")
    seen, hidden = occlude.frame_coverage(job["live"], job["grid"])
    return {"seen": seen, "hidden": hidden}


KINDS = {"order": run_order, "occlude": run_occlude}


def main():
    folder = sys.argv[1]
    wid = int(sys.argv[2]) if len(sys.argv) > 2 else 0

    # The parent writes every job before it writes `ready`, so waiting on that
    # one file is enough to know the whole set is there.
    deadline = time.time() + 30.0
    while not os.path.exists(os.path.join(folder, "ready")):
        if time.time() > deadline:
            print("gave up waiting for the job list", file=sys.stderr)
            return 1
        time.sleep(0.01)

    handled = 0
    while True:
        path = claim(folder, wid + handled)
        if path is None:
            break
        fi = int(os.path.basename(path).split("_")[1].split(".")[0])
        try:
            with open(path, "rb") as fh:
                job = pickle.load(fh)
            payload = KINDS[job.get("kind", "order")](job)
        except Exception:
            # Reported rather than raised. The parent redoes this frame itself,
            # and a worker that keeps going still earns its core on the rest.
            payload = {"error": traceback.format_exc()}
            print(f"frame {fi} failed:\n{payload['error']}", file=sys.stderr)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

        # Written aside and renamed into place, so the parent never reads a
        # half-written result: the file appears complete or not at all.
        final = os.path.join(folder, "res_%04d.pkl" % fi)
        scratch = f"{final}.{os.getpid()}.tmp"
        with open(scratch, "wb") as fh:
            pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(scratch, final)
        handled += 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
