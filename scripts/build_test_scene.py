"""Build the VecBake test scene: a low-poly lattice comms tower + camera orbit.

Deliberately shaped like the real target case -- a see-through lattice mast
(pure edges, no faces) surrounded by solid shaded volumes (deck, cabinets,
dish), so the bake exercises wire edges, hidden-line edges, filled n-gons and
depth-sorted occlusion all at once.

Run inside Blender:  exec(open(__file__).read())
"""

import math

import bpy

TOWER_HEIGHT = 10.0
SEGMENTS = 8
BASE_HALF = 1.55
TOP_HALF = 0.55


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for block in (bpy.data.meshes, bpy.data.materials, bpy.data.cameras, bpy.data.lights):
        for item in list(block):
            if item.users == 0:
                block.remove(item)


def material(name, color):
    """Set both colour surfaces.

    Blender 5 keeps materials node-based whatever you do with `use_nodes`, so
    the viewport colour alone is not enough -- set the Principled base colour
    too and the exporter agrees with the viewport under any colour source.
    """
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1.0)
    if mat.use_nodes and mat.node_tree:
        for node in mat.node_tree.nodes:
            if node.type == "BSDF_PRINCIPLED":
                node.inputs["Base Color"].default_value = (*color, 1.0)
                node.inputs["Roughness"].default_value = 0.6
    return mat


def make_object(name, verts, edges, faces, mat=None, group=None):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, edges, faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    if mat:
        obj.data.materials.append(mat)
    if group:
        obj["vecbake_group"] = group
    bpy.context.scene.collection.objects.link(obj)
    return obj


def box(cx, cy, cz, hx, hy, hz):
    verts = [
        (cx - hx, cy - hy, cz - hz), (cx + hx, cy - hy, cz - hz),
        (cx + hx, cy + hy, cz - hz), (cx - hx, cy + hy, cz - hz),
        (cx - hx, cy - hy, cz + hz), (cx + hx, cy - hy, cz + hz),
        (cx + hx, cy + hy, cz + hz), (cx - hx, cy + hy, cz + hz),
    ]
    faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
             (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    return verts, faces


def append_box(verts, faces, cx, cy, cz, hx, hy, hz):
    offset = len(verts)
    v, f = box(cx, cy, cz, hx, hy, hz)
    verts.extend(v)
    faces.extend(tuple(i + offset for i in face) for face in f)


# ---------------------------------------------------------------------------
# the tower
# ---------------------------------------------------------------------------

def build_mast():
    """Tapered lattice: legs, horizontal rings, alternating cross-bracing.

    Pure edge geometry -- no faces at all.  This is what a real see-through
    mast should be, and it exercises the baker's face-less edge path.
    """
    verts, edges = [], []
    for level in range(SEGMENTS + 1):
        u = level / SEGMENTS
        half = BASE_HALF + (TOP_HALF - BASE_HALF) * u
        z = TOWER_HEIGHT * u
        verts.extend([(-half, -half, z), (half, -half, z), (half, half, z), (-half, half, z)])

    for level in range(SEGMENTS + 1):
        base = level * 4
        # horizontal ring
        for i in range(4):
            edges.append((base + i, base + (i + 1) % 4))
        if level == SEGMENTS:
            continue
        nxt = base + 4
        for i in range(4):
            edges.append((base + i, nxt + i))          # legs
        # cross-bracing, flipped each level so it reads as a zigzag
        for i in range(4):
            a, b = base + i, base + (i + 1) % 4
            c, d = nxt + i, nxt + (i + 1) % 4
            edges.append((a, d) if level % 2 == 0 else (b, c))
    return verts, edges


def build_scene():
    clear_scene()
    scene = bpy.context.scene

    steel = material("Steel", (0.55, 0.58, 0.62))
    deck = material("Deck", (0.42, 0.45, 0.50))
    panel = material("Antenna", (0.86, 0.87, 0.89))
    cabinet = material("Cabinet", (0.30, 0.55, 0.72))
    ground = material("Ground", (0.88, 0.89, 0.90))
    dish_mat = material("Dish", (0.93, 0.93, 0.94))

    # --- mast (edges only) ---
    mverts, medges = build_mast()
    make_object("Mast", mverts, medges, [], steel, group="Mast")

    # --- ground pad ---
    # Subdivided on purpose. Painter's algorithm sorts whole elements, so one
    # huge quad can only be entirely in front of or entirely behind the tower.
    # A grid gives the sort something to work with; vecbake_strokes=False keeps
    # the subdivision from drawing as a grid on the floor.
    pad, div = 7.0, 4
    pverts, pfaces = [], []
    for iy in range(div + 1):
        for ix in range(div + 1):
            pverts.append((-pad + 2 * pad * ix / div, -pad + 2 * pad * iy / div, 0.0))
    for iy in range(div):
        for ix in range(div):
            a = iy * (div + 1) + ix
            pfaces.append((a, a + 1, a + div + 2, a + div + 1))
    padobj = make_object("Pad", pverts, [], pfaces, ground, group="Site")
    padobj["vecbake_strokes"] = False

    # --- deck at the top ---
    verts, faces = [], []
    append_box(verts, faces, 0, 0, TOWER_HEIGHT + 0.12, TOP_HALF + 0.55, TOP_HALF + 0.55, 0.12)
    make_object("Deck", verts, [], faces, deck, group="Deck")

    # --- three antenna panels around the deck ---
    verts, faces = [], []
    for i in range(3):
        angle = math.radians(90 * i + 30)
        r = 1.05
        append_box(verts, faces, r * math.cos(angle), r * math.sin(angle),
                   TOWER_HEIGHT + 0.95, 0.12, 0.32, 0.62)
    make_object("Antennas", verts, [], faces, panel, group="Antennas")

    # --- microwave dish: an octagonal disc on a short stub ---
    verts, faces = [], []
    cx, cy, cz, r = 1.25, -0.35, 8.1, 0.85
    rim = []
    for i in range(8):
        a = math.radians(45 * i)
        rim.append((cx + 0.08 * math.cos(a), cy - r * math.sin(a) * 0.15 - 0.0, cz + r * math.sin(a)))
    ring = []
    for i in range(8):
        a = math.radians(45 * i)
        ring.append((cx, cy + r * math.cos(a), cz + r * math.sin(a)))
    verts.extend(ring)
    faces.append(tuple(range(8)))
    append_box(verts, faces, cx - 0.35, cy, cz, 0.35, 0.09, 0.09)
    make_object("Dish", verts, [], faces, dish_mat, group="Dish")

    # --- equipment cabinets ---
    verts, faces = [], []
    append_box(verts, faces, -3.1, 1.9, 0.85, 0.75, 0.55, 0.85)
    append_box(verts, faces, -3.1, 0.15, 0.7, 0.75, 0.55, 0.7)
    make_object("Cabinets", verts, [], faces, cabinet, group="Cabinets")

    # --- a 24-sided tank: smooth-ish sides, hard rims ---
    # This is what Sharp Edges mode is for. At a 30 degree threshold the 24
    # side edges (15 degrees apart) drop out and only the rims and silhouette
    # survive, taking the tank from ~72 strokes to a handful.
    tank_verts, tank_faces = [], []
    tcx, tcy, tr, th, seg = 3.4, 2.2, 1.05, 2.3, 24
    for level, z in ((0, 0.0), (1, th)):
        for i in range(seg):
            a = 2 * math.pi * i / seg
            tank_verts.append((tcx + tr * math.cos(a), tcy + tr * math.sin(a), z))
    for i in range(seg):
        j = (i + 1) % seg
        tank_faces.append((i, j, seg + j, seg + i))
    tank_faces.append(tuple(range(seg - 1, -1, -1)))
    tank_faces.append(tuple(range(seg, seg * 2)))
    make_object("Tank", tank_verts, [], tank_faces, panel, group="Tank")

    # --- perimeter fence: posts + two rails, edges only ---
    fverts, fedges = [], []
    fence, posts, ph = 5.2, 16, 1.5
    for i in range(posts):
        a = 2 * math.pi * i / posts
        x, y = fence * math.cos(a), fence * math.sin(a)
        base = len(fverts)
        fverts.extend([(x, y, 0.0), (x, y, ph * 0.55), (x, y, ph)])
        fedges.append((base, base + 2))
    for ring_offset in (1, 2):
        for i in range(posts):
            a = ring_offset + i * 3
            b = ring_offset + ((i + 1) % posts) * 3
            fedges.append((a, b))
    make_object("Fence", fverts, fedges, [], steel, group="Site")

    # --- camera on an orbiting rig ---
    target = bpy.data.objects.new("OrbitTarget", None)
    target.location = (0.0, 0.0, 5.0)
    scene.collection.objects.link(target)

    cam_data = bpy.data.cameras.new("Camera")
    cam_data.lens = 42.0
    cam = bpy.data.objects.new("Camera", cam_data)
    cam.location = (24.0, -21.0, 3.5)
    cam.parent = target
    scene.collection.objects.link(cam)
    track = cam.constraints.new("TRACK_TO")
    track.target = target
    track.track_axis = "TRACK_NEGATIVE_Z"
    track.up_axis = "UP_Y"
    scene.camera = cam

    # a second, tighter camera so multi-camera bakes have something to bake
    detail_data = bpy.data.cameras.new("CamDetail")
    detail_data.lens = 55.0
    detail = bpy.data.objects.new("CamDetail", detail_data)
    detail.location = (13.0, -11.0, -1.2)
    detail.parent = target
    scene.collection.objects.link(detail)
    detail_track = detail.constraints.new("TRACK_TO")
    detail_track.target = target
    detail_track.track_axis = "TRACK_NEGATIVE_Z"
    detail_track.up_axis = "UP_Y"

    light_data = bpy.data.lights.new("Sun", type="SUN")
    light = bpy.data.objects.new("Sun", light_data)
    light.location = (6, -8, 14)
    scene.collection.objects.link(light)

    # --- a half-second orbit, eased at both ends ---
    scene.frame_start, scene.frame_end = 1, 25
    scene.render.fps = 24
    scene.render.resolution_x, scene.render.resolution_y = 960, 540
    scene.render.resolution_percentage = 100

    target.rotation_euler = (0.0, 0.0, 0.0)
    target.keyframe_insert("rotation_euler", index=2, frame=1)
    target.rotation_euler = (0.0, 0.0, math.radians(38.0))
    target.keyframe_insert("rotation_euler", index=2, frame=25)
    scene.frame_set(1)

    counts = {}
    for obj in scene.objects:
        if obj.type == "MESH":
            counts[obj.name] = {"edges": len(obj.data.edges), "faces": len(obj.data.polygons)}
    return counts


if __name__ == "__main__":
    print(build_scene())
