# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""Bake settings as a plain dataclass.

Kept free of bpy types so scripts can call the baker headlessly without a
Scene PropertyGroup.  The operator builds one of these from the UI props.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BakeSettings:
    # --- what to bake -----------------------------------------------------
    objects: list = field(default_factory=list)   # bpy Objects; empty = visible meshes
    camera: object = None          # bpy camera Object; None = the scene camera
    all_cameras: bool = False      # bake once per camera in the scene
    bidirectional: bool = False    # also emit each bake played backwards
    frame_start: int | None = None
    frame_end: int | None = None
    frame_step: int = 1

    # --- resolution -------------------------------------------------------
    width: int | None = None      # defaults to the render resolution
    height: int | None = None

    # --- what to draw -----------------------------------------------------
    strokes: bool = True
    fills: bool = True
    # One filled outline per object instead of one fill per face. For parts that
    # only need to read as a silhouette; see merge.py. Per-object override is
    # the `vecbake_merge_fill` custom property.
    merge_fills: bool = False
    edge_mode: str = "HIDDEN"     # HIDDEN = only edges with a front-facing face
                                  # WIRE   = every edge, see-through
                                  # BOUNDARY = silhouette + open/boundary edges
                                  # SHARP = creases over crease_angle, + silhouette
                                  # SHADING = same, but the angle is the object's
                                  #   own Smooth by Angle; shade-flat faces count
                                  #   as creases all round, shade-smooth leaves
                                  #   only the silhouette
    crease_angle: float = 30.0    # degrees between adjacent faces, SHARP mode
    backface_cull: bool = True

    # --- curves -----------------------------------------------------------
    # Which vertices a path bends smoothly through, rather than turning a
    # corner at. See curves.py; the mask is static, so it costs nothing at
    # runtime and cannot pop mid-animation.
    #   NONE    = every vertex is a corner (polylines, as before)
    #   SMOOTH  = every vertex is smooth
    #   ANGLE   = 2D screen turn angle vs curve_angle
    #   SHADING = 3D turn angle, threshold taken from Blender's own shading
    curve_mode: str = "NONE"
    curve_angle: float = 30.0     # degrees, ANGLE mode
    # Extra thinning for loose-edge chains only, in pixels. 0 means use
    # dissolve_px like everything else.
    loose_dissolve_px: float = 0.0
    cull_offscreen: bool = True
    occlusion_cull: bool = False   # drop fills hidden behind other fills
    occlusion_px: float = 6.0      # coverage grid cell, in pixels
    offscreen_margin: float = 24.0

    # --- style ------------------------------------------------------------
    # scene-linear, like every Blender COLOR property; these are the linear
    # equivalents of #121217 and #c7ccd6
    stroke_color: tuple = (0.00605, 0.00605, 0.00857)
    stroke_width: float = 1.6
    stroke_source: str = "FIXED"   # FIXED = stroke_color | MATERIAL = darkened fill
    stroke_darken: float = 0.55    # how far towards black, MATERIAL source

    # force edges into the drawing whatever the edge mode selected
    always_freestyle: bool = False   # edges marked Freestyle in the mesh
    always_crease: bool = False      # edges with a crease value above zero
    default_fill: tuple = (0.57112, 0.60383, 0.67244)
    use_material_color: bool = True
    color_source: str = "AUTO"     # AUTO | MATERIAL (Principled) | VIEWPORT
    # Take fill opacity from the material as well as its colour: the Principled
    # Alpha socket, or the viewport colour's fourth channel, matching whichever
    # the colour came from. Off makes every fill solid however it is authored.
    material_alpha: bool = True
    # A translucent surface should not hide its own far side. Lets the back
    # faces of a see-through material through the backface cull, and its edges
    # through hidden-line removal -- for that material only, so solid geometry
    # elsewhere in the scene stays solid. Does nothing without material_alpha.
    see_through: bool = True
    shading: bool = True
    light_dir: tuple = (-0.4, -0.7, 0.6)   # direction *towards* the light
    ambient: float = 0.42

    # --- precision --------------------------------------------------------
    chain_edges: bool = True      # merge runs of edges into single polylines
    chain_junctions: bool = True  # run chains straight through crossings
    chain_max_turn: float = 40.0  # degrees off straight a continuation may bend
    dissolve_px: float = 0.35     # drop interior vertices within this many pixels

    tol_px: float = 0.35          # geometry keyframe tolerance, pixels
    tol_opacity: float = 0.02
    tol_depth: float = 0.004      # relative to the scene's depth range
    tol_shade: float = 0.02
    coord_decimals: int = 2
    auto_precision: bool = True   # derive stored precision from tol_px

    def effective_coord_decimals(self):
        """Storing 0.01px precision under a 4px tolerance is wasted digits.

        Pin the quantum well under the tolerance -- an eighth of it -- so
        rounding never contributes meaningfully to the error budget, then store
        no more digits than that needs.
        """
        if not self.auto_precision:
            return self.coord_decimals
        import math
        quantum = max(self.tol_px, 1e-3) / 8.0
        return max(0, min(6, int(math.ceil(-math.log10(quantum)))))

    # --- misc -------------------------------------------------------------
    depth_bias: float = 1e-4      # pulls edges in front of their own faces
    accurate_order: bool = True   # resolve true paint order offline (see order.py)
    order_workers: int = 0        # processes for that pass; 0 picks from the machine
    include_ids: bool = True
    lerpa_states: bool = True       # embed the resting states in the animated SVG

    def resolved_range(self, scene):
        start = self.frame_start if self.frame_start is not None else scene.frame_start
        end = self.frame_end if self.frame_end is not None else scene.frame_end
        return int(start), int(end), max(1, int(self.frame_step))

    def resolved_size(self, scene):
        if self.width and self.height:
            return int(self.width), int(self.height)
        r = scene.render
        pct = r.resolution_percentage / 100.0
        return max(1, int(r.resolution_x * pct)), max(1, int(r.resolution_y * pct))
