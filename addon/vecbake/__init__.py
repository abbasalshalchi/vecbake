# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""VecBake -- bake a 3D scene to an interpolated 2D vector animation.

The primary output is an **animated SVG**: one `.svg` per camera that renders
anywhere an SVG does, with the keyframes and both resting states carried inside
it. Load the web player and the same file animates.

Optional secondary outputs from the same bake: the bare track JSON, the resting
states as loose SVGs, and a Lottie file for previewing in standard tools.

The 3D simulation happens here, once, at bake time. Playback is pure 2D
interpolation of sparse keyframes.
"""

from __future__ import annotations

import math
import time

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import Operator, Panel, PropertyGroup

from . import api, audit, bake as bake_mod, emit_json, emit_lottie, emit_svg, ir, reduce, settings as settings_mod
from .bake import BakeError
from .settings import BakeSettings


def _shading_hint(context):
    """What Blender Smooth by Angle will actually use, for the objects in scope.

    The threshold lives on each object, not in the panel, so the row has to
    report rather than offer. Objects disagreeing with each other is normal and
    is the point of the mode -- say so instead of picking one to display.
    """
    objs = [o for o in getattr(context, "selected_objects", ()) if bake_mod.drawable(o)]
    if not objs:
        active = getattr(context, "active_object", None)
        objs = [active] if bake_mod.drawable(active) else []
    if not objs:
        return "Angle comes from each object's own modifier"

    angles = []
    for obj in objs:
        deg, _read = bake_mod._smooth_by_angle(obj)
        angles.append(round(deg, 1) if deg is not None else None)
    unique = {a for a in angles}
    if len(unique) == 1:
        only = angles[0]
        if only is None:
            return "No Smooth by Angle here — flat shades every edge, smooth only the outline"
        return f"Using {only:g}° from Smooth by Angle"
    named = sorted(a for a in unique if a is not None)
    shown = ", ".join(f"{a:g}°" for a in named)
    if None in unique:
        shown += " (some objects have none)"
    return f"Per object: {shown}"

__all__ = ["api", "bake_mod", "emit_json", "emit_lottie", "emit_svg", "ir", "reduce", "BakeSettings"]

bake_and_write = api.bake_and_write
bake_scene = api.bake_scene


# ---------------------------------------------------------------------------
# properties
# ---------------------------------------------------------------------------

class VECBAKE_PG_settings(PropertyGroup):
    source: EnumProperty(
        name="Source",
        items=[
            ("VISIBLE", "Visible", "Every visible mesh, curve, text and surface in the scene"),
            ("SELECTED", "Selected", "The selected meshes, curves, texts and surfaces"),
            ("COLLECTION", "Collection", "The meshes, curves, texts and surfaces in one collection"),
        ],
        default="VISIBLE",
    )
    collection: PointerProperty(name="Collection", type=bpy.types.Collection)

    all_cameras: BoolProperty(
        name="All Cameras", default=False,
        description="Bake once from every camera in the scene instead of just the "
                    "active one. Output files are suffixed with the camera name")
    bidirectional: BoolProperty(
        name="Forward + Reverse", default=False,
        description="Also write each JSON and Lottie bake played backwards, as "
                    "<name>.rev.json. The reverse is a transform of the baked "
                    "keyframes, so it costs no extra bake time. Both directions "
                    "share the same resting SVGs. Nothing is written for the "
                    "animated SVG, whose player runs the timeline backwards "
                    "already -- turn this on only for a consumer that cannot")

    use_scene_range: BoolProperty(name="Scene Frame Range", default=True)
    frame_start: IntProperty(name="Start", default=1)
    frame_end: IntProperty(name="End", default=24)
    frame_step: IntProperty(name="Step", default=1, min=1, soft_max=4,
                            description="Sample every Nth frame. The reducer removes "
                                        "redundant keys anyway; raise this only for very long "
                                        "bakes. The end frame is always sampled even when the "
                                        "step does not divide the range, so the clip lands "
                                        "where the camera move does")

    use_scene_res: BoolProperty(name="Render Resolution", default=True)
    width: IntProperty(name="Width", default=960, min=1)
    height: IntProperty(name="Height", default=540, min=1)

    strokes: BoolProperty(name="Strokes", default=True)
    fills: BoolProperty(name="Fills", default=True)
    loose_dissolve_px: FloatProperty(
        name="Loose Edge Thinning",
        description=("Drop interior points within this many pixels from outlines "
                     "built only of edges with fewer than two faces. Applies to "
                     "fill outlines and stroke chains alike, so it works with "
                     "Strokes off. Outlines along shared edges are left alone, "
                     "and so is a merged fill. Zero uses the global Dissolve"),
        default=0.0, min=0.0, max=8.0, subtype="PIXEL",
    )
    merge_fills: BoolProperty(
        name="Merge Fills",
        description=("One filled outline per object instead of one fill per face. "
                     "For parts that only need to read as a silhouette. Override "
                     "per object with a vecbake_merge_fill custom property"),
        default=False,
    )
    weld_seams: BoolProperty(
        name="Weld Seams", default=False,
        description="Treat an open edge that exactly meets an open edge on another "
                    "object as the interior edge it really is, and judge it by the "
                    "angle between the two faces like any other. Splitting a part so "
                    "its pieces each merge is the cheapest way to cut fills, and "
                    "without this it costs a line down every cut: the halves still "
                    "touch, but each sees an open border and draws it")
    weld_tol: FloatProperty(
        name="Weld Distance", default=1e-4, min=1e-6, max=1.0, precision=5,
        description="How close two endpoints must be to count as the same point. "
                    "A clean split leaves them identical; raise this only if a "
                    "modifier or an applied transform has moved one side")
    merge_slack_px: FloatProperty(
        name="Merge Slack", default=6.0, min=0.0, soft_max=32.0, max=512.0,
        subtype="PIXEL",
        description="How much worse a merged outline may be than drawing the object's "
                    "faces before it is drawn face by face instead. Both are measured "
                    "against the true silhouette halfway between two keyframes, where "
                    "nothing is stored. A part that turns edge-on can go from a "
                    "pentagon to a triangle in one frame, and no outline survives that "
                    "-- its faces do. Zero merges whatever rings",
    )
    merge_false_px: FloatProperty(
        name="Merge False Outline", default=160.0, min=0.0, soft_max=1000.0, max=100000.0,
        subtype="PIXEL",
        description="How much outline a merged silhouette made of several pieces may "
                    "draw where the object has none, between keyframes and in view, "
                    "summed over the clip, before it is drawn face by face instead. A "
                    "piece appearing or points sliding across a corner draw line "
                    "through empty space. Zero switches the test off",
    )
    split_span_pct: FloatProperty(
        name="Split Deep Bodies", default=50.0, min=0.0, soft_max=100.0, max=100.0,
        subtype="PERCENTAGE",
        description="Draw an object face by face, rather than as one merged silhouette, "
                    "when its own depth range covers this much of the scene's. A merged "
                    "fill carries one depth per frame, so a body reaching from the front "
                    "of the scene to the back cannot be ordered against the fittings "
                    "bolted to it -- the pairs contradict, and breaking the cycle spends "
                    "a constraint that was right. Its faces each span little depth and "
                    "order cleanly. Zero switches the test off",
    )
    edge_mode: EnumProperty(
        name="Edges",
        items=[
            ("HIDDEN", "Hidden Line", "Edges touching a front-facing face. The usual choice"),
            ("SHARP", "Sharp Edges", "Only creases sharper than the angle threshold, plus "
                                     "silhouette and open edges. The way to get a dense mesh "
                                     "down to a few hundred strokes"),
            ("SHADING", "Blender Smooth by Angle", "Like Sharp Edges, but the angle is the "
                                                   "object's own Smooth by Angle modifier, so "
                                                   "the strokes match the edges the viewport "
                                                   "shades hard. Shade-flat faces are outlined "
                                                   "all round; a fully shade-smooth object "
                                                   "keeps only its silhouette"),
            ("BOUNDARY", "Silhouette", "Silhouette and open edges only -- far fewer strokes"),
            ("WIRE", "Wireframe", "Every edge, see-through"),
        ],
        default="HIDDEN",
    )
    # Blender stores ANGLE properties in radians and shows degrees; the baker
    # works in degrees, so this is converted in to_bake_settings().
    crease_angle: FloatProperty(
        name="Angle", default=math.radians(30.0), min=0.0, max=math.pi, subtype="ANGLE",
        description="Minimum angle between two faces for their shared edge to count as "
                    "a crease. Higher keeps only the hardest edges. Edges marked Sharp "
                    "in the mesh are always kept")
    curve_mode: EnumProperty(
        name="Curves",
        items=[
            ("NONE", "Off", "Straight segments between every point, as baked"),
            ("SMOOTH", "Smooth", "Bend through every point. A baked circle comes out "
                                 "round instead of faceted"),
            # Named for what it measures, not for Blender's modifier of almost
            # the same name -- this one never reads it. "Blender Shading" below
            # is the entry that does, and having both called Smooth by Angle
            # made the panel unreadable.
            ("ANGLE", "Screen Angle", "Bend only where the line turns by less than "
                                      "the threshold, measured on screen in 2D. "
                                      "Corners stay sharp"),
            ("SHADING", "Blender Shading", "Follow the object's own shading: shade-smooth "
                                           "curves throughout, shade-flat stays straight, "
                                           "and Smooth by Angle uses its angle against the "
                                           "3D shape"),
        ],
        default="NONE",
        description="Draw curves through the baked points instead of straight lines. "
                    "Costs no extra data -- the control points are derived from the "
                    "points either side",
    )
    curve_angle: FloatProperty(
        name="Curve Angle", default=math.radians(30.0), min=0.0, max=math.pi, subtype="ANGLE",
        description="How far the line may turn at a point and still be drawn as a curve. "
                    "Measured in 2D on screen, across every frame -- a point that is a "
                    "corner in any frame stays a corner in all of them, so the shape "
                    "cannot pop as the camera moves")
    backface_cull: BoolProperty(name="Backface Cull", default=True)
    see_through: BoolProperty(
        name="See Through", default=True,
        description="A translucent material does not hide its own far side: its back "
                    "faces survive the backface cull and its edges survive hidden-line "
                    "removal. Only that material -- solid geometry elsewhere stays "
                    "solid. Needs Material Alpha to have found some transparency")
    cull_offscreen: BoolProperty(name="Cull Offscreen", default=True)
    occlusion_cull: BoolProperty(
        name="Cull Hidden", default=False,
        description="Drop fills that stay behind other fills in every frame. "
                    "Backface and offscreen culling never remove a wall standing "
                    "directly behind another wall, which in a dense scene is most "
                    "of the geometry. Cuts the file and the bake, and cuts Resolve "
                    "Paint Order hardest of all. Approximate: it works from a "
                    "coverage grid, so something visible only through a very narrow "
                    "gap can be missed")
    occlusion_strokes: BoolProperty(
        name="Cull Hidden Strokes", default=False,
        description="Also drop strokes that stay behind other objects' fills in "
                    "every frame. Hidden-line removal only ever asked whether an "
                    "edge is behind its own object; an edge behind a different "
                    "object is still drawn, and then painted over. Strokes are the "
                    "larger half of most scenes, so this is where the remaining "
                    "overdraw is. Held to a stricter test than fills -- a stroke is "
                    "kept unless its cell and every neighbouring cell are covered -- "
                    "because a stroke lying along a fill's edge would otherwise be "
                    "culled by the sliver the grid rounds away")
    occlusion_px: FloatProperty(
        name="Cull Detail", default=6.0, min=1.0, max=32.0, subtype="PIXEL",
        description="Coverage grid cell size for Cull Hidden. Smaller sees narrower "
                    "gaps and costs more. Raise it if the bake is slow, lower it if "
                    "something visible went missing")
    accurate_order: BoolProperty(
        name="Resolve Paint Order", default=True,
        description="Work out true occlusion order per frame using plane tests "
                    "instead of centroid depth. Costs bake time, nothing at runtime, "
                    "and stops large floors slicing through objects standing on them")
    order_workers: IntProperty(
        name="Order Workers", default=0, min=0, max=64,
        description="How many processes share out Resolve Paint Order, which is "
                    "most of a dense bake. Zero uses one per core, less one for "
                    "Blender. Set it to 1 to run the pass in Blender itself -- "
                    "slower, and the way to check a result the pool produced")

    stroke_color: FloatVectorProperty(name="Stroke Colour", subtype="COLOR", size=3,
                                      min=0.0, max=1.0, default=(0.00605, 0.00605, 0.00857),
                                      description="Colour for every stroke. Override per "
                                                  "object with a 'vecbake_stroke' custom property")
    stroke_width: FloatProperty(name="Width", default=1.6, min=0.0, soft_max=8.0)
    stroke_source: EnumProperty(
        name="Stroke From",
        items=[
            ("FIXED", "Fixed", "One colour for every stroke"),
            ("MATERIAL", "Material", "Darkened version of the colour of the face each "
                                     "edge borders, so outlines tint with their surface"),
        ],
        default="FIXED",
    )
    stroke_darken: FloatProperty(name="Darken", default=0.55, min=0.0, max=1.0,
                                 description="How far to push the material colour towards "
                                             "black for the stroke")

    always_freestyle: BoolProperty(
        name="Always Freestyle Edges", default=False,
        description="Draw every edge marked Freestyle in the mesh, whatever the edge "
                    "mode would have selected. They are still depth sorted, so fills "
                    "can still cover them")
    always_crease: BoolProperty(
        name="Always Crease Edges", default=False,
        description="Draw every edge with a crease value above zero, whatever the edge "
                    "mode would have selected")
    use_material_color: BoolProperty(name="Material Colours", default=True)
    material_alpha: BoolProperty(
        name="Material Alpha", default=True,
        description="Take fill opacity from the material too: the Principled "
                    "Alpha socket, or the viewport colour's alpha, matching "
                    "wherever the colour itself came from. Turn off to make "
                    "every fill solid however it is authored")
    color_source: EnumProperty(
        name="Colour From",
        items=[
            ("AUTO", "Auto", "Shader base colour when it has been set, otherwise the "
                             "viewport display colour"),
            ("MATERIAL", "Shader", "Principled BSDF base colour"),
            ("VIEWPORT", "Viewport", "Material viewport display colour"),
        ],
        default="AUTO",
    )
    default_fill: FloatVectorProperty(name="Fill", subtype="COLOR", size=3,
                                      min=0.0, max=1.0, default=(0.57112, 0.60383, 0.67244))
    shading: BoolProperty(name="Shade Fills", default=True,
                          description="Modulate fill colour by face normal")
    light_dir: FloatVectorProperty(name="Light", subtype="DIRECTION", size=3,
                                   default=(-0.4, -0.7, 0.6))
    ambient: FloatProperty(name="Ambient", default=0.42, min=0.0, max=1.0)

    chain_edges: BoolProperty(
        name="Chain Edges", default=True,
        description="Merge runs of edges that meet end to end into single polylines. "
                    "Only merges edges drawn identically in every frame, so it cannot "
                    "reveal hidden geometry. Gives fewer elements and real line joins")
    chain_junctions: BoolProperty(
        name="Through Crossings", default=True,
        description="Let chains run straight through junctions. An X in the mesh is "
                    "four edges, but strokes may overlap for free in a vector "
                    "renderer, so it ships as two crossing polylines instead")
    chain_max_turn: FloatProperty(
        name="Max Turn", default=math.radians(40.0), min=0.0, max=math.pi / 2,
        subtype="ANGLE",
        description="How far off straight a chain may bend to continue through a "
                    "junction. Lower keeps only near-collinear continuations")
    dissolve_px: FloatProperty(
        name="Dissolve", default=0.35, min=0.0, soft_max=32.0, max=512.0, subtype="PIXEL",
        description="Drop interior vertices while the drawn outline never moves more "
                    "than this many pixels, in any frame. A straight rail built from "
                    "many segments collapses to two points. Applies to stroke chains "
                    "and to fill outlines along loose edges, so it does something with "
                    "Strokes off")

    tol_px: FloatProperty(name="Tolerance", default=0.35, min=0.0, soft_max=32.0, max=512.0,
                          subtype="PIXEL",
                          description="Max error of the interpolated playback, in pixels. "
                                      "This is what trades file size against accuracy. "
                                      "Only bites once you sample more densely than the "
                                      "motion needs")
    auto_precision: BoolProperty(
        name="Auto Precision", default=True,
        description="Derive stored coordinate precision from the tolerance. Keeping "
                    "0.01px precision when the tolerance is 4px just stores digits "
                    "nobody can see")
    coord_decimals: IntProperty(name="Decimals", default=2, min=0, max=6)
    include_ids: BoolProperty(name="Element IDs", default=True,
                              description="Ship stable per-element ids for hit-testing "
                                          "and data binding")

    out_dir: StringProperty(name="Folder", subtype="DIR_PATH", default="//vecbake/")
    basename: StringProperty(name="Name", default="transition")
    emit_lerpa: BoolProperty(name="Animated SVG", default=True,
                           description="One self-contained .svg per camera: a real SVG that "
                                       "renders anywhere, carrying the animation and both "
                                       "resting states inside it. This is what the web "
                                       "player consumes")
    emit_json: BoolProperty(name="Player JSON", default=False,
                            description="The bare track data, without the resting states")
    emit_svg: BoolProperty(name="Resting SVGs", default=False,
                           description="Resting states as separate .svg files. Already "
                                       "inside the .lerpa unless you turn that off")
    emit_lottie: BoolProperty(name="Lottie", default=False)
    emit_still: BoolProperty(name="Still SVG", default=False,
                             description="One plain .svg of the scene at the current "
                                         "frame, with no animation in it. On its own it "
                                         "bakes that frame only, so it is quick whatever "
                                         "the frame range says")
    lerpa_states: BoolProperty(name="Embed States", default=True,
                             description="Include the resting-state SVGs inside the .lerpa. "
                                         "They gzip to a few KB and remove the need to ship "
                                         "matching files alongside")
    emit_preview: BoolProperty(
        name="Preview Page", default=False,
        description="Write a self-contained .preview.html next to the bake. Double-click "
                    "it and the animation plays -- scrubber, per-group colours, and a "
                    "click-to-identify readout. The runtime is inlined, so it needs no "
                    "server, no build and no network, and it still works emailed to "
                    "someone else")
    pretty: BoolProperty(
        name="Readable JSON", default=False,
        description="Write the JSON payload indented, one value per line, instead "
                    "of with every space stripped out. For reading or diffing a "
                    "bake by hand: it changes nothing a player sees, and costs "
                    "two to four times the file size -- Lottie the worst of them. "
                    "Gzip narrows that to well under half, but never to nothing. "
                    "Ship the compact form")

    last_stats: StringProperty(name="Last Result", default="")
    # The audit's findings, kept as text so the panel can list them without a
    # second data structure. One finding per line: element | kind | over | px |
    # cause. Cleared on every bake so a stale verdict is never shown next to
    # fresh output.
    audit_rows: StringProperty(name="Audit", default="", options={"SKIP_SAVE"})
    audit_summary: StringProperty(name="Audit summary", default="", options={"SKIP_SAVE"})
    audit_frames: IntProperty(
        name="Frames", default=8, min=1, max=240,
        description="How many frames to sample, spread evenly across the range. "
                    "Every overlap pixel in each is tested, so this is the whole "
                    "cost -- start low and raise it if something looks suspicious")

    # live progress, mirrored into the panel and the status bar so it survives
    # switching away from Blender -- the cursor readout does not
    progress_text: StringProperty(name="Progress", default="")
    progress_fraction: FloatProperty(name="Progress", default=0.0, min=0.0, max=1.0)
    baking: BoolProperty(name="Baking", default=False)

    def to_bake_settings(self, context) -> BakeSettings:
        s = BakeSettings()
        if self.source == "SELECTED":
            s.objects = [o for o in context.selected_objects if bake_mod.drawable(o)]
        elif self.source == "COLLECTION" and self.collection:
            s.objects = [o for o in self.collection.all_objects if bake_mod.drawable(o)]
        s.all_cameras = self.all_cameras
        s.bidirectional = self.bidirectional
        if not self.use_scene_range:
            s.frame_start, s.frame_end = self.frame_start, self.frame_end
        s.frame_step = self.frame_step
        if not self.use_scene_res:
            s.width, s.height = self.width, self.height
        s.strokes, s.fills = self.strokes, self.fills
        s.merge_fills = self.merge_fills
        s.merge_slack_px = self.merge_slack_px
        s.merge_false_px = self.merge_false_px
        s.split_span_pct = self.split_span_pct
        s.weld_seams = self.weld_seams
        s.weld_tol = self.weld_tol
        s.edge_mode = self.edge_mode
        s.crease_angle = math.degrees(self.crease_angle)
        s.curve_mode = self.curve_mode
        s.curve_angle = math.degrees(self.curve_angle)
        s.loose_dissolve_px = self.loose_dissolve_px
        s.backface_cull = self.backface_cull
        s.see_through = self.see_through
        s.cull_offscreen = self.cull_offscreen
        s.occlusion_cull = self.occlusion_cull
        s.occlusion_px = self.occlusion_px
        s.occlusion_strokes = self.occlusion_strokes
        s.accurate_order = self.accurate_order
        s.order_workers = self.order_workers
        s.stroke_color = tuple(self.stroke_color)
        s.stroke_width = self.stroke_width
        s.stroke_source = self.stroke_source
        s.stroke_darken = self.stroke_darken
        s.always_freestyle = self.always_freestyle
        s.always_crease = self.always_crease
        s.use_material_color = self.use_material_color
        s.material_alpha = self.material_alpha
        s.color_source = self.color_source
        s.default_fill = tuple(self.default_fill)
        s.shading = self.shading
        s.light_dir = tuple(self.light_dir)
        s.ambient = self.ambient
        s.chain_edges = self.chain_edges
        s.chain_junctions = self.chain_junctions
        s.chain_max_turn = math.degrees(self.chain_max_turn)
        s.dissolve_px = self.dissolve_px
        s.tol_px = self.tol_px
        s.auto_precision = self.auto_precision
        s.coord_decimals = self.coord_decimals
        s.include_ids = self.include_ids
        s.lerpa_states = self.lerpa_states
        return s


# ---------------------------------------------------------------------------
# operators
# ---------------------------------------------------------------------------

def _formats(props):
    wanted = (("lerpa", props.emit_lerpa), ("json", props.emit_json),
              ("svg", props.emit_svg), ("lottie", props.emit_lottie),
              ("still", props.emit_still), ("preview", props.emit_preview))
    return [name for name, on in wanted if on]


def _used(props, option) -> bool:
    """Is a format that consumes this option switched on?

    Governs whether the control is worth drawing at all. `_writes_reverse` is
    the narrower question of whether this run would write the reverse files.
    """
    return api.option_used(option, _formats(props))


def _writes_reverse(props) -> bool:
    return props.bidirectional and _used(props, "bidirectional")


def _summary(stats, files=None, bakes=1, scenes=()):
    text = (
        f"{stats['elements']} elements "
        f"({stats['strokes']} strokes, {stats['fills']} fills), "
        f"{stats.get('drawn_peak', 0)} drawn/frame, "
        f"{stats['keyframes']} keyframes"
    )
    if bakes > 1:
        text = f"{bakes} bakes: " + text
    if files:
        total = sum(files.values())
        text += f", {total / 1024:.1f} KB"
    note = _merge_note(scenes)
    if note:
        text += " — " + note
    return text


def _merge_note(scenes):
    """Say so when Merge Fills quietly did not apply.

    An object whose outline is not one stable ring is re-sampled unmerged
    rather than drawn shuffled, which is the right call and completely
    invisible: the setting stays ticked and the object comes back as one fill
    per face, shaded per face. Someone then reasonably asks how a merged object
    can have faces at all. On the Voyager probe 9 of 11 objects fell back and
    nothing said a word.
    """
    fell = set()
    for vec in scenes:
        fell.update((getattr(vec, "meta", None) or {}).get("merged_fallback") or ())
    if not fell:
        return ""
    listed = ", ".join(sorted(fell)[:3])
    if len(fell) > 3:
        listed += f" +{len(fell) - 3} more"
    return f"{len(fell)} could not merge ({listed})"


class VECBAKE_OT_analyze(Operator):
    """Bake without writing -- reports how many on-screen elements you'd ship"""

    bl_idname = "vecbake.analyze"
    bl_label = "Analyze"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = context.scene.vecbake
        settings = props.to_bake_settings(context)
        try:
            cameras = api.resolve_cameras(context, settings)
            per_camera = []
            for cam in cameras:
                per_cam = props.to_bake_settings(context)
                per_cam.camera = cam
                per_camera.append(api.bake_scene(context, per_cam).stats())
            totals = api.combine_stats(per_camera)
        except BakeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        stats = totals
        props.last_stats = _summary(stats, bakes=len(cameras) * (2 if settings.bidirectional else 1))
        self.report({"INFO"}, f"VecBake: {props.last_stats}")
        return {"FINISHED"}


def _redraw(context):
    """Repaint the panels showing progress, plus the status bar."""
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type in {"VIEW_3D", "PROPERTIES"}:
                area.tag_redraw()


class VECBAKE_OT_audit(Operator):
    """Bake, then ray-cast the result against the scene and list what is drawn wrong

    Finds the errors a person finds by staring at frames, which is the only way
    they have been found so far. Only pixels where two objects overlap are
    tested -- everywhere else there is nothing to be wrong about -- so it is
    fast enough to run before shipping a scene.
    """

    bl_idname = "vecbake.audit"
    bl_label = "Check Occlusion"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = context.scene.vecbake
        try:
            settings = props.to_bake_settings(context)
            report = audit.run(settings=settings, context=context,
                               frames=_audit_frames(context, props),
                               budget=100000, verbose=False)
        except BakeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        errors = report["errors"]
        props.audit_rows = "\n".join(
            "{}|{}|{}|{}|{}".format(
                r["element"], r["kind"], r["occluder"], r["pixels"],
                (r.get("diagnosis") or {}).get("cause", "-"))
            for r in errors[:8])
        if errors:
            props.audit_summary = (
                f"{report['wrong_pixels']} wrong of "
                f"{report['overlap_pixels_tested']} overlap px, "
                f"{len(errors)} distinct")
        elif report["overlap_pixels_tested"]:
            props.audit_summary = (
                f"clean -- {report['overlap_pixels_tested']} overlap px checked")
        else:
            # Not the same as clean, and saying so saves a puzzled minute: an
            # ordering error needs two objects overlapping on screen, so a
            # single-object scene has nothing to be wrong about and a pass here
            # means nothing was looked at.
            props.audit_summary = "nothing to check -- no two objects overlap on screen"
        self.report({"INFO"}, "VecBake audit: " + props.audit_summary)
        return {"FINISHED"}


def _audit_frames(context, props):
    """Spread the sample evenly across whatever range will be baked."""
    scene = context.scene
    if props.use_scene_range:
        start, end, step = scene.frame_start, scene.frame_end, props.frame_step
    else:
        start, end, step = props.frame_start, props.frame_end, props.frame_step
    count = max(1, len(range(start, end + 1, max(1, step))))
    want = min(props.audit_frames, count)
    if want <= 1:
        return [0]
    return [round(i * (count - 1) / (want - 1)) for i in range(want)]


class VECBAKE_OT_export(Operator):
    """Bake the animation and write the selected formats"""

    bl_idname = "vecbake.export"
    bl_label = "Bake & Export"
    bl_options = {"REGISTER"}

    _timer = None
    _steps = None
    _slice = 0.12          # seconds of work per tick, then let Blender breathe

    # --- modal path: what you get from the button -------------------------

    def invoke(self, context, event):
        props = context.scene.vecbake
        formats = _formats(props)
        if not formats:
            self.report({"ERROR"}, "No output format selected.")
            return {"CANCELLED"}
        try:
            self._steps = api.bake_and_write_iter(
                context,
                directory=props.out_dir,
                basename=props.basename,
                settings=props.to_bake_settings(context),
                formats=formats,
                pretty=props.pretty,
            )
        except BakeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        props.baking = True
        props.progress_text = "Starting…"
        props.progress_fraction = 0.0
        self._set_status(context, "VecBake: starting…")
        _redraw(context)
        self._timer = context.window_manager.event_timer_add(0.05, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        props = context.scene.vecbake
        if event.type in {"ESC"}:
            self._finish(context)
            props.progress_text = ""
            self.report({"WARNING"}, "VecBake: cancelled")
            return {"CANCELLED"}
        if event.type != "TIMER":
            return {"PASS_THROUGH"}

        # always take at least one step, then keep going until the slice is up
        phase, done, total = "", 0, 0
        deadline = time.perf_counter() + self._slice
        try:
            while True:
                phase, done, total = next(self._steps)
                if time.perf_counter() >= deadline:
                    break
        except StopIteration as finished:
            result = finished.value
            self._finish(context)
            props.last_stats = _summary(
                result["stats"], result["files"], result["bakes"],
                scenes=[o["scene"] for o in result.get("outputs", ()) if o.get("scene")])
            props.progress_text = ""
            self.report({"INFO"}, f"VecBake: {props.last_stats} -> {props.out_dir}")
            return {"FINISHED"}
        except BakeError as exc:
            self._finish(context)
            props.progress_text = ""
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        props.progress_fraction = (done / total) if total else 0.0
        props.progress_text = f"{phase} {done}/{total}" if total > 1 else phase
        self._set_status(context, f"VecBake: {props.progress_text}")
        _redraw(context)
        return {"RUNNING_MODAL"}

    def _set_status(self, context, text):
        try:
            context.workspace.status_text_set(text)
        except AttributeError:
            pass

    def _finish(self, context):
        if self._steps is not None:
            self._steps.close()      # unwinds the bake, restoring the frame
            self._steps = None
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        context.scene.vecbake.baking = False
        context.scene.vecbake.progress_fraction = 0.0
        self._set_status(context, None)
        _redraw(context)

    # --- blocking path: what a script gets from bpy.ops.vecbake.export() ---

    def execute(self, context):
        props = context.scene.vecbake
        formats = _formats(props)
        if not formats:
            self.report({"ERROR"}, "No output format selected.")
            return {"CANCELLED"}
        wm = context.window_manager
        wm.progress_begin(0, 100)
        try:
            result = api.bake_and_write(
                context,
                directory=props.out_dir,
                basename=props.basename,
                settings=props.to_bake_settings(context),
                formats=formats,
                pretty=props.pretty,
                # bake_and_write reports (phase, done, total). Taking two here
                # meant bpy.ops.vecbake.export() died on its first progress
                # tick -- the modal path the button uses never calls this, so
                # only scripted and headless bakes ever hit it.
                report=lambda phase, done, total: wm.progress_update(
                    int(100 * done / max(1, total))),
            )
        except BakeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        finally:
            wm.progress_end()

        props.last_stats = _summary(
            result["stats"], result["files"], result["bakes"],
            scenes=[o["scene"] for o in result.get("outputs", ()) if o.get("scene")])
        self.report({"INFO"}, f"VecBake: {props.last_stats} -> {props.out_dir}")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# panel
# ---------------------------------------------------------------------------

class VECBAKE_PT_panel(Panel):
    bl_label = "VecBake"
    bl_idname = "VECBAKE_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "VecBake"

    @staticmethod
    def _section(layout, title, icon="NONE"):
        """A titled box and the two-column grid to fill it with.

        Everything that fits goes in the grid; anything that would be unreadable
        at half width -- a file path, a colour swatch, a vector -- is put on the
        box itself, full width. Two columns of truncated text is not tidier than
        one column of legible text.
        """
        box = layout.box()
        box.label(text=title, icon=icon)
        grid = box.grid_flow(row_major=True, columns=2, even_columns=True,
                             even_rows=False, align=False)
        return box, grid

    def draw(self, context):
        layout = self.layout
        props = context.scene.vecbake
        scene = context.scene

        box, grid = self._section(layout, "Scene", "SCENE_DATA")
        grid.prop(props, "source", text="")
        if props.source == "COLLECTION":
            grid.prop(props, "collection", text="")
        grid.prop(props, "all_cameras")
        # Only shown when a format that consumes it is on. A checkbox that
        # writes nothing is worse than a missing one: it reads as a setting the
        # bake ignored rather than one this output has no use for.
        if _used(props, "bidirectional"):
            grid.prop(props, "bidirectional")
        cams = [o for o in scene.objects if o.type == "CAMERA"]
        if props.all_cameras:
            count = len(cams) * (2 if _writes_reverse(props) else 1)
            box.label(text=f"{len(cams)} camera(s) -> {count} file set(s)",
                      icon="OUTLINER_OB_CAMERA")
        elif scene.camera:
            box.label(text=scene.camera.name, icon="CAMERA_DATA")

        box, grid = self._section(layout, "Frames & Size", "TIME")
        grid.prop(props, "use_scene_range")
        grid.prop(props, "frame_step")
        if not props.use_scene_range:
            grid.prop(props, "frame_start")
            grid.prop(props, "frame_end")
        grid.prop(props, "use_scene_res")
        grid.separator()
        if not props.use_scene_res:
            grid.prop(props, "width")
            grid.prop(props, "height")

        box, grid = self._section(layout, "Geometry", "MESH_DATA")
        grid.prop(props, "strokes", toggle=True)
        grid.prop(props, "fills", toggle=True)
        grid.prop(props, "merge_fills", toggle=True)
        if props.merge_fills:
            grid.prop(props, "merge_slack_px")
            grid.prop(props, "merge_false_px")
            grid.prop(props, "split_span_pct")
        grid.prop(props, "weld_seams")
        if props.weld_seams:
            grid.prop(props, "weld_tol")
        grid.prop(props, "always_freestyle")
        grid.prop(props, "always_crease")
        grid.prop(props, "accurate_order")
        if props.accurate_order:
            grid.prop(props, "order_workers")
        grid.prop(props, "chain_edges")
        if props.chain_edges:
            grid.prop(props, "chain_junctions")
            if props.chain_junctions:
                grid.prop(props, "chain_max_turn")
        # Both thinning dials sit outside the Chain Edges block, because both
        # now thin fill outlines as well as stroke chains and so do something
        # with Strokes off. What stays inside is only what chaining actually
        # is -- merging many 2-point edges into one polyline, which a fill,
        # being a closed loop already, has no use for.
        grid.prop(props, "dissolve_px")
        grid.prop(props, "loose_dissolve_px")
        box.prop(props, "edge_mode")
        if props.edge_mode == "SHARP":
            box.prop(props, "crease_angle")
        elif props.edge_mode == "SHADING":
            box.label(text=_shading_hint(context), icon="MOD_SMOOTH")

        # Everything that decides what is *not* drawn, in one place. They stack:
        # each one removes a different reason geometry cannot be seen, and the
        # last of them only exists because the first three never look at what is
        # standing in front of what.
        box, grid = self._section(layout, "Culling", "HIDE_OFF")
        grid.prop(props, "backface_cull")
        grid.prop(props, "see_through")
        grid.prop(props, "cull_offscreen")
        grid.prop(props, "occlusion_cull")
        if props.occlusion_cull:
            grid.prop(props, "occlusion_strokes")
            grid.prop(props, "occlusion_px")
            if not props.fills:
                box.label(text="Needs Fills — strokes use hidden-line removal",
                          icon="INFO")

        box, grid = self._section(layout, "Curves", "IPO_EASE_IN_OUT")
        box.prop(props, "curve_mode", text="")
        if props.curve_mode == "ANGLE":
            box.prop(props, "curve_angle")

        box, grid = self._section(layout, "Style", "BRUSH_DATA")
        grid.prop(props, "stroke_width")
        grid.prop(props, "use_material_color")
        if props.use_material_color:
            grid.prop(props, "material_alpha")
        grid.prop(props, "shading")
        if props.shading:
            grid.prop(props, "ambient")
        if props.stroke_source == "MATERIAL":
            grid.prop(props, "stroke_darken")
        box.prop(props, "stroke_source", text="")
        if props.stroke_source != "MATERIAL":
            box.prop(props, "stroke_color", text="")
        if props.use_material_color:
            box.prop(props, "color_source", text="")
        else:
            box.prop(props, "default_fill", text="")
        if props.shading:
            box.prop(props, "light_dir", text="")

        box, grid = self._section(layout, "Precision", "DRIVER")
        grid.prop(props, "tol_px")
        grid.prop(props, "auto_precision")
        if not props.auto_precision:
            grid.prop(props, "coord_decimals")
        # Only the formats carrying an element table have anywhere to put ids.
        if _used(props, "include_ids"):
            grid.prop(props, "include_ids")

        box, grid = self._section(layout, "Output", "EXPORT")
        grid.prop(props, "emit_lerpa", toggle=True)
        grid.prop(props, "emit_still", toggle=True)
        grid.prop(props, "emit_json", toggle=True)
        grid.prop(props, "emit_svg", toggle=True)
        grid.prop(props, "emit_lottie", toggle=True)
        grid.prop(props, "emit_preview", toggle=True)
        # Both are JSON-only: a plain SVG has no payload to indent, and the
        # resting states live inside the animated SVG and nowhere else.
        if _used(props, "pretty"):
            grid.prop(props, "pretty")
        if _used(props, "lerpa_states"):
            grid.prop(props, "lerpa_states")
        box.prop(props, "out_dir")
        box.prop(props, "basename")
        if props.emit_still:
            box.label(text=f"Still: frame {scene.frame_current}", icon="RENDER_STILL")

        row = layout.row(align=True)
        row.enabled = not props.baking
        row.operator("vecbake.analyze", icon="INFO")
        row.operator("vecbake.export", icon="EXPORT")

        # --- audit ----------------------------------------------------------
        # Occlusion errors are a few dozen pixels in a few hundred thousand, so
        # they are found by eye or not at all. This is the "or not at all" half.
        box, grid = self._section(layout, "Audit", "ZOOM_SELECTED")
        row = box.row(align=True)
        row.enabled = not props.baking
        row.prop(props, "audit_frames")
        row.operator("vecbake.audit", icon="VIEWZOOM")
        if props.audit_summary:
            clean = props.audit_summary.startswith("clean")
            box.label(text=props.audit_summary,
                      icon="CHECKMARK" if clean else "ERROR")
        for line in filter(None, props.audit_rows.split("\n")):
            key, kind, over, px, cause = (line.split("|") + [""] * 5)[:5]
            box.label(text=f"{key} ({kind}) over {over} -- {px}px, {cause}",
                      icon="DOT")

        if props.baking:
            # The cursor readout vanishes the moment Blender loses focus, so
            # the same progress is mirrored here and in the status bar.
            box = layout.box()
            if hasattr(box, "progress"):
                box.progress(factor=props.progress_fraction, text=props.progress_text)
            else:
                box.label(text=props.progress_text, icon="TIME")
            box.label(text="Esc to cancel", icon="EVENT_ESC")
        elif props.last_stats:
            layout.label(text=props.last_stats, icon="CHECKMARK")


classes = (
    VECBAKE_PG_settings,
    VECBAKE_OT_analyze,
    VECBAKE_OT_audit,
    VECBAKE_OT_export,
    VECBAKE_PT_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.vecbake = PointerProperty(type=VECBAKE_PG_settings)


def unregister():
    del bpy.types.Scene.vecbake
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
