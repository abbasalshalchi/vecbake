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
| Occlusion violations | **0** across every frame |

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
| `json` | `tower.json` | bare track data, no artwork or states |
| `svg` | `tower.start.svg`, `tower.end.svg` | resting states as loose files |
| `lottie` | `tower.lottie.json` | for standard Lottie tools |

**Forward + Reverse** only affects `json` and `lottie`. The animated SVG needs
no reverse file, since the player runs the timeline backwards for free.

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

Two further traps, both fixed:

- **Don't skip pairs whose depth ranges are disjoint.** It looks safe — seed
  depth already orders them — but the topological sort reorders *globally*, so
  an element held back by its own constraints gets carried straight past an
  unconstrained neighbour. This is what let the ground paint over the mast legs.
- **Emitted depth must be injective.** Seeds are not distinct (an edge inherits
  the depth of the face it borders, so every edge around a face ties). Indexing
  sorted seeds collapses distinct ranks onto one value and hands the tie back to
  the player to break arbitrarily.

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

**All Cameras** bakes once per camera. Either way you also get a
`tower.index.json` manifest listing every clip with its duration, size and
measured camera motion. The manifest is **merged, not overwritten**: baking one
camera updates only that row, and rows whose file no longer exists are dropped.

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
  constraints and therefore cycles. Two things keep that survivable: "coplanar"
  is judged relative to the scene's depth scale rather than an absolute epsilon,
  and breaking a cycle gives up the *weakest* constraints — those resting on the
  smallest depth differences, i.e. the least evidence — instead of discarding
  every constraint on some element.

  It is still the one case with no correct answer. On a model whose dishes sit
  flush against the mast, surfaces separated by 0.002 units on a 30-unit scene
  are genuinely ambiguous, and the cheapest real fix is in the model: give
  mounted parts a little clearance. `validate_bake.py` reports `cycle_breaks`,
  and a large number there means the geometry, not the exporter, is deciding.
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
