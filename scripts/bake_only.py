# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""Bake one scorecard model and write it, with no audit -- for "is it the same drawing".

    blender -b --factory-startup <blend> --python scripts/bake_only.py -- \
        --name <name> --out <dir> [--camera <name>] [--workers N]

Same camera handling and settings as `scorecard.py`, so the file it writes can be
put next to a scorecard run with `compare_bakes.py`. The audit has to order every
frame in its own process; this does not, so `--workers 0` (the default, the
machine's pool) bakes as a panel bake would. A drawing that comes back identical
needs no new audit -- the audit grades the drawing, and it is the same one.
"""

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
_defs = {"__file__": os.path.join(HERE, "scorecard.py"), "__name__": "scorecard_defs"}
_src = open(_defs["__file__"], encoding="utf-8").read()
exec(compile(_src.split("\ndef main():")[0], "scorecard_defs", "exec"), _defs)

import bpy  # noqa: E402

api, emit_lerpa = _defs["api"], _defs["emit_lerpa"]


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--camera")
    ap.add_argument("--workers", type=int, default=0)
    args = ap.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    scene = bpy.context.scene
    if args.camera:
        scene.camera = bpy.data.objects[args.camera]
    camera = _defs["setup_camera"](scene)
    _defs["reset_settings"](scene.vecbake)
    for key, value in _defs["SETTINGS"].items():
        setattr(scene.vecbake, key, value)
    settings = scene.vecbake.to_bake_settings(bpy.context)
    settings.order_workers = args.workers
    started, cpu = time.perf_counter(), time.process_time()
    vec = api.bake_scene(bpy.context, settings)
    wall, cpu = time.perf_counter() - started, time.process_time() - cpu
    path = os.path.join(args.out, f"{args.name}.svg")
    emit_lerpa.write(vec, path, include_ids=settings.include_ids,
                     coord_decimals=settings.effective_coord_decimals(),
                     states=settings.lerpa_states, name=args.name,
                     source=os.path.basename(bpy.data.filepath), pretty=False)
    m = vec.meta
    record = {"name": args.name, "workers": args.workers, "camera": camera,
              "elements": len(vec.elements), "wall_seconds": round(wall, 1),
              "cpu_seconds_this_process": round(cpu, 1),
              "merged_fills": m.get("merged_fills"), "merged_fallback": m.get("merged_fallback"),
              "merged_uncarried": m.get("merged_uncarried"),
              "merged_false_line": m.get("merged_false_line"),
              "order_pool": m.get("order_pool"), "file_bytes": os.path.getsize(path)}
    with open(os.path.join(args.out, "bake_only.json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=1, default=str)
    print("[bake-only]", {k: record[k] for k in ("name", "workers", "elements", "wall_seconds",
                                                 "merged_fills", "order_pool", "file_bytes")},
          flush=True)


try:
    main()
except Exception:
    import traceback
    traceback.print_exc()
sys.stdout.flush()
os._exit(0)
