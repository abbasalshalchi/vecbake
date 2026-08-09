# VecBake

Bake a 3D Blender scene into an interpolated 2D vector animation.

The 3D simulation runs **once, offline, at bake time**. Playback is pure 2D
interpolation of sparse keyframes — no camera, no projection, no z-sort at
runtime. That relocation is the whole idea: it buys accurate hidden-line
removal and true occlusion ordering at a runtime cost of a few multiply-adds
per point.

The primary output is an **animated SVG**: one `.svg` per camera that is a real
SVG file *and* carries its own animation.

```
Blender scene ──bake──> IR ──┬──> tower.svg          animated SVG  ← the output
                             │                       (artwork + keyframes +
                             │                        both resting states)
                             ├──> tower.json         bare track data, optional
                             ├──> tower.start.svg    states as loose files, optional
                             └──> tower.lottie.json  for standard Lottie tools
```

`tower.svg` renders anywhere an SVG does — double-click it, drop it in an
`<img>`, open it in Figma — because it *is* an SVG showing the resting state.
Load the [web player](web/README.md) and the same file animates, because the
keyframes ride along inside it in a `<metadata>` block. No new extension, no
MIME configuration, no build plugin, and no broken image if JavaScript never
runs.

Both resting states travel inside it, so a transition and the two states it
sits between cannot drift apart or go missing separately. There is no reverse
file either — the player runs the timeline backwards.

Measured on a real telecom-site scene (1,654 elements, 1280×720, 12 frames):

| | |
|---|---|
| Bake | ~24 s, one camera |
| `tower.svg` | 1,198 KB raw, **259 KB gzipped** |
| — of which resting states | ~361 KB raw, a few KB gzipped |
| Player frame cost | 8–11 ms, or **4–5 ms** with seam fixing off |
| Occlusion violations | 95, all under **0.4%** of scene depth (flush-mounted parts) |
| Lines covered by their own face | **0** |

Most of the bake is offline occlusion resolution, which is quadratic in
overlapping elements — the price of correct ordering, paid once. Sharp-edge
filtering cuts it substantially by cutting element count.

Frame cost is driven by element count, not resolution — it measures flat from
640×360 to 1920×1080. See [web/README.md](web/README.md#performance).

---

## Install

Copy `addon/vecbake` into Blender's user extensions folder, or from Blender's
Python console:

```python
exec(open(r'<project>/scripts/dev.py').read())
install()
```

Then enable **VecBake** in `Edit > Preferences > Add-ons`. The panel lands in
the 3D viewport N-panel under a **VecBake** tab. Blender 4.2+ (developed
against 5.1).

## Use

Set a camera animation, pick your meshes, press **Bake & Export**. **Analyze**
runs the same bake without writing anything and reports the element count —
use it to check you are inside your budget before committing.

The bake runs as a modal operator, in slices of about an eighth of a second, so
Blender stays responsive throughout and progress appears **in the panel and the
status bar** rather than only on the cursor — a cursor readout disappears the
moment you switch to another window, and a blocking bake would leave the whole
app looking hung. Esc cancels, and the scene's current frame is restored either
way. Calling `bpy.ops.vecbake.export()` from a script still runs blocking.

From script:

```python
import vecbake
from vecbake.settings import BakeSettings

s = BakeSettings()
s.edge_mode = "SHARP"      # HIDDEN | SHARP | BOUNDARY | WIRE
s.crease_angle = 30.0
s.all_cameras = True
vecbake.bake_and_write(bpy.context, "//out", "tower",
                       settings=s, formats=("ivg",))   # "ivg" writes tower.svg
```

Works headless: `blender -b scene.blend -P bake.py`.

Output formats, all optional except the first:

| format | file | |
|---|---|---|
| `ivg` | `tower.svg` | the animated SVG — what the web player consumes |
| `still` | `tower.still.svg` | one plain picture of the current frame, no animation |
| `json` | `tower.json` | bare track data, no artwork or states |
| `svg` | `tower.start.svg`, `tower.end.svg` | resting states as loose files |
| `lottie` | `tower.lottie.json` | for standard Lottie tools |

**Forward + Reverse** only affects `json` and `lottie`. The animated SVG needs
no reverse file, since the player runs the timeline backwards for free.

**Still** is the one that has nothing to do with animation: a flat SVG of the
scene as it stands at the **current frame**, the way Render Image means the
current frame. No `<metadata>`, no tracks, nothing for a player to read —
artwork you can drop into Illustrator or an `<img>` and be done.

It is always its own single-frame bake, never a slice of the animated one. That
costs a few milliseconds and buys two things. Asked for on its own it bakes one
frame instead of the whole range — 0.05s against 5.65s on a 250-frame clip, and
the gap only widens. And the picture never depends on the export range: sampling
the clip would mean a range that happens not to contain the current frame quietly
handing back a different frame instead. Ticked alongside the animated formats it
is byte-identical to the same still exported alone.

## Play

Install the [player](web/README.md) once, then drop the file in:

```html
<script type="module" src="https://unpkg.com/ivg"></script>

<ivg-player src="/anim/tower.svg" autoplay loop></ivg-player>
```

`<ivg-player>` is a custom element, so it behaves the same in Vue, Svelte,
Angular, plain HTML and React 19+. There is a React binding and an imperative
`load()` API too — see [web/README.md](web/README.md).

Without the player it is still a valid SVG:

```html
<img src="/anim/tower.svg" alt="site">   <!-- renders the resting state -->
```

The player API:

| | |
|---|---|
| `play({loop})` / `reverse()` / `pause()` / `seek(t)` | playback, either direction |
| `setGroupColor('Cabinets', '#e5484d')` | live data binding — tint a group mid-animation |
| `setElementColor(id, css)` / `setColor(i, css)` | per-element / per-swatch theming |
| `pick(clientX, clientY)` | hit-test **during** playback; returns `{id, group}` |
| `showState('end')` | hard-cut to the resting state as real, interactive DOM |
| `lastRenderMs`, `visible` | instrumentation |

Two harnesses ship with the project:

* `web/examples/vanilla.html` — the same file loaded three ways: a plain
  `<img>`, the element, and the API, with group tinting and click-to-pick.
* `web/examples/bench.html` — frame cost, with and without seam fixing.
* `player/demo.html` — the original scrubber and A/B seam check, kept because
  it exercises the bare `.json` track format directly.

---

## The three things that were hard

Worth reading before changing `order.py` or `reduce.py` — each of these was a
real bug that produced a visible artifact.

### 1. Correspondence is free, but only from topology

An element's identity is a **mesh index** (`Mast/e37` is always edge 37), not a
re-traced silhouette stroke. So there is never a "which point became which
point" problem — we re-project the same points every frame. This is why
Freestyle output can't feed a format like this and a mesh can.

The cost: **geometry must be topologically static**. Deformation is fine
(armature, shape keys, transforms, camera moves); anything changing vertex,
edge or face counts mid-animation is rejected with an explicit error rather
than silently producing garbage.

### 2. One depth scalar per element cannot express occlusion

A large floor quad and a box standing on its far half: the floor's *centroid*
is nearer, so a centroid sort paints the floor over the box and slices it in
half. `order.py` resolves true order offline — for every pair overlapping on
screen, it samples depth **inside the shared region** and builds a DAG, then
topologically sorts it. The player still sorts one scalar; it just receives
values already in the right order.

Comparing at the overlap is what makes this **antisymmetric** — each pair
yields at most one constraint. The obvious alternative, Newell's pairwise
plane-side tests, is not: coplanar polygons each test as "in front of" the
other, and adjacent faces of a box each test as "behind" the other. Both
produce 2-cycles between every neighbouring pair, saturating the graph with
cycles until it silently degrades back to centroid order.

Three further traps, all fixed:

- **Don't skip pairs whose depth ranges are disjoint.** It looks safe — seed
  depth already orders them — but the topological sort reorders *globally*, so
  an element held back by its own constraints gets carried straight past an
  unconstrained neighbour. This is what let the ground paint over the mast legs.
- **Emitted depth must be injective.** Seeds are not distinct (an edge inherits
  the depth of the face it borders, so every edge around a face ties). Indexing
  sorted seeds collapses distinct ranks onto one value and hands the tie back to
  the player to break arbitrarily.
- **An edge lying on a face cannot be ordered by sampling it.** The two are
  coincident, and the plane fitted to an n-gon that is not perfectly planar
  misses its own boundary by a hair — enough to read as "the face is nearer".
  The fill then paints over the inner half of its own outline and the line looks
  thinned, or disappears where coverage is total. Sampling cannot fix this
  because sampling is what is wrong; the relation is asserted from topology
  instead. Two details matter: retract the opposing measured edge first, or the
  pair sits in the graph as a 2-cycle the breaker then pays to resolve, and
  never spend an assertion to break a cycle while any measured edge is available
  — a component dominated by assertions otherwise exhausts its measured edges
  partway through the budget and the remainder comes out of the facts.

### 3. Keyframe reduction can destroy a correct order

Reducing each element's depth track independently lets two elements a hair
apart each drift within tolerance and swap. Symptom: correct on the first and
last frames (RDP preserves endpoints exactly) and wrong everywhere between —
plus mast legs flickering in and out of the ground.

So depth uses a **per-sample error budget**: at every frame the budget is half
the distance to the nearest other visible element in depth. Elements out on
their own still compress hard; elements packed together keep their keys.

Run `scripts/validate_bake.py` after changing any of this. It recomputes ground
truth independently and should report zero violations, zero flips, zero cycles.

---

## Reference

### Edge modes

| Mode | Draws |
|---|---|
| `HIDDEN` | edges touching a front-facing face — the usual choice |
| `SHARP` | creases above **Angle**, plus silhouette and open edges |
| `BOUNDARY` | silhouette and open edges only |
| `WIRE` | every edge, see-through |

`SHARP` is how you get a dense mesh down to a few hundred strokes. Silhouette is
always included — without it a smooth curved surface loses its outline and reads
as a hole. Edges marked Sharp in the mesh are always kept.

**Always Freestyle Edges** / **Always Crease Edges** force edges into the
drawing whatever the mode selected — useful for hand-marking lines a geometric
filter would miss. They remain depth sorted, so fills still cover them. In
Blender 5 these are generic attributes (`freestyle_edge`, `crease_edge`), not
`MeshEdge` properties, and they only exist once something has been marked, so an
unmarked mesh simply reads as "none".

### Chaining and dissolving

**Chain Edges** merges runs of edges meeting end to end into single polylines:
fewer elements to sort and draw, a smaller file (shared vertices stop being
stored twice), and real line joins instead of butted segments.

**Through Crossings** is where most of the saving comes from. A junction does
not have to end a chain: an X in the mesh is four edges, but strokes may
*overlap for free* in a vector renderer, so it ships as two crossing polylines
rather than four stubs. At each junction the incident edges are paired by
angular continuity, straightest first, and a chain runs straight through.
**Max Turn** caps how far off straight a continuation may bend. Pairing uses 3D
directions so it is decided once and holds for every frame — element identity
depends on it.

Measured on a real telecom-site scene (10,563 edges, 3 frames, Hidden Line):

| | strokes | chains | edges merged |
|---|---|---|---|
| no chaining | 920 | — | — |
| chaining, junctions off | 826 | 48 | 142 |
| Max Turn 20° | 584 | 107 | 443 |
| Max Turn 40° (default) | 560 | 116 | 476 |
| Max Turn 60° | 528 | 126 | 518 |
| Max Turn 85° | 514 | 131 | 537 |
| **Sharp Edges 30° + Max Turn 60°** | **417** | 92 | 365 |

Returns flatten past about 60°.

Merging is only legal when the members are drawn *identically in every frame* —
otherwise a chain would light up sections that should be hidden. Rather than
guess from topology, chaining compares the sampled visibility arrays bit for
bit, which is exact and independent of the edge mode in force.

**Dissolve** then drops interior vertices, reusing the keyframe reducer's
philosophy: a vertex goes only if removing it never moves the drawn line by more
than the tolerance **in pixels**, in any frame. A straight rail modelled as
eight collinear segments collapses to two points.

### Why chains sometimes have to be split

A chain carries a single depth value. Depth is only a sort key, so being behind
one object and in front of another is fine — the ordering pass just ranks the
chain between them. There is exactly one arrangement no rank can express: a
single surface that is in front of *part* of a chain and behind another part.
Whichever side of it the chain is placed, the other half is wrong, and the
symptom is a chained line painting straight over something it ran behind.

Chaining therefore tests each chain against the fills it actually overlaps on
screen, comparing depths at the midpoint of every member inside the fill, and
cuts only where that comparison changes sign along the chain's length.

The distinction matters. A cruder rule — "cut wherever a fill's depth falls
between two members" — makes **48 cuts on the bundled test scene where 2 are
needed**, since it fires on fills the chain never overlaps. Cutting on genuine
straddles costs 2 extra elements and about a third more bake time.

Note that **element count is not what you should watch** — it counts anything
visible in *any* frame, and an edge that is a silhouette for one frame of an
orbit still ships. `drawn_peak` in the stats is the number that governs runtime
cost.

### Per-object overrides

Custom properties on any object:

| Property | Effect |
|---|---|
| `vecbake_group` | group name for theming / hit-testing (default: object name) |
| `vecbake_strokes` | `False` to suppress this object's edges |
| `vecbake_fills` | `False` to suppress its faces |
| `vecbake_edge_mode` | per-object edge mode |
| `vecbake_crease_angle` | per-object crease threshold |
| `vecbake_stroke` | per-object stroke colour |

`vecbake_strokes = False` on a **subdivided** ground plane is the standard
recipe: you want the subdivision so depth sorting has something to work with,
but not a grid drawn on the floor.

### Cameras and direction

Output is **always** named after the camera — `tower.tower_cam.svg`,
`tower.map_cam.svg` — whether you bake one camera or all of them. Naming that
varied with how many cameras were selected meant re-baking a single camera
wrote a different filename and left the old file behind; now a bake replaces
exactly what it should.

**All Cameras** bakes once per camera. A folder holding more than one clip also
gets a `tower.index.json` manifest listing every clip with its duration, size
and measured camera motion.

You will not see it for a single clip, because there is nothing to choose
between — the `.svg` is self-contained and plays on its own, so an index beside
it is a second file that has to be explained and does nothing. It appears the
moment a choice exists, and once it exists it is kept current rather than
orphaned. Delete it and a single-clip bake will not bring it back.

Building it never depends on having seen the other clips: rows for cameras
baked on an earlier run are read back out of those clips' own metadata, which
is where the row came from in the first place. So baking one camera at a time
and baking All Cameras produce the same index. It is also **merged, not
overwritten** — baking one camera updates only that row, and rows whose file no
longer exists are dropped. `npx ivg manifest <folder>` rebuilds one from
scratch.

The manifest exists because a filename cannot say which way a camera moves.
`tower.bts_cam.svg` could travel towards the cabinet or away from it, and a
consumer that guesses gets a transition running backwards — which looks like
broken artwork rather than broken wiring. Every bake measures it and records
`{"scale": 0.42, "direction": "out"}`, so the answer ships with the file.

Label what a move connects with custom properties on the **camera** and they
travel into the manifest too, turning it into an exact lookup:

| Property | Effect |
|---|---|
| `vecbake_from` | state this move starts at, e.g. `full` |
| `vecbake_to` | state it ends at, e.g. `bts` |

**Forward + Reverse** writes `<name>.rev.json` / `<name>.rev.lottie.json`, a
pure transform of the baked keyframes rather than a second bake. It does
nothing for the animated SVG, which needs no reverse file: the player runs the
timeline backwards, so shipping one would double the asset to encode something
already in it. Turn it on only if you are consuming the raw `json` or `lottie`
outputs in a player that cannot reverse.

### Curves

A baked circle is a polygon, and no tolerance setting fixes that — the points
*are* the shape. **Curves** draws through them instead of between them, so a
10-sided cylinder reads as round.

It costs no extra data. Rather than fitting and storing Bézier control points,
the bake stores one static bit per vertex — *does the path bend here* — and the
control points are derived at draw time from the neighbours, using uniform
Catmull-Rom. A corner carries a zero tangent, which collapses its cubic to
exactly the straight segment it replaced, so corners and curves mix inside one
path with no special case at the join.

| Mode | Decides from |
|---|---|
| **Off** | nothing — straight segments, as baked |
| **Smooth** | every vertex that is not a hard corner |
| **Smooth by Angle** | the **2D** turn angle on screen, against **Curve Angle** |
| **Blender Shading** | the object's own shading — see below |

**No mode ever smooths a hard corner** (a turn over 80°), because a curve
through one cannot stay inside the shape: it has to pass *through* the corner,
so between two corners it bows outward. Smoothing the four 90° corners of a
cylinder's side quad turns the flat face into a barrel that overflows its own
vertical edges. No tangent scaling avoids this — only not smoothing the corner
does. The limit keeps quads (90°) and triangles (120°) flat while still
rounding a cylinder as coarse as five sides (72°). Faceted hard-surface shapes
that fall under it — a hex nut at 60° — are what **Blender Shading** is for:
they are normally shade-flat, and it leaves them alone.

**Blender Shading** follows what the object is already set to: shade-smooth
curves throughout, shade-flat stays straight, and a *Smooth by Angle* modifier
contributes its angle, applied to the **3D** turn along the path. Note that
this deliberately is *not* the modifier's own meaning — it compares faces, we
compare the drawn line — because a cylinder's rim is one smooth circle even
though every edge on it is a hard crease.

### Following

An edge belongs to more than one element, and only one of them may be in a
position to curve it. A cylinder's cap curves its rim, because going round the
rim is a run of shallow turns. The side quads meeting that same rim cannot —
their own corners turn 90°, and see above. Left there, the two draw one edge two
different ways and the shape splits along it: the cap overhangs the sides at the
top, and at the bottom the rim sweeps out past the fills with background showing
between them. On the demo cylinder that was **610 pixels of enclosed background**
— holes inside the silhouette.

No per-element rule can close them, and that is not an oversight. Tangents come
from each element's own points, which is exactly what keeps the format free of
stored control points; the side quad does not contain the rim's next vertex, so
nothing inside it says which way its top edge should bow.

So the flat one **follows**. `follow.py` splits it along the curve its neighbour
draws, adding as few points as it takes to stay within `dissolve_px` of that
curve in every frame — the dissolve pass's bargain run backwards. Whoever curves
the edge hardest owns it, ties broken by key so the choice is the same in every
frame and every re-bake.

One curve per **rim**, though, not one per edge — an element that loses anywhere
is made to follow everywhere. Deciding edge by edge looks equivalent and is not.
A chain runs out of neighbours at its last vertex, so its tangent there is forced
to zero and its final segment straightens; the faces beside it, having measured
that same segment against a closed ring that keeps curving, track the fuller
curve. The two part company by about 3px exactly where the eye is on them, at the
silhouette — the fill spilling past the line on one side of the cylinder and
falling short of it on the other. Demoting the loser over its whole length leaves
nobody holding a second opinion.

The split count comes from the exact bow, not the usual flatness bound — with one
tangent zero the bound reads 70% high, and the count is its square root, so that
is points bought for nothing in every frame. The n² rule gets within one piece
and a direct check settles it. Enclosed background on the demo cylinder: **zero**.
Nothing downstream needs to know any of this happened; a follower is just an
element with more points, so the player, both SVG paths, Lottie and anything else
that opens an SVG agree on the shape without implementing a thing.

It is not free. On the demo cylinder — where almost every face abuts a curve, so
it is close to the worst case — points went 58 → 95 and the clip 2,900 → 3,447
bytes. Raise `Dissolve` to trade accuracy back for size; the budget it spends is
the same one it spends removing vertices elsewhere.

### Silhouettes

Following closes the gaps but does not, on its own, put the outline in the right
place. Curving an outline *moves* it: the rim is a polygon inscribed in the real
circle, so the curve drawn through it reaches past every vertex — 8.3px past, on
the demo cylinder, at a point lying *between* two vertices.

A silhouette is a mesh edge, and a mesh edge runs through a vertex. So the side
of the cylinder stopped 8.3px short of the shape the curve had just drawn: the
rim bulged into open space and the side stepped back in underneath it. A bump,
where a cylinder should have a straight edge running between the two rims'
furthest points.

`silhouette.py` moves the corner out to meet it. For each edge separating a drawn
face from a hidden one, it walks the rim curve either side of the corner and
finds the outermost point — outermost meaning perpendicular to the silhouette
edge, away from the face.

**Either side**, and that is the part that matters. The silhouette of the
reconstructed surface falls anywhere within half a facet of the mesh edge, so as
the camera turns it crosses the vertex and keeps going. Searching only outwards
would pin the corner to the vertex for half of every facet and then jump, which
reads as a stutter running round the object. Searching both segments slides it
through instead: past the vertex the face is extended, short of it the face is
trimmed, and at the vertex the two agree. Swept through a full facet, the
outermost point of the drawing moves 0.013px per frame on average and 0.145px at
worst — no step to see.

The face is not the only thing that moves. The line drawn along the silhouette
edge goes with it, or it ends up sitting 8px inside the fill it is meant to
outline, and so does the rim arc that stops there. And the curve consulted is not
always one that gets *drawn*: at the bottom of a cylinder the rim arc ends at the
silhouette, so it cannot say where the rim goes next — the culled bottom cap can,
being a closed ring sampled every frame whether it is painted or not.

Two details decide whether the result is watertight or a mess, and both are about
what happens to points that are already there.

The split points `follow.py` put on the edge running into a corner have to stop
where the corner now does. Ordering them by distance *outward* does not work: the
curve bulges, so a point can be closer to the chord than the corner is and still
lie beyond it. They are ordered along the edge instead. Get this wrong and the
outline runs out to the corner, doubles back behind it and comes out again — a
spur, which a fill rule renders as either a spike or a notch depending on which
way the loop wound.

And the old vertex is only worth keeping for something that follows the rim
*through* it — a face, or the rim arc. The line drawn along the silhouette never
goes near it: give that one both points and it overshoots to the old vertex and
hooks back, poking out past the fill it is supposed to outline.

Where the rim and the silhouette meet, the boundary is genuinely cusped: they are
tangent there, so the side face comes to a point. That is the shape, not a defect
— a 172° turn in the outline is the correct answer.

This is a **reconstruction, not a measurement**, and it is the one place VecBake
deliberately parts company with what Blender renders. Blender draws the mesh, so
its silhouette is faceted; this draws the silhouette of the smooth shape the mesh
stands for — the same shape `curves.py` is drawing the outline of. With `Curves`
off it does not run at all.

A chained stroke that comes back to its own start is kept as a **ring**, and
that matters here more than it sounds. An open path has two ends, and an end is
a vertex with nothing on one side of it, so no tangent can be measured and the
curve has to straighten — meaning a rim drawn as an arc gets a chord across the
join no matter how smooth the mask says it is. Rings have no ends. Closing them
also reclaims the edge that used to be stranded as a lone straight segment
across the same join. In `.ivg` a ring carries `cl: 1`; fills are closed by
definition and say nothing.

The mask is decided **once for the whole bake, never per frame.** A vertex that
could be a corner in one frame and smooth in the next would visibly pop as the
camera moved, so the 2D test asks whether a vertex is sharp in *any* frame, not
in the current one — a real corner that happens to project straight in one view
stays a corner. This is why the 2D mode still needs the whole animation before
it can answer, and why it is not simply the 3D test done later.

Three implementations have to agree on the tangent formula or a clip stops
matching its own resting states: `curves.py` (classification), `emit_svg.py`
(the `<path d>` in the states) and `player.js` (`_trace`). Change one, change
all three — and `follow.py` is a fourth, since it has to reproduce the very
curve the owner will draw in order to put points on it. Lottie is a fifth, and
falls out almost free: its per-vertex `i`/`o` handles *are* the tangent over
three, stated relative to the vertex, so it draws the same curve rather than
approximating it. `scripts/test_chain_trace.py` guards the ring invariants they
depend on, and `scripts/test_follow.py` checks that a follower's points land on
the owner's curve, the right way round.

### Stroke colour

**Stroke From** is either `Fixed` (one colour, the **Stroke Colour** swatch) or
`Material` — a darkened version of the colour of the face each edge borders, so
outlines tint with their surface. Edges with no adjacent face fall back to the
fixed colour. Override per object with a `vecbake_stroke` custom property.

### Fill colour

Blender 5 keeps `use_nodes` on for every material, so trusting the Principled
base colour flattens any scene authored with viewport display colours to a
uniform grey. **Auto** takes the shader colour when someone has actually set it
and falls back to the viewport colour; **Shader** and **Viewport** force either.

Colours are converted from **scene-linear to sRGB** on the way out. Blender
stores every COLOR property linear and only converts for display, so the float
behind a swatch reading `#4c8cb8` is `(0.072, 0.262, 0.479)`. Writing those
numbers straight into a hex triple gives `#12437a` — every colour noticeably
darker than the one that was picked, with or without shading. What you see in
the swatch is now what lands in the file.

This applies to the add-on's own **Stroke Colour** and **Fill** pickers too,
since they are ordinary linear COLOR properties. A value chosen before this fix
will now export lighter — matching its swatch, which it did not before.

### Fill transparency

**Material Alpha** takes opacity from the material along with its colour, so a
translucent surface bakes translucent. It reads from wherever the *colour* came
from, never mixed: the Principled **Alpha socket** beside a shader colour, or
`diffuse_color`'s fourth channel beside a viewport colour. Taking one from each
would match neither authoring style.

Note it is the Alpha *socket*, not the fourth channel of Base Color. That one
exists because the colour picker carries an alpha around; the shader ignores it.
Reading it would make materials look transparent in the bake and solid in a
render.

Opacity rides the per-element opacity track that already existed, so it costs no
new format field, and the painter's order already draws far to near — which is
exactly what blending translucent fills needs. A material at alpha 0 drops out of
the file entirely, the same as any element nothing ever sees.

**Translucent fills skip the seam fix.** Canvas applies its alpha per draw call,
so stroking a fill with its own colour — which is how the hairline between
abutting polygons is normally closed — blends twice along the boundary and rings
every face in a darker outline. SVG composites the element before applying
opacity and would not, but it follows anyway, because the two renderers agreeing
matters more than a hairline you can see through a translucent surface. The cost
is measurable and small: on the demo cylinder, cross-renderer disagreement goes
from 0.37% of inked pixels to 0.48%, all of it single-pixel antialiasing along
shared edges.

### Seeing through

Nothing culls geometry behind *other* geometry. The painter's order draws far to
near and simply paints over, which is what lets a translucent fill reveal what is
behind it at no cost. What an object hides is **itself**: its far side faces away,
so the backface cull drops those faces and hidden-line removal drops their edges.

**See Through** stops a translucent material doing that to itself. Its back faces
survive the cull and its edges survive hidden-line removal — for that material
only, so solid geometry elsewhere stays solid. On the demo cylinder it takes the
drawing from 6 of 12 faces and 21 of 30 edges to all of both.

Both of those now answer to one mask. Faces and the edges around them used to be
decided separately — faces from `Backface Cull`, edges from face orientation
alone, which never consulted the setting. Turning the cull off therefore revealed
the far faces while leaving every edge whose faces all pointed away culled. They
are derived from the same "is this face drawn" mask now, so the two agree by
construction rather than by coincidence.

*Silhouette* still means what it always did — where the surface turns away — and
is still built from facing alone. Whether the far side happens to be drawn is a
different question.

**Chains do not merge across the surface.** A chain gets one depth, so an
element running from the near side of an object round to the far side cannot be
ordered against anything: it paints at its nearest member's depth, on top of the
very faces that should be covering half of it. On a see-through cylinder that put
the whole bottom rim in front of the body — a black line at luminance 20 laid over
everything, where it should read as 72–111 seen through two translucent walls.

Backface culling hid the problem rather than solving it: the far half was never
drawn, so no chain ever spanned a silhouette. Facing is now recorded per frame and
compared whole, exactly the way visibility already was, so the merge only splits
where the two genuinely differ. On the demo cylinder the bottom rim becomes a
front arc and a back arc — two elements instead of one — while the top rim stays
whole, because its far half borders the cap, which faces the camera, so nothing
covers it. Opaque scenes are unaffected: a culled edge is invisible in every
frame, so its visibility signature already kept it apart.

### How the animation is encoded

This is the payload inside the animated SVG's `<metadata>` block, and the whole
of the optional `.json` output.

Keyframes are per **element**, per **track** — never per object. Each element
carries up to four independent channels (geometry, opacity, depth, shade), so a
scene of 1,600 elements has ~5,500 tracks. The headline keyframe number is
tracks × keys-per-track, and keys-per-track is small.

Three encodings, chosen per track by whichever serialises shorter:

| | |
|---|---|
| `{"c": …}` | constant — one value, no timeline |
| `{"q": …, "d": […]}` | **dense**: keyed against the document's uniform sample grid, so no times are stored at all |
| `{"t": […], "q": …, "d": […]}` | sparse: explicit times |

`d` is always **quantised integer deltas along time**, and this is where
correlated motion pays off. Elements travelling together produce near-identical
delta streams, and small integers are both shorter to write and highly
repetitive, so the compressor on the wire matches them *across* elements.
Storing absolute coordinates throws that structure away. Decoding is a running
sum per component, accumulated in integers and divided once — do it in floats
and error compounds along the timeline.

Dense tracks also make lookup cheaper: the segment index is arithmetic rather
than a binary search.

Depth is stored as an integer **rank**, not a distance. The player only ever
sorts these values, so the absolute distance is information nobody reads — and
it is expensive information, six quantised digits changing every frame. Ranks
are small integers, and they are distinct by construction, which also removes
the tie problem absolute depths had.

Coordinate precision is derived from the tolerance (**Auto Precision**). Keeping
0.01 px precision under a 4 px tolerance stores digits nobody can see; the
quantum is pinned at an eighth of the tolerance so rounding never contributes
meaningfully to the error budget. This is what makes raising the tolerance
actually pay — before it, going 0.35 → 1.0 px saved 4%.

Measured on a real 1,654-element scene, 12 frames — the animation payload only,
before the resting states are added:

| | raw | gzipped |
|---|---|---|
| absolute values, explicit times | 1,341 KB | 314 KB |
| quantised deltas, dense timeline | 875 KB | 234 KB |
| **+ rank depth, auto precision** | **837 KB** | **222 KB** |
| the same at tolerance 2 px | 717 KB | 173 KB |
| the same at tolerance 4 px | 667 KB | 146 KB |

The depth channel alone went from 250 KB to 84 KB. Wrapped as an animated SVG
with both resting states embedded, the default row becomes 1,198 KB raw /
259 KB gzipped.

None of these changed frame cost meaningfully; encoding affects size, and the
decode happens once at load. Frame cost is set by element count — see
[web/README.md](web/README.md#performance).

Verified against the SVG emitter, which reads the IR directly and shares none
of this path: **max coordinate error 0.0001 px**.

### What tolerance actually costs

Pixel diff against the 0.35 px bake, same scene and frame:

| tolerance | pixels differing strongly | mean delta |
|---|---|---|
| 1 px | 1.6% | 1.5 / 255 |
| 2 px | 3.6% | 3.3 / 255 |
| 4 px | 5.6% | 6.7 / 255 |
| 8 px | 13.1% | 20.7 / 255 |

Read those with care: pixel diff **overstates** perceptual damage on line art,
because displacing a 1 px black line on white by 2 px scores a huge per-pixel
delta while looking fine. Inspected at 3× zoom, output holds up through 4 px;
at 8 px lines are visibly displaced. 2–4 px is the useful range.

### Precision

`Tolerance` is the maximum error of the interpolated playback **in pixels** — a
direct bound on visual error, which is what makes linear interpolation
defensible here.

**Tolerance only helps when you sample more densely than the motion needs.** On
a fast camera move at 12 frames, consecutive frames already differ by more than
a pixel, so nothing is redundant and geometry keeps ~11.6 of a possible 12 keys;
raising tolerance 0.35 → 1.0 px saved 4%. Raising `Step` cuts the data far more
effectively — but note the difference in kind: tolerance drops keys *having
measured* that the error stays bounded, while `Step` drops frames without ever
looking at them, so its error is real but unmeasured. If a larger step still
looks right, your actual quality bar is looser than the tolerance you set, and
raising both is the honest configuration.

Once **Auto Precision** is on, raising the tolerance also drops stored digits,
which is what makes the knob bite. Same 1,654-element scene, animation payload
gzipped:

| Tolerance | stored decimals | gzipped |
|---|---|---|
| 0.35 (default) | 2 | 222 KB |
| 1 px | 1 | 176 KB |
| 2 px | 1 | 173 KB |
| 4 px | 1 | 146 KB |
| 8 px | 0 | 84 KB |

---

## Known limits

- **Stroke-vs-stroke order is unconstrained.** Both are hairlines of the same
  colour, so it isn't visible — but it is not resolved.
- **Geometry that touches or interpenetrates** produces contradictory
  constraints and therefore cycles. Three things keep that survivable:
  "coplanar" is judged relative to the scene's depth scale rather than an
  absolute epsilon; only edges lying *inside a strongly connected component* are
  ever broken, so constraints that are merely downstream of a deadlock survive
  it; and among those, the weakest go first — the ones resting on the smallest
  depth differences, i.e. the least evidence.

  It is still the one case with no correct answer. On a model whose dishes sit
  flush against the mast, surfaces separated by 0.002 units on a 30-unit scene
  are genuinely ambiguous, and the cheapest real fix is in the model: give
  mounted parts a little clearance. `validate_bake.py` reports `cycle_breaks`
  and `constraints_discarded`; a large number in either means the geometry, not
  the exporter, is deciding. On the reference tower the residue is 95 pairs,
  every one of them under 0.4% of the scene's depth span.
- **Lottie layer order is static.** Depth becomes a fixed layer stack, so
  occlusion cannot flip mid-animation there. Invisible for a modest camera move,
  not for a full orbit around a lattice. This is why the native player exists and
  Lottie is the preview path.
- Interpolation between keyframes is **linear**, bounded by the pixel tolerance
  rather than curve-fitted. Cheaper at runtime and the error bound is explicit.
- **SVG optimisers strip `<metadata>`.** SVGO removes it by default, which
  deletes the animation from an animated SVG and leaves a still image. Exclude
  these files or set `removeMetadata: false`.
- **`<img>` shows the resting state, not the animation** — images do not run
  scripts. That is the intended fallback, not a bug.

## Layout

```
addon/vecbake/            the Blender add-on
  ir.py                   format-neutral IR -- every emitter consumes one
  bake.py                 the only module that simulates 3D
  order.py                offline paint-order resolution
  chain.py                edge chaining and vertex dissolve
  reduce.py               keyframe reduction
  emit_ivg.py             animated SVG   <- the primary output
  emit_json.py            bare track data
  emit_svg.py             static resting states
  emit_lottie.py          Lottie, for preview in standard tools

web/                      the npm package -- see web/README.md
  src/player.js           Canvas 2D player, no dependencies
  src/element.js          <ivg-player> custom element
  src/react.js            React binding
  bin/ivg.js              CLI: info / strip / states
  examples/               vanilla.html, bench.html

scripts/dev.py            reload() bake() check() install()
                          scene(confirm=True) rebuilds the test tower --
                          DESTRUCTIVE, it wipes every object in the scene
scripts/validate_bake.py  occlusion + reduction audit
scripts/build_test_scene.py
scripts/devserver.py      static server + POST /save/<name> snapshot endpoint
player/demo.html          older harness for the bare .json track format
```

## Licence

**GPL-3.0-or-later** — see [addon/vecbake/LICENSE](addon/vecbake/LICENSE). The
add-on links against Blender's Python API, so this is not a choice: any Blender
add-on is a derivative work and must be GPL. A copy you receive is yours to
modify and redistribute.

The web runtime in [web/](web/) is separate code under **AGPL-3.0-or-later**,
with a commercial licence available for closed-source products — see
[web/COMMERCIAL.md](web/COMMERCIAL.md).

**Files you bake are yours.** Neither licence reaches the output.
