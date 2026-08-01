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

from . import api, bake as bake_mod, emit_json, emit_lottie, emit_svg, ir, reduce, settings as settings_mod
from .bake import BakeError
from .settings import BakeSettings

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
            ("VISIBLE", "Visible", "Every visible mesh in the scene"),
            ("SELECTED", "Selected", "Selected meshes only"),
            ("COLLECTION", "Collection", "Meshes in one collection"),
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
        description="Also write each bake played backwards, as <name>.rev.json. "
                    "The reverse is a transform of the baked keyframes, so it costs "
                    "no extra bake time. Both directions share the same resting SVGs")

    use_scene_range: BoolProperty(name="Scene Frame Range", default=True)
    frame_start: IntProperty(name="Start", default=1)
    frame_end: IntProperty(name="End", default=24)
    frame_step: IntProperty(name="Step", default=1, min=1, soft_max=4,
                            description="Sample every Nth frame. The reducer removes "
                                        "redundant keys anyway; raise this only for very long bakes")

    use_scene_res: BoolProperty(name="Render Resolution", default=True)
    width: IntProperty(name="Width", default=960, min=1)
    height: IntProperty(name="Height", default=540, min=1)

    strokes: BoolProperty(name="Strokes", default=True)
    fills: BoolProperty(name="Fills", default=True)
    edge_mode: EnumProperty(
        name="Edges",
        items=[
            ("HIDDEN", "Hidden Line", "Edges touching a front-facing face. The usual choice"),
            ("SHARP", "Sharp Edges", "Only creases sharper than the angle threshold, plus "
                                     "silhouette and open edges. The way to get a dense mesh "
                                     "down to a few hundred strokes"),
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
    backface_cull: BoolProperty(name="Backface Cull", default=True)
    cull_offscreen: BoolProperty(name="Cull Offscreen", default=True)
    accurate_order: BoolProperty(
        name="Resolve Paint Order", default=True,
        description="Work out true occlusion order per frame using plane tests "
                    "instead of centroid depth. Costs bake time, nothing at runtime, "
                    "and stops large floors slicing through objects standing on them")

    stroke_color: FloatVectorProperty(name="Stroke Colour", subtype="COLOR", size=3,
                                      min=0.0, max=1.0, default=(0.07, 0.07, 0.09),
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
                                      min=0.0, max=1.0, default=(0.78, 0.80, 0.84))
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
        description="Drop a chain's interior vertices while the drawn line never moves "
                    "more than this many pixels, in any frame. A straight rail built "
                    "from many segments collapses to two points")

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
    emit_ivg: BoolProperty(name="Animated SVG", default=True,
                           description="One self-contained .svg per camera: a real SVG that "
                                       "renders anywhere, carrying the animation and both "
                                       "resting states inside it. This is what the web "
                                       "player consumes")
    emit_json: BoolProperty(name="Player JSON", default=False,
                            description="The bare track data, without the resting states")
    emit_svg: BoolProperty(name="Resting SVGs", default=False,
                           description="Resting states as separate .svg files. Already "
                                       "inside the .ivg unless you turn that off")
    emit_lottie: BoolProperty(name="Lottie", default=False)
    ivg_states: BoolProperty(name="Embed States", default=True,
                             description="Include the resting-state SVGs inside the .ivg. "
                                         "They gzip to a few KB and remove the need to ship "
                                         "matching files alongside")
    pretty: BoolProperty(name="Readable JSON", default=False)

    last_stats: StringProperty(name="Last Result", default="")

    def to_bake_settings(self, context) -> BakeSettings:
        s = BakeSettings()
        if self.source == "SELECTED":
            s.objects = [o for o in context.selected_objects if o.type == "MESH"]
        elif self.source == "COLLECTION" and self.collection:
            s.objects = [o for o in self.collection.all_objects if o.type == "MESH"]
        s.all_cameras = self.all_cameras
        s.bidirectional = self.bidirectional
        if not self.use_scene_range:
            s.frame_start, s.frame_end = self.frame_start, self.frame_end
        s.frame_step = self.frame_step
        if not self.use_scene_res:
            s.width, s.height = self.width, self.height
        s.strokes, s.fills = self.strokes, self.fills
        s.edge_mode = self.edge_mode
        s.crease_angle = math.degrees(self.crease_angle)
        s.backface_cull = self.backface_cull
        s.cull_offscreen = self.cull_offscreen
        s.accurate_order = self.accurate_order
        s.stroke_color = tuple(self.stroke_color)
        s.stroke_width = self.stroke_width
        s.stroke_source = self.stroke_source
        s.stroke_darken = self.stroke_darken
        s.always_freestyle = self.always_freestyle
        s.always_crease = self.always_crease
        s.use_material_color = self.use_material_color
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
        s.ivg_states = self.ivg_states
        return s


# ---------------------------------------------------------------------------
# operators
# ---------------------------------------------------------------------------

def _summary(stats, files=None, bakes=1):
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
    return text


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


class VECBAKE_OT_export(Operator):
    """Bake the animation and write the selected formats"""

    bl_idname = "vecbake.export"
    bl_label = "Bake & Export"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = context.scene.vecbake
        formats = []
        if props.emit_ivg:
            formats.append("ivg")
        if props.emit_json:
            formats.append("json")
        if props.emit_svg:
            formats.append("svg")
        if props.emit_lottie:
            formats.append("lottie")
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
                report=lambda i, n: wm.progress_update(int(100 * i / max(1, n))),
            )
        except BakeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        finally:
            wm.progress_end()

        props.last_stats = _summary(result["stats"], result["files"], result["bakes"])
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

    def draw(self, context):
        layout = self.layout
        props = context.scene.vecbake

        col = layout.column(align=True)
        col.prop(props, "source")
        if props.source == "COLLECTION":
            col.prop(props, "collection")

        box = layout.box()
        box.label(text="Cameras")
        box.prop(props, "all_cameras")
        box.prop(props, "bidirectional")
        cams = [o for o in context.scene.objects if o.type == "CAMERA"]
        if props.all_cameras:
            count = len(cams) * (2 if props.bidirectional else 1)
            box.label(text=f"{len(cams)} camera(s) -> {count} file set(s)",
                      icon="OUTLINER_OB_CAMERA")
        elif context.scene.camera:
            box.label(text=context.scene.camera.name, icon="CAMERA_DATA")

        box = layout.box()
        box.prop(props, "use_scene_range")
        if not props.use_scene_range:
            row = box.row(align=True)
            row.prop(props, "frame_start")
            row.prop(props, "frame_end")
        box.prop(props, "frame_step")
        box.prop(props, "use_scene_res")
        if not props.use_scene_res:
            row = box.row(align=True)
            row.prop(props, "width")
            row.prop(props, "height")

        box = layout.box()
        box.label(text="Geometry")
        row = box.row(align=True)
        row.prop(props, "strokes", toggle=True)
        row.prop(props, "fills", toggle=True)
        box.prop(props, "edge_mode")
        if props.edge_mode == "SHARP":
            box.prop(props, "crease_angle")
        col = box.column(align=True)
        col.prop(props, "always_freestyle")
        col.prop(props, "always_crease")
        box.prop(props, "backface_cull")
        box.prop(props, "cull_offscreen")
        box.prop(props, "accurate_order")
        box.prop(props, "chain_edges")
        if props.chain_edges:
            box.prop(props, "chain_junctions")
            if props.chain_junctions:
                box.prop(props, "chain_max_turn")
            box.prop(props, "dissolve_px")

        box = layout.box()
        box.label(text="Style")
        box.prop(props, "stroke_source")
        if props.stroke_source == "MATERIAL":
            box.prop(props, "stroke_darken")
        else:
            box.prop(props, "stroke_color")
        box.prop(props, "stroke_width")
        box.prop(props, "use_material_color")
        if props.use_material_color:
            box.prop(props, "color_source")
        else:
            box.prop(props, "default_fill", text="")
        box.prop(props, "shading")
        if props.shading:
            box.prop(props, "light_dir", text="")
            box.prop(props, "ambient")

        box = layout.box()
        box.label(text="Precision")
        box.prop(props, "tol_px")
        box.prop(props, "auto_precision")
        if not props.auto_precision:
            box.prop(props, "coord_decimals")
        box.prop(props, "include_ids")

        box = layout.box()
        box.label(text="Output")
        box.prop(props, "out_dir")
        box.prop(props, "basename")
        row = box.row(align=True)
        row.prop(props, "emit_ivg", toggle=True)
        row.prop(props, "emit_json", toggle=True)
        row.prop(props, "emit_svg", toggle=True)
        row.prop(props, "emit_lottie", toggle=True)
        if props.emit_ivg:
            box.prop(props, "ivg_states")
        box.prop(props, "pretty")

        row = layout.row(align=True)
        row.operator("vecbake.analyze", icon="INFO")
        row.operator("vecbake.export", icon="EXPORT")
        if props.last_stats:
            layout.label(text=props.last_stats, icon="CHECKMARK")


classes = (
    VECBAKE_PG_settings,
    VECBAKE_OT_analyze,
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
