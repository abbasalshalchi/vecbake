"""Known front/back relations, with an executable negative control.

python scripts/test_interleave.py             # candidate must pass
python scripts/test_interleave.py --legacy    # current selector must FAIL
"""
import sys
from types import SimpleNamespace
import numpy as np
import _addon
from interleave_lab import fixtures, item, quad
from interleave_experiment import frame_evidence, selected, raster, SamplingProbe

_addon.stub_blender()
order=_addon.load("order"); bake=_addon.load("bake")
fails=[]
def check(label,cond,detail=""):
    print(f"{'PASS' if cond else 'FAIL'} {label} {detail}")
    if not cond: fails.append(label)

def choose(items):
    if "--legacy" in sys.argv:
        extent={it["owner"]:[(float(it["tris"][2].min()),float(it["tris"][2].max()),
                  float(it["tris"][0].min()),float(it["tris"][0].max()),
                  float(it["tris"][1].min()),float(it["tris"][1].max()))] for it in items}
        return bake._deep_bodies(extent,50)
    return selected(frame_evidence(items,120,60,order,True))

cases=fixtures()
for name,expected in (("crossing",["stepped_body"]),("bolted",[]),("separate",[])):
    got=choose(cases[name]); check(name+" selects exactly what cannot hold one rank",got==expected,str(got))
rows=frame_evidence(cases["crossing"],120,60,order,True)
check("crossing has 400 pixels of each sign",len(rows)==1 and rows[0]["front_pixels"]==400 and rows[0]["back_pixels"]==400)
check("tolerance is resolve's scene-relative definition",rows[0]["tolerance"]==max(1e-4,(50-10)*1e-4))

# Splitting the stepped body is sufficient, not merely a positive detector.
# One flat bracket rank lies between its two face ranks. The two inequalities
# cannot be satisfied by replacing those face ranks with one merged rank.
def face(key,x0,x1,d):
    return dict(key=key,kind=1,seed=d,pts=np.array([[x0,10.],[x1,10.],
        [x1,50.],[x0,50.]]),depths=np.full(4,d))
parts=[face("left",10.,40.,10.),face("right",60.,90.,14.),face("bracket",20.,80.,12.)]
ranks=order.resolve(parts,is_ortho=True)
check("face ranks solve the controlled contradiction",ranks[0]<ranks[2]<ranks[1])

# Front in one frame, behind in another is a rank change, not interleaving.
temporal=[]
for d in (9,15):
    temporal.extend(frame_evidence([item("body",quad(10,10,90,50,12)),
        item("moving",quad(20,20,80,40,d),False)],120,60,order,True))
check("signs from different frames cannot combine",not temporal)
check("one genuinely contradicting frame commits the clip",selected(temporal+rows)==["stepped_body"])

# Opposite signs against separately ranked faces are not one P contradicting B.
split_parts=[item("body",quad(10,10,90,50,12)),
             item("part",quad(10,10,40,50,10),False),
             item("part",quad(60,10,90,50,14),False)]
split_parts[2]["key"]="part/f1"
check("two independent opposing face ranks do not trigger a split",
      not frame_evidence(split_parts,120,60,order,True))
probe=SamplingProbe(bake,order,120,60)
probe.plans={"stepped_body":SimpleNamespace(merge_fill=True,outline_n=8,outline_need=0),
             "bracket":SimpleNamespace(merge_fill=False,outline_n=None,outline_need=0)}
extent={"stepped_body":[dict(tris=cases["crossing"][0]["tris"],faces=np.array([0,0,1,1]))],
        "bracket":[dict(tris=cases["crossing"][1]["tris"],faces=np.array([0,0]))]}
check("selection consumes settled representations after sizing reset the plan",
      probe.choose(extent,50,[True])==["stepped_body"])

# Overlapping own faces: only the nearest surface counts.
rear=quad(10,10,90,50,[10,20,20,10]); front=quad(10,10,90,50,5)
covered=tuple(np.concatenate([a,b]) for a,b in zip(rear,front))
check("hidden rear faces cannot manufacture a contradiction",not frame_evidence([
    item("folded",covered),item("part",quad(20,20,80,40,12),False)],120,60,order,True))
touch=[item("a",quad(10,10,90,50,[10,20,20,10])),
       item("b",quad(10,10,90,50,[10.0001,19.9999,19.9999,10.0001]),False)]
check("touching inside existing tolerance is not evidence",not frame_evidence(touch,120,60,order,True))
persp=item("ramp",quad(0,0,2,2,[1,3,3,1]))
_,depth=raster(persp["tris"],2,2,order,False)
check("perspective pixel depths use reciprocal interpolation",np.allclose(depth[0],[1.2,2.0]))
check("missing surfaces are empty",not frame_evidence([],120,60,order,True))
print(f"[interleave-test] {len(fails)} failures")
raise SystemExit(bool(fails))
