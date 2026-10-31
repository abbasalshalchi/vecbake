# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""The baker: 3D scene + camera -> corresponded 2D keyframes.

This is the only module that simulates 3D, and it runs exactly once, offline.
Everything downstream is 2D interpolation.

Correspondence -- the hard part of any vector-animation format -- is solved by
construction here: an element's identity is a *mesh topology index* (edge 237
is always edge 237), not a silhouette stroke that gets re-traced every frame.
So there is never a matching problem; we just re-project the same points.

Requires topologically static geometry.  Deformation (armature, shape keys,
transforms, camera moves) is fine; anything that changes vertex/edge/face
counts mid-animation is rejected with a clear error rather than silently
producing garbage correspondence.
"""

from __future__ import annotations

import math

import numpy as np

from typing import NamedTuple

import bpy
from mathutils import Vector

from . import chain as chain_mod
from . import curves
from . import follow
from . import merge
from . import occlude
from . import order as order_mod
from . import outline as outline_mod
from . import parallel
from . import silhouette
from .ir import FILL, STROKE, Element, Track, VecScene
from .reduce import (
    blind_fade_samples,
    freeze_hidden,
    reduce_points,
    reduce_scalar,
    reduce_scalar_bounded,
)
from .settings import BakeSettings


class BakeError(Exception):
    pass


# ---------------------------------------------------------------------------
# scene inspection helpers
# ---------------------------------------------------------------------------

# Object types whose evaluated geometry is a mesh: everything the baker reads
# comes from `evaluated_get(dg).to_mesh()`, which a curve, a text and a surface
# answer exactly as a mesh does. Taking meshes only dropped them silently --
# 3,072 of a flashbang scene's 5,221 faces were bevelled curves, and the ray
# cast saw straight through where they should have been.
DRAWABLE = {"MESH", "CURVE", "FONT", "SURFACE"}


def drawable(obj) -> bool:
    return obj is not None and obj.type in DRAWABLE


def _has_geometry(obj, depsgraph) -> bool:
    """Whether the object evaluates to any vertices at all.

    A curve with no splines evaluates to no mesh, and an object with nothing
    to draw has no plan to make; asked of every type, not only curves.
    """
    ev = obj.evaluated_get(depsgraph)
    try:
        me = ev.to_mesh()
        return me is not None and len(me.vertices) > 0
    finally:
        ev.to_mesh_clear()


def collect_objects(context, settings: BakeSettings):
    if settings.objects:
        objs = list(settings.objects)
    else:
        objs = [o for o in context.scene.objects if drawable(o)]
        try:
            objs = [o for o in objs if o.visible_get()]
        except RuntimeError:
            pass  # no view layer (background mode) -- take them all
    depsgraph = context.evaluated_depsgraph_get()
    objs = [o for o in objs if drawable(o) and _has_geometry(o, depsgraph)]
    if not objs:
        raise BakeError("Nothing to bake. Select some meshes, curves, text or surfaces, "
                        "or make them visible.")
    return objs


# An untouched Principled BSDF and an untouched viewport colour are both this.
NEUTRAL_GREY = (0.8, 0.8, 0.8)

# How many surface samples a merged element hands the ordering pass. Enough to
# describe a part's curvature, few enough that the nearest-sample lookup stays
# cheap -- the pass asks for one point per pair of elements.
PROBE_CAP = 256


def _display_color(color):
    """Scene-linear -> sRGB, the space CSS and SVG hex actually mean.

    Blender stores every COLOR property in scene-linear and only converts for
    display, so the float behind a swatch reading #4c8cb8 is (0.072, 0.262,
    0.479). Writing those numbers straight into a hex triple yields #12437a --
    every colour markedly darker than the one that was picked, whatever the
    shading setting does. Convert on the way into the IR so all three emitters
    inherit the fix.
    """
    try:
        from mathutils import Color
        return tuple(Color(tuple(color[:3])).from_scene_linear_to_srgb())
    except (ImportError, AttributeError):
        out = []
        for c in color[:3]:
            c = max(0.0, min(1.0, c))
            out.append(c * 12.92 if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055)
        return tuple(out)


def _principled_base(mat):
    """(base colour, alpha) from an unlinked Principled BSDF, or None.

    Transparency in a Principled shader is the **Alpha socket**, not the fourth
    channel of Base Color. That fourth channel exists because the colour picker
    carries one around; the shader ignores it, so reading it would make a
    material look transparent in the bake and solid in a render.
    """
    if not (mat.use_nodes and mat.node_tree):
        return None
    for node in mat.node_tree.nodes:
        if node.type != "BSDF_PRINCIPLED":
            continue
        base = node.inputs.get("Base Color")
        if base is None or base.is_linked:
            continue
        alpha = node.inputs.get("Alpha")
        value = 1.0
        if alpha is not None and not alpha.is_linked:
            try:
                value = float(alpha.default_value)
            except (TypeError, ValueError):
                value = 1.0
        return tuple(base.default_value[:3]), max(0.0, min(1.0, value))
    return None


def _is_default(color):
    return all(abs(c - d) < 1e-4 for c, d in zip(color, NEUTRAL_GREY))


def _material_color(mat, default, source="AUTO", use_alpha=True):
    """Resolve a material to one flat colour and one opacity.

    Blender 5 keeps `use_nodes` on for every material, so naively trusting the
    Principled base colour flattens any scene authored with viewport display
    colours to a uniform grey. AUTO takes the shader colour when someone has
    actually set it and falls back to the viewport colour otherwise, which is
    the behaviour both authoring styles expect.

    Opacity comes from wherever the colour came from, never mixed: the shader's
    Alpha socket alongside a shader colour, `diffuse_color`'s fourth channel
    alongside a viewport colour. Taking the colour from one and the opacity from
    the other would give a result that matches neither authoring style.
    """
    if mat is None:
        return default, 1.0
    viewport = tuple(mat.diffuse_color[:3])
    viewport_alpha = float(mat.diffuse_color[3]) if len(mat.diffuse_color) > 3 else 1.0
    found = _principled_base(mat)
    base, base_alpha = found if found is not None else (None, 1.0)

    if source == "MATERIAL":
        color, alpha = (base, base_alpha) if base is not None else (viewport, viewport_alpha)
    elif source == "VIEWPORT":
        color, alpha = viewport, viewport_alpha
    elif base is not None and not _is_default(base):
        color, alpha = base, base_alpha
    else:
        color, alpha = viewport, viewport_alpha
    return color, (alpha if use_alpha else 1.0)


def _edge_attribute(me, name, count, dtype):
    """Read an edge-domain attribute, or zeros when it was never created."""
    out = np.zeros(count, dtype=dtype)
    if not count:
        return out
    attr = me.attributes.get(name)
    if attr is None or attr.domain != "EDGE":
        return out
    try:
        attr.data.foreach_get("value", out)
    except (TypeError, RuntimeError):
        for i, item in enumerate(attr.data):
            if i >= count:
                break
            out[i] = item.value
    return out


def _topology_signature(me):
    return (len(me.vertices), len(me.edges), len(me.polygons), len(me.loops))


def _modifier_input(mod, identifier):
    """What a geometry-nodes modifier holds for one input socket, or None.

    Blender 5.2 moved these off the modifier's ID properties and onto
    `modifier.properties.inputs`, one struct per socket identifier. Both paths
    are read because the old one does not fail loudly on the new Blender: the
    modifier still accepts a subscript and reports the socket as *unset*, which
    is indistinguishable from a socket nobody configured. A reader written for
    5.1 therefore gets a plausible answer and falls back to a default.

    That is not hypothetical. Every Smooth by Angle modifier on the Voyager
    probe carries 73.59 degrees; under 5.2 they all read as unset, the fallback
    supplied 30, and the bake drew 1095 more strokes than the same file drew on
    5.1 -- with nothing anywhere reporting that a value had been guessed.
    """
    inputs = getattr(getattr(mod, "properties", None), "inputs", None)
    socket = getattr(inputs, identifier, None) if inputs is not None else None
    value = getattr(socket, "value", None) if socket is not None else None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    try:
        held = mod[identifier]
    except (KeyError, TypeError):
        return None
    if isinstance(held, (int, float)) and not isinstance(held, bool):
        return float(held)
    return None


def _smooth_by_angle(obj):
    """(degrees, was it read) from a Smooth by Angle modifier, or (None, True).

    The second half matters as much as the first. An angle that had to be
    guessed changes which edges are creases, and creases are most of what gets
    drawn -- so a guess is a different picture, not a rounding error, and the
    caller reports how often it happened rather than letting it pass.
    """
    for mod in getattr(obj, "modifiers", ()):
        if getattr(mod, "type", "") != "NODES":
            continue
        group = getattr(mod, "node_group", None)
        if group is None or "smooth by angle" not in (group.name or "").lower():
            continue
        try:
            items = group.interface.items_tree
        except AttributeError:
            items = ()
        for item in items:
            if (getattr(item, "item_type", "") == "SOCKET"
                    and getattr(item, "in_out", "") == "INPUT"
                    and (item.name or "").lower() == "angle"):
                value = _modifier_input(mod, item.identifier)
                if value is not None:
                    return math.degrees(value), True
                break
        return 30.0, False     # present but unreadable: Blender's own default
    return None, True


def _shading(obj, me, nfaces):
    """(per-face flat flags, mode, degrees, was the angle read) for this object.

    Blender 4.1 moved smoothing out of a per-face flag into attributes plus a
    "Smooth by Angle" node modifier, so both are read: `sharp_face` marks the
    faces set to flat, and the modifier -- when present -- carries the angle it
    uses. We reuse that angle but apply it to the turn along the drawn path
    rather than to the dihedral between faces: what matters here is whether the
    *line* bends, not whether the surface creases. A cylinder's rim is one
    smooth circle even though every edge on it is a hard crease.
    """
    sharp = np.zeros(nfaces, dtype=bool)
    attributes = getattr(me, "attributes", None)
    attr = attributes.get("sharp_face") if attributes is not None else None
    if attr is not None and nfaces:
        try:
            attr.data.foreach_get("value", sharp)
        except (TypeError, AttributeError, RuntimeError):
            sharp[:] = False

    angle, read = _smooth_by_angle(obj)
    if angle is not None:
        return sharp, "ANGLE", angle, read
    if nfaces and sharp.all():
        return sharp, "FLAT", 0.0, True
    return sharp, "SMOOTH", 180.0, True


def _loop_totals(me):
    n = len(me.polygons)
    starts = np.empty(n, dtype=np.int32)
    me.polygons.foreach_get("loop_start", starts)
    totals = np.empty(n, dtype=np.int32)
    if n:
        totals[:-1] = starts[1:] - starts[:-1]
        totals[-1] = len(me.loops) - starts[-1]
    return starts, totals


def adopt_welds(plan, obj, me, index, q):
    """Give one plan its half of the weld index."""
    if not index or not plan.nedges:
        return 0
    nv = len(me.vertices)
    co = np.empty(nv * 3, dtype=np.float64)
    me.vertices.foreach_get("co", co)
    m = np.array(obj.matrix_world, dtype=np.float64)
    world = co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3]
    found = 0
    for e in np.nonzero(plan.edge_face_count < 2)[0]:
        a, b = plan.edge_verts[e]
        pa = tuple(np.round(world[a] / q).astype(np.int64))
        pb = tuple(np.round(world[b] / q).astype(np.int64))
        key = (pa, pb) if pa <= pb else (pb, pa)
        partners = index.get(key)
        if not partners:
            continue
        far = next(((name, n, c, f) for name, idx, n, c, f in partners
                    if (name, idx) != (obj.name, int(e)) and n is not None), None)
        if far is None:
            continue
        other_name, other, other_c, other_face = far
        plan.edge_welded[e] = True
        plan.edge_weld_normal[e] = other
        if other_c is not None:
            plan.edge_weld_center[e] = other_c
        # Who is on the other side, so the ordering pass can say what that
        # object's own fill is allowed to do to this edge. See edge_weld_keys.
        plan.edge_weld_partner[e] = (other_name, other_face)
        # Both halves hold the same edge, so both would draw it, and a crease
        # down a cut would come out twice as heavy as the same crease anywhere
        # else. One side owns it: the first object name in the pair, which is
        # stable across a re-bake and does not depend on evaluation order.
        owner = min((name, idx) for name, idx, _, _, _ in partners)
        plan.edge_weld_owner[e] = (owner == (obj.name, int(e)))
        found += 1
    return found


def weld_index(objects, depsgraph, settings):
    """Find boundary edges that different objects share, and pair them up.

    Splitting a mesh so its pieces each merge is the cheapest way to cut fills
    -- one merged fill against a fill per face -- and it costs a line. The cut
    leaves an open boundary on both halves, boundary edges are drawn, and a
    seam appears across a surface that has none.

    Nothing about that line is real. The two halves still meet exactly: the cut
    duplicated the vertices, it did not move them. So an edge on one piece whose
    endpoints coincide with an edge on another is not a boundary at all, it is
    an interior edge that happens to be stored twice, and it should be judged
    the way every other interior edge is -- by the angle between the faces on
    either side.

    Returns `{(rounded endpoints): [(object name, edge index, face normal)]}`,
    from which each plan takes its own half. Rounded, because the two copies are
    bit-identical only until a modifier or an object transform touches one of
    them; the quantum is `weld_px` in scene units.

    Costs one mesh evaluation per object, once, before sampling starts. Only
    boundary edges are indexed -- interior ones already know their neighbour.

    On part_018.001 -- a closed solid, 62 faces, split along its 80 sharp edges
    into 5 pieces, 8 frames -- which is the case this is for:

                    elements   fills   strokes
      whole               52      41        11
      cut, weld off       42       4        38
      cut, weld on        19       4        15

    Fills 41 to 4, and the strokes end up where they started. Off, the cut draws
    a heavy ring around the seam; on, the ring is gone.

    The ring the *outline* draws is a separate one, and it survived all of the
    above because it is not an edge decision at all. A merged outline is a
    stroked fill, `merge.silhouette_edges` counts drawn faces, and a cut piece's
    rim has exactly one -- so the cut joins that piece's silhouette and both
    halves paint it in the outline colour. Exempting welded edges there does not
    work: the loop then fails to close and `_loops` drops it, and the piece
    loses its fill altogether. So the fill keeps the cut and the stroke gives it
    up, per span, via `_seam_spans` and the `sw` mask the player and the static
    SVG both honour. On a cube cut in two that is the whole of the seam: 1,710
    differing pixels down to 4.

    Two marks survive on that bake and they are *not* a weld failure, though it
    took three wrong answers to establish it. Leave-one-out over every element
    puts them on `part_018.001/e19` and `/c0_1` -- strokes, not fills. Those edges
    are welded, are owned, and their measured dihedral is 90.00 degrees: piece
    001 is the end cap, normal (1, 0, 0), and its partner across the weld is a
    tube facet at (0, 0.459, -0.888). The cap meets the body at a right angle,
    the shading threshold here is 73.59 degrees, so the edge is a crease and
    drawing it is correct. Welding found it, measured it, and kept it on purpose.

    Which sets the rule for where to cut. A cut along an edge the bake already
    considers a crease is free -- the line was there anyway. A cut along a
    genuinely sharp edge that the bake did *not* draw, because the sharp angle
    sits below the shading threshold, is also free. What is not free is
    assuming the two thresholds agree: this test split at 30 degrees while the
    bake creases at 73.59, so it cut plenty of edges the bake calls smooth and
    a few, like the cap rim, that it does not.

    `reachable` had to learn about welds too, and that closed the question. Its
    job is "is any face touching this edge being drawn", and for a welded edge
    the second face lives on another object, so it was judging against half the
    surface. Feeding the partner's facing in -- from the normal and centre the
    weld already recorded -- brings the split part's stroke set into line with
    the whole part's: the cap rim that appeared as two chevrons is now the whole
    ellipse, which is what the whole part draws as well. Render either bake's
    strokes on their own and both show it.

    That left the picture 9.5% different from the whole part against 14.5%
    unwelded, and none of the remainder was a seam: it was paint order. Chased,
    because it looks exactly like one. Two faults, and they are the same fault
    upside down -- `edge_face_keys` asserts "a fill can never paint over an edge
    that lies on it", which a merged fill cannot honour, because one opaque
    silhouette draws a part's near wall and its far wall at a single depth. The
    cap's rim at the near end was eaten by the tube's fill; the rim at the far
    end was drawn straight through it.

    Only a weld can separate those two, and it already had what it needed --
    the far face's normal and centre, so whether that face points at the camera
    is a per-frame fact. Front-facing means the edge is on the neighbour's near
    wall and its fill must stay behind; facing away means the edge is round the
    back and that same fill is the thing that should be hiding it. See
    `ObjectPlan.edge_weld_keys` and the `under_faces` half of `order.resolve`.
    Chained strokes carry the same assertions, and a chain that straddles the
    neighbour's silhouette asserts neither direction.

    Measured, ink differing from the whole part over the clip:

                    before   after
      weld off      14.54%  14.54%
      weld on        9.53%   7.53%

    A closed test says the rest is not weld business either. A cube cut in two
    is the case where the whole part *also* merges, so identity is reachable:
    with the user's own settings the cut differs from the joined cube by **4
    pixels of 358,264**, and all four are the near half's fill clipping the far
    half's outline cap where the cut meets the silhouette. On part_018 the
    whole part never merges -- 41 face fills against 4 merged ones -- so the
    two are different pictures by construction and 7.5% is what that costs, not
    a defect to chase.

    Union-merging the pieces' outlines would close that gap and is the wrong
    move: the pieces merge *because* they were cut, and the union is the whole
    part's silhouette, which is exactly the thing that would not ring.

    Measured on part_012.001, split along its 186 sharp edges into 41 pieces and
    baked over 8 frames:

                    elements   fills   strokes
      whole              203      97       106
      cut, weld off      250      62       188
      cut, weld on       154      62        92

    The fills are why anyone would cut: 97 face fills become 62, because pieces
    that ring cleanly merge where the whole part never could. The strokes are
    what it costs, and welding is what stops it costing them -- 188 back down to
    77, and the difference between the two cut bakes is exactly the seam lines.

    What welding does *not* undo: a merged fill is an opaque silhouette. Drawn
    face by face, part_012 reads as an open tube with its far rim visible
    through it; drawn as merged pieces it is a solid cylinder. That is what
    merging means, not a fault in the weld, and it is the real limit on this as
    a strategy -- cut the parts that should read solid, and leave the hollow
    ones alone.
    """
    if not getattr(settings, "weld_seams", False):
        return {}
    q = max(1e-9, float(getattr(settings, "weld_tol", 1e-4)))
    index: dict = {}
    for obj in objects:
        eval_obj = obj.evaluated_get(depsgraph)
        me = eval_obj.to_mesh()
        try:
            edge_verts, adj_edge, adj_face = _edge_face_adjacency(me)
            if not len(edge_verts):
                continue
            count = np.zeros(len(edge_verts), dtype=np.int32)
            np.add.at(count, adj_edge, 1)
            open_edges = np.nonzero(count < 2)[0]
            if not len(open_edges):
                continue
            face_of = {}
            for e, f in zip(adj_edge, adj_face):
                face_of.setdefault(int(e), int(f))
            nv = len(me.vertices)
            co = np.empty(nv * 3, dtype=np.float64)
            me.vertices.foreach_get("co", co)
            co = co.reshape(-1, 3)
            m = np.array(obj.matrix_world, dtype=np.float64)
            world = co @ m[:3, :3].T + m[:3, 3]
            nf = len(me.polygons)
            normals = np.empty(nf * 3, dtype=np.float64)
            me.polygons.foreach_get("normal", normals)
            normals = normals.reshape(-1, 3) @ np.linalg.inv(m[:3, :3])
            lens = np.linalg.norm(normals, axis=1, keepdims=True)
            normals = normals / np.where(lens < 1e-12, 1.0, lens)
            # centres as well: whether the far face points at the camera is a
            # perspective question, and that needs a position, not just a normal
            centers = np.empty(nf * 3, dtype=np.float64)
            me.polygons.foreach_get("center", centers)
            centers = centers.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3]
            for e in open_edges:
                a, b = edge_verts[e]
                pa = tuple(np.round(world[a] / q).astype(np.int64))
                pb = tuple(np.round(world[b] / q).astype(np.int64))
                key = (pa, pb) if pa <= pb else (pb, pa)
                fi = face_of.get(int(e))
                index.setdefault(key, []).append(
                    (obj.name, int(e),
                     normals[fi] if fi is not None else None,
                     centers[fi] if fi is not None else None,
                     -1 if fi is None else int(fi)))
        finally:
            eval_obj.to_mesh_clear()
    # Any open edge that meets another open edge at the same place is a seam,
    # including one on the *same* object. Requiring two different objects was a
    # bug: splitting along sharp edges and separating by loose parts leaves
    # plenty of cuts that did not disconnect anything -- a slit through a piece,
    # not a division of it -- and both sides of those stayed boundaries and drew.
    return {k: v for k, v in index.items() if len(v) > 1}


def _edge_face_adjacency(me):
    """Flat (edge_index, face_index) pairs. Built once -- topology is static."""
    lookup = {}
    ev = np.empty(len(me.edges) * 2, dtype=np.int32)
    me.edges.foreach_get("vertices", ev)
    ev = ev.reshape(-1, 2)
    for i, (a, b) in enumerate(ev):
        lookup[(int(a), int(b)) if a < b else (int(b), int(a))] = i
    e_idx, f_idx = [], []
    for poly in me.polygons:
        for key in poly.edge_keys:
            k = key if key[0] < key[1] else (key[1], key[0])
            ei = lookup.get(k)
            if ei is not None:
                e_idx.append(ei)
                f_idx.append(poly.index)
    return ev, np.array(e_idx, dtype=np.int32), np.array(f_idx, dtype=np.int32)


# ---------------------------------------------------------------------------
# per-object static data
# ---------------------------------------------------------------------------

class ObjectPlan:
    """Everything about one object that does not change across frames."""

    def __init__(self, obj, me, settings: BakeSettings):
        self.name = obj.name
        self.group = obj.get("vecbake_group", obj.name)
        self.signature = _topology_signature(me)
        self.nverts = len(me.vertices)

        # per-object overrides via custom properties. The useful one is
        # vecbake_strokes=False on a subdivided ground plane: you want the
        # subdivision for depth sorting, but not a grid drawn on the floor.
        self.strokes = bool(obj.get("vecbake_strokes", settings.strokes))
        self.fills = bool(obj.get("vecbake_fills", settings.fills))
        self.merge_fill = bool(obj.get("vecbake_merge_fill", settings.merge_fills))
        # Per-object shading override. Turning it off is what makes a part read
        # as one flat silhouette whether or not its fills were merged, which is
        # how a merged and an unmerged bake of the same object can be cut
        # between without the facets popping in.
        self.shading = bool(obj.get("vecbake_shading", settings.shading))
        # Slots for the merged outline. `outline_n` is fixed before the pass
        # that writes geometry; `outline_need` is what the measuring pass
        # found this object actually wants -- see bake_iter.
        self.outline_n = None
        self.outline_need = 0
        self.outline_prev = None   # (anchors, points, ring, outline)
        # A merged fill may carry several subpaths: a nozzle's mouth cut out of
        # its bell, the four bells of a thruster quad. `outline_parts` is how
        # the total splits between them and is fixed for the clip exactly as
        # the total is; the rest is per-subpath state, one entry each.
        self.outline_parts = None    # [points per subpath], None for one ring
        self.outline_prevs = None    # one carry state per subpath
        self.piece_verts = None      # each slot's vertices when last seen, to match pieces
        self.piece_present = {}      # frame -> which subpath slots held a piece
        self.piece_needs = None      # measuring: points each subpath earns
        # How far the best possible interpolation of this outline lands from
        # the real silhouette between keyframes, and what drawing the faces
        # instead would cost over the same gaps. Filled by the measuring pass.
        self.carry_cost = 0.0
        # Whether this outline is drawn with the area refinement. Decided by
        # measurement, per object, and only asked about where the outline is
        # already drawing badly -- see try_refine.
        self.use_refine = False
        self.face_cost = 0.0
        # Pixels of outline a multi-subpath merged fill draws between keyframes
        # where the part has none and nothing nearer covers it, over the clip.
        self.false_seen = 0.0
        self.edge_mode = str(obj.get("vecbake_edge_mode", settings.edge_mode))

        # kept for chaining: junction pairing needs edge directions
        co = np.empty(self.nverts * 3, dtype=np.float64)
        me.vertices.foreach_get("co", co)
        self.vert_co = co.reshape(-1, 3)
        self.matrix3 = np.array(obj.matrix_world, dtype=np.float64)[:3, :3]
        # The curve classifier measures 3D angles, which only survive a
        # non-uniform object scale if the scale is applied first.
        self.vert_co_world = self.vert_co @ self.matrix3.T

        self.loop_start, self.loop_total = _loop_totals(me)
        self.face_of_loop = None       # built on demand, see _surface_probe
        self.tri_face = None           # and these, see _surface_tris
        self.tri_verts = None
        self.loop_verts = np.empty(len(me.loops), dtype=np.int32)
        me.loops.foreach_get("vertex_index", self.loop_verts)

        self.edge_verts, self.adj_edge, self.adj_face = _edge_face_adjacency(me)
        self.nedges = len(self.edge_verts)
        self.nfaces = len(me.polygons)

        # how many faces touch each edge -- distinguishes boundary from interior
        self.edge_face_count = np.zeros(self.nedges, dtype=np.int32)
        if len(self.adj_edge):
            np.add.at(self.edge_face_count, self.adj_edge, 1)

        # "Loose" in the sense that matters to curving: nothing on the other
        # side. Covers a wire edge with no face at all and the open border of a
        # single plane alike -- neither has a surface whose shading could say
        # whether the edge should bend.
        self.edge_loose = self.edge_face_count < 2

        # the two faces flanking each interior edge, for the crease angle
        self.edge_face_a = np.full(self.nedges, -1, dtype=np.int32)
        self.edge_face_b = np.full(self.nedges, -1, dtype=np.int32)
        for edge_index, face_index in zip(self.adj_edge, self.adj_face):
            if self.edge_face_a[edge_index] < 0:
                self.edge_face_a[edge_index] = face_index
            elif self.edge_face_b[edge_index] < 0:
                self.edge_face_b[edge_index] = face_index
        self.edge_has_pair = (self.edge_face_a >= 0) & (self.edge_face_b >= 0)

        # Filled by `adopt_welds` when Weld Seams is on: which open edges are
        # really interior edges shared with another object, and the normal of
        # the face on the far side, so the crease angle can still be measured.
        self.edge_welded = np.zeros(self.nedges, dtype=bool)
        self.edge_weld_owner = np.zeros(self.nedges, dtype=bool)
        self.edge_weld_normal = np.zeros((self.nedges, 3), dtype=np.float64)
        self.edge_weld_center = np.zeros((self.nedges, 3), dtype=np.float64)
        # (partner object name, its face index) per welded edge, or None.
        self.edge_weld_partner = [None] * self.nedges
        # frame -> the welded edges the neighbour's fill is allowed to hide.
        # Kept because chaining rebuilds an item's assertions long after the
        # frame that could work them out, and only a few edges are ever welded.
        self.weld_cover_frames = {}
        # (vertex pair) -> edge index, built once when the merged outline first
        # needs to ask whether a span of its ring lies on a weld.
        self.edge_of_pair = None
        # How many drawn frames put each outline slot's outgoing span on a
        # weld, and how many drawn frames there were. See seam_mask.
        self.seam_hits = None
        self.seam_frames = 0

        # edges the artist marked sharp in Blender count as creases whatever
        # the angle says
        self.edge_marked_sharp = np.zeros(self.nedges, dtype=bool)
        if self.nedges:
            try:
                me.edges.foreach_get("use_edge_sharp", self.edge_marked_sharp)
            except (TypeError, AttributeError):
                pass

        # how Blender itself is shading this object, for curve_mode SHADING
        (self.face_sharp, self.shade_mode, self.shade_angle,
         self.shade_angle_read) = _shading(obj, me, self.nfaces)

        self.crease_angle = float(obj.get("vecbake_crease_angle", settings.crease_angle))

        # per-face colour from its material
        default = tuple(settings.default_fill)
        mats = [None] * max(1, len(obj.material_slots))
        for i, slot in enumerate(obj.material_slots):
            mats[i] = slot.material
        mat_idx = np.zeros(self.nfaces, dtype=np.int32)
        if self.nfaces:
            me.polygons.foreach_get("material_index", mat_idx)
        if settings.use_material_color:
            resolved = [_material_color(m, default, settings.color_source,
                                        settings.material_alpha) for m in mats]
        else:
            resolved = [(default, 1.0) for _ in mats]
        resolved = resolved or [(default, 1.0)]
        palette = [_display_color(c) for c, _a in resolved]
        alphas = [a for _c, a in resolved]
        self.face_color = [palette[min(int(i), len(palette) - 1)] for i in mat_idx]
        # Opacity rides the existing per-element opacity track rather than
        # needing one of its own, and the painter's order already draws far to
        # near, which is exactly what blending translucent fills requires.
        self.face_alpha = np.array(
            [alphas[min(int(i), len(alphas) - 1)] for i in mat_idx], dtype=np.float64)

        stroke = obj.get("vecbake_stroke", None)
        self.stroke_color = _display_color(stroke[:3] if stroke else settings.stroke_color)

        # optional per-edge colour taken from the face the edge borders
        self.edge_colors = None
        if settings.stroke_source == "MATERIAL" and stroke is None and self.nedges:
            keep = 1.0 - max(0.0, min(1.0, settings.stroke_darken))
            first_face = np.full(self.nedges, -1, dtype=np.int32)
            for edge_index, face_index in zip(self.adj_edge, self.adj_face):
                if first_face[edge_index] < 0:
                    first_face[edge_index] = face_index
            self.edge_colors = [
                tuple(c * keep for c in self.face_color[first_face[i]])
                if first_face[i] >= 0 else self.stroke_color
                for i in range(self.nedges)
            ]

        # Freestyle marks and creases are generic attributes in Blender 5, not
        # MeshEdge properties, and they only exist once something has been
        # marked -- so absence is normal and means "none".
        self.edge_freestyle = _edge_attribute(me, "freestyle_edge", self.nedges, bool)
        self.edge_crease = _edge_attribute(me, "crease_edge", self.nedges, float)

    def edge_weld_keys(self, edge_index, may_cover):
        """What the object across a weld may do to this edge, this frame.

        A welded edge is an interior edge stored twice, so it lies *on* the
        other piece's surface as much as on this one -- but which side of that
        surface decides everything, and a merged fill cannot say. One opaque
        silhouette covers a part's near wall and its far wall alike, at one
        depth, so the same fill has to paint over the rim at the far end and
        stay behind the rim at the near end. `on_faces` can only assert the
        second, and asserting it everywhere is what drew the far rim straight
        through the neighbour.

        `may_cover` is the one case where the neighbour's fill is entitled to
        hide this edge: its face across the weld points away from the camera.

        A finer rule was tried and is worse. Where the far face points away and
        this one does not, the edge is the neighbour's *silhouette* -- a front
        face meeting a back one -- so arguably its fill ends there rather than
        covering it. Nine strokes of part_018.002 are buried under
        part_018.005 that way and the whole part draws all nine. Exempting the
        silhouette case anyway costs more than it buys: against the whole part,
        7.67% of ink differs under this rule, 8.30% exempting it, and 8.32%
        leaving it unasserted for sampling to settle. At a silhouette the fill's
        own boundary is on the line, so "covered" is the better guess even
        where it is visibly wrong.

        Returns `(keys the partner must stay behind, keys that may cover this)`,
        each key offered in both spellings because whether the partner merged is
        not known here. `resolve` takes whichever names a real element.
        """
        partner = self.edge_weld_partner[edge_index]
        if partner is None:
            return (), ()
        name, face = partner
        keys = ((f"{name}/m",) if face < 0
                else ((f"{name}/m", f"{name}/f{face}"),))
        if face < 0:
            keys = ((f"{name}/m",),)
        return ((), keys) if may_cover else (keys, ())

    def edge_face_keys(self, edge_index):
        """Element keys of the surfaces this edge lies on, if they are drawn.

        A merged object draws one fill for its whole silhouette, so the faces
        this edge borders are not elements any more and naming them asserts
        nothing: no element in the frame answers to those keys. That is how
        merging quietly dropped this assertion for most of the scene. The
        strokes a merged object keeps are its creases, sharp edges and open
        boundaries -- interior detail lying *on* the surface the merged fill
        draws, so that fill can no more paint over them than a face can paint
        over its own border.
        """
        if not self.fills:
            return ()
        if self.merge_fill:
            return (f"{self.name}/m",)
        return tuple(
            f"{self.name}/f{int(face)}"
            for face in (self.edge_face_a[edge_index], self.edge_face_b[edge_index])
            if face >= 0
        )

    def check(self, me):
        if _topology_signature(me) != self.signature:
            raise BakeError(
                f"'{self.name}' changes topology during the animation "
                f"{self.signature} -> {_topology_signature(me)}. Keyframe interpolation "
                "needs a stable point identity, so topology-changing modifiers "
                "(boolean, remesh, decimate with animated ratio, build) must be "
                "applied or disabled before baking."
            )


# ---------------------------------------------------------------------------
# projection
# ---------------------------------------------------------------------------

def camera_matrices(scene, cam, depsgraph, width, height):
    # Always read the *evaluated* camera: a parented, constrained or driven
    # camera has a stale matrix_world on the original datablock, which silently
    # bakes the whole animation from the wrong viewpoint.
    r = scene.render
    cam = cam.evaluated_get(depsgraph)
    view = np.array(cam.matrix_world.inverted(), dtype=np.float64)
    proj = np.array(
        cam.calc_matrix_camera(
            depsgraph,
            x=width,
            y=height,
            scale_x=r.pixel_aspect_x,
            scale_y=r.pixel_aspect_y,
        ),
        dtype=np.float64,
    )
    return view, proj @ view


def project(world, view, mvp, width, height):
    """world (N,3) -> pixel xy (N,2), camera-axis depth (N,), in-front mask."""
    n = len(world)
    homo = np.empty((n, 4), dtype=np.float64)
    homo[:, :3] = world
    homo[:, 3] = 1.0
    clip = homo @ mvp.T
    w = clip[:, 3]
    safe = np.where(np.abs(w) < 1e-12, 1e-12, w)
    ndc_x = clip[:, 0] / safe
    ndc_y = clip[:, 1] / safe
    px = (ndc_x * 0.5 + 0.5) * width
    py = (1.0 - (ndc_y * 0.5 + 0.5)) * height
    cam_space = world @ view[:3, :3].T + view[:3, 3]
    depth = -cam_space[:, 2]
    in_front = depth > 1e-6
    return np.stack([px, py], axis=1), depth, in_front


# ---------------------------------------------------------------------------
# the bake
# ---------------------------------------------------------------------------

def bake(context, settings: BakeSettings, report=None) -> VecScene:
    """Run a whole bake, blocking. `report(phase, done, total)` if given."""
    steps = bake_iter(context, settings)
    try:
        while True:
            phase, done, total = next(steps)
            if report:
                report(phase, done, total)
    except StopIteration as finished:
        return finished.value


def bake_iter(context, settings: BakeSettings):
    """Step-wise bake, so a caller can keep the UI alive between chunks.

    Yields `(phase, done, total)` after each unit of work and returns the
    finished VecScene. Closing the generator early unwinds it, which restores
    the scene's current frame just as a completed run does.
    """
    scene = context.scene
    cam = settings.camera or scene.camera
    if cam is None:
        raise BakeError("The scene has no active camera.")
    if cam.type != "CAMERA":
        raise BakeError(f"'{cam.name}' is not a camera.")

    width, height = settings.resolved_size(scene)
    start, end, step = settings.resolved_range(scene)
    if end < start:
        raise BakeError(f"Frame range {start}..{end} is empty.")
    fps = scene.render.fps / scene.render.fps_base
    frames = list(range(start, end + 1, step))
    # A step that does not divide the range evenly would otherwise stop short --
    # 1..60 by 10 samples 1..51 and silently drops the last nine frames of the
    # move. The end frame is the one the animation has to land on, so it is
    # always sampled, even when that makes the final gap shorter than the step.
    if frames[-1] != end:
        frames.append(end)
    original_frame = scene.frame_current

    objects = collect_objects(context, settings)
    light = np.array(settings.light_dir, dtype=np.float64)
    norm = np.linalg.norm(light)
    light = light / norm if norm > 1e-9 else np.array([0.0, 0.0, 1.0])

    plans: dict[str, ObjectPlan] = {}
    elements: list[Element] = []
    slots: dict[str, dict] = {}   # element key -> sample buffers

    # Ordering has to run *after* chaining, since chaining changes what the
    # elements are -- so the per-frame data it needs is collected first and the
    # resolve pass happens once sampling is done.
    collect_items = settings.accurate_order or settings.chain_edges
    frame_items: list = []
    frame_ortho: list = []
    vertex_cache: list = []

    # Objects whose merged outline turned out not to survive the camera move.
    # Filled by the first pass, honoured by the second -- see below.
    no_merge: set[str] = set()

    # {name: {frame index: (dmin, dmax, x0, x1, y0, y1) or None}} -- how much
    # depth each object's own geometry occupies, for the deep-body test after
    # the first sampling pass. Keyed by frame rather than appended: the pass
    # runs more than once, and an object with nothing finite in a frame must
    # leave a gap there instead of shifting every later frame onto the wrong
    # one.
    depth_extent: dict[str, dict[int, tuple | None]] | None = (
        {} if settings.split_span_pct > 0.0 else None)

    carry_state = {"prev": {}, "mid": {}, "drew": {}, "pieces": {}}
    drawn_names = {o.name for o in objects}

    def judge_carry(frame, fi, depsgraph, view, mvp, cam_pos, cam_fwd, is_ortho):
        """Does each merged outline actually survive the gap to the next frame?

        Asked of the shape halfway between two frames -- which no keyframe
        holds and Blender will evaluate on request -- rather than guessed at
        from something easier to reach.

        It is asked of the points that were really written, not of an idealised
        stand-in. An earlier version scored a dense even correspondence around
        each ring, on the reasoning that no placement rule could do better. It
        could not do better on a plain ring, but it had nothing to say about a
        ring that pinches: on part_079, a V-shaped stick whose boundary touches
        itself, it reported 2.91px across a gap where the outline as written
        lands 11.53px from the truth, and the object merged on the strength of
        it. The legs of the V trade places there and the score never saw it,
        because the correspondence it scored was not the one being drawn.

        So this runs in the pass that writes geometry, reads that geometry back
        out of the slots, and compares its midpoint against the silhouette that
        is really there. Two numbers per object: what the outline as drawn
        costs across the gap, and what drawing the object's faces would cost
        instead.
        """
        def look(dg, v, m, cp, cf, ortho, pieces=None):
            out = {}
            for obj in objects:
                plan = plans.get(obj.name)
                if plan is None or not (plan.fills and plan.merge_fill):
                    continue
                if obj.name in no_merge:
                    continue
                ev = obj.evaluated_get(dg)
                me = ev.to_mesh()
                try:
                    ring, poly, xy = silhouette_of(plan, me, ev, v, m,
                                                   width, height,
                                                   cp, cf, ortho)
                    out[obj.name] = (ring, poly, xy.copy())
                    if (pieces is not None and plan.outline_parts
                            and len(plan.outline_parts) > 1):
                        pieces[obj.name] = silhouette_pieces_of(
                            plan, me, ev, v, m, width, height, cp, cf, ortho)
                finally:
                    ev.to_mesh_clear()
            return out

        def written():
            """The merged outlines as this pass has just written them."""
            out = {}
            for name, plan in plans.items():
                if not (plan.fills and plan.merge_fill) or name in no_merge:
                    continue
                slot = slots.get(f"{name}/m")
                if slot is None or slot["vis"][fi] <= 0.0:
                    continue
                geo = slot["geo"][fi]
                if geo is None or len(geo) < 6:
                    continue
                out[name] = np.asarray(geo, dtype=np.float64).reshape(-1, 2)
            return out

        here = look(depsgraph, view, mvp, cam_pos, cam_fwd, is_ortho)
        drew = written()
        was, half = carry_state["prev"], carry_state["mid"]
        prev_drew = carry_state["drew"]
        for name, (ring, poly, xy) in here.items():
            if name not in was or name not in half:
                continue
            plan = plans[name]
            before_xy = was[name][2]
            _, middle, middle_xy = half[name]
            # The outline as drawn, halfway between the two keyframes, against
            # the silhouette that is really there. This is what a viewer sees
            # between two frames, so it is what the decision is made on.
            #
            # One ring only. A multi-subpath outline is judged by Merge False
            # Outline below, as sizing already leaves it out of `_gap_cost`:
            # scored here it was read as one loop with its rings joined end to
            # end, against `walk_loop`'s bridged ring -- which `walk_loop` only
            # gives for two pieces. That drops each ring's closing segment and
            # adds two chords across the body, so a torus whose rim and hole
            # are drawn within 0.3 px of the truth scored 13.2 px and was
            # refused (docs/audits.md section 22; section 10 measured the same
            # joining on the thrusters).
            single = not (plan.outline_parts and len(plan.outline_parts) > 1)
            if single and middle is not None and name in drew and name in prev_drew:
                a, b = prev_drew[name], drew[name]
                if a.shape == b.shape:
                    plan.carry_cost = max(
                        plan.carry_cost,
                        outline_mod.stray(0.5 * (a + b), middle))
            if before_xy.shape == xy.shape == middle_xy.shape:
                drawn = 0.5 * (before_xy + xy)
                plan.face_cost = max(plan.face_cost, float(
                    np.linalg.norm(drawn - middle_xy, axis=1).max()))

        # A multi-subpath outline has no single ring to score against, so the
        # gap test above never sees it. What it gets wrong between keyframes is
        # line where the part has no outline -- a ring half grown, points
        # sliding across a corner -- and what matters of that is the part a
        # viewer can see. Summed over the clip and compared with
        # `merge_false_px`; see docs/audits.md section 13 for the measurement.
        false_now = {}
        if settings.merge_false_px > 0.0 and fi > 0:
            allow = settings.tol_px * 1.25
            for name, truth in carry_state["pieces"].items():
                plan = plans[name]
                if name not in drew or name not in prev_drew:
                    continue
                a, b = prev_drew[name], drew[name]
                if a.shape != b.shape:
                    continue
                rings = np.split(0.5 * (a + b), np.cumsum(plan.outline_parts)[:-1])
                pts, dep = _false_outline(rings, truth, allow, width, height)
                if len(pts):
                    false_now[name] = (pts, dep)
        if false_now:
            scene.frame_set(frames[fi - 1], subframe=0.5)
            dg = context.evaluated_depsgraph_get()
            _v, m_mid = camera_matrices(scene, cam, dg, width, height)
            for name, (pts, dep) in false_now.items():
                seen = _unhidden(scene, dg, m_mid, width, height, pts, dep,
                                 name, drawn_names)
                plans[name].false_seen += seen * 0.5
            if fi + 1 >= len(frames):
                scene.frame_set(frame)

        carry_state["prev"] = here
        carry_state["drew"] = drew
        carry_state["mid"] = {}
        carry_state["pieces"] = {}
        if fi + 1 >= len(frames):
            return

        scene.frame_set(frame, subframe=0.5)
        dg = context.evaluated_depsgraph_get()
        v2, m2 = camera_matrices(scene, cam, dg, width, height)
        ce = cam.evaluated_get(dg)
        cp = np.array(ce.matrix_world.translation, dtype=np.float64)
        cf = np.array((ce.matrix_world.to_3x3() @ Vector((0, 0, -1))).normalized())
        carry_state["mid"] = look(dg, v2, m2, cp, cf, ce.data.type == "ORTHO",
                                  carry_state["pieces"]
                                  if settings.merge_false_px > 0.0 else None)

    def _cam_at(dg):
        v, m = camera_matrices(scene, cam, dg, width, height)
        ce = cam.evaluated_get(dg)
        cp = np.array(ce.matrix_world.translation, dtype=np.float64)
        cf = np.array(
            (ce.matrix_world.to_3x3() @ Vector((0, 0, -1))).normalized())
        return v, m, cp, cf, ce.data.type == "ORTHO"

    def _rings_of(dg, objs):
        """`{name: (anchors, polygon)}` for these objects at this instant."""
        v, m, cp, cf, ortho = _cam_at(dg)
        out = {}
        for obj in objs:
            plan = plans.get(obj.name)
            if plan is None:
                continue
            ev = obj.evaluated_get(dg)
            me = ev.to_mesh()
            try:
                ring, poly, _ = silhouette_of(plan, me, ev, v, m, width, height,
                                              cp, cf, ortho)
            finally:
                ev.to_mesh_clear()
            out[obj.name] = (ring, poly)
        return out

    def _gap_cost(shapes, n):
        """Worst error between keyframes if this outline carried `n` points."""
        prev_state = prev_pts = None
        worst = 0.0
        for fi in range(len(frames)):
            got = shapes.get((fi, 0))
            if got is None or got[0] is None:
                prev_state = prev_pts = None
                continue
            ring, poly = got
            pos, ids = outline_mod.carry(poly, ring, n, settings.dissolve_px,
                                         prev_state)
            pts = outline_mod.evaluate(pos, poly)
            prev_state = (ids, pts, ring, poly)
            if prev_pts is not None:
                mid = shapes.get((fi - 1, 1))
                if mid is not None:
                    worst = max(worst, outline_mod.stray(
                        0.5 * (prev_pts + pts), mid))
            prev_pts = pts
        return worst

    def walk_shapes(names):
        """Every shape these objects take, at each frame and each half-frame.

        Walked once and kept, because more than one decision needs it and the
        walking is the expensive part -- pulling a mesh out of the depsgraph
        and projecting it, sixty times over, dwarfs anything done with the
        result. Sizing an outline and choosing how to place it were each doing
        this separately, which made the second one look expensive when what it
        actually cost was a repeat of the first.
        """
        mine = [o for o in objects if o.name in set(names)]
        if not mine:
            return
        for name in names:
            ring_cache.setdefault(name, {})
        for fi, frame in enumerate(frames):
            scene.frame_set(frame)
            for obj in mine:
                for name, got in _rings_of(context.evaluated_depsgraph_get(),
                                           [obj]).items():
                    ring_cache[name][(fi, 0)] = got
                yield ("Sizing", fi, len(frames))
            if fi + 1 < len(frames):
                scene.frame_set(frame, subframe=0.5)
                for obj in mine:
                    for name, (_, poly) in _rings_of(
                            context.evaluated_depsgraph_get(), [obj]).items():
                        if poly is not None:
                            ring_cache[name][(fi, 1)] = poly
                    yield ("Sizing", fi, len(frames))
            yield ("Sizing", fi + 1, len(frames))

    def size_outlines(names):
        """How many points should each merged outline carry for the whole clip?

        It used to be the largest number any single frame asked for, at the
        dissolve tolerance -- so that every frame is well drawn *as a keyframe*.
        That is the wrong question. An element's points are interpolated, and
        what a viewer sees is the shape between two keyframes; a count that
        flatters each frame on its own does not have to serve the space between
        them.

        It does not, and not by a little. More points means more slots that must
        each hold a place on an outline that is changing, and the extra ones
        settle on features that come and go, then wander when they go. Measured
        between keyframes across twenty objects, choosing the count by what it
        actually draws gives 13% fewer points and a *lower* error on fourteen of
        them, the same on six and worse on none. part_083 draws better with six
        points than with eleven; part_070 better with fourteen than
        twenty-three.

        So: take the old answer as an upper bound, try a ladder of smaller
        counts, and keep whichever draws best between keyframes. Ties go to
        fewer points, because points are the file.

        Searching *upward* as well was tried and taken out again. It is not
        that it finds nothing -- part_042 halves its error again at twenty-four
        points against sixteen -- but that what it finds does not matter: the
        downward search had already taken that part from 5.34px to about 3px,
        and the parts setting the worst case in the clip are elsewhere. On the
        whole model it produced the same file, the same element count, the same
        253 merges and an identical worst-case ranking, for 46% more bake time.
        The curve is worth knowing about -- it is not monotone in either
        direction, and part_083 swings from 9.47px at thirteen points to 3.85px
        at fourteen -- but not worth searching both ways.
        """
        cache = ring_cache

        for index, name in enumerate(names):
            yield ("Sizing", index, len(names))
            hi = outline_slots.get(name, 0)
            shapes = cache.get(name)
            if hi < 5 or not shapes:
                continue                      # nothing worth taking away
            if len(part_slots.get(name, ())) > 1:
                # Sizing, refinement and the gap test all reason about one ring
                # and one point count. A fill carrying several subpaths has
                # neither, and takes the count each subpath measured for itself.
                continue
            best_n = hi
            best_err = _gap_cost(shapes, hi)
            tried = {hi}
            ladder = sorted({max(3, int(round(hi * f)))
                             for f in (0.4, 0.5, 0.6, 0.7, 0.8, 0.9)})
            for n in ladder:
                if n in tried or not (3 <= n <= hi):
                    continue
                tried.add(n)
                yield ("Sizing", index, len(names))
                err = _gap_cost(shapes, n)
                if err < best_err - 1e-9:
                    best_n, best_err = n, err
            # A step either side of the winner, which the ladder is too coarse
            # to have looked at.
            for n in (best_n - 1, best_n + 1):
                if n in tried or not (3 <= n <= hi):
                    continue
                tried.add(n)
                yield ("Sizing", index, len(names))
                err = _gap_cost(shapes, n)
                if err < best_err - 1e-9:
                    best_n, best_err = n, err
            outline_slots[name] = best_n
            sized[name] = (hi, best_n)

    def try_refine(names):
        """Would the area refinement draw these outlines better than plain?

        Asked of the ones already drawing badly and of nobody else. It is
        expensive, and on an outline that is already close it is a coin toss:
        across thirty objects at a quarter-slot leash it improved five and
        worsened four, and left twenty-one untouched. So there is no constant
        to set. There is a measurement to make, per object, and it is the same
        one the merge decision rests on -- what the outline as drawn costs
        against the silhouette halfway between two keyframes.

        Both placements are chained across the whole clip, because a placement
        cannot be judged a frame at a time: each frame's answer depends on the
        one before it.
        """
        # Each object gets a few ways of drawing itself to choose between. The
        # third exists because sizing and placing are decided in that order and
        # they interact: part_024 keeps 13 points at 6.26px, having been sized
        # without knowing a refinement was available, when 21 points placed
        # well draw it at 5.47px. Neither pass can see that on its own, so the
        # count it was sized down from is offered back here, refined.
        options = {}
        for name in names:
            n_now = plans[name].outline_n
            opts = [(n_now, False), (n_now, True)]
            was = sized.get(name, (n_now, n_now))[0]
            if was != n_now and was >= 3:
                opts.append((was, True))
            options[name] = opts
        chain = {n: [None] * len(options[n]) for n in names}
        last: dict[str, tuple] = {}
        cost = {n: [0.0] * len(options[n]) for n in names}
        mid_truth: dict[str, object] = {}

        for fi in range(len(frames)):
            here = {n: ring_cache.get(n, {}).get((fi, 0), (None, None))
                    for n in names}

            now = {}
            for name, (ring, poly) in here.items():
                yield ("Refining outlines", fi, len(frames))
                if ring is None:
                    chain[name] = [None] * len(options[name])
                    continue
                drawn = []
                for k, (n, refined) in enumerate(options[name]):
                    pos, ids = outline_mod.carry(poly, ring, n,
                                                 settings.dissolve_px,
                                                 chain[name][k])
                    if refined:
                        pos = outline_mod.refine_exact(
                            pos, poly, rounds=outline_mod.REFINE_ROUNDS,
                            limit=outline_mod.REFINE_LEASH / max(n, 1))
                    pts = outline_mod.evaluate(pos, poly)
                    chain[name][k] = (ids, pts, ring, poly)
                    drawn.append(pts)
                now[name] = drawn

            for name, before in last.items():
                if name not in now or mid_truth.get(name) is None:
                    continue
                after = now[name]
                middle = mid_truth[name]
                for k in range(min(len(before), len(after))):
                    cost[name][k] = max(cost[name][k], outline_mod.stray(
                        0.5 * (before[k] + after[k]), middle))
            last = now

            mid_truth = {n: ring_cache.get(n, {}).get((fi, 1)) for n in names}

        for name in names:
            scores = cost[name]
            best = min(range(len(scores)), key=lambda k: scores[k])
            if best == 0 or scores[best] >= scores[0] - 1e-9:
                continue                       # what it already does is best
            n, refined = options[name][best]
            plans[name].carry_cost = scores[best]
            if refined:
                refine_set.add(name)
                plans[name].use_refine = True
            if n != plans[name].outline_n:
                # Sizing took this one too far down given how well it can be
                # placed. Hand the points back before anything is written.
                outline_slots[name] = n
                sized[name] = (sized.get(name, (n, n))[0], n)

    # Built once, before any sampling: which open edges are really shared with
    # another object. Needs every object's evaluated mesh at once, which is why
    # it cannot live in ObjectPlan -- a plan only ever sees its own.
    welds: dict = {}
    weld_q = max(1e-9, float(getattr(settings, "weld_tol", 1e-4)))
    welded_edges = 0
    if getattr(settings, "weld_seams", False):
        scene.frame_set(frames[0])
        welds = weld_index(objects, context.evaluated_depsgraph_get(), settings)

    def sample_pass():
        nonlocal welded_edges
        for fi, frame in enumerate(frames):
            scene.frame_set(frame)
            depsgraph = context.evaluated_depsgraph_get()
            cam_eval = cam.evaluated_get(depsgraph)
            view, mvp = camera_matrices(scene, cam, depsgraph, width, height)
            cam_pos = np.array(cam_eval.matrix_world.translation, dtype=np.float64)
            cam_fwd = np.array((cam_eval.matrix_world.to_3x3() @ Vector((0, 0, -1))).normalized())
            is_ortho = cam_eval.data.type == "ORTHO"

            items = [] if collect_items else None
            cache = {}
            extent = {} if depth_extent is not None else None
            for obj in objects:
                eval_obj = obj.evaluated_get(depsgraph)
                me = eval_obj.to_mesh()
                try:
                    plan = plans.get(obj.name)
                    if plan is None:
                        plan = ObjectPlan(obj, me, settings)
                        if welds:
                            welded_edges += adopt_welds(plan, obj, me, welds,
                                                        weld_q)
                        if obj.name in no_merge:
                            plan.merge_fill = False
                        plan.outline_n = outline_slots.get(obj.name)
                        plan.outline_parts = part_slots.get(obj.name)
                        plan.use_refine = obj.name in refine_set
                        plans[obj.name] = plan
                        _make_elements(plan, settings, elements, slots, len(frames))
                    else:
                        plan.check(me)
                    _sample_object(
                        plan, me, eval_obj, settings, slots, fi,
                        view, mvp, width, height, cam_pos, cam_fwd, is_ortho, light,
                        items, cache, extent,
                    )
                finally:
                    eval_obj.to_mesh_clear()

            # Per frame, like everything sampled here; the deep-body test wants
            # the whole clip, so keep them as we go.
            if extent is not None:
                for nm, row in extent.items():
                    depth_extent.setdefault(nm, {})[fi] = row
            frame_items.append(items)
            frame_ortho.append(is_ortho)
            vertex_cache.append(cache)
            if judging:
                judge_carry(frame, fi, depsgraph, view, mvp,
                            cam_pos, cam_fwd, is_ortho)

            yield ("Sampling", fi + 1, len(frames))

    merge_fallback: list[str] = []
    # What the gap test measured, kept out here because the pass that measures
    # it is not the last pass to run. A guard that silently stops measuring
    # looks exactly like a guard with nothing to complain about, so the counts
    # are reported rather than inferred from the merge total.
    gap_scores: dict[str, tuple[float, float]] = {}
    # Visible false outline per multi-subpath merged fill, pixels over the clip.
    false_scores: dict[str, float] = {}
    # Objects whose outline is drawn with the area refinement. Held out here
    # because the pass that decides it is not the pass that writes it, and
    # plans are rebuilt from scratch between the two.
    refine_set: set[str] = set()
    # Point counts the sizing pass changed, as (what one frame asked for, what
    # the clip actually wanted).
    sized: dict[str, tuple[int, int]] = {}
    # Every merged candidate's silhouette at every frame and half-frame, walked
    # once. Sizing and placing both need it and the walk is what costs.
    ring_cache: dict[str, dict] = {}
    uncarried: list[str] = []
    # How many points each merged outline turned out to need. Empty on the
    # measuring pass, filled before the pass that writes geometry.
    outline_slots: dict[str, int] = {}
    # And how each total is split between subpaths. `restart()` throws the plans
    # away and rebuilds them, so anything the measuring pass learned has to live
    # out here and be handed back at construction -- which is what
    # `outline_slots` already does for the total.
    part_slots: dict[str, list[int]] = {}
    # The gap test only means anything between adjacent frames. It judges the
    # geometry as written, so it runs on the pass that writes it -- not the one
    # that only counts points, where there is nothing yet to judge.
    can_judge = settings.merge_slack_px > 0.0 and step == 1
    judging = False
    try:
        yield from sample_pass()

        # A merged fill is baked in two passes, and the reason is arithmetic:
        # an element declares its point count once, for the whole clip, but an
        # outline's length is a per-frame fact. The old code resolved that by
        # spending a slot on every mesh vertex -- an upper bound nothing can
        # exceed, and 482 of them to draw the lab sphere's circle.
        #
        # So measure first. The pass above wrote no merged geometry; it only
        # recorded, per object, the largest point count any single frame's
        # outline earned at the dissolve tolerance. That is the count, and now
        # it is known the geometry can be written.
        #
        # Experimental depth-share split: draw face by face a body deep enough
        # to hold another object inside its depth range. There is no measured
        # knee behind the 50% default -- front-surface shares on Apollo-Soyuz
        # run 58.2 / 51.7 / 42.6% -- and splitting apollo_003 by hand helped
        # the nearest-vertex read (446 -> 265 wrong pixels) before the surface
        # read fixed the corner depths that caused it. Whether this earns its
        # place against that read is in docs/experiments.md.
        deep = []
        if depth_extent:
            deep = _deep_bodies(
                {n: [rows.get(fi) for fi in range(len(frames))]
                 for n, rows in depth_extent.items()},
                settings.split_span_pct)
        no_merge.update(deep)
        wanted = {name: p.outline_need for name, p in plans.items()
                  if p.fills and p.merge_fill and name not in no_merge}
        if wanted:
            # Merge only what rings in every frame it is visible in.
            # `need < 0` is set the moment a frame fails -- a ring that
            # crosses itself on screen, or faces pointing at the camera that
            # no ring can be walked from. `need < 3` also covers an object no
            # frame ever gave a ring for at all: two separate loops, a genus
            # above zero, or a closed solid with Backface Cull off, where every
            # edge has two drawn faces and there is no silhouette to find.
            merge_fallback = sorted(
                set(deep) | {n for n, need in wanted.items() if need < 3})
            no_merge.update(merge_fallback)
            outline_slots.update({n: need for n, need in wanted.items() if need >= 3})
            part_slots.update({n: list(plans[n].piece_needs)
                               for n, need in wanted.items()
                               if need >= 3 and plans[n].piece_needs
                               and len(plans[n].piece_needs) > 1})

            def restart():
                plans.clear()
                elements.clear()
                slots.clear()
                frame_items.clear()
                frame_ortho.clear()
                vertex_cache.clear()

            # The count an outline carries is fixed for the whole clip, so it
            # has to be settled before any element is built -- which is here,
            # between the pass that measured an upper bound for it and the pass
            # that writes geometry against it.
            if step == 1 and outline_slots and (
                    settings.size_by_gap or settings.refine_gate_px > 0.0):
                yield from walk_shapes(sorted(n for n in outline_slots
                                              if len(part_slots.get(n, ())) <= 1))
            # Most of what gets refused is refused for being far past saving,
            # and it costs a fortune to find out politely: sizing tries eight
            # point counts and the refinement three placements, all of it on
            # the objects with the biggest rings, and then the gap test throws
            # the answer away. One chain at the count already measured says so
            # for a fraction of that.
            #
            # The bar is set high on purpose. Sizing has never gained more than
            # about a factor of two -- part_042 at 5.34px to 2.46px -- and the
            # refinement about the same, so an outline several times past the
            # slack is not going to be brought under it by either.
            if ring_cache and settings.merge_slack_px > 0.0:
                hopeless = []
                for index, name in enumerate(sorted(outline_slots)):
                    yield ("Sizing", index, len(outline_slots))
                    shapes = ring_cache.get(name)
                    if not shapes or len(part_slots.get(name, ())) > 1:
                        continue
                    if _gap_cost(shapes, outline_slots[name]) > (
                            settings.merge_slack_px * 4.0):
                        hopeless.append(name)
                if hopeless:
                    merge_fallback = sorted(set(merge_fallback) | set(hopeless))
                    no_merge.update(hopeless)
                    for name in hopeless:
                        outline_slots.pop(name, None)
                        ring_cache.pop(name, None)

            if settings.size_by_gap and ring_cache:
                yield from size_outlines(sorted(outline_slots))

            judging = can_judge
            restart()
            yield from sample_pass()

            # A silhouette that cannot be carried across a gap is not being
            # drawn badly -- it is not carryable, and no placement rule will
            # change that. A part turning edge-on goes from a pentagon to a
            # triangle between two frames, and there is no correspondence
            # between a pentagon and a triangle. Drawn face by face it has no
            # correspondence to get wrong at all: on the probe's blades, 1.24px
            # against 48px merged.
            #
            # This is only knowable once the geometry exists, so the pass above
            # wrote it and judged it. Anything refused here has to be written
            # again, face by face -- but only if something was refused.
            gap_scores = {n: (round(p.carry_cost, 2), round(p.face_cost, 2))
                          for n, p in plans.items()
                          if n in wanted and p.carry_cost > 0.0}
            # Where an outline is drawing badly there may be a better
            # placement of the same points available. Ask before condemning it
            # to face-by-face, and only where the gap test says there is
            # something to gain.
            if settings.refine_gate_px > 0.0 and step == 1:
                gated = sorted(
                    n for n, p in plans.items()
                    if n in wanted and n not in merge_fallback
                    and p.outline_n and p.carry_cost > settings.refine_gate_px
                    and len(part_slots.get(n, ())) <= 1)
                if gated and ring_cache:
                    yield from try_refine(gated)

            false_scores = {n: round(p.false_seen, 1) for n, p in plans.items()
                            if n in wanted and p.false_seen > 0.0}
            uncarried = sorted(
                n for n, p in plans.items()
                if n in wanted and n not in merge_fallback
                and (p.carry_cost > p.face_cost + settings.merge_slack_px
                     or (settings.merge_false_px > 0.0
                         and p.false_seen > settings.merge_false_px)))
            if uncarried or refine_set:
                merge_fallback = sorted(set(merge_fallback) | set(uncarried))
                no_merge.update(uncarried)
                judging = False
                restart()
                yield from sample_pass()
    finally:
        scene.frame_set(original_frame)

    # An absent subpath's placeholder point is only right once every frame is
    # known: it belongs next to the piece in the nearest frame that has one.
    for plan in plans.values():
        if plan.outline_parts and len(plan.outline_parts) > 1 and plan.piece_present:
            slot = slots.get(f"{plan.name}/m")
            if slot is not None:
                _settle_absent_pieces(
                    slot["geo"], plan.outline_parts, plan.piece_present,
                    lambda fi, name=plan.name: (
                        vertex_cache[fi][name][0]
                        if fi < len(vertex_cache) and name in vertex_cache[fi] else None))

    # Which spans of a merged outline are the cut, and so must not be stroked.
    # Settled once for the clip rather than per frame, like `smooth`: the point
    # count and the slot order are both fixed by now -- neither chaining, the
    # fill thinner nor the curve classifier touches a merged outline -- and a
    # stroke that switched on and off along an arc as the camera moved would
    # flicker where a seam should simply be absent.
    seam_report = {"outlines": 0, "spans": 0, "unstable": 0}
    element_by_key = {el.key: el for el in elements}
    for plan in plans.values():
        if plan.seam_hits is None or not plan.seam_frames:
            continue
        share = plan.seam_hits / float(plan.seam_frames)
        mask = share >= 1.0 - 1e-9
        if not mask.any():
            continue
        el = element_by_key.get(f"{plan.name}/m")
        if el is None:
            continue
        el.seam = mask
        seam_report["outlines"] += 1
        seam_report["spans"] += int(mask.sum())
        # A span the frames disagree about. Reported rather than smoothed over:
        # if this is not near zero the static mask is the wrong shape for the
        # problem and the flag has to become a track after all.
        seam_report["unstable"] += int(((share > 0.0) & (share < 1.0)).sum())

    chain_report = {"chains": 0, "edges_merged": 0, "paths": {}}
    if settings.chain_edges:
        chain_report = yield from chain_mod.apply_iter(plans, elements, slots, frame_items,
                                                       vertex_cache, settings, len(frames),
                                                       frame_ortho)

    # Fills get the same thinning strokes have always had. Placed here, beside
    # chaining, because both rewrite element geometry and both must finish
    # before the ordering pass reads it.
    thin_report = chain_mod.thin_fills(plans, elements, slots, frame_items,
                                       vertex_cache, settings, len(frames))

    curve_report = {"mode": curves.NONE, "curved": 0, "vertices": 0}
    follow_report = {"followed": 0, "points": 0, "splits": 0, "silhouettes": 0}
    if settings.curve_mode != curves.NONE:
        yield ("Fitting curves", 0, 1)
        # Which mesh vertices each element runs through. The 3D test needs it
        # to measure angles on the shape rather than on one view of it, and the
        # follow pass needs it to know which elements share an edge.
        element_verts = dict(chain_report.get("paths") or {})
        for plan in plans.values():
            if plan.fills and plan.merge_fill:
                # Nothing to hand the curve classifier. A merged outline's
                # points are positions *on* the loop now, not mesh vertices, so
                # there is no vertex index to measure a 3D angle at -- and the
                # 2D outline is already reduced to the points its shape earns.
                pass
            elif plan.fills:
                for fidx in range(plan.nfaces):
                    # NOT `start` -- that name holds the bake's first frame and
                    # is still needed at the end to compute `times`/`duration`.
                    lstart = int(plan.loop_start[fidx])
                    element_verts[f"{plan.name}/f{fidx}"] = \
                        plan.loop_verts[lstart:lstart + int(plan.loop_total[fidx])]
            if plan.strokes:
                # Edges that never chained are still edges, and a lone one
                # lying on a curved rim has to follow it like any other.
                for eidx in range(plan.nedges):
                    element_verts.setdefault(f"{plan.name}/e{eidx}",
                                             plan.edge_verts[eidx])
        # Thinned outlines win: the curve classifier has to measure the path
        # that is actually drawn, not the one before points were dropped.
        element_verts.update(thin_report.get("paths") or {})
        curve_report = curves.classify(elements, slots, element_verts, plans, settings,
                                       chain_report.get("loose"))

        if curve_report["curved"]:
            yield ("Following curves", 0, 1)
            follow_report = follow.apply(elements, slots, element_verts,
                                         settings, len(frames))
            # Curving an outline moves it, so the silhouette has to move with
            # it -- otherwise the rim bulges into open space and the side steps
            # back in underneath. Runs after following, which is what settles
            # where each element's points are.
            yield ("Placing silhouettes", 0, 1)
            sil_report = silhouette.apply(elements, slots, element_verts,
                                          len(frames))
            follow_report["silhouettes"] = sil_report["corners"]
            follow_report["points"] += sil_report["points"]

    # Runs before ordering, not after: every fill removed here is one fewer
    # element for order.resolve to compare against every other, and that pass
    # is the expensive one on a dense scene.
    occlusion_report = {"culled": 0, "hidden": 0, "cells": 0}
    if settings.occlusion_cull and settings.fills:
        yield ("Culling hidden fills", 0, 1)
        cull_notes: list = []
        occlusion_report = parallel.cull_hidden(
            elements, slots, frame_items, settings, len(frames), width, height,
            workers=settings.order_workers, report=cull_notes.append)

    order_report = {"held": 0, "unranked": 0}
    if settings.accurate_order:
        # Which elements actually received a rank in which frame. Only what is
        # drawn gets ranked, so this is not every element every frame, and the
        # gaps have to be repaired before the tracks are reduced -- see
        # order.hold_unranked() for what happens if they are not.
        ranked = {el.key: np.zeros(len(frames), dtype=bool) for el in elements}
        # Frames are resolved in worker processes and land as they finish, so
        # the counter is how many are done rather than how far along the clip
        # we are. Empty frames never reach the pool and are counted here.
        done = sum(1 for items in frame_items if not items)
        yield ("Resolving occlusion", done, len(frame_items))
        rows = []
        # Why the pass ran the way it did -- how many workers, or why none.
        # Worth carrying into the stats: this is the phase that dominates a
        # dense bake, and "it quietly fell back to one core" is otherwise
        # indistinguishable from "the scene got harder".
        notes: list = list(cull_notes) if settings.occlusion_cull and settings.fills else []
        for fi, resolved, stats in parallel.imap_resolve(
                frame_items, frame_ortho, workers=settings.order_workers,
                report=notes.append):
            for item, value in zip(frame_items[fi], resolved):
                slots[item["key"]]["depth"][fi] = value
                ranked[item["key"]][fi] = True
            rows.append(stats)
            done += 1
            yield ("Resolving occlusion", done, len(frame_items))
        order_mod.LAST_STATS.clear()
        order_mod.LAST_STATS.update(parallel.merge_stats(rows))
        held, never = order_mod.hold_unranked(slots, ranked)
        order_report = {"held": held, "unranked": never,
                        "pool": "; ".join(notes)}
        # What the pass had to give up to produce a total order. These are the
        # numbers that move when the ordering gets better or worse, and until
        # now they were readable only by attaching a spy: a bake reported how
        # many elements it ranked and nothing about how much of the evidence it
        # discarded doing it.
        order_report.update({k: v for k, v in order_mod.LAST_STATS.items()
                             if k in ("constraints", "cycle_breaks",
                                      "constraints_dropped", "assertions",
                                      "assertions_lost",
                                      "assertions_face_not_drawn")})

    yield ("Reducing keyframes", 0, 1)

    times = [(f - start) / fps for f in frames]
    duration = times[-1] if len(times) > 1 else 1.0 / fps
    vec = _finish(elements, slots, times, settings, width, height, fps, duration)
    vec.meta.update(
        {
            "frames": len(frames),
            "frame_start": start,
            "frame_end": end,
            "frame_step": step,
            # Did every gap come out the same length? Always sampling the end
            # frame means it sometimes does not, and the dense track encoding
            # is only valid on an even grid -- see emit_json.to_dict.
            "uniform_frames": len({b - a for a, b in zip(frames, frames[1:])}) <= 1,
            "objects": [p.name for p in plans.values()],
            "edge_mode": settings.edge_mode,
            "camera": cam.name,
            "direction": "forward",
            # Optional labels for what this move connects, set as custom
            # properties on the camera. They travel into the manifest so a
            # consumer can look a clip up by intent instead of by filename.
            "from": cam.get("vecbake_from", None),
            "to": cam.get("vecbake_to", None),
            "chains": chain_report["chains"],
            "edges_merged": chain_report["edges_merged"],
            "curve_mode": curve_report["mode"],
            "curved_elements": curve_report["curved"],
            "followed_elements": follow_report["followed"],
            "follow_points": follow_report["points"],
            "silhouette_corners": follow_report["silhouettes"],
            "merged_fills": sum(1 for p in plans.values()
                                if p.fills and p.merge_fill and p.outline_n),
            # Points each merged outline ended up carrying. Worth reporting:
            # this is the number the whole feature turns on, and a count near
            # an object's vertex total means its outline is genuinely that
            # complicated rather than that the measuring pass failed.
            "merged_slots": {n: p.outline_n for n, p in sorted(plans.items())
                             if p.outline_n},
            "occlusion_culled": occlusion_report["culled"],
            "occlusion_hidden_frames": occlusion_report["hidden"],
            # Frames whose paint rank was held over from a drawn frame,
            # and elements that never got one. The second must be 0.
            "order_held_frames": order_report["held"],
            "order_unranked": order_report["unranked"],
            "order_pool": order_report.get("pool", ""),
            # Objects whose Smooth by Angle modifier was found but whose angle
            # could not be read, so the crease threshold was guessed. Should be
            # zero; anything else means this bake drew a different set of lines
            # from the one the scene asks for.
            "shading_angle_guessed": sorted(
                name for name, p in plans.items() if not p.shade_angle_read),
            "order_constraints": order_report.get("constraints", 0),
            "order_cycle_breaks": order_report.get("cycle_breaks", 0),
            "order_constraints_dropped": order_report.get("constraints_dropped", 0),
            "order_assertions": order_report.get("assertions", 0),
            "order_assertions_lost": order_report.get("assertions_lost", 0),
            "order_assertions_unmatched": order_report.get(
                "assertions_face_not_drawn", 0),
            # Objects with no single silhouette loop to merge: two separate
            # loops, or a genus above zero. Losing the slot *order* is no
            # longer on this list -- slots are positions around the loop now,
            # which are monotone by construction -- see outline.py.
            # Open edges found to be shared with another object and so not
            # drawn as borders. Zero with Weld Seams on means nothing matched:
            # the pieces are not touching, or weld_tol is too tight.
            "welded_edges": welded_edges,
            # Merged outlines whose stroke skips the cut, how many of their
            # spans it skips, and how many spans the frames disagreed about.
            # `unstable` well above zero means the mask should be a track.
            "welded_outlines": seam_report["outlines"],
            "welded_spans": seam_report["spans"],
            "welded_spans_unstable": seam_report["unstable"],
            "merged_fallback": merge_fallback,
            # The gap test: how many merged outlines it actually scored, the
            # worst of them as (outline as drawn, faces instead) in pixels
            # halfway between two keyframes, and which ones it refused. Zero
            # scored means the test is not running, which is not the same
            # thing as nothing being wrong.
            "merged_gap_scored": len(gap_scores),
            "merged_gap_worst": dict(sorted(gap_scores.items(),
                                            key=lambda kv: -kv[1][0])[:10]),
            "merged_uncarried": uncarried,
            # Visible false outline of each multi-subpath merged fill, pixels
            # summed over the clip's half frames, worst first. Over
            # `merge_false_px` it was drawn face by face.
            "merged_false_line": dict(sorted(false_scores.items(),
                                             key=lambda kv: -kv[1])),
            # Outlines drawn with the area refinement, and what it was worth.
            "merged_refined": sorted(refine_set),
            # Outlines the clip wanted smaller than any one frame did, as
            # {name: [asked for, kept]}, and the points that saved.
            "merged_resized": {n: [a, b] for n, (a, b) in sorted(sized.items())
                               if a != b},
            "merged_points_saved": sum(a - b for a, b in sized.values()),
        }
    )
    return vec


def _make_elements(plan: ObjectPlan, settings, elements, slots, nframes):
    def new_slot(key, npts):
        slots[key] = {
            "geo": [None] * nframes,
            "vis": np.zeros(nframes, dtype=np.float64),
            # Which side of the surface an edge is on, frame by frame. Only
            # meaningful for strokes; chaining refuses to merge across it.
            "front": np.zeros(nframes, dtype=bool),
            "depth": np.zeros(nframes, dtype=np.float64),
            "shade": np.ones(nframes, dtype=np.float64),
            "npts": npts,
        }

    if plan.fills and plan.merge_fill:
        # One element for the whole object, carrying as many points as the
        # busiest frame's outline needs. That number is a per-frame fact and an
        # element's point count is not, so it comes from the measuring pass --
        # see bake_iter. Without one there is nothing to go on but an upper
        # bound, and every vertex is an upper bound.
        key = f"{plan.name}/m"
        elements.append(
            Element(
                key=key,
                kind=FILL,
                npts=plan.outline_n or plan.nverts,
                group=plan.group,
                color=plan.face_color[0] if plan.nfaces else 0,
                # The outline draws its own silhouette. Nothing else can: which
                # edges are on it changes every frame, so as strokes it costs
                # one element per edge that ever qualifies -- 554 of them for
                # the lab sphere, which has no sharp edge at all. Sharp edges
                # are unaffected and stay ordinary strokes; they are fixed by
                # topology and already cost one element each.
                width=settings.stroke_width if settings.strokes else 0.0,
                edge_color=plan.stroke_color if settings.strokes else None,
                # Where one subpath stops and the next starts. None for the one
                # ring every other outline is.
                subpaths=(list(plan.outline_parts)
                          if plan.outline_parts and len(plan.outline_parts) > 1
                          else None),
            )
        )
        new_slot(key, plan.outline_n or plan.nverts)
    elif plan.fills:
        for fidx in range(plan.nfaces):
            npts = int(plan.loop_total[fidx])
            key = f"{plan.name}/f{fidx}"
            elements.append(
                Element(
                    key=key,
                    kind=FILL,
                    npts=npts,
                    group=plan.group,
                    color=plan.face_color[fidx],
                    width=0.0,
                )
            )
            new_slot(key, npts)
    if plan.strokes:
        for eidx in range(plan.nedges):
            key = f"{plan.name}/e{eidx}"
            elements.append(
                Element(
                    key=key,
                    kind=STROKE,
                    npts=2,
                    group=plan.group,
                    color=plan.edge_colors[eidx] if plan.edge_colors else plan.stroke_color,
                    width=settings.stroke_width,
                )
            )
            new_slot(key, 2)


class Projected(NamedTuple):
    """Where an object lands on screen this instant, and which way its faces face."""
    xy: object
    depth: object
    in_front: object
    world_n: object
    world_c: object
    face_front: object
    face_ok: object
    face_depth: object


def project_object(plan, me, eval_obj, view, mvp, width, height,
                   cam_pos, cam_fwd, is_ortho):
    """Project an object and work out which of its faces point at the camera.

    Pulled out so that more than one pass can ask the question and get the same
    answer. The pass that writes geometry and the pass that judges whether a
    merged outline can be carried across a gap have to agree about where the
    silhouette is, exactly -- two implementations would drift, and the
    judgement would then be about a shape that is not the one being drawn.
    """
    nv = len(me.vertices)
    co = np.empty(nv * 3, dtype=np.float64)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    M = np.array(eval_obj.matrix_world, dtype=np.float64)
    world = co @ M[:3, :3].T + M[:3, 3]
    xy, depth, in_front = project(world, view, mvp, width, height)

    nf = plan.nfaces
    if not nf:
        empty = np.ones(0, dtype=bool)
        return Projected(xy, depth, in_front, None, None,
                         empty, empty, np.zeros(0, dtype=np.float64))

    normals = np.empty(nf * 3, dtype=np.float64)
    me.polygons.foreach_get("normal", normals)
    normals = normals.reshape(-1, 3)
    centers = np.empty(nf * 3, dtype=np.float64)
    me.polygons.foreach_get("center", centers)
    centers = centers.reshape(-1, 3)

    normal_matrix = np.linalg.inv(M[:3, :3])
    world_n = normals @ normal_matrix
    lengths = np.linalg.norm(world_n, axis=1, keepdims=True)
    world_n = world_n / np.where(lengths < 1e-12, 1.0, lengths)
    world_c = centers @ M[:3, :3].T + M[:3, 3]

    if is_ortho:
        facing = world_n @ cam_fwd
    else:
        facing = np.einsum("ij,ij->i", world_n, world_c - cam_pos)
    face_front = facing < 0.0

    # per-face mean depth / visibility, via segmented reduction over loops
    lv, starts = plan.loop_verts, plan.loop_start
    totals = plan.loop_total.astype(np.float64)
    face_depth = np.add.reduceat(depth[lv], starts) / totals
    face_ok = np.add.reduceat(in_front[lv].astype(np.float64), starts) == totals
    return Projected(xy, depth, in_front, world_n, world_c,
                     face_front, face_ok, face_depth)


# How much of a clip a span has to spend on the weld before the stroke gives it
# up. All of it: a span the frames disagree about keeps its line.
#
# Swept on part_018.001, where 8 of 63 spans do disagree -- ink differing from
# the whole part was 7.622% taking any frame's word for it, 7.666% by majority
# and 7.445% requiring every frame. The safest reading is also the best one,
# which is the useful part: where the mask is in doubt the stroke is the thing
# to keep, because a line drawn over a seam is a smaller error than a piece of
# real silhouette missing. On a clean cut it makes no difference at all -- the
# cube's spans are unanimous in all 249 frames.


def _seam_spans(plan, ring_verts, pos, nslots):
    """Which outline slots start a span that lies on a weld.

    A merged outline is one path, filled and then stroked, and that stroke is
    the whole point of merging: which mesh edges are on a silhouette changes
    every frame, so the line has to come from the fill. But a piece cut off
    another has the cut in its own silhouette -- the cut *is* its boundary --
    and stroking it draws the seam that welding exists to remove. Both halves
    draw it, in the outline colour, and no edge decision can reach it because
    it is not an edge element at all.

    So the fill keeps the cut and the stroke does not. `pos` is where each slot
    sits, as (ring segment, fraction); a span between two slots is on the weld
    when the ring segment under its midpoint is a welded edge. Midpoint rather
    than "every segment it covers": a span that straddles the end of the weld
    has to choose, and choosing by where most of it lies keeps the join in the
    same place from both sides.
    """
    m = len(ring_verts)
    if m < 2 or nslots < 2:
        return None
    if plan.edge_of_pair is None:
        plan.edge_of_pair = {
            (int(a), int(b)) if a < b else (int(b), int(a)): i
            for i, (a, b) in enumerate(plan.edge_verts)
        }
    lookup = plan.edge_of_pair
    welded = np.zeros(m, dtype=bool)
    for j in range(m):
        a, b = int(ring_verts[j]), int(ring_verts[(j + 1) % m])
        if a == b:
            continue
        e = lookup.get((a, b) if a < b else (b, a))
        # A splice or bridge segment joins two points that share no edge. It is
        # not a weld, and it is already covered by the second fill bake.py
        # draws over it.
        if e is not None and plan.edge_welded[e]:
            welded[j] = True
    if not welded.any():
        return None

    # Slot i's span runs to slot i+1. Both ends are (segment, fraction), so
    # position along the ring in segment units is just their sum, and the
    # midpoint is half a step forward from there.
    #
    # Forward *on the circle*, not in the array: slot 0 does not have to sit at
    # the start of the ring, and a ring that visits a vertex twice carries two
    # slots at the same place. Correcting only the last span's wrap left the
    # second half of a two-segment cut stroked, which is exactly the seam this
    # is here to remove.
    here = (pos[:, 0] + pos[:, 1]) % m
    step = (np.roll(here, -1) - here) % m
    mid = (here + step * 0.5).astype(np.int64) % m
    return welded[mid]


def silhouette_of(plan, me, eval_obj, view, mvp, width, height,
                  cam_pos, cam_fwd, is_ortho):
    """`(anchors, polygon, projected vertices)` this instant. Anchors and
    polygon are None when there is no ring to be had.

    The anchors come back as well as the shape because more than one caller
    needs to *place* points on this ring, not only measure it, and placing
    needs to know which vertex is which.
    """
    seen = project_object(plan, me, eval_obj, view, mvp, width, height,
                          cam_pos, cam_fwd, is_ortho)
    facing = seen.face_ok & seen.face_front
    if not facing.any():
        return None, None, seen.xy
    walk = merge.walk_loop(plan, merge.silhouette_edges(plan, facing), seen.xy)
    if walk is None:
        return None, None, seen.xy
    ids, verts = walk
    ring, poly = outline_mod.wound(ids, seen.xy, seen.xy[verts])
    return ring, poly, seen.xy


def silhouette_pieces_of(plan, me, eval_obj, view, mvp, width, height,
                         cam_pos, cam_fwd, is_ortho):
    """Every piece of the silhouette this instant, as `(xy ring, depth ring)`.

    The rings a multi-subpath merged outline is drawn from, with the camera
    depth of each ring vertex so a point near the outline can be given the depth
    the outline has there.
    """
    seen = project_object(plan, me, eval_obj, view, mvp, width, height,
                          cam_pos, cam_fwd, is_ortho)
    facing = seen.face_ok & seen.face_front
    if not facing.any():
        return []
    pieces = merge.walk_pieces(plan, merge.silhouette_edges(plan, facing),
                               seen.xy, facing) or []
    return [(seen.xy[v].copy(), seen.depth[v].copy()) for _ids, v, _ccw in pieces]


def _false_outline(drawn, truth, allow, width, height, step=0.5):
    """Samples of a drawn outline farther than `allow` from the true one.

    `drawn` is a list of closed rings as the player will draw them; `truth`
    the silhouette's pieces from `silhouette_pieces_of`. Samples are `step`
    pixels apart along each ring and only those on the canvas count. Returns
    the false samples (N, 2) and, for each, the depth of the true outline vertex
    nearest to it -- the depth that line is drawn at there.
    """
    if not truth:
        return np.zeros((0, 2)), np.zeros(0)
    tv = np.vstack([r for r, _d in truth])
    td = np.concatenate([d for _r, d in truth])
    ta = tv
    tb = np.vstack([np.roll(r, -1, axis=0) for r, _d in truth])
    out = []
    for ring in drawn:
        if len(ring) < 2 or np.ptp(ring, axis=0).max() <= 0.0:
            continue
        a, b = ring, np.roll(ring, -1, axis=0)
        ln = np.hypot(*(b - a).T)
        k = np.maximum(1, np.ceil(ln / step).astype(np.int64))
        idx = np.repeat(np.arange(len(a)), k)
        t = (np.arange(int(k.sum())) - np.repeat(np.cumsum(k) - k, k) + 0.5) / np.repeat(k, k)
        s = a[idx] + (b - a)[idx] * t[:, None]
        on = (s[:, 0] >= 0) & (s[:, 0] <= width) & (s[:, 1] >= 0) & (s[:, 1] <= height)
        if on.any():
            out.append(s[on])
    if not out:
        return np.zeros((0, 2)), np.zeros(0)
    s = np.vstack(out)
    seg = tb - ta
    den = np.einsum("ij,ij->i", seg, seg)
    den = np.where(den > 0, den, 1e-12)
    far = np.empty(len(s), dtype=bool)
    for lo in range(0, len(s), 512):
        p = s[lo:lo + 512]
        rel = p[:, None, :] - ta[None, :, :]
        t = np.clip(np.einsum("kij,ij->ki", rel, seg) / den[None, :], 0.0, 1.0)
        foot = ta[None, :, :] + t[:, :, None] * seg[None, :, :]
        far[lo:lo + 512] = np.linalg.norm(p[:, None, :] - foot, axis=2).min(axis=1) > allow
    s = s[far]
    if not len(s):
        return s, np.zeros(0)
    near = np.argmin(((s[:, None, :] - tv[None, :, :]) ** 2).sum(axis=2), axis=1)
    return s, td[near]


def _unhidden(scene, depsgraph, mvp, width, height, pts, depth, own, drawn_names):
    """How many of `pts` no other drawn object covers from nearer the camera.

    Ground truth is `scene.ray_cast` through each pixel: a sample is hidden
    when the first surface the ray meets belongs to another object the bake
    draws and is nearer than the depth the line is drawn at. The object's own
    surface does not hide its own outline.
    """
    inv = np.linalg.inv(mvp)
    view_rows = None
    seen = 0
    for (px, py), d in zip(pts, depth):
        nx = px / width * 2.0 - 1.0
        ny = 1.0 - py / height * 2.0
        near = inv @ np.array([nx, ny, -1.0, 1.0])
        far = inv @ np.array([nx, ny, 1.0, 1.0])
        near = near[:3] / near[3]
        far = far[:3] / far[3]
        ray = far - near
        L = float(np.linalg.norm(ray))
        if L <= 0.0:
            seen += 1
            continue
        ok, loc, _n, _f, obj, _m = scene.ray_cast(depsgraph, Vector(near), Vector(ray / L))
        if not ok or obj is None or obj.name == own or obj.name not in drawn_names:
            seen += 1
            continue
        if view_rows is None:
            cam = scene.camera.evaluated_get(depsgraph)
            view_rows = np.array(cam.matrix_world.inverted(), dtype=np.float64)
        hit_depth = -float((view_rows[:3, :3] @ np.array(loc) + view_rows[:3, 3])[2])
        if hit_depth >= d:
            seen += 1
    return seen


def _settle_absent_pieces(geo, parts, present, positions):
    """Park each absent subpath on its own piece, where that piece is *this* frame.

    A subpath slot with no piece in a frame is collapsed to one point, and
    between keyframes the player interpolates from that point to the ring the
    next frame draws. Collapsed onto ring 1's first point -- the far end of the
    part -- that interpolation swept a long thin wedge right across dock_001.

    The point is a mesh vertex of the piece the slot holds in the nearest frame
    that has one, projected in the absent frame itself. Parking on that frame's
    *screen position* instead was the first version, and it is wrong wherever
    the part moves: on the Voyager, part_012.004's slot was absent for fifty
    frames and its parked point sat up to 253 px off the part by the end, which
    is a sweep of its own the moment the piece appears. A vertex travels with
    the part, so the ring grows out of, or shrinks into, its own place.

    `geo` is the slot's per-frame flat point lists; `present` frame -> per-slot
    anchor vertex, or None where the slot is empty; `positions(fi)` that frame's
    projected vertex positions. Nearest by frame distance; a tie takes the
    earlier frame, so a piece that vanishes shrinks where it was.
    """
    frames = sorted(fi for fi in present if geo[fi] is not None)
    if not frames or len(parts) < 2:
        return 0
    starts = np.concatenate([[0], np.cumsum(parts)[:-1]]).astype(int)
    moved = 0
    for k, (s, n) in enumerate(zip(starts, parts)):
        have = [fi for fi in frames if present[fi][k] is not None]
        if not have or len(have) == len(frames):
            continue
        have_arr = np.asarray(have)
        for fi in frames:
            if present[fi][k] is not None:
                continue
            src = int(have_arr[np.argmin(np.abs(have_arr - fi) * 2 + (have_arr > fi))])
            xy = positions(fi)
            if xy is None:
                continue
            x, y = (float(c) for c in xy[present[src][k]])
            geo[fi][s * 2:(s + n) * 2] = [x, y] * n
            moved += 1
    return moved


def _match_pieces(prev, pieces, cap):
    """Which subpath slot each of this frame's pieces belongs to.

    A slot has to hold the *same* piece from frame to frame or the fill tears:
    the nozzle that was subpath 2 last frame cannot become subpath 0 in this
    one. Area order will not do it -- a thruster quad is four pieces of nearly
    equal area, and their ranking swaps on rounding.

    Mesh vertices will. A piece is a set of silhouette vertices, and between two
    frames a few degrees apart most of them are the same ones, so the overlap
    identifies it. That is the rule `carry` already uses to keep a point on the
    vertex it was on; this is the same idea one level up.

    `prev` is what each slot held *when it last held anything*, not only last
    frame. A piece that goes behind the body for a few frames and comes back has
    to come back to its own slot; matched against last frame alone its slot is
    empty, the piece takes whichever slot is free, and two pieces trade places --
    on dock_001 a ring crossed the whole part between two keyframes to get there.

    Returns `slots` -- the piece index in each slot, or None -- and, per slot,
    the vertex set it holds now or last held, to match against next frame.
    """
    sets = [set(int(v) for v in verts) for _, verts, _ in pieces]
    slots = [None] * len(prev)
    if prev:
        cand = sorted(
            (-len(sets[i] & prev[k]), i, k)
            for i in range(len(sets)) for k in range(len(prev))
            if prev[k] and sets[i] & prev[k])
        got_piece, got_slot = set(), set()
        for _, i, k in cand:
            if i in got_piece or k in got_slot:
                continue
            slots[k] = i
            got_piece.add(i)
            got_slot.add(k)
    else:
        got_piece = set()
    for i in range(len(sets)):
        if i in got_piece:
            continue
        for k in range(len(slots)):
            if slots[k] is None:
                slots[k] = i
                break
        else:
            if len(slots) < cap:
                slots.append(i)
            # else: no room, and this piece goes undrawn this frame
    held = [sets[i] if i is not None else (prev[k] if k < len(prev) else None)
            for k, i in enumerate(slots)]
    return slots, held


def _sample_outline_pieces(plan, pieces, xy, depth, slot, fi, settings,
                           face_visible, face_depth, face_shade,
                           face_alpha_max, cull, bounds,
                           key=None, collect=None, face_facing=None):
    """One merged fill, carried as several subpaths.

    Each piece is an ordinary outline: its own ring, its own slots, its own
    `carry` state. Nothing here re-implements what one ring does -- it does it
    once per piece and lays the points end to end, with the split recorded so
    the player knows where one subpath stops and the next starts.
    """
    lo_x, lo_y, hi_x, hi_y = bounds
    prev_sets = plan.piece_verts or []
    cap = plan.outline_parts and len(plan.outline_parts) or merge.MAX_PIECES
    slots, sets = _match_pieces(prev_sets, pieces, cap)

    if plan.outline_n is None:
        # Measuring. Record what each subpath earns; the totals are summed by
        # the caller once every frame has been seen.
        needs = list(plan.piece_needs or [])
        while len(needs) < len(slots):
            needs.append(0)
        for k, i in enumerate(slots):
            if i is None:
                continue
            ids, verts, ccw = pieces[i]
            poly = outline_mod.polyline(ids, xy, xy[verts])
            needs[k] = max(needs[k], outline_mod.need_for(poly, settings.dissolve_px))
        plan.piece_needs = needs
        if plan.outline_need >= 0:
            plan.outline_need = sum(needs)
        plan.piece_verts = sets
        slot["vis"][fi] = 0.0
        return

    parts = plan.outline_parts or [plan.outline_n]
    if plan.outline_prevs is None or len(plan.outline_prevs) != len(parts):
        plan.outline_prevs = [None] * len(parts)

    out = np.zeros((sum(parts), 2), dtype=np.float64)
    at = 0
    drawn_any = False
    present = []
    for k, n in enumerate(parts):
        i = slots[k] if k < len(slots) else None
        if i is None:
            present.append(None)
            # No piece for this slot this frame. Collapse it onto a point: a
            # subpath of coincident points has no area to fill and, with butt
            # caps, no length to stroke. The alternative -- letting the subpath
            # count change between frames -- cannot be expressed at all, since
            # an element's shape is fixed for the whole clip.
            #
            # *Where* is settled once the clip is sampled, in
            # `_settle_absent_pieces`: on a vertex of the piece this slot holds
            # in the nearest frame that has it, projected in *this* frame. Any
            # point chosen here is only a placeholder.
            out[at:at + n] = out[0] if at else xy[pieces[0][1][0]]
            at += n
            continue
        ids, verts, ccw = pieces[i]
        ring, poly = outline_mod.wound(ids, xy, xy[verts], ccw=ccw)
        pos, ring_ids = outline_mod.carry(poly, ring, n, settings.dissolve_px,
                                          plan.outline_prevs[k])
        pts = outline_mod.evaluate(pos, poly)
        plan.outline_prevs[k] = (ring_ids, pts, ring, poly)
        out[at:at + n] = pts
        # The mesh vertex this ring's first point sits nearest: where an absent
        # frame of this slot will park, projected in that frame. A slot sized
        # to no points at all (a piece the measuring pass never saw earn any)
        # has no first point; park on the piece's first vertex instead.
        if len(pts):
            near = np.hypot(*(xy[verts] - pts[0]).T)
            present.append(int(verts[int(np.argmin(near))]))
        else:
            present.append(int(verts[0]))
        at += n
        drawn_any = True
    plan.piece_verts = sets
    plan.piece_present[fi] = present

    visible = drawn_any
    if visible and cull:
        visible = not (out[:, 0].max() < lo_x or out[:, 0].min() > hi_x
                       or out[:, 1].max() < lo_y or out[:, 1].min() > hi_y)
    vis_faces = np.nonzero(face_visible)[0]
    slot["geo"][fi] = out.ravel().tolist()
    slot["vis"][fi] = float(face_alpha_max[vis_faces].max()) if visible and len(vis_faces) else 0.0
    slot["depth"][fi] = float(face_depth[vis_faces].mean()) if len(vis_faces) else 0.0
    slot["shade"][fi] = (float(face_shade[vis_faces].mean())
                         if plan.shading and len(vis_faces) else 1.0)

    # Hand the element to the ordering pass, exactly as the single-ring path
    # does. Leaving it out is silent: the fill still draws, and sorts against
    # everything else on nothing at all. The occlusion audit named it --
    # "element not in this frame's ordering pass" against soyuz_015/m and
    # soyuz_001/m, both of which carry subpaths.
    if visible and collect is not None and key is not None:
        every = np.concatenate([verts for _, verts, _ in pieces])
        item = {
            "key": key, "kind": 1, "seed": slot["depth"][fi],
            "pts": xy[every], "depths": depth[every],
        }
        # Same reason as the single ring: a merged element's own points are the
        # rim, so a plane fitted to them is asked about a middle it never saw.
        # The surface answers with the nearest sample instead.
        if face_facing is not None:
            probe = _surface_probe(plan, face_facing, xy, depth)
            if probe is not None:
                item["probe"] = probe
                tris = _surface_tris(plan, face_facing, xy, depth)
                if tris is not None:
                    item["tris"] = tris
        collect.append(item)


def _deep_bodies(extent, pct):
    """Objects deep enough to span the scene, with something inside them.

    `extent` is `{name: [(dmin, dmax, x0, x1, y0, y1) or None, per frame]}`,
    every list the same length; None is a frame the object had nothing finite
    in, and it counts for nothing either way rather than as a shallow frame.

    Depth is measured per frame, because the scene's depth range is a per-frame
    fact -- a camera orbiting a rigid model sees it deepen and flatten -- and a
    body that is deep only in the frames where everything is deep has not earned
    the split. The fractions are averaged rather than maxed for the same reason:
    one edge-on frame should not commit the whole clip.

    Depth alone is not the whole test, and a scene with one object shows why:
    its range *is* the scene's, so every such object would be split, and split
    for nothing -- there is no second element for it to be ordered against. The
    fault this exists to fix is a pair that cannot be settled, so the body has
    to have something to be confused with: another object overlapping it on
    screen whose own depth range sits inside its own. Not a count -- one is
    enough, and the count is the wrong measure anyway (the probe has a boom
    straddled by 59 objects that spans 8.9% of the scene and orders perfectly
    well by one scalar).
    """
    names = sorted(extent)
    if not names:
        return []
    nf = min((len(extent[n]) for n in names), default=0)
    if nf <= 0:
        return []
    share = {n: 0.0 for n in names}
    present = {n: 0 for n in names}
    holds = {n: False for n in names}
    for fi in range(nf):
        rows = {n: extent[n][fi] for n in names if extent[n][fi] is not None}
        if len(rows) < 2:
            continue
        lo = min(r[0] for r in rows.values())
        hi = max(r[1] for r in rows.values())
        scene = hi - lo
        if scene > 0.0:
            for n, r in rows.items():
                share[n] += (r[1] - r[0]) / scene
                present[n] += 1
        for n, (d0, d1, x0, x1, y0, y1) in rows.items():
            if holds[n]:
                continue
            for m, (e0, e1, u0, u1, v0, v1) in rows.items():
                if m == n or u1 < x0 or u0 > x1 or v1 < y0 or v0 > y1:
                    continue
                if e0 > d0 and e1 < d1:
                    holds[n] = True
                    break
    bar = pct / 100.0
    return sorted(n for n in names
                  if holds[n] and present[n] and share[n] / present[n] >= bar)


def _sample_object(plan, me, eval_obj, settings, slots, fi,
                   view, mvp, width, height, cam_pos, cam_fwd, is_ortho, light,
                   collect=None, cache=None, extent=None):
    nv = len(me.vertices)
    seen = project_object(plan, me, eval_obj, view, mvp, width, height,
                          cam_pos, cam_fwd, is_ortho)
    xy, depth, in_front = seen.xy, seen.depth, seen.in_front
    world_n, world_c = seen.world_n, seen.world_c
    face_front, face_ok, face_depth = seen.face_front, seen.face_ok, seen.face_depth
    if cache is not None:
        cache[plan.name] = (xy, depth)

    nf = plan.nfaces
    face_shade = np.ones(nf, dtype=np.float64)
    if nf:
        half_lambert = 0.5 * (world_n @ light) + 0.5
        face_shade = settings.ambient + (1.0 - settings.ambient) * np.clip(half_lambert, 0.0, 1.0)

    # Nothing here culls geometry behind *other* geometry -- the painter's order
    # simply draws it first and paints over it, which is what lets a translucent
    # fill reveal whatever is behind it for free. What an object does hide is
    # itself: its far side faces away, so the backface cull and hidden-line
    # removal both drop it. For a see-through material that is wrong, and only
    # for that material.
    see_through = (plan.face_alpha < 0.999 if settings.see_through
                   else np.zeros(nf, dtype=bool))

    # One definition of "this face is drawn", used for faces and for the edges
    # around them alike. Deriving edge visibility from `face_front` instead is
    # how edges came to ignore Backface Cull entirely: turning the cull off made
    # the far faces appear while every edge whose faces all pointed away stayed
    # culled, because nothing in the edge path ever consulted the setting.
    if settings.backface_cull:
        face_drawn = face_front | see_through
    else:
        face_drawn = np.ones(nf, dtype=bool)
    face_visible = face_ok & face_drawn

    # Which faces *point* at the camera, as opposed to which get painted. The
    # silhouette is the boundary between the two sides of a solid, so it is
    # this that defines it -- and only this. Deriving it from face_visible ties
    # it to two settings it has no business depending on:
    #
    #   Backface Cull off  -> face_drawn is all True, so every interior edge of
    #                         a closed solid has two drawn faces, none has one,
    #                         and the silhouette set comes back empty. No frame
    #                         yields a ring, so every merge falls back.
    #   a see-through material -> face_drawn is front | see_through, which
    #                         draws both sides of that object for the same
    #                         result, this time with the cull still on.
    #
    # Neither says anything about where the object's outline is. See merge.py.
    face_facing = face_ok & face_front
    if extent is not None:
        extent[plan.name] = _front_extent(plan, face_facing, xy, depth)

    # ---- edges ----------------------------------------------------------
    ne = plan.nedges
    ev = plan.edge_verts
    edge_ok = in_front[ev[:, 0]] & in_front[ev[:, 1]] if ne else np.zeros(0, dtype=bool)
    edge_depth = (depth[ev[:, 0]] + depth[ev[:, 1]]) * 0.5 if ne else np.zeros(0)

    if ne and len(plan.adj_edge):
        # inherit the nearest adjacent face's depth so an edge always sorts in
        # front of the faces it borders
        inherited = np.full(ne, np.inf)
        np.minimum.at(inherited, plan.adj_edge, face_depth[plan.adj_face])
        has_face = np.isfinite(inherited)
        edge_depth = np.where(has_face, inherited, edge_depth)

        front_count = np.zeros(ne, dtype=np.int32)
        np.add.at(front_count, plan.adj_edge, face_front[plan.adj_face].astype(np.int32))
        any_front = front_count > 0
        all_front = front_count == plan.edge_face_count
        boundary = plan.edge_face_count < 2
        # A welded edge is not an open border -- the surface continues on the
        # other object. Leaving it in `boundary` is exactly the seam that Weld
        # Seams exists to remove, and it would be drawn whatever the angle,
        # because boundary bypasses `reachable` too.
        if plan.edge_welded.any():
            boundary = boundary & ~plan.edge_welded
        # Silhouette stays a statement about *facing*, so it is built from
        # front_count alone -- it is where the surface turns away, whether or
        # not the far side happens to be drawn.
        silhouette = any_front & ~all_front
        # Reachable is the separate question of whether the eye gets to the edge
        # at all, and it follows whichever faces are actually being drawn.
        drawn_count = np.zeros(ne, dtype=np.int32)
        np.add.at(drawn_count, plan.adj_edge, face_drawn[plan.adj_face].astype(np.int32))
        weld_cover = np.zeros(ne, dtype=bool)
        # A welded edge's other face belongs to another object, so it is missing
        # from `adj_face` and `reachable` would judge the edge against half the
        # surface it really has. Splitting a part would then hide edges the whole
        # part drew, purely because the neighbour moved out of the mesh -- the
        # same fault the `boundary` and `crease` terms already needed telling
        # about. The far normal and centre were recorded with the weld, so its
        # facing is the same arithmetic `project_object` does.
        if plan.edge_welded.any():
            w = np.nonzero(plan.edge_welded)[0]
            n_far = plan.edge_weld_normal[w]
            if is_ortho:
                far_facing = n_far @ cam_fwd
            else:
                far_facing = np.einsum("ij,ij->i",
                                       n_far, plan.edge_weld_center[w] - cam_pos)
            # `see_through` is a property of the far object's material, which is
            # not reachable from here; front-facing is the case that matters and
            # keeping too much is the safe direction.
            drawn_count[w[far_facing < 0.0]] += 1
            # When may the neighbour's fill hide this edge? Only when the edge
            # is round the back of *both* pieces.
            #
            # "The far face points away" looked like the test and is not. Where
            # the far face points away and this one does not, the edge is the
            # neighbour's *silhouette* -- by definition, a front face meeting a
            # back one -- so its merged fill ends there rather than covering it,
            # and the neighbour draws its own outline along the same line. Nine
            # strokes of part_018.002 were being buried under part_018.005 that
            # way, worth about 420 pixels; the whole part draws every one.
            cover = np.zeros(ne, dtype=bool)
            cover[w] = far_facing >= 0.0
            plan.weld_cover_frames[fi] = set(np.nonzero(cover)[0].tolist())
            weld_cover = cover
        reachable = drawn_count > 0
    else:
        any_front = np.ones(ne, dtype=bool)
        boundary = np.ones(ne, dtype=bool)
        silhouette = np.zeros(ne, dtype=bool)
        reachable = np.ones(ne, dtype=bool)
        weld_cover = np.zeros(ne, dtype=bool)

    mode = plan.edge_mode
    # A merged outline draws this object's silhouette itself, so emitting the
    # silhouette edges as strokes as well is the same line twice -- at one
    # element per edge that ever qualifies. That is the whole cost: the lab
    # sphere has no sharp edge at all and still spent 554 stroke elements, one
    # for each edge the silhouette crossed at some point in the spin.
    #
    # Only the silhouette term goes. Creases, edges marked sharp and open
    # boundaries are fixed by topology, chain into a handful of elements, and
    # are exactly the interior detail a silhouette cannot express -- a
    # cylinder's cap rims, and any edge that stops partway across the shape.
    merged_outline = bool(plan.fills and plan.merge_fill)
    if merged_outline:
        silhouette = np.zeros(ne, dtype=bool)

    if mode == "WIRE":
        edge_shown = np.ones(ne, dtype=bool)
    elif mode == "BOUNDARY":
        edge_shown = silhouette | (boundary & reachable)
    elif mode in ("SHARP", "SHADING"):
        # Creases steeper than the threshold, plus silhouette and open edges.
        # Silhouette has to be included or a smooth curved surface loses its
        # outline entirely and reads as a hole.
        #
        # The two modes differ only in where the threshold comes from. SHARP
        # takes the panel's Angle. SHADING takes whatever Blender is already
        # shading the object with, so the drawn edges are the ones the viewport
        # is drawing hard -- a Smooth by Angle modifier hands over its own
        # angle, and the two stay in step when it is changed. The degenerate
        # cases fall out of the same arithmetic: shade-flat reports 0 degrees,
        # which makes every non-coplanar edge a crease, and shade-smooth
        # reports 180, which makes none of them one and leaves the silhouette.
        limit = plan.shade_angle if mode == "SHADING" else plan.crease_angle
        crease = np.zeros(ne, dtype=bool)
        # A welded edge is an interior edge stored twice, so it gets the same
        # question every interior edge gets: how far do the two faces turn
        # through? The far face belongs to another object, so its normal was
        # recorded when the weld was found rather than looked up here.
        if ne and plan.edge_welded.any():
            w = plan.edge_welded & (plan.edge_face_a >= 0)
            if w.any():
                own = world_n[plan.edge_face_a[w]]
                far = plan.edge_weld_normal[w]
                cos_w = math.cos(math.radians(
                    plan.shade_angle if mode == "SHADING" else plan.crease_angle)) - 1e-9
                crease[w] = np.einsum("ij,ij->i", own, far) < cos_w
            # and only the owning half draws it
            crease &= ~(plan.edge_welded & ~plan.edge_weld_owner)
        if ne and nf and plan.edge_has_pair.any():
            pair = plan.edge_has_pair
            na = world_n[plan.edge_face_a[pair]]
            nb = world_n[plan.edge_face_b[pair]]
            # Nudged off the exact cosine so a 0-degree threshold means "not
            # coplanar" rather than "not bit-identical": two faces of a flat
            # surface dot to 1 - 1e-16, which is below cos(0) and would put a
            # crease down the middle of every flat panel in the scene.
            cos_limit = math.cos(math.radians(limit)) - 1e-9
            dot = np.einsum("ij,ij->i", na, nb)
            crease[pair] = dot < cos_limit
            if mode == "SHADING" and len(plan.adj_edge):
                # A face set to flat shades hard the whole way round whatever
                # the angle says, so its edges are creases as well -- otherwise
                # the mode disagrees with the viewport on any mesh that mixes
                # shade-flat faces into a smooth one.
                #
                # Still only where the surface actually bends. Flat shading a
                # plane looks exactly like smoothing it, so an edge between two
                # coplanar faces is invisible however it is shaded, and drawing
                # it puts a stroke down the middle of every flat panel: a
                # 6x6 shade-flat grid went from 24 strokes to 84.
                bends = np.zeros(ne, dtype=bool)
                bends[pair] = dot < 1.0 - 1e-9
                flat = np.zeros(ne, dtype=bool)
                np.logical_or.at(flat, plan.adj_edge, plan.face_sharp[plan.adj_face])
                crease |= flat & bends
        # A boundary edge is exempt from `reachable`, and the exemption is load
        # bearing. It looks redundant next to the open-shell fallback in
        # c5462e5 -- if a culled shell is drawn from its other side anyway, the
        # face is drawn and the rim is reachable without any help. It is not:
        # that fallback is per object and all or nothing (`if not
        # draw_faces.any()`), it lives in the merged-outline path, and it never
        # touches `face_drawn`. A shell only *partly* turned away keeps its
        # far region culled, and the exemption is the only thing drawing the
        # rim of that region.
        #
        # Which matters because every material in this scene has
        # `use_backface_culling` off, so Blender draws both sides and the bake
        # does not. Measured on 20 frames of the reduced Voyager at tol 1.0,
        # dropping the exemption to `& reachable`: elements 7365 -> 6799, the
        # delivered file 0.97 -> 0.84MB gzipped, and **the ray cast 2.225% ->
        # 2.471% of sampled overlap pixels wrong**. All 572 lost elements are
        # strokes, no object loses every stroke, and the arithmetic looked like
        # a 13% saving for nothing.
        #
        # It is not. One of the 572 is `part_001.001/c1_0`: the magnetometer
        # boom is two quads, one of them faces away, and that chain is its long
        # edge. Without it the boom -- the largest single feature in frame --
        # collapses from a band to a hairline. The ray cast sees the same thing
        # from the other end: `part_002.002/m` over `part_001.001` goes from
        # 4px to 21px wrong at a worst gap of 6.56, because the boom no longer
        # states its own extent.
        marked = plan.edge_marked_sharp
        if plan.edge_welded.any():
            marked = marked & ~(plan.edge_welded & ~plan.edge_weld_owner)
        edge_shown = (crease | marked | boundary | silhouette) & (reachable | boundary)
    else:  # HIDDEN
        edge_shown = reachable | boundary

    # Forced edges bypass the mode filter entirely -- that is what "always"
    # means. They stay subject to depth sorting, so fills still occlude them.
    forced = np.zeros(ne, dtype=bool)
    if settings.always_freestyle:
        forced |= plan.edge_freestyle
    if settings.always_crease:
        forced |= plan.edge_crease > 0.0
    if forced.any():
        edge_shown = edge_shown | forced

    edge_visible = edge_ok & edge_shown

    depth_span = float(np.ptp(depth)) if nv else 1.0
    bias = settings.depth_bias * max(depth_span, 1e-6)
    edge_depth = edge_depth - bias

    # ---- write samples ---------------------------------------------------
    margin = settings.offscreen_margin
    lo_x, lo_y = -margin, -margin
    hi_x, hi_y = width + margin, height + margin
    cull = settings.cull_offscreen

    if plan.fills and plan.merge_fill:
        key = f"{plan.name}/m"
        slot = slots[key]
        # A silhouette that comes apart is several subpaths of one fill now,
        # not one ring with the pieces bridged together. `walk_pieces` hands
        # them back separately and says which way each should be wound; a
        # single piece is the common case and goes down exactly the path it
        # always did.
        pieces = None
        if face_facing.any():
            pieces = merge.walk_pieces(plan, merge.silhouette_edges(plan, face_facing),
                                       xy, face_facing)
        walk = None
        # Once the element carries several subpaths, every frame is written as
        # subpaths -- a frame with a single piece included. Sent down the
        # single-ring path instead, that one ring was written across all the
        # slots, and the player then closed each slot's share of it as a ring
        # of its own: apollo_018.013 (49 / 7 / 9 points) drew two short arcs of
        # its outline as slivers 27 px from the bell they belong to in frames
        # 8-16, on real edges, so no geometry check saw them. The measuring
        # pass has no subpath sizes yet and keeps the single-ring path.
        multi = bool(plan.outline_parts and len(plan.outline_parts) > 1)
        # The measuring pass sends every frame down the pieces path, one piece
        # or several. Sent down the single-ring path, a one-piece frame set
        # `piece_needs` to one number and threw away what earlier frames had
        # measured for the others -- so whether an object was sized as several
        # subpaths depended on its last frame: a torus whose hole is open in
        # frames 8-24 of 31 was sized as one ring, and the same torus cut to
        # frames 1-20 as two (docs/audits.md section 23). Per piece, matched by
        # vertices, it is the same number whatever order the frames come in;
        # for an object that is one ring throughout it is the same polyline and
        # the same `need_for`, so nothing about it changes.
        measuring = plan.outline_n is None
        if pieces is not None and len(pieces) == 1 and not multi and not measuring:
            walk = (pieces[0][0], pieces[0][1])
        if pieces is not None and (len(pieces) > 1 or multi or measuring):
            _sample_outline_pieces(plan, pieces, xy, depth, slot, fi, settings,
                                   face_visible, face_depth, face_shade,
                                   face_alpha_max=plan.face_alpha,
                                   cull=cull, bounds=(lo_x, lo_y, hi_x, hi_y),
                                   key=key, collect=collect,
                                   face_facing=face_facing)
        elif walk is None:
            if plan.outline_n is None and face_facing.any():
                # There are faces pointing at the camera and no ring to be had
                # from them. Merging anyway is what produced a part that draws
                # in eleven frames of sixty and is simply absent for the other
                # forty-nine: the decision was per object and asked whether any
                # frame worked, while the failure is per frame. So an object
                # that cannot be ringed in every frame it faces the camera does
                # not merge in any of them, and draws face by face throughout.
                # A bigger file, and a part that is always there.
                plan.outline_need = -1
            # Nothing to draw this frame either way. On the writing pass the
            # object either merged, in which case this is a frame where it
            # genuinely faces away, or it never merged and is not here at all.
            slot["vis"][fi] = 0.0
        elif plan.outline_n is None:
            # Measuring pass. Record how many points this frame's outline earns
            # and write nothing: the element does not have its final point
            # count yet, so there is no slot of the right width to write into.
            poly = outline_mod.polyline(walk[0], xy, xy[walk[1]])
            # A ring that closes in 3D can still cross itself on screen, where
            # a non-convex part's silhouette folds behind its own body. That
            # used to refuse the object outright, for the whole clip, and it
            # is the largest single reason parts do not merge: 615 frames of a
            # sixty-frame orbit of the probe.
            #
            # It is refusing something nothing has looked at. The player fills
            # with the nonzero winding rule, under which a fold traversed the
            # same way as the body counts as inside and fills solid -- which
            # is the right answer. Only a fold that reverses leaves a hole.
            # Draw it and judge it, rather than assume it draws as a knot.
            if plan.outline_need >= 0:
                want = outline_mod.need_for(poly, settings.dissolve_px)
                plan.outline_need = max(plan.outline_need, want)
                plan.piece_needs = [plan.outline_need]
            slot["vis"][fi] = 0.0
        else:
            ring, poly = outline_mod.wound(walk[0], xy, xy[walk[1]])
            # Where the points go is settled by the loop's measure -- arc
            # length with the corners widened -- so it is a continuous function
            # of the shape and nothing is chosen per frame. Which slot is which
            # is settled by the mesh: a slot stays on the vertex it was on for
            # as long as that vertex is on the silhouette. See outline.py.
            pos, ring_ids = outline_mod.carry(
                poly, ring, plan.outline_n, settings.dissolve_px,
                plan.outline_prev)
            if plan.use_refine:
                # Slide each point to where it costs the least area, within a
                # short leash of where the correspondence put it. The leash is
                # the point: unleashed this optimises each frame separately,
                # and what a viewer sees is the space between frames.
                pos = outline_mod.refine_exact(
                    pos, poly, rounds=outline_mod.REFINE_ROUNDS,
                    limit=outline_mod.REFINE_LEASH / max(plan.outline_n, 1))
            pts = outline_mod.evaluate(pos, poly)
            plan.outline_prev = (ring_ids, pts, ring, poly)
            if plan.edge_welded.any():
                # Wound from the same points, so it reverses exactly when
                # `wound` reversed the ring above and the two stay in step.
                ring_verts, _ = outline_mod.wound(walk[1], xy, xy[walk[1]])
                span = _seam_spans(plan, ring_verts, pos, plan.outline_n)
                if plan.seam_hits is None:
                    plan.seam_hits = np.zeros(plan.outline_n, dtype=np.int64)
                if span is not None:
                    plan.seam_hits += span.astype(np.int64)
                plan.seam_frames += 1
            visible = True
            if cull:
                visible = not (
                    pts[:, 0].max() < lo_x or pts[:, 0].min() > hi_x
                    or pts[:, 1].max() < lo_y or pts[:, 1].min() > hi_y
                )
            vis_faces = np.nonzero(face_visible)[0]
            slot["geo"][fi] = pts.ravel().tolist()
            slot["vis"][fi] = float(plan.face_alpha[vis_faces].max()) if visible else 0.0
            slot["depth"][fi] = float(face_depth[vis_faces].mean())
            slot["shade"][fi] = (float(face_shade[vis_faces].mean())
                                 if plan.shading else 1.0)
            if visible and collect is not None:
                lv_loop = walk[1]
                item = {
                    "key": key, "kind": 1, "seed": slot["depth"][fi],
                    "pts": xy[lv_loop], "depths": depth[lv_loop],
                }
                # The ordering pass compares two fills by evaluating each where
                # they overlap, and for every other element it does that against
                # a plane fitted to that element's own points. For a merged
                # element those points are the silhouette ring, which is the
                # *rim* of the part -- so the plane is fitted to the edge of a
                # bowl and then asked about its middle. The Voyager dish's back
                # shell missed its own depth by up to 1.35 units that way, on a
                # shell 0.067 thick, and the beams sitting inside it sorted
                # behind it. A quadratic halves that on the dish and makes
                # part_005 worse, so there is no polynomial worth picking.
                #
                # Hand over the surface instead: its triangles, so the depth at
                # a query point is read off the face that point lies on, and the
                # samples for the depth range and for points off the surface.
                probe = _surface_probe(plan, face_facing, xy, depth)
                if probe is not None:
                    item["probe"] = probe
                    tris = _surface_tris(plan, face_facing, xy, depth)
                    if tris is not None:
                        item["tris"] = tris
                collect.append(item)
    elif plan.fills:
        lv = plan.loop_verts
        for fidx in range(nf):
            key = f"{plan.name}/f{fidx}"
            slot = slots[key]
            s = int(plan.loop_start[fidx])
            e = s + int(plan.loop_total[fidx])
            verts = lv[s:e]
            pts = xy[verts]
            visible = bool(face_visible[fidx])
            if visible and cull:
                visible = not (
                    pts[:, 0].max() < lo_x or pts[:, 0].min() > hi_x
                    or pts[:, 1].max() < lo_y or pts[:, 1].min() > hi_y
                )
            slot["geo"][fi] = pts.ravel().tolist()
            slot["vis"][fi] = float(plan.face_alpha[fidx]) if visible else 0.0
            slot["depth"][fi] = face_depth[fidx]
            slot["shade"][fi] = face_shade[fidx] if plan.shading else 1.0
            if visible and collect is not None:
                collect.append({
                    "key": key, "kind": 1, "seed": float(face_depth[fidx]),
                    "pts": pts, "depths": depth[verts],
                })

    if plan.strokes:
        for eidx in range(ne):
            key = f"{plan.name}/e{eidx}"
            slot = slots[key]
            a, b = ev[eidx]
            pa, pb = xy[a], xy[b]
            visible = bool(edge_visible[eidx])
            if visible and cull:
                visible = not (
                    max(pa[0], pb[0]) < lo_x or min(pa[0], pb[0]) > hi_x
                    or max(pa[1], pb[1]) < lo_y or min(pa[1], pb[1]) > hi_y
                )
            slot["geo"][fi] = [pa[0], pa[1], pb[0], pb[1]]
            slot["vis"][fi] = 1.0 if visible else 0.0
            slot["front"][fi] = bool(any_front[eidx])
            slot["depth"][fi] = edge_depth[eidx]
            if visible and collect is not None:
                pair = [int(a), int(b)]
                item = {
                    "key": key, "kind": 0, "seed": float(edge_depth[eidx]),
                    "pts": np.array([pa, pb]), "depths": depth[pair],
                    # The faces this edge physically lies on. Their relative
                    # order is topology, not something to be measured -- see
                    # order.resolve().
                    "on_faces": plan.edge_face_keys(eidx),
                    # How much screen a wrong order over this line costs; see
                    # order.resolve's cycle breaker.
                    "width": float(settings.stroke_width),
                }
                if plan.edge_welded[eidx]:
                    over, under = plan.edge_weld_keys(eidx, bool(weld_cover[eidx]))
                    if over:
                        item["on_faces"] = item["on_faces"] + over
                    if under:
                        item["under_faces"] = under
                collect.append(item)


def _surface_probe(plan, facing, xy, depth):
    """A merged part's front surface, as screen points carrying real depths.

    Front-facing rather than drawn: what occludes is the near side, and the
    silhouette these samples sit inside was found from the same set, so the
    outline and the surface it bounds cannot disagree about which side of the
    part they describe.

    The nearest and furthest vertices survive the thinning whatever else does.
    `resolve` reads an element's depth range off these samples and settles a
    pair outright when two ranges do not overlap, so a range short by one
    vertex is not a slightly worse comparison -- it is a pair decided without
    ever being measured.
    """
    fv = _front_verts(plan, facing)
    if fv is None or len(fv) < 3:
        return None
    if len(fv) > PROBE_CAP:
        d = depth[fv]
        ends = fv[[int(d.argmin()), int(d.argmax())]]
        thin = fv[np.linspace(0, len(fv) - 1, PROBE_CAP).astype(np.int64)]
        fv = np.unique(np.concatenate([thin, ends]))
    return xy[fv, 0].copy(), xy[fv, 1].copy(), depth[fv].copy()


def _surface_tris(plan, facing, xy, depth):
    """A merged part's front surface as screen triangles carrying real depths.

    Returns `(tx, ty, td)`, each shaped (T, 3), or None.

    The ordering pass settles a pair by reading both elements' depth at the
    centre of what they share. For a merged part that read used to be the
    nearest surface *vertex*, and on a low-poly body the nearest vertex is
    nowhere near the point: the middle of a long side face is half the face
    away from its closest corner, so a service module was read at the depth of
    one of its ends and sorted over the fittings bolted to its middle. The
    triangle the point lies on answers exactly, and agrees with its neighbour
    along the edge they share, so the read is continuous where the surface is.

    Fan-triangulated, which is exact for the planar faces a mesh mostly has.
    Nothing is thinned: a triangle dropped is a hole the read falls through.
    """
    nf = int(plan.nfaces)
    if not nf or not plan.nverts or not facing.any():
        return None
    if plan.tri_verts is None:
        # Topology, fixed for the clip, so built once per plan.
        totals = plan.loop_total.astype(np.int64)
        starts = plan.loop_start.astype(np.int64)
        ntri = np.maximum(totals - 2, 0)
        count = int(ntri.sum())
        face = np.repeat(np.arange(nf), ntri)
        first = np.repeat(starts, ntri)
        k = np.arange(count) - np.repeat(np.cumsum(ntri) - ntri, ntri) + 1
        lv = plan.loop_verts
        plan.tri_face = face
        plan.tri_verts = (np.stack([lv[first], lv[first + k], lv[first + k + 1]], axis=1)
                          if count else np.empty((0, 3), dtype=np.int64))
    tv = plan.tri_verts[facing[plan.tri_face]]
    if not len(tv):
        return None
    tx, ty, td = xy[tv, 0], xy[tv, 1], depth[tv]
    ok = np.isfinite(tx).all(axis=1) & np.isfinite(ty).all(axis=1) & np.isfinite(td).all(axis=1)
    if not ok.any():
        return None
    return tx[ok].copy(), ty[ok].copy(), td[ok].copy()


def _front_verts(plan, facing):
    """Vertex indices of the faces in `facing`, or None when there are none."""
    nf = int(plan.nfaces)
    if not nf or not plan.nverts or not facing.any():
        return None
    # Which face each loop belongs to is topology, and topology is fixed for
    # the whole clip -- `plan.check` refuses a mesh that changes it. Rebuilding
    # it per frame is three hundred objects' worth of arithmetic per pass, for
    # an answer that was already known before the first frame.
    face_of_loop = plan.face_of_loop
    if face_of_loop is None:
        face_of_loop = plan.face_of_loop = np.repeat(np.arange(nf),
                                                     plan.loop_total)
    return np.unique(plan.loop_verts[facing[face_of_loop]])


def _front_extent(plan, facing, xy, depth):
    """(dmin, dmax, x0, x1, y0, y1) of an object's near side, or None.

    The same vertices `_surface_probe` samples, because the question this
    answers -- can one depth per frame order this body -- is about the range
    `resolve` will actually see for it, and `resolve` reads a merged fill's
    range off the probe. Measured over every vertex instead, the far side of a
    closed body counts, and on Apollo-Soyuz the test picked apollo_002 and
    apollo_024 along with apollo_003.
    """
    fv = _front_verts(plan, facing)
    if fv is None:
        return None
    d, q = depth[fv], xy[fv]
    ok = np.isfinite(d) & np.isfinite(q).all(axis=1)
    if not ok.any():
        return None
    d, q = d[ok], q[ok]
    return (float(d.min()), float(d.max()),
            float(q[:, 0].min()), float(q[:, 0].max()),
            float(q[:, 1].min()), float(q[:, 1].max()))


def _depth_budgets(elements, slots, nframes, settings, depth_range):
    """How far each element's depth may drift per frame before it swaps order.

    Reducing depth tracks independently is what silently destroys a resolved
    paint order: two elements a hair apart in depth each move within their own
    tolerance and cross. So the budget at every frame is half the distance to
    the nearest other visible element in depth. Elements out on their own
    compress freely; elements packed together (mast legs sitting on the ground)
    keep their keys and stop flickering.
    """
    ceiling = settings.tol_depth * depth_range
    keys = [el.key for el in elements]
    if not keys:
        return {}
    depths = np.array([slots[k]["depth"] for k in keys])
    vis = np.array([slots[k]["vis"] for k in keys])
    budget = np.full(depths.shape, np.inf)

    for fi in range(nframes):
        idx = np.nonzero(vis[:, fi] > 0.0)[0]
        if len(idx) < 2:
            continue
        vals = depths[idx, fi]
        order = np.argsort(vals)
        sorted_vals = vals[order]
        gaps = np.diff(sorted_vals)
        margin = np.empty(len(sorted_vals))
        margin[0] = gaps[0]
        margin[-1] = gaps[-1]
        if len(sorted_vals) > 2:
            margin[1:-1] = np.minimum(gaps[:-1], gaps[1:])
        budget[idx[order], fi] = 0.45 * margin

    np.minimum(budget, ceiling, out=budget)
    return {k: budget[i].tolist() for i, k in enumerate(keys)}


def _finish(elements, slots, times, settings, width, height, fps, duration) -> VecScene:
    """Reduce every track and drop elements nobody ever sees."""
    all_depths = []
    for slot in slots.values():
        if slot["vis"].max() > 0.0:
            all_depths.append(slot["depth"][slot["vis"] > 0.0])
    if all_depths:
        stacked = np.concatenate(all_depths)
        depth_range = float(stacked.max() - stacked.min()) or 1.0
    else:
        depth_range = 1.0

    depth_tol = _depth_budgets(elements, slots, len(times), settings, depth_range)

    kept: list[Element] = []
    groups: list[str] = []
    for el in elements:
        slot = slots[el.key]
        vis = slot["vis"]
        if vis.max() <= 0.0:
            continue  # never on screen in any frame -- not worth shipping

        geo = freeze_hidden(slot["geo"], vis)
        el.geo = reduce_points(times, geo, settings.tol_px)
        # Opacity gets its own key times: a fade with no pose behind it is
        # collapsed to the frame the pose was last real on.
        ot, ov = blind_fade_samples(times, vis.tolist(), slot["geo"])
        el.opacity = reduce_scalar(ot, ov, settings.tol_opacity)
        el.depth = reduce_scalar_bounded(times, slot["depth"].tolist(), depth_tol[el.key])
        if el.kind == FILL and settings.shading:
            el.shade = reduce_scalar(times, slot["shade"].tolist(), settings.tol_shade)
            if el.shade.constant and abs(el.shade.values[0] - 1.0) < 1e-6:
                el.shade = None
        else:
            el.shade = None
        if el.group not in groups:
            groups.append(el.group)
        kept.append(el)

    return VecScene(
        width=width,
        height=height,
        fps=fps,
        duration=duration,
        elements=kept,
        groups=groups,
        meta={
            "depth_range": depth_range,
            "tol_px": settings.tol_px,
            # resolved order emits integer ranks, so no decimals are needed;
            # raw depths are real distances and still want precision
            "depth_decimals": 0 if settings.accurate_order else 4,
        },
    )
