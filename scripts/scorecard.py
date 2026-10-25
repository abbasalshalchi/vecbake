# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""Grade one model for the cross-model scorecard (docs/audits.md §20).

    blender -b --factory-startup <model.blend> --python scripts/scorecard.py -- \
        --name <name> --out out/scorecard/<name>

One bake, graded three ways, plus what it costs:

- the occlusion audit (`audit.run`), ray cast at the first, middle and last
  frame, 3000 overlap pixels a frame -- §19's sampling;
- `check_geometry.py` on the written file, at the sampled frames;
- `check_outline_segments.py` on the written file, halfway between them;
- bake CPU (one process: the audit forces one ordering worker), elements, file
  size raw and gzipped, peak memory.

Every model gets the settings the hero audits use (`SETTINGS`, the same as
`screen_surface_batch.configure`). A scene whose camera already moves keeps its
move. Any other scene gets a 60 degree orbit over 31 frames at 24 fps, Apollo's
arc: an empty at a pivot turns from -30 to +30 degrees about world Z with the
camera parented to it. An existing static camera keeps its aim and lens; the
pivot is the point on its view axis level with the scene's geometry. A scene
with no camera gets a 50 mm one looking at the broad side, 20 degrees up.

**The audit margin is scaled to the scene.** `audit.run` defaults to 0.05 scene
units, which was chosen on Apollo. On a 10 cm model that is wider than the parts,
so every error would pass. Here it is 0.05 x (framed width / Apollo's framed
width), the width being what the camera spans at the pivot -- so Apollo grades at
exactly 0.05 and stays comparable with §19, and a model framed ten times tighter
grades ten times tighter. Every tested pixel's gap is also logged, so the
report can be re-graded at any margin without another audit (`--also-margin`).

`--factory-startup` is not optional: a third-party add-on in this profile crashes
Blender 5.2.2 in the background on loading the Voyager blend (a Python thread
setting an RNA property; see docs/gotchas.md). Only VecBake is enabled here.
"""

import argparse
import ctypes
from ctypes import wintypes
import gzip
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

import addon_utils
import bpy
from mathutils import Vector

addon_utils.enable("bl_ext.user_default.vecbake", default_set=False, persistent=False)
from bl_ext.user_default.vecbake import api, audit, emit_lerpa  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

SETTINGS = dict(use_scene_range=True, frame_step=1, use_scene_res=False,
                width=1920, height=1080, merge_fills=True, weld_seams=True,
                edge_mode="SHARP", crease_angle=math.radians(30.0),
                backface_cull=True, see_through=True, cull_offscreen=True,
                occlusion_cull=False, occlusion_strokes=False,
                accurate_order=True, chain_edges=True, tol_px=1.0,
                dissolve_px=1.0, shading=False, use_material_color=False,
                material_alpha=False, stroke_source="FIXED", stroke_width=1.6,
                split_span_pct=0.0)

ORBIT_FRAMES = 31
ORBIT_FPS = 24
ORBIT_DEGREES = 60.0
# Apollo-Soyuz's lerpa_cam at frame 1: 35.4739 units from lerpa_orbit along the
# view axis, 50 mm on a 36 mm sensor. Measured 2026-10-25.
APOLLO_WIDTH = 25.5412
APOLLO_MARGIN = 0.05
GEOMETRY = {"MESH", "CURVE", "FONT", "SURFACE", "META"}


def say(*parts):
    print("[scorecard]", *parts, flush=True)


def sha(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class _Counters(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


def peak_memory_mb():
    try:
        counters = _Counters()
        counters.cb = ctypes.sizeof(_Counters)
        process = ctypes.windll.kernel32.GetCurrentProcess
        process.restype = wintypes.HANDLE
        info = ctypes.windll.psapi.GetProcessMemoryInfo
        info.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Counters), wintypes.DWORD]
        info(process(), ctypes.byref(counters), counters.cb)
        return {"peak_working_set_mb": round(counters.PeakWorkingSetSize / 2**20),
                "peak_commit_mb": round(counters.PeakPagefileUsage / 2**20)}
    except (AttributeError, OSError):
        return {}


# ---------------------------------------------------------------------------
# camera
# ---------------------------------------------------------------------------

def _geometry(scene, dg):
    """(object, face count, world bbox corners) for everything that draws."""
    out = []
    for obj in scene.objects:
        if obj.type not in GEOMETRY or not obj.visible_get():
            continue
        ev = obj.evaluated_get(dg)
        try:
            mesh = ev.to_mesh()
        except RuntimeError:
            continue
        if mesh is None:                  # a curve with no geometry
            continue
        faces = len(mesh.polygons)
        ev.to_mesh_clear()
        if faces:
            out.append((obj, faces, [obj.matrix_world @ Vector(c) for c in obj.bound_box]))
    return out


def _centre(parts):
    """Bounding-box centres weighted by face count: where the geometry is."""
    total = sum(n for _, n, _ in parts)
    acc = Vector()
    for _, n, corners in parts:
        acc += (sum(corners, Vector()) / 8.0) * n
    return acc / total


def _camera_moves(scene, cam):
    start, end = scene.frame_start, scene.frame_end
    seen = []
    for f in (start, (start + end) // 2, end):
        scene.frame_set(f)
        seen.append(cam.matrix_world.copy())
    scene.frame_set(start)
    return any(m != seen[0] for m in seen[1:])


def _framed_width(cam, pivot):
    """What the camera spans, side to side, at the pivot."""
    if cam.data.type == "ORTHO":
        return cam.data.ortho_scale
    fwd = -(cam.matrix_world.to_3x3().col[2]).normalized()
    along = (pivot - cam.matrix_world.translation).dot(fwd)
    return along * cam.data.sensor_width / cam.data.lens


def setup_camera(scene):
    dg = bpy.context.evaluated_depsgraph_get()
    cam = scene.camera
    record = {"source_camera": cam.name if cam else None,
              "source_frames": [scene.frame_start, scene.frame_end],
              "source_fps": scene.render.fps}
    if cam is not None and _camera_moves(scene, cam):
        scene.frame_set(scene.frame_start)
        pivot = cam.parent.matrix_world.translation.copy() if cam.parent else None
        if pivot is None:
            parts = _geometry(scene, bpy.context.evaluated_depsgraph_get())
            centre = _centre(parts)
            fwd = -(cam.matrix_world.to_3x3().col[2]).normalized()
            eye = cam.matrix_world.translation
            pivot = eye + fwd * (centre - eye).dot(fwd)
        record.update(mode="own move", camera=cam.name,
                      framed_width=_framed_width(cam, pivot))
        return record

    scene.frame_start, scene.frame_end = 1, ORBIT_FRAMES
    scene.render.fps, scene.render.fps_base = ORBIT_FPS, 1.0
    scene.frame_set(1)
    dg = bpy.context.evaluated_depsgraph_get()
    parts = _geometry(scene, dg)
    centre = _centre(parts)

    if cam is not None:
        fwd = -(cam.matrix_world.to_3x3().col[2]).normalized()
        eye = cam.matrix_world.translation.copy()
        pivot = eye + fwd * (centre - eye).dot(fwd)
        mode = "orbit, artist's camera"
    else:
        corners = [c for _, _, cs in parts for c in cs]
        radius = max((c - centre).length for c in corners)
        lo = Vector([min(c[i] for c in corners) for i in range(3)])
        hi = Vector([max(c[i] for c in corners) for i in range(3)])
        # Look at the broad side: along whichever horizontal axis is thinner.
        axis = Vector((1, 0, 0)) if (hi.x - lo.x) < (hi.y - lo.y) else Vector((0, 1, 0))
        up = math.radians(20.0)
        data = bpy.data.cameras.new("scorecard_cam")
        data.lens, data.sensor_width = 50.0, 36.0
        half = math.atan(data.sensor_width * 9.0 / 16.0 / 2.0 / data.lens)
        dist = radius / math.sin(half)
        data.clip_start, data.clip_end = dist / 1000.0, dist * 100.0
        cam = bpy.data.objects.new("scorecard_cam", data)
        scene.collection.objects.link(cam)
        cam.location = centre - axis * dist * math.cos(up) + Vector((0, 0, dist * math.sin(up)))
        cam.rotation_euler = (centre - cam.location).to_track_quat("-Z", "Y").to_euler()
        scene.camera = cam
        pivot = centre.copy()
        mode = "orbit, new camera"
        record["radius"] = radius

    empty = bpy.data.objects.new("scorecard_orbit", None)
    scene.collection.objects.link(empty)
    empty.location = pivot
    bpy.context.view_layer.update()
    world = cam.matrix_world.copy()
    cam.parent = empty
    cam.matrix_parent_inverse.identity()
    cam.matrix_world = world
    prefs = bpy.context.preferences.edit
    prefs.keyframe_new_interpolation_type = "LINEAR"
    half_arc = math.radians(ORBIT_DEGREES / 2.0)
    for frame, angle in ((1, -half_arc), (ORBIT_FRAMES, half_arc)):
        empty.rotation_euler = (0.0, 0.0, angle)
        empty.keyframe_insert("rotation_euler", index=2, frame=frame)
    scene.frame_set(1)
    record.update(mode=mode, camera=cam.name, pivot=list(pivot),
                  framed_width=_framed_width(cam, pivot))
    return record


def reset_settings(props):
    """Put every saved panel setting back to its default; return what it was."""
    changed = {}
    for prop in props.bl_rna.properties:
        key = prop.identifier
        if key == "rna_type" or prop.is_readonly or prop.type in ("POINTER", "COLLECTION"):
            continue
        if not props.is_property_set(key):
            continue
        before = getattr(props, key)
        before = before[:] if hasattr(before, "__len__") and not isinstance(before, str) else before
        props.property_unset(key)
        after = getattr(props, key)
        after = after[:] if hasattr(after, "__len__") and not isinstance(after, str) else after
        if before != after:
            changed[key] = str(before)
    return changed


# ---------------------------------------------------------------------------
# grading
# ---------------------------------------------------------------------------

def _load(name):
    path = HERE / f"{name}.py"
    ns = {"__name__": name, "__file__": str(path)}
    exec(compile(open(path, encoding="utf-8").read(), str(path), "exec"), ns)
    return ns


def _regrade(gaps, margin):
    pairs = {}
    for group, occluder, gap in gaps:
        if gap > margin:
            pairs[(group, occluder)] = pairs.get((group, occluder), 0) + 1
    return {"margin": margin, "wrong_pixels": sum(pairs.values()), "pairs": len(pairs)}


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--budget", type=int, default=3000)
    parser.add_argument("--also-margin", type=float, action="append", default=[])
    # The scene camera is not always the shot: the doughnut tutorial's is a
    # texture-baking camera that sees one plane. The corpus names the shot.
    parser.add_argument("--camera")
    args = parser.parse_args(argv)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    report_path = out / "scorecard.json"

    def save(record):
        record["peak_memory"] = peak_memory_mb()
        record["elapsed_seconds"] = round(time.perf_counter() - started, 1)
        report_path.write_text(json.dumps(record, indent=1, default=str), encoding="utf-8")

    scene = bpy.context.scene
    blend = Path(bpy.data.filepath)
    record = {"name": args.name, "blend": str(blend), "blend_sha256": sha(blend),
              "blender": bpy.app.version_string,
              "commit": subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                                       capture_output=True, text=True).stdout.strip(),
              "dirty": bool(subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain",
                                            "addon"], capture_output=True, text=True).stdout.strip()),
              "status": "camera"}
    if args.camera:
        scene.camera = bpy.data.objects[args.camera]
    record["camera"] = setup_camera(scene)
    record["camera"]["chosen"] = args.camera
    say(args.name, "camera:", record["camera"]["mode"], "width",
        round(record["camera"]["framed_width"], 4))

    # Same settings means all of them: a blend saves its panel, and celltower's
    # has include_ids off, which leaves both geometry checkers nothing to match
    # and reporting a clean pass over zero point-sets. Reset, then apply.
    props = scene.vecbake
    record["blend_settings_reset"] = reset_settings(props)
    for key, value in SETTINGS.items():
        setattr(props, key, value)
    settings = props.to_bake_settings(bpy.context)
    n = scene.frame_end - scene.frame_start + 1
    frames = sorted({0, n // 2, n - 1})
    margin = APOLLO_MARGIN * record["camera"]["framed_width"] / APOLLO_WIDTH
    record.update(frames=frames, frame_range=[scene.frame_start, scene.frame_end],
                  fps=scene.render.fps, budget=args.budget, margin=margin, status="baking")
    save(record)
    say(args.name, f"{n} frames, auditing {frames}, margin {margin:.5f}")

    # One bake: watch the audit's own, and keep what it made.
    captured = {}
    real_bake = api.bake_scene
    last = {"phase": None, "at": 0.0}

    def progress(phase, done, total):
        now = time.perf_counter()
        if phase != last["phase"] or now - last["at"] > 60.0:
            last.update(phase=phase, at=now)
            say(args.name, f"bake: {phase} {done}/{total} ({now - started:.0f}s)")

    def bake_spy(context=None, settings=None, report=None):
        cpu, wall = time.process_time(), time.perf_counter()
        vec = real_bake(context, settings, report=report or progress)
        captured.update(vec=vec, cpu=time.process_time() - cpu, wall=time.perf_counter() - wall,
                        settings=settings)
        say(args.name, f"baked in {captured['cpu']:.0f}s CPU, {len(vec.elements)} elements; auditing")
        return vec

    gaps = []
    truth = {"frame": -1, "last": None}
    real_init = audit._Truth.__init__
    real_nearest = audit._Truth.nearest
    real_surface = audit._Truth.surface_of

    def init_spy(self, *a, **kw):
        truth["frame"] += 1
        say(args.name, f"audit frame {frames[truth['frame']]} ({time.perf_counter() - started:.0f}s)")
        return real_init(self, *a, **kw)

    def nearest_spy(self, px, py):
        truth["last"] = real_nearest(self, px, py)
        return truth["last"]

    def surface_spy(self, group, px, py):
        own = real_surface(self, group, px, py)
        if own is not None and truth["last"] and truth["last"][0] is not None:
            gaps.append((group, truth["last"][0], float(own - truth["last"][1])))
        return own

    api.bake_scene = bake_spy
    audit._Truth.__init__ = init_spy
    audit._Truth.nearest = nearest_spy
    audit._Truth.surface_of = surface_spy
    try:
        report = audit.run(settings=settings, frames=frames, budget=args.budget, margin=margin)
    finally:
        api.bake_scene = real_bake
        audit._Truth.__init__ = real_init
        audit._Truth.nearest = real_nearest
        audit._Truth.surface_of = real_surface

    vec = captured["vec"]
    causes = {}
    for rec in report["errors"]:
        cause = (rec.get("diagnosis") or {}).get("cause", "undiagnosed")
        row = causes.setdefault(cause, {"errors": 0, "pixels": 0})
        row["errors"] += 1
        row["pixels"] += rec["pixels"]
    kinds = {}
    for el in vec.elements:
        kinds[el.kind] = kinds.get(el.kind, 0) + 1
    meta = {k: v for k, v in vec.meta.items()
            if isinstance(v, (int, float, str, bool)) or v is None}
    meta.update({f"{k}_count": len(v) for k, v in vec.meta.items() if isinstance(v, (list, dict))})
    record.update(
        status="audited",
        bake_cpu_seconds=round(captured["cpu"], 1),
        bake_wall_seconds=round(captured["wall"], 1),
        elements=len(vec.elements), element_kinds=kinds, meta=meta,
        audit={k: report[k] for k in ("frames_checked", "overlap_pixels_available",
                                      "overlap_pixels_tested", "sampled_pct", "wrong_pixels",
                                      "rate_pct", "wrong_pixels_estimated_total",
                                      "bake_seconds", "total_seconds")},
        audit_distinct=len(report["errors"]), audit_causes=causes,
        audit_top=[{k: r.get(k) for k in ("element", "kind", "occluder", "pixels", "worst_gap",
                                          "worst_at")}
                   | {"cause": (r.get("diagnosis") or {}).get("cause")}
                   for r in report["errors"][:25]],
        regraded=[_regrade(gaps, m) for m in sorted({margin, APOLLO_MARGIN, *args.also_margin})],
        gaps_logged=len(gaps))
    save(record)

    s = captured["settings"]
    path = out / f"{args.name}.svg"
    emit_lerpa.write(vec, str(path), include_ids=s.include_ids,
                     coord_decimals=s.effective_coord_decimals(), states=s.lerpa_states,
                     name=args.name, source=blend.name, pretty=False)
    raw = path.read_bytes()
    record.update(status="written", file_bytes=len(raw), gzip_bytes=len(gzip.compress(raw, 9)))
    save(record)
    say(args.name, f"wrote {len(raw) / 1e6:.2f} MB; checking geometry")

    geo = _load("check_geometry")["run"](str(path), verbose=True)
    worst = {}
    for r in geo["bad"]:
        worst.setdefault(r["id"], r)
    record.update(status="geometry", geometry={
        "checked": geo["checked"], "skipped": geo["skipped"], "offenders": geo["offenders"],
        "elements_offending": len(worst), "worst_px": geo["worst"], "worst_at": geo["worst_at"],
        "allow_px": geo["allow"], "over_3px": sum(1 for r in geo["bad"] if r["d"] > 3.0),
        "over_10px": sum(1 for r in geo["bad"] if r["d"] > 10.0),
        "top": list(worst.values())[:15],
        "all": [[r["id"], r["frame"], round(r["d"], 2)] for r in geo["bad"]]})
    save(record)
    say(args.name, "checking outline segments at subframe 0.5")

    bad = _load("check_outline_segments")["run"](str(path), verbose=True, subframe=0.5)
    per = {}
    for r in bad:
        per.setdefault(r["id"], []).append(r)
    record.update(status="done", segments={
        "subframe": 0.5, "offending_pairs": len(bad), "elements_offending": len(per),
        "worst_px": bad[0]["d"] if bad else 0.0,
        "over_3px": sum(1 for r in bad if r["d"] > 3.0),
        "over_10px": sum(1 for r in bad if r["d"] > 10.0),
        "top": [rows[0] | {"frames": len(rows)} for rows in
                sorted(per.values(), key=lambda rows: -rows[0]["d"])[:15]],
        "all": [[r["id"], r["frame"], round(r["d"], 2)] for r in bad]})
    save(record)
    say(args.name, f"done in {time.perf_counter() - started:.0f}s")


try:
    main()
except Exception:
    import traceback
    traceback.print_exc()
    sys.stdout.flush()
    os._exit(1)
os._exit(0)
