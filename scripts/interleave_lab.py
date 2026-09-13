"""Controlled projected surfaces; also builds a viewable Blender lab scene.

blender -b --python scripts/interleave_lab.py -- out/interleave/lab.blend

A stepped body has two disjoint front faces at depths 10 and 14. One flat
bracket at depth 12 overlaps both. A single body rank is impossible; drawing
its faces separately is sufficient. A sloping plate with a flush fitting and
two separated boxes are negative controls. Everything is stated in screen
coordinates so standalone tests and the Blender lab use the same geometry.
"""
import sys
from pathlib import Path
import numpy as np


def quad(x0,y0,x1,y1,depths):
    xy=np.array([[x0,y0],[x1,y0],[x1,y1],[x0,y1]],dtype=float)
    d=np.broadcast_to(np.asarray(depths,dtype=float),(4,)).copy()
    t=np.array([[0,1,2],[0,2,3]])
    return xy[t,0],xy[t,1],d[t]


def item(name, tris, merged=True):
    return dict(key=name+("/m" if merged else "/f0"),owner=name,merged=merged,tris=tris)


def fixtures():
    stepped=tuple(np.concatenate([a,b]) for a,b in zip(
        quad(10,10,40,50,10),quad(60,10,90,50,14)))
    crossing=[item("stepped_body",stepped),
              item("bracket",quad(20,20,80,40,12),False),
              item("remote",quad(105,10,115,50,50),False)]
    bolted=[item("plate",quad(10,10,90,50,[10,20,20,10])),
            item("bolt",quad(45,20,55,40,[14.275,15.525,15.525,14.275]),False)]
    separate=[item("left",quad(10,10,40,50,10)),item("right",quad(60,10,90,50,12))]
    return {"crossing":crossing,"bolted":bolted,"separate":separate}


def build(path):
    import bpy
    from mathutils import Vector
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for group,items in fixtures().items():
        offset={"crossing":0,"bolted":130,"separate":260}[group]
        for it in items:
            tx,ty,td=it["tris"]
            verts=[((float(x)+offset)*.05, float(d)*.05, -float(y)*.05)
                   for x,y,d in zip(tx.ravel(),ty.ravel(),td.ravel())]
            faces=[(k,k+2,k+1) for k in range(0,len(verts),3)]
            mesh=bpy.data.meshes.new(group+"_"+it["owner"])
            mesh.from_pydata(verts,[],faces); mesh.update()
            obj=bpy.data.objects.new(mesh.name,mesh)
            bpy.context.collection.objects.link(obj)
            obj["vecbake_merge_fill"]=int(it["merged"])
    cam_data=bpy.data.cameras.new("Lab camera"); cam_data.type="ORTHO"; cam_data.ortho_scale=20
    cam=bpy.data.objects.new("Lab camera",cam_data); bpy.context.collection.objects.link(cam)
    cam.location=(9.75,-10,-1.5)
    direction=Vector((9.75,0,-1.5))-cam.location
    cam.rotation_euler=direction.to_track_quat("-Z","Y").to_euler()
    scene=bpy.context.scene; scene.camera=cam
    scene.render.resolution_x=1560; scene.render.resolution_y=320; scene.render.resolution_percentage=100
    scene.frame_start=scene.frame_end=1
    path=Path(path).resolve(); path.parent.mkdir(parents=True,exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    print("[interleave-lab] "+str(path),flush=True)


if __name__=="__main__":
    argv=sys.argv[sys.argv.index("--")+1:]
    build(argv[0])
