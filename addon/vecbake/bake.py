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

import bpy
from mathutils import Vector

from . import chain as chain_mod
from . import curves
from . import follow
from . import merge
from . import order as order_mod
from . import silhouette
from .ir import FILL, STROKE, Element, Track, VecScene
from .reduce import freeze_hidden, reduce_points, reduce_scalar, reduce_scalar_bounded
from .settings import BakeSettings


class BakeError(Exception):
    pass


# ---------------------------------------------------------------------------
# scene inspection helpers
# ---------------------------------------------------------------------------

def collect_objects(context, settings: BakeSettings):
    if settings.objects:
        objs = list(settings.objects)
    else:
        objs = [o for o in context.scene.objects if o.type == "MESH"]
        try:
            objs = [o for o in objs if o.visible_get()]
        except RuntimeError:
            pass  # no view layer (background mode) -- take them all
    objs = [o for o in objs if o.type == "MESH"]
    if not objs:
        raise BakeError("No mesh objects to bake. Select some meshes or make them visible.")
    return objs


# An untouched Principled BSDF and an untouched viewport colour are both this.
NEUTRAL_GREY = (0.8, 0.8, 0.8)


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


def _smooth_by_angle(obj):
    """Degrees from the object's Smooth by Angle modifier, or None if absent."""
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
                try:
                    return math.degrees(float(mod[item.identifier]))
                except (KeyError, TypeError, ValueError):
                    break
        return 30.0        # present but unreadable: Blender's own default
    return None


def _shading(obj, me, nfaces):
    """(per-face flat flags, mode, degrees) describing Blender's own shading.

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

    angle = _smooth_by_angle(obj)
    if angle is not None:
        return sharp, "ANGLE", angle
    if nfaces and sharp.all():
        return sharp, "FLAT", 0.0
    return sharp, "SMOOTH", 180.0


def _loop_totals(me):
    n = len(me.polygons)
    starts = np.empty(n, dtype=np.int32)
    me.polygons.foreach_get("loop_start", starts)
    totals = np.empty(n, dtype=np.int32)
    if n:
        totals[:-1] = starts[1:] - starts[:-1]
        totals[-1] = len(me.loops) - starts[-1]
    return starts, totals


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
        self.merged = None            # a merge.MergedPath, built on first sample
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

        # edges the artist marked sharp in Blender count as creases whatever
        # the angle says
        self.edge_marked_sharp = np.zeros(self.nedges, dtype=bool)
        if self.nedges:
            try:
                me.edges.foreach_get("use_edge_sharp", self.edge_marked_sharp)
            except (TypeError, AttributeError):
                pass

        # how Blender itself is shading this object, for curve_mode SHADING
        self.face_sharp, self.shade_mode, self.shade_angle = _shading(obj, me, self.nfaces)

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

    def edge_face_keys(self, edge_index):
        """Element keys of the faces this edge borders, if they are drawn."""
        if not self.fills:
            return ()
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

    def sample_pass():
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
            for obj in objects:
                eval_obj = obj.evaluated_get(depsgraph)
                me = eval_obj.to_mesh()
                try:
                    plan = plans.get(obj.name)
                    if plan is None:
                        plan = ObjectPlan(obj, me, settings)
                        if obj.name in no_merge:
                            plan.merge_fill = False
                        plans[obj.name] = plan
                        _make_elements(plan, settings, elements, slots, len(frames))
                    else:
                        plan.check(me)
                    _sample_object(
                        plan, me, eval_obj, settings, slots, fi,
                        view, mvp, width, height, cam_pos, cam_fwd, is_ortho, light,
                        items, cache,
                    )
                finally:
                    eval_obj.to_mesh_clear()

            frame_items.append(items)
            frame_ortho.append(is_ortho)
            vertex_cache.append(cache)

            yield ("Sampling", fi + 1, len(frames))

    merge_fallback: list[str] = []
    try:
        yield from sample_pass()

        # A merged fill commits its slot order on the first frame that yields a
        # ring. Rotate far enough and the silhouette's *cyclic order* changes,
        # not just which vertices are on it, and a vertex pops out into a slot
        # that is no longer in the right place -- the outline crosses itself and
        # the shape collapses. There is no way to fix that after the fact: an
        # element's point count is fixed before sampling starts.
        #
        # So sample again with those objects unmerged. One retry is enough --
        # they have no merged path the second time, so nothing can re-trigger.
        # Two ways a merged fill fails, and both end here.
        #
        # `not stable` is the outline losing its slot order, above. `merged is
        # None` is subtler: no frame ever produced a ring at all. That happens
        # to a closed solid with Backface Cull off, because the silhouette test
        # looks for edges with exactly one *drawn* face and culling is what
        # hides the other one -- with both drawn, every edge has two and there
        # is no silhouette to find. Left alone the object simply never appears.
        merge_fallback = sorted(
            name for name, p in plans.items()
            if p.fills and p.merge_fill
            and (p.merged is None or not p.merged.stable)
        )
        if merge_fallback:
            no_merge.update(merge_fallback)
            plans.clear()
            elements.clear()
            slots.clear()
            frame_items.clear()
            frame_ortho.clear()
            vertex_cache.clear()
            yield from sample_pass()
    finally:
        scene.frame_set(original_frame)

    chain_report = {"chains": 0, "edges_merged": 0, "paths": {}}
    if settings.chain_edges:
        yield ("Chaining edges", 0, 1)
        chain_report = chain_mod.apply(plans, elements, slots, frame_items,
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
                # The slot order is only settled once sampling has run, which
                # it has by the time we get here. Handing it over lets the
                # curve classifier measure angles on the merged ring in 3D
                # like it does for any other path.
                if plan.merged is not None and plan.merged.order is not None:
                    element_verts[f"{plan.name}/m"] = plan.merged.order
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

    if settings.accurate_order:
        for fi, items in enumerate(frame_items):
            if items:
                resolved = order_mod.resolve(items, frame_ortho[fi])
                for item, value in zip(items, resolved):
                    slots[item["key"]]["depth"][fi] = value
            yield ("Resolving occlusion", fi + 1, len(frame_items))

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
                                if p.fills and p.merge_fill and p.merged is not None),
            # Objects whose outline stopped running around the slots in the
            # order the first frame fixed, and were therefore re-sampled
            # unmerged rather than drawn shuffled -- see merge.py.
            "merged_fallback": merge_fallback,
            # Zero by construction now: anything unstable was retried unmerged.
            # A non-zero here would mean the retry itself failed.
            "merged_unstable": sum(1 for p in plans.values()
                                   if p.merged is not None and not p.merged.stable),
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
        # One element for the whole object. It carries a slot per vertex, not
        # per outline point, because the outline's length is a per-frame fact
        # and an element's point count is not -- see merge.py.
        key = f"{plan.name}/m"
        elements.append(
            Element(
                key=key,
                kind=FILL,
                npts=plan.nverts,
                group=plan.group,
                color=plan.face_color[0] if plan.nfaces else 0,
                width=0.0,
            )
        )
        new_slot(key, plan.nverts)
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


def _sample_object(plan, me, eval_obj, settings, slots, fi,
                   view, mvp, width, height, cam_pos, cam_fwd, is_ortho, light,
                   collect=None, cache=None):
    nv = len(me.vertices)
    co = np.empty(nv * 3, dtype=np.float64)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    M = np.array(eval_obj.matrix_world, dtype=np.float64)
    world = co @ M[:3, :3].T + M[:3, 3]

    xy, depth, in_front = project(world, view, mvp, width, height)
    if cache is not None:
        cache[plan.name] = (xy, depth)

    # ---- faces ----------------------------------------------------------
    nf = plan.nfaces
    world_n = world_c = None
    face_front = np.ones(nf, dtype=bool)
    face_depth = np.zeros(nf, dtype=np.float64)
    face_ok = np.ones(nf, dtype=bool)
    face_shade = np.ones(nf, dtype=np.float64)
    if nf:
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
            to_face = world_c - cam_pos
            facing = np.einsum("ij,ij->i", world_n, to_face)
        face_front = facing < 0.0

        # per-face mean depth / visibility, via segmented reduction over loops
        lv = plan.loop_verts
        starts = plan.loop_start
        totals = plan.loop_total.astype(np.float64)
        face_depth = np.add.reduceat(depth[lv], starts) / totals
        face_ok = np.add.reduceat(in_front[lv].astype(np.float64), starts) == totals

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
        # Silhouette stays a statement about *facing*, so it is built from
        # front_count alone -- it is where the surface turns away, whether or
        # not the far side happens to be drawn.
        silhouette = any_front & ~all_front
        # Reachable is the separate question of whether the eye gets to the edge
        # at all, and it follows whichever faces are actually being drawn.
        drawn_count = np.zeros(ne, dtype=np.int32)
        np.add.at(drawn_count, plan.adj_edge, face_drawn[plan.adj_face].astype(np.int32))
        reachable = drawn_count > 0
    else:
        any_front = np.ones(ne, dtype=bool)
        boundary = np.ones(ne, dtype=bool)
        silhouette = np.zeros(ne, dtype=bool)
        reachable = np.ones(ne, dtype=bool)

    mode = plan.edge_mode
    if mode == "WIRE":
        edge_shown = np.ones(ne, dtype=bool)
    elif mode == "BOUNDARY":
        edge_shown = silhouette | (boundary & reachable)
    elif mode == "SHARP":
        # Creases steeper than the threshold, plus silhouette and open edges.
        # Silhouette has to be included or a smooth curved surface loses its
        # outline entirely and reads as a hole.
        crease = np.zeros(ne, dtype=bool)
        if ne and nf and plan.edge_has_pair.any():
            pair = plan.edge_has_pair
            na = world_n[plan.edge_face_a[pair]]
            nb = world_n[plan.edge_face_b[pair]]
            cos_limit = math.cos(math.radians(plan.crease_angle))
            crease[pair] = np.einsum("ij,ij->i", na, nb) < cos_limit
        edge_shown = (crease | plan.edge_marked_sharp | boundary | silhouette) & (reachable | boundary)
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
        loop = None
        if face_visible.any():
            loop = merge.walk_loop(plan, merge.silhouette_edges(plan, face_visible))
        if loop is None:
            # No usable ring this frame -- nothing drawn, or a shape whose
            # outline is not one simple loop. Leave the slot empty; freezing
            # takes care of holding the last good frame.
            slot["vis"][fi] = 0.0
        else:
            if plan.merged is None:
                plan.merged = merge.MergedPath(plan.nverts)
            pts = plan.merged.points(loop, xy)
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
                lv_loop = np.asarray(loop, dtype=np.int64)
                collect.append({
                    "key": key, "kind": 1, "seed": slot["depth"][fi],
                    "pts": xy[lv_loop], "depths": depth[lv_loop],
                })
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
                collect.append({
                    "key": key, "kind": 0, "seed": float(edge_depth[eidx]),
                    "pts": np.array([pa, pb]), "depths": depth[pair],
                    # The faces this edge physically lies on. Their relative
                    # order is topology, not something to be measured -- see
                    # order.resolve().
                    "on_faces": plan.edge_face_keys(eidx),
                })


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
        el.opacity = reduce_scalar(times, vis.tolist(), settings.tol_opacity)
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
