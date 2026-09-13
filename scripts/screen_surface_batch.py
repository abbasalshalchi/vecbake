r"""Reproduce surface-depth screening and replay the actual ordering inputs.

Run capture/audit in a separate headless Blender with the Apollo blend loaded:
    blender -b scene.blend --python scripts/screen_surface_batch.py -- \
        capture --out out/surface-batch/capture
    blender -b scene.blend --python scripts/screen_surface_batch.py -- \
        audit --arm split --serial-timing --out out/surface-batch/split

Replay under the bundled standalone Python (no Blender or installed add-on):
    python scripts/screen_surface_batch.py replay --capture out/surface-batch/capture \
        --order scalar=out/surface-batch/order_scalar.py \
        --order batch=addon/vecbake/order.py --out out/surface-batch/replay

The split arm forces only apollo_003 face by face by changing the local
no_merge initializer in memory. It never changes the installed source, blend,
or production rule. Capture stops at the actual post-chain ordering boundary.
Pickles are local measurement inputs: only replay files you trust.
"""

from __future__ import annotations

import argparse
from collections import Counter
import copy
from dataclasses import fields
import hashlib
import importlib.util
import inspect
import json
import math
from pathlib import Path
import pickle
import statistics
import sys
import time
import traceback


ROOT = Path(__file__).resolve().parents[1]
TARGET = "apollo_003"


def sha(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def plain(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(v) for v in value]
    if hasattr(value, "item"):
        return plain(value.item())
    return {"name": getattr(value, "name", str(value)),
            "type": type(value).__name__}


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(plain(value), indent=2, sort_keys=True),
                         encoding="utf-8")
    temporary.replace(path)


def event(out, kind, **values):
    row = {"event": kind, "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           **plain(values)}
    text = json.dumps(row, sort_keys=True)
    with (out / "run.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(text + "\n")
    print("[surface-screen] " + text, flush=True)


def frame_numbers(value):
    result = [int(part) for part in value.split(",")]
    if not result or len(result) != len(set(result)) or min(result) < 0:
        raise argparse.ArgumentTypeError("frames must be distinct nonnegative indices")
    return result


def parser():
    arg = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = arg.add_subparsers(dest="mode", required=True)
    for mode in ("capture", "audit", "select", "bake"):
        run = sub.add_parser(mode)
        run.add_argument("--out", type=Path, required=True)
        run.add_argument("--arm", choices=("triangle", "split", "interleave"), default="triangle")
        run.add_argument("--frames", type=frame_numbers, default=[0, 15, 30])
        run.add_argument("--budget", type=int, default=3000)
        run.add_argument("--serial-timing", action="store_true")
    replay = sub.add_parser("replay")
    replay.add_argument("--out", type=Path, required=True)
    replay.add_argument("--capture", type=Path, required=True)
    replay.add_argument("--frames", type=frame_numbers,
                        help="replay only these captured indices (default: all)")
    replay.add_argument("--order", action="append", required=True,
                        help="label=path/to/order.py; repeat for comparison")
    replay.add_argument("--repeats", type=int, default=2)
    return arg


def configure(scene):
    p = scene.vecbake
    values = dict(use_scene_range=True, frame_step=1, use_scene_res=False,
                  width=1920, height=1080, merge_fills=True, weld_seams=True,
                  edge_mode="SHARP", crease_angle=math.radians(30.0),
                  backface_cull=True, see_through=True, cull_offscreen=True,
                  occlusion_cull=False, occlusion_strokes=False,
                  accurate_order=True, chain_edges=True, tol_px=1.0,
                  dissolve_px=1.0, shading=False, use_material_color=False,
                  material_alpha=False, stroke_source="FIXED", stroke_width=1.6)
    # Remains replayable after the unsupported production threshold is removed.
    if hasattr(p, "split_span_pct"):
        values["split_span_pct"] = 0.0
    for key, value in values.items():
        setattr(p, key, value)
    return {key: plain(getattr(p, key)) for key in values}


def fingerprint(bpy, bake, settings, props):
    scene = bpy.context.scene
    path = Path(bpy.data.filepath).resolve()
    source = Path(bake.__file__).resolve().parent
    files = {str(p.relative_to(source)): sha(p) for p in sorted(source.rglob("*.py"))}
    objects = [{"name": obj.name, "type": obj.type,
                "matrix_world": [list(row) for row in obj.matrix_world],
                "hide_render": obj.hide_render,
                "data": getattr(obj.data, "name", None)}
               for obj in sorted(scene.objects, key=lambda o: o.name)]
    return {"blend": str(path), "blend_sha256": sha(path),
            "blend_bytes": path.stat().st_size,
            "blend_mtime_ns": path.stat().st_mtime_ns,
            "blender": bpy.app.version_string,
            "source_directory": str(source), "source_sha256": files,
            "harness_sha256": sha(__file__), "scene": scene.name,
            "frame_start": scene.frame_start, "frame_end": scene.frame_end,
            "frame_current": scene.frame_current,
            "camera": scene.camera.name if scene.camera else None,
            "objects": objects, "properties": props,
            "settings": {f.name: plain(getattr(settings, f.name)) for f in fields(settings)}}


class CaptureComplete(Exception):
    pass


def blender_run(args):
    import bpy
    from bl_ext.user_default.vecbake import api, audit, bake, order, parallel

    if not bpy.app.background:
        raise RuntimeError("This harness is only for a separate headless Blender process")
    if not bpy.data.filepath or TARGET not in bpy.context.scene.objects:
        raise RuntimeError("Load the Apollo scene containing apollo_003 first")
    if args.budget < 1:
        raise ValueError("budget must be positive")
    props = configure(bpy.context.scene)
    settings = bpy.context.scene.vecbake.to_bake_settings(bpy.context)
    settings.order_workers = 1
    if args.mode == "select" and args.arm != "interleave":
        raise ValueError("select mode requires --arm interleave")
    if args.arm == "interleave":
        # Enable the old collection plumbing, not its percentage criterion.
        # The scoped probe below replaces that criterion with pixel evidence.
        settings.split_span_pct = 50.0
    manifest = {"arm": args.arm, "mode": args.mode, "frames": args.frames,
                "budget": args.budget,
                "fingerprint": fingerprint(bpy, bake, settings, props),
                "captures": [], "serial_resolve": []}
    write_json(args.out / "manifest.json", manifest)
    event(args.out, "start", mode=args.mode, arm=args.arm,
          blend_sha256=manifest["fingerprint"]["blend_sha256"])

    old_iter, old_imap = bake.bake_iter, parallel.imap_resolve
    old_resolve, old_read, old_bake_scene = order.resolve, order._surface_read, api.bake_scene
    old_nearest, old_diagnose = audit._Truth.nearest, audit._diagnose
    old_extent = bake._front_extent
    counts = Counter()
    phase = ["idle"]

    def forced_no_merge():
        counts["no_merge_hook"] += 1
        return {TARGET} if args.arm == "split" else set()

    source = inspect.getsource(old_iter)
    needle = "no_merge: set[str] = set()"
    if source.count(needle) != 1:
        raise RuntimeError("bake_iter no_merge initializer changed; review injection")
    namespace = dict(bake.__dict__)
    namespace["_screen_forced_no_merge"] = forced_no_merge
    injected = source.replace(needle, "no_merge: set[str] = _screen_forced_no_merge()")
    probe = None
    if args.arm == "interleave":
        sys.path.insert(0, str(ROOT / "scripts"))
        from interleave_experiment import SamplingProbe
        manifest["experiment_sha256"] = sha(ROOT / "scripts" / "interleave_experiment.py")
        probe = SamplingProbe(bake, order, settings.width, settings.height,
            report=lambda fi, picks, pairs: event(args.out, "interleave_frame",
                frame=fi, picks=picks, pairs=pairs))
        sample_original = bake._sample_object
        namespace["_sample_object"] = lambda *a, **kw: probe.sample(sample_original, *a, **kw)
        bake._front_extent = probe.surface
        def choose(extent, pct, frame_ortho):
            picks = probe.choose(extent, pct, frame_ortho)
            write_json(args.out / "interleave.json", dict(picks=picks, rows=probe.rows,
                cpu_seconds=probe.cpu_seconds, calls=probe.calls,
                candidates=sorted(n for n,p in probe.plans.items()
                    if p.merge_fill and p.outline_n and p.outline_n >= 3),
                face_counts={n: int(probe.plans[n].nfaces) for n in picks}))
            event(args.out, "interleave_selected", picks=picks, cpu_seconds=probe.cpu_seconds)
            if args.mode == "select":
                raise CaptureComplete()
            return picks
        # The initial depth-share decision precedes the outline gap tests.
        # Defer this experiment until those tests settle P's element identity;
        # otherwise a rejected merge creates a false one-rank contradiction.
        namespace["_deep_bodies"] = lambda extent, pct: []
        namespace["_screen_interleave"] = choose
        needle = "    finally:\n        scene.frame_set(original_frame)"
        if injected.count(needle) != 1:
            raise RuntimeError("Selector injection site changed")
        extra = '''        deep = _screen_interleave(
            {n: [rows.get(fi) for fi in range(len(frames))]
             for n, rows in depth_extent.items()}, settings.split_span_pct, frame_ortho)
        if deep:
            no_merge.update(deep)
            merge_fallback = sorted(set(merge_fallback) | set(deep))
            for name in deep:
                outline_slots.pop(name, None)
                part_slots.pop(name, None)
            judging = False
            restart()
            yield from sample_pass()
'''
        injected = injected.replace(needle, extra + needle)
    exec(compile(injected, str(bake.__file__) + "<screen-no-merge>", "exec"), namespace)
    manifest["injection_sha256"] = hashlib.sha256(injected.encode()).hexdigest()

    def read_spy(item, qx, qy, is_ortho):
        counts["surface_calls_" + phase[0]] += 1
        counts["surface_points_" + phase[0]] += len(qx)
        return old_read(item, qx, qy, is_ortho)

    def resolve_spy(items, is_ortho=False, tol=1e-4):
        counts["resolve_calls_" + phase[0]] += 1
        started = time.perf_counter()
        cpu_start = time.process_time()
        result = old_resolve(items, is_ortho, tol)
        if args.serial_timing and phase[0] == "bake":
            row = {"call": counts["resolve_calls_bake"] - 1,
                   "items": len(items), "seconds": time.perf_counter() - started,
                   "cpu_seconds": time.process_time() - cpu_start,
                   "stats": dict(order.LAST_STATS)}
            manifest["serial_resolve"].append(row)
            event(args.out, "resolve", **row)
        return result

    def imap_spy(frame_items, frame_ortho, workers=0, report=None):
        counts["imap_hook"] += 1
        if workers != 1:
            raise RuntimeError("Capture and audit must resolve in-process")
        if max(args.frames) >= len(frame_items):
            raise RuntimeError("Requested screening frame is outside the baked range")
        for fi in args.frames:
            items = frame_items[fi]
            merged = sum(it["key"] == TARGET + "/m" for it in items)
            target_fills = sum(it["kind"] == order.FILL_KIND
                               and it["key"].split("/")[0] == TARGET for it in items)
            if not items or not target_fills:
                raise RuntimeError(f"Capture frame {fi} has no target fill")
            target_split = args.arm == "split" or (probe is not None and TARGET in probe.picks)
            if merged != (0 if target_split else 1):
                raise RuntimeError(f"Wrong target representation in frame {fi}: {merged} merged")
            if not any(it.get("tris") is not None for it in items):
                raise RuntimeError(f"Frame {fi} has no surface triangles")
            path = args.out / f"frame_{fi:03d}.pkl"
            with path.open("wb") as stream:
                pickle.dump({"frame": fi, "items": items,
                             "is_ortho": bool(frame_ortho[fi]), "tol": 1e-4},
                            stream, protocol=pickle.HIGHEST_PROTOCOL)
            row = {"frame": fi, "path": path.name, "sha256": sha(path),
                   "items": len(items), "target_fills": target_fills,
                   "surface_items": sum(it.get("tris") is not None for it in items)}
            manifest["captures"].append(row)
            event(args.out, "capture", **row)
        manifest["baked_frame_count"] = len(frame_items)
        write_json(args.out / "manifest.json", manifest)
        if args.mode == "capture":
            raise CaptureComplete()
        yield from old_imap(frame_items, frame_ortho, workers=workers, report=report)

    progress = {"phase": None, "at": 0.0}

    def bake_spy(*a, **kw):
        phase[0] = "bake"
        cpu_start = time.process_time()
        previous_report = kw.get("report")

        def report(stage, done, total):
            now = time.perf_counter()
            if stage != progress["phase"] or now - progress["at"] >= 30:
                event(args.out, "bake_progress", stage=stage, done=done, total=total)
                progress.update(phase=stage, at=now)
            if previous_report:
                previous_report(stage, done, total)

        kw["report"] = report
        try:
            return old_bake_scene(*a, **kw)
        finally:
            manifest["bake_cpu_seconds"] = time.process_time() - cpu_start
            phase[0] = "diagnosis"

    def nearest_spy(truth, px, py):
        counts["ray_pixels"] += 1
        if counts["ray_pixels"] == 1 or counts["ray_pixels"] % 500 == 0:
            event(args.out, "ray_progress", tested=counts["ray_pixels"],
                  scene_frame=bpy.context.scene.frame_current)
        return old_nearest(truth, px, py)

    def diagnose_spy(*a, **kw):
        result = old_diagnose(*a, **kw)
        counts["diagnosed_errors"] += 1
        event(args.out, "diagnosis_progress", done=counts["diagnosed_errors"],
              element=a[2], occluder=a[3], cause=result["cause"])
        return result

    bake.bake_iter = namespace["bake_iter"]
    parallel.imap_resolve = imap_spy
    order.resolve, order._surface_read, api.bake_scene = resolve_spy, read_spy, bake_spy
    audit._Truth.nearest, audit._diagnose = nearest_spy, diagnose_spy
    started = time.perf_counter()
    report = None
    try:
        if args.mode in ("capture", "select"):
            try:
                api.bake_scene(bpy.context, settings)
            except CaptureComplete:
                pass
        elif args.mode == "bake":
            vec = api.bake_scene(bpy.context, settings)
        else:
            report = audit.run(settings=settings, frames=args.frames, budget=args.budget)
        if args.mode == "select":
            if probe.calls != 1:
                raise RuntimeError("Selection probe did not collect evidence")
            return
        if counts["no_merge_hook"] != 1 or counts["imap_hook"] != 1:
            raise RuntimeError(f"Instrumentation did not fire exactly once: {dict(counts)}")
        if len(manifest["captures"]) != len(args.frames):
            raise RuntimeError("Capture is incomplete")
        if args.mode == "bake":
            if not counts["resolve_calls_bake"]:
                raise RuntimeError("No in-process ordering was observed")
            summary = dict(elements=len(vec.elements),
                bake_seconds=time.perf_counter()-started,
                bake_cpu_seconds=manifest["bake_cpu_seconds"])
            write_json(args.out / "bake.json", summary)
            event(args.out, "bake_complete", **summary)
        elif args.mode == "audit":
            if not counts["resolve_calls_bake"] or not counts["surface_calls_bake"]:
                raise RuntimeError("No in-process surface ordering was observed")
            if not counts["ray_pixels"] or counts["ray_pixels"] != report["overlap_pixels_tested"]:
                raise RuntimeError("Ray-cast instrumentation did not observe the tested pixels")
            if any("diagnosis" not in rec for rec in report["errors"]):
                raise RuntimeError("Audit contains undiagnosed errors; capture spy missed frames")
            causes = Counter(rec["diagnosis"]["cause"] for rec in report["errors"])
            target = [rec for rec in report["errors"]
                      if TARGET in rec["element"] or TARGET in rec["occluder"]]
            summary = {"wrong_pixels": report["wrong_pixels"],
                       "errors": len(report["errors"]), "causes": dict(causes),
                       "target_errors": len(target),
                       "target_wrong_pixels": sum(rec["pixels"] for rec in target),
                       "elements": report["elements"], "bake_seconds": report["bake_seconds"],
                       "bake_cpu_seconds": manifest["bake_cpu_seconds"],
                       "total_seconds": report["total_seconds"], "rate_pct": report["rate_pct"]}
            write_json(args.out / "audit.json", report)
            write_json(args.out / "summary.json", summary)
            event(args.out, "result", **summary)
        else:
            event(args.out, "capture_complete", frames=args.frames,
                  seconds=time.perf_counter() - started)
    finally:
        bake.bake_iter, parallel.imap_resolve = old_iter, old_imap
        order.resolve, order._surface_read, api.bake_scene = old_resolve, old_read, old_bake_scene
        audit._Truth.nearest, audit._diagnose = old_nearest, old_diagnose
        bake._front_extent = old_extent
        manifest["instrumentation"] = dict(counts)
        manifest["harness_seconds"] = time.perf_counter() - started
        write_json(args.out / "manifest.json", manifest)


def replay(args):
    import numpy as np

    if args.repeats < 1:
        raise ValueError("repeats must be positive")
    manifest = json.loads((args.capture / "manifest.json").read_text(encoding="utf-8"))
    if args.frames and not set(args.frames).issubset(c["frame"] for c in manifest["captures"]):
        raise ValueError("Requested replay frame was not captured")
    modules = []
    sources = {}
    for index, entry in enumerate(args.order):
        label, value = entry.split("=", 1)
        if not label or label in sources:
            raise ValueError("order labels must be nonempty and unique")
        path = Path(value).resolve()
        spec = importlib.util.spec_from_file_location(f"_screen_order_{index}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append((label, module))
        sources[label] = {"path": str(path), "sha256": sha(path)}
    results = {"capture_manifest_sha256": sha(args.capture / "manifest.json"),
               "sources": sources, "rows": [], "exact_ranks": True, "exact_stats": True}
    event(args.out, "replay_start", sources=sources, repeats=args.repeats)
    for capture in manifest["captures"]:
        if args.frames and capture["frame"] not in args.frames:
            continue
        path = args.capture / capture["path"]
        if sha(path) != capture["sha256"]:
            raise RuntimeError(f"Capture hash mismatch: {path}")
        with path.open("rb") as stream:
            frame = pickle.load(stream)
        reference = None
        reference_stats = None
        for repeat in range(args.repeats):
            sequence = modules if repeat % 2 == 0 else list(reversed(modules))
            for label, module in sequence:
                # Copy outside the timer so prep fields never leak between runs.
                items = copy.deepcopy(frame["items"])
                started = time.perf_counter()
                ranks = module.resolve(items, frame["is_ortho"], frame["tol"])
                seconds = time.perf_counter() - started
                stats = dict(module.LAST_STATS)
                if reference is None:
                    reference = np.array(ranks)
                    reference_stats = stats
                exact = bool(np.array_equal(reference, ranks))
                exact_stats = stats == reference_stats
                row = {"frame": frame["frame"], "label": label, "repeat": repeat,
                       "seconds": seconds, "items": len(items), "stats": stats,
                       "exact_ranks": exact, "exact_stats": exact_stats}
                results["rows"].append(row)
                results["exact_ranks"] &= exact
                results["exact_stats"] &= exact_stats
                write_json(args.out / "replay.json", results)
                event(args.out, "replay", **row)
    if not results["rows"]:
        raise RuntimeError("No captured frames were replayed")
    results["median_seconds_by_label"] = {
        label: statistics.median(row["seconds"] for row in results["rows"]
                                 if row["label"] == label) for label, _ in modules}
    write_json(args.out / "replay.json", results)
    event(args.out, "replay_complete", exact_ranks=results["exact_ranks"],
          exact_stats=results["exact_stats"], median_seconds=results["median_seconds_by_label"])
    if not results["exact_ranks"] or not results["exact_stats"]:
        raise RuntimeError("Replay changed ranks or ordering statistics")


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    args = parser().parse_args(argv)
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    # Each arm has its own directory. Refuse accidentally replacing evidence.
    if (args.out / "run.jsonl").exists():
        raise RuntimeError(f"Output already contains a run: {args.out}")
    try:
        replay(args) if args.mode == "replay" else blender_run(args)
    except Exception:
        event(args.out, "failed", traceback=traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
