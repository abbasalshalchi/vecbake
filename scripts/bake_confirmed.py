"""Bake Apollo-Soyuz with the adjustments the audits have confirmed, plus a preview.

blender -b apollo-soyuz.blend --python scripts/bake_confirmed.py -- <out dir>

Confirmed as of 2026-09-13 (docs/audits.md): the surface depth read and batched
ordering are in the add-on; `apollo_003` drawn face by face is the best audited
selection (214 wrong px). The automatic depth-share split has not been audited,
so it is off. Settings match the screening audit's `configure()`.
"""
import inspect
import math
import sys
import time
from pathlib import Path

import bpy
from bl_ext.user_default.vecbake import api, bake

SPLIT = {"apollo_003"}

out = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
out.mkdir(parents=True, exist_ok=True)

p = bpy.context.scene.vecbake
for k, v in dict(use_scene_range=True, frame_step=1, use_scene_res=False,
                 width=1920, height=1080, merge_fills=True, weld_seams=True,
                 edge_mode="SHARP", crease_angle=math.radians(30.0),
                 backface_cull=True, see_through=True, cull_offscreen=True,
                 occlusion_cull=False, occlusion_strokes=False,
                 accurate_order=True, chain_edges=True, tol_px=1.0,
                 dissolve_px=1.0, shading=False, use_material_color=False,
                 material_alpha=False, stroke_source="FIXED", stroke_width=1.6,
                 split_span_pct=0.0).items():
    setattr(p, k, v)
settings = p.to_bake_settings(bpy.context)

# There is no setting for drawing one named object face by face; seed the
# baker's own no_merge set, as the audit harness's split arm does.
source = inspect.getsource(bake.bake_iter)
needle = "no_merge: set[str] = set()"
if source.count(needle) != 1:
    raise RuntimeError("bake_iter no_merge initializer changed")
namespace = dict(bake.__dict__)
namespace["_confirmed_split"] = lambda: set(SPLIT)
exec(compile(source.replace(needle, "no_merge: set[str] = _confirmed_split()"),
             bake.__file__ + "<confirmed>", "exec"), namespace)
bake.bake_iter = namespace["bake_iter"]

started = time.perf_counter()
result = api.bake_and_write(bpy.context, str(out), "apollo-soyuz-confirmed", settings,
                            formats=("lerpa", "preview"),
                            report=lambda ph, d, t: None)
print(f"[confirmed] {time.perf_counter() - started:.1f}s", flush=True)
for path in sorted(result.get("files", {}) if isinstance(result, dict) else []):
    print("[confirmed] wrote", path, flush=True)
print("[confirmed] result keys", list(result) if isinstance(result, dict) else type(result), flush=True)
