"""Check saved selector witnesses independently with Blender object ray casts.

blender -b apollo-soyuz.blend --python scripts/check_interleave_evidence.py -- \
    out/interleave/select/interleave.json out/interleave/witnesses.json

For a nonmerged opposing face, its owning object's nearer self-occluding face
may hide it. That distinction is reported separately rather than re-labelled
as the depth of the tested face. No bake or representation changes are made.
"""
import json
import sys
from pathlib import Path

import bpy
from bl_ext.user_default.vecbake import audit

args=sys.argv[sys.argv.index("--")+1:]
source=Path(args[0]); dest=Path(args[1])
data=json.loads(source.read_text(encoding="utf-8"))
scene=bpy.context.scene
scene.render.resolution_x=1920; scene.render.resolution_y=1080
scene.render.resolution_percentage=100
if scene.camera.data.type != "PERSP":
    raise RuntimeError("audit._Truth currently supplies perspective rays only")
start=scene.frame_start; original=scene.frame_current
tested=0; failures=[]; hidden=[]; max_error=0.0
frame=None
try:
    for row in data["rows"]:
        if row["frame"] != frame:
            frame=row["frame"]; scene.frame_set(start+frame)
            truth=audit._Truth(scene,1920,1080)
        for side,sign in (("front",-1),("back",1)):
            x,y,expected_a,expected_b=row[side]
            a=truth.surface_of(row["body"],x,y)
            b=truth.surface_of(row["part_owner"],x,y)
            tested+=1
            entry=dict(frame=frame,body=row["body"],part=row["part"],side=side,
                       pixel=[x,y],expected=[expected_a,expected_b],ray=[a,b])
            if a is None or b is None:
                failures.append(dict(reason="ray missed",**entry)); continue
            max_error=max(max_error,abs(a-expected_a),abs(b-expected_b))
            if abs(a-expected_a)>row["tolerance"]:
                failures.append(dict(reason="body surface disagrees",**entry))
            elif abs(b-expected_b)>row["tolerance"] and not row["part_merged"]:
                hidden.append(entry)
            elif sign*(a-b)<=row["tolerance"]:
                failures.append(dict(reason="ray contradicts sign",**entry))
finally:
    scene.frame_set(original)
report=dict(source=str(source.resolve()),tested=tested,failures=failures,
            hidden_other_faces=hidden,max_depth_difference=max_error)
dest.write_text(json.dumps(report,indent=2),encoding="utf-8")
print("[interleave-rays]",tested,"witnesses",len(failures),"failures",
      len(hidden),"hidden opposing faces", "max difference",max_error,flush=True)
raise SystemExit(bool(failures))
