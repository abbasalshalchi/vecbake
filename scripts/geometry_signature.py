# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
r"""What is in a .blend, exactly enough to tell two saves apart.

Before attributing a change in a bake to a code change, rule out the scene.
This writes a signature of every object -- its mesh, its modifiers, its
transform -- plus the VecBake settings, so two saves can be compared object by
object instead of by eye.

    blender -b scene.blend --python scripts/geometry_signature.py -- \
        --out out/sig/after.json
    python scripts/geometry_signature.py --compare out/sig/before.json out/sig/after.json

Two mesh hashes are taken for each object, and the pair is the point:

- **stored** -- the mesh as saved.
- **evaluated** -- the mesh after the depsgraph has run every modifier.

They are equal exactly when nothing is left unapplied. That matters because a
modifier's inputs moved in Blender 5.2: ``mod["Input_1"]`` no longer raises, it
reports the socket unset, so a reader with a sensible default substitutes it
and reports success. That silently replaced an authored 73.59 degree Smooth by
Angle with a 30 degree fallback on 310 objects and rewrote a whole bake. Any
geometry-nodes input here is read from both locations, new first, and a value
that answers at only one of them is flagged.

Written 2026-10-10, rebuilding a tool that was lost with its scratch directory
after the measurements in docs/audits.md section 18 were taken with it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys


def _args(argv):
    arg = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    arg.add_argument("--out", help="write the signature of the open .blend here")
    arg.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"),
                     help="print what changed between two signature files")
    arg.add_argument("--quiet", action="store_true")
    arg.add_argument("--at-frame", type=int,
                     help="read transforms at this frame; default the scene start")
    return arg.parse_args(argv)


# --- writing -----------------------------------------------------------------

def _mesh_hash(mesh):
    """A hash of the geometry itself: vertex positions and face corners.

    Not of the datablock -- names, materials and custom data are deliberately
    out, so a rename does not read as a geometry change.
    """
    import numpy as np
    digest = hashlib.sha256()
    n = len(mesh.vertices)
    co = np.empty(n * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", co)
    digest.update(co.tobytes())
    loops = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", loops)
    digest.update(loops.tobytes())
    starts = np.empty(len(mesh.polygons), dtype=np.int32)
    mesh.polygons.foreach_get("loop_start", starts)
    digest.update(starts.tobytes())
    return digest.hexdigest()[:16], n, len(mesh.polygons)


def _plain(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if hasattr(value, "__len__") and not isinstance(value, str):
        try:
            return [_plain(v) for v in value]
        except TypeError:
            pass
    if hasattr(value, "name"):
        return value.name
    return str(value)


def _node_inputs(mod):
    """Read a geometry-nodes modifier's inputs from both homes, new first.

    5.2:  mod.properties.inputs.<id>.value
    5.1:  mod[<id>]

    A socket that answers at only one is flagged, because on 5.2 the old
    subscript reports "unset" instead of raising -- which is the whole trap.
    """
    out = {}
    props = getattr(mod, "properties", None)
    inputs = getattr(props, "inputs", None) if props is not None else None
    if inputs is not None:
        for sock in inputs:
            out[sock.identifier] = {"new": _plain(getattr(sock, "value", None))}
    try:
        keys = list(mod.keys())
    except TypeError:          # 5.2: the modifier has no ID properties at all
        keys = []
    for key in keys:
        try:
            old = _plain(mod[key])
        except (KeyError, TypeError):
            old = None
        out.setdefault(key, {})["old"] = old
    for both in out.values():
        if "new" in both and "old" in both:
            if both["new"] != both["old"]:
                both["disagree"] = True
        else:
            both["only"] = "new" if "new" in both else "old"
    return out


def write(path, quiet=False, at_frame=None):
    import bpy
    scene = bpy.context.scene

    # A .blend opens at whatever frame it was saved on, and an animated object's
    # matrix_world is a function of that frame. Comparing two saves taken at
    # different frames therefore reports every animated object as moved -- which
    # it did, on the first run of this tool: the camera, the orbit and two
    # thrusters, none of which had been touched. Fix a frame, and record it.
    original = scene.frame_current
    frame = scene.frame_start if at_frame is None else at_frame
    scene.frame_set(frame)
    depsgraph = bpy.context.evaluated_depsgraph_get()

    objects = {}
    for obj in sorted(scene.objects, key=lambda o: o.name):
        rec = {"type": obj.type, "hidden": not obj.visible_get(),
               "matrix": [round(v, 9) for row in obj.matrix_world for v in row],
               "modifiers": []}
        for mod in obj.modifiers:
            entry = {"name": mod.name, "type": mod.type,
                     "show_viewport": mod.show_viewport, "show_render": mod.show_render}
            if mod.type == "NODES":
                entry["node_group"] = getattr(mod.node_group, "name", None)
                entry["inputs"] = _node_inputs(mod)
            rec["modifiers"].append(entry)
        if obj.type == "MESH":
            rec["stored"], rec["verts"], rec["faces"] = _mesh_hash(obj.data)
            evaluated = obj.evaluated_get(depsgraph)
            tmp = evaluated.to_mesh()
            try:
                rec["evaluated"], rec["eval_verts"], rec["eval_faces"] = _mesh_hash(tmp)
            finally:
                evaluated.to_mesh_clear()
            # Equal hashes mean nothing is waiting to be applied. Unequal with
            # no modifier listed means something is generating geometry this
            # file does not record.
            rec["unapplied"] = rec["stored"] != rec["evaluated"]
        objects[obj.name] = rec

    settings = {}
    props = getattr(scene, "vecbake", None)
    if props is not None:
        for prop in props.bl_rna.properties:
            if prop.identifier == "rna_type" or prop.is_readonly:
                continue
            settings[prop.identifier] = _plain(getattr(props, prop.identifier))

    scene.frame_set(original)
    sig = {"blend": bpy.data.filepath, "blender": bpy.app.version_string,
           "scene": scene.name, "frames": [scene.frame_start, scene.frame_end],
           "read_at_frame": frame, "saved_at_frame": original,
           "objects": objects, "vecbake": settings}
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(sig, stream, indent=1, sort_keys=True)
    if not quiet:
        meshes = [r for r in objects.values() if r["type"] == "MESH"]
        print("{} objects ({} meshes), {} vertices -> {}".format(
            len(objects), len(meshes), sum(r["verts"] for r in meshes), path))
        stale = [n for n, r in objects.items() if r.get("unapplied")]
        if stale:
            print("  unapplied geometry on {}: {}".format(
                len(stale), ", ".join(stale[:6])))
    return sig


# --- comparing ---------------------------------------------------------------

def compare(before, after):
    with open(before, encoding="utf-8") as stream:
        a = json.load(stream)
    with open(after, encoding="utf-8") as stream:
        b = json.load(stream)
    oa, ob = a["objects"], b["objects"]

    gone = sorted(set(oa) - set(ob))
    new = sorted(set(ob) - set(oa))
    shared = sorted(set(oa) & set(ob))
    changed, moved, modified = [], [], []
    for name in shared:
        ra, rb = oa[name], ob[name]
        if ra.get("stored") != rb.get("stored"):
            changed.append((name, ra.get("verts"), rb.get("verts"),
                            ra.get("faces"), rb.get("faces")))
        if ra.get("matrix") != rb.get("matrix"):
            moved.append(name)
        if ra.get("modifiers") != rb.get("modifiers"):
            modified.append(name)

    print("{} -> {}".format(before, after))
    fa, fb = a.get("read_at_frame"), b.get("read_at_frame")
    if fa != fb:
        print("  WARNING: read at different frames ({} and {}); every animated"
              " object will read as moved".format(fa, fb))
    line = "  objects {} -> {}".format(len(oa), len(ob))
    if new or gone:
        line += ", {} new, {} gone".format(len(new), len(gone))
    print(line)
    for name in new:
        print("  + " + name)
    for name in gone:
        print("  - " + name)
    if changed:
        print("  mesh changed on {} of {}:".format(len(changed), len(shared)))
        for name, va, vb, fa, fb in changed:
            print("      {}: {} -> {} vertices, {} -> {} faces".format(name, va, vb, fa, fb))
    else:
        print("  no mesh changed")
    if moved:
        print("  moved: " + ", ".join(moved[:8]) + (" ..." if len(moved) > 8 else ""))
    if modified:
        print("  modifiers changed: " + ", ".join(modified[:8]))
    stale = [n for n, r in ob.items() if r.get("unapplied")]
    print("  unapplied geometry after: {}{}".format(
        len(stale), " (" + ", ".join(stale[:4]) + ")" if stale else ""))

    sa, sb = a.get("vecbake", {}), b.get("vecbake", {})
    diff = {k: (sa.get(k), sb.get(k)) for k in set(sa) | set(sb) if sa.get(k) != sb.get(k)}
    print("  VecBake settings: " + ("identical" if not diff else str(diff)))
    return {"changed": changed, "new": new, "gone": gone,
            "moved": moved, "modifiers": modified, "settings": diff}


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    args = _args(argv)
    if args.compare:
        compare(*args.compare)
    elif args.out:
        write(args.out, args.quiet, args.at_frame)
    else:
        raise SystemExit("give --out (inside Blender) or --compare A B")
