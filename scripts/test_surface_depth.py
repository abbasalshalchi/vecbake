"""A merged part is read at the depth of its surface, not of its nearest vertex.

    "<blender>/5.2/python/bin/python.exe" scripts/test_surface_depth.py

The ordering pass settles two overlapping elements by reading each one's depth
at the centre of what they share. A merged part used to answer with the depth of
the surface sample nearest that point -- and a sample is a vertex. On a low-poly
body the vertices are the corners of long faces, so the nearest one to the
middle of a face is half the face away and the depth read there belongs to a
corner: a fitting bolted to the middle of a long side is judged against one end
of the body, and a body that reaches away from the camera paints over it.

Reading the front triangle the point lies on is exact. This pins that, the
interpolation a perspective camera needs, what happens off the surface, and the
triangulation that feeds it.
"""

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import _addon                                              # noqa: E402

_addon.stub_blender()
order = _addon.load("order")
bake = _addon.load("bake")
fails = []


def check(label, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {label}{'  ' + detail if detail else ''}")
    if not cond:
        fails.append(label)


def merged(key, corners, depths, tris, seed):
    """A merged fill as bake.py hands it over: ring, probe samples, triangles."""
    c = np.asarray(corners, dtype=float)
    d = np.asarray(depths, dtype=float)
    t = np.asarray(tris)
    return dict(key=key, kind=order.FILL_KIND, seed=seed, pts=c, depths=d,
                probe=(c[:, 0].copy(), c[:, 1].copy(), d.copy()),
                tris=(c[t, 0], c[t, 1], d[t]))


# --- the fault, in miniature -----------------------------------------------
# `body` is one long side face reaching away from the camera: 10 units deep at
# its left end, 20 at its right. `bolt` sits on its middle, 0.1 in front of it.
# Every corner of the body is the same distance from the middle, so the nearest
# sample there is a corner -- 10 or 20 -- and neither is 15.
body = merged("body", [(0, 0), (400, 0), (400, 100), (0, 100)],
              [10.0, 20.0, 20.0, 10.0], [(0, 1, 2), (0, 2, 3)], seed=15.0)
bolt = dict(key="bolt", kind=order.FILL_KIND, seed=14.9,
            pts=np.array([[180.0, 40.0], [220.0, 40.0], [220.0, 60.0], [180.0, 60.0]]),
            depths=np.full(4, 14.9))

got = float(order._depths_at(dict(body), [200.0], [50.0], True)[0])
check("the body is read at its surface under the bolt", abs(got - 15.0) < 1e-9,
      f"got {got}")

no_tris = dict(body)
no_tris.pop("tris")
old = float(order._depths_at(no_tris, [200.0], [50.0], True)[0])
check("...where the nearest sample alone reads a corner, which is the fault",
      old in (10.0, 20.0), f"nearest sample gives {old}")

items = [body, bolt]
ranks = order.resolve([dict(it) for it in items], is_ortho=True)
r = {it["key"]: ranks[i] for i, it in enumerate(items)}
# larger value = painted earlier = further back
check("the bolt is painted over the body it is bolted to",
      r["bolt"] < r["body"], f"bolt={r['bolt']} body={r['body']}")

# --- a perspective camera interpolates inverse depth -----------------------
# Halfway across the screen between depths 1 and 3 is not depth 2: the near end
# covers more of the screen, so halfway on screen is 1 / (0.5/1 + 0.5/3) = 1.5.
ramp = merged("ramp", [(0, 0), (100, 0), (100, 100), (0, 100)],
              [1.0, 3.0, 3.0, 1.0], [(0, 1, 2), (0, 2, 3)], seed=2.0)
persp = float(order._depths_at(ramp, [50.0], [50.0], False)[0])
ortho = float(order._depths_at(dict(ramp, surface=None), [50.0], [50.0], True)[0])
check("perspective reads inverse depth across the triangle", abs(persp - 1.5) < 1e-9,
      f"got {persp}")
check("orthographic reads depth itself", abs(ortho - 2.0) < 1e-9, f"got {ortho}")

# --- continuous where the surface is ---------------------------------------
# Either side of the diagonal the two triangles share, the read must agree: a
# step there is the swapping the nearest-sample read was already known for.
a = float(order._depths_at(dict(ramp), [50.0], [49.999], True)[0])
b = float(order._depths_at(dict(ramp), [50.0], [50.001], True)[0])
check("the read does not step across the edge two faces share", abs(a - b) < 1e-3,
      f"{a} vs {b}")

# --- where front faces overlap on screen, the nearest is the one painted ---
# A bowl seen from the side shows its near wall over its far wall, and both face
# the camera. A point over both is on the near one.
bowl = merged("bowl", [(0, 0), (100, 0), (0, 100), (0, 0), (100, 0), (0, 100)],
              [8.0, 8.0, 8.0, 5.0, 5.0, 5.0], [(0, 1, 2), (3, 4, 5)], seed=6.0)
near = float(order._depths_at(bowl, [20.0], [20.0], True)[0])
check("overlapping front faces read the nearest", abs(near - 5.0) < 1e-9, f"got {near}")

# --- off the surface, the nearest sample still answers ---------------------
# The centre of what two parts share can fall between two patches of one of
# them. There is no surface there to read, and no reason to fail the pair.
off = float(order._depths_at(dict(body), [600.0], [50.0], True)[0])
check("a point off the surface falls back to the nearest sample", off == 20.0,
      f"got {off}")

# --- degenerate input ------------------------------------------------------
flat = merged("flat", [(0, 0), (50, 0), (100, 0)], [1.0, 2.0, 3.0], [(0, 1, 2)], seed=2.0)
val = float(order._depths_at(flat, [50.0], [0.0], True)[0])
check("an edge-on face covers no pixel and does not divide by zero",
      np.isfinite(val), f"got {val}")


# --- the triangles bake.py hands over --------------------------------------
class Plan:
    """Just the topology `_surface_tris` reads: a quad and a pentagon."""
    nfaces = 2
    nverts = 9
    loop_start = np.array([0, 4], dtype=np.int32)
    loop_total = np.array([4, 5], dtype=np.int32)
    loop_verts = np.array([0, 1, 2, 3, 4, 5, 6, 7, 8], dtype=np.int32)
    tri_face = None
    tri_verts = None


plan = Plan()
xy = np.column_stack([np.arange(9.0), np.arange(9.0) * 2.0])
depth = np.arange(9.0) + 1.0
both = bake._surface_tris(plan, np.array([True, True]), xy, depth)
check("a quad fans into two triangles and a pentagon into three",
      both is not None and both[0].shape == (5, 3),
      f"got {None if both is None else both[0].shape}")
check("...each fan pivoting on the face's first corner",
      both is not None and plan.tri_verts.tolist()
      == [[0, 1, 2], [0, 2, 3], [4, 5, 6], [4, 6, 7], [4, 7, 8]],
      f"got {plan.tri_verts.tolist()}")
quad_only = bake._surface_tris(plan, np.array([True, False]), xy, depth)
check("only faces facing the camera are handed over",
      quad_only is not None and quad_only[2].tolist() == [[1, 2, 3], [1, 3, 4]],
      f"got {None if quad_only is None else quad_only[2].tolist()}")
# Vertex 1 is only in the quad's first triangle; vertex 2 would be in both.
depth_hole = depth.copy()
depth_hole[1] = np.nan
cut = bake._surface_tris(plan, np.array([True, False]), xy, depth_hole)
check("a triangle with a vertex behind the camera is dropped, not passed on",
      cut is not None and cut[2].tolist() == [[1.0, 3.0, 4.0]],
      f"got {None if cut is None else cut[2].tolist()}")
check("nothing facing is None",
      bake._surface_tris(plan, np.array([False, False]), xy, depth) is None)

print()
print(f"{len(fails)} failure(s)" + (": " + ", ".join(fails) if fails else ""))
raise SystemExit(1 if fails else 0)
