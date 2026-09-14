# Bug ledger

Every bug found in VecBake, the Lerpa player, the preview and the website from
1 August to 13 September 2026: what it looked like, what actually caused it, how
it was fixed, and what now stops it coming back.

The format of each entry:

- **Seen** — the date, and the symptom as it was first reported. Most were
  reported by eye, and the symptom rarely named the cause.
- **Cause** — what was really wrong, as established by measurement.
- **Fix** — the change, with the commit where there is one (`vecbake` unless the
  repo is named). The first week predates descriptive commit messages, so those
  entries cite the date only.
- **Guard** — the test or check that pins it, where one exists.

Entries in [§11](#11-reported-and-investigated-not-bugs) were reported as bugs and
turned out to be the model, the scene or expected behaviour. They are here
because each one cost time to rule out, and several will be reported again.

Experiments — changes built to test an idea, whether kept or not — are in
[experiments.md](experiments.md). How to measure without being fooled is in
[measurement.md](measurement.md).

---

## Contents

1. [Colour and materials](#1-colour-and-materials)
2. [Paint order and occlusion](#2-paint-order-and-occlusion)
3. [Chaining and thinning](#3-chaining-and-thinning)
4. [Curves and following](#4-curves-and-following)
5. [Merged fills and outlines](#5-merged-fills-and-outlines)
6. [Visibility, culling and track encoding](#6-visibility-culling-and-track-encoding)
7. [Welds and seams](#7-welds-and-seams)
8. [The Lerpa player](#8-the-lerpa-player)
9. [Preview, audit and tests](#9-preview-audit-and-tests)
10. [Website, hero and demos](#10-website-hero-and-demos)
11. [Reported and investigated: not bugs](#11-reported-and-investigated-not-bugs)
12. [Open](#12-open)

---

## 1. Colour and materials

### Fills and strokes darker than the shader colour
- **Seen:** 2026-08-02 — colours always darker than the shader's, even with
  Shading unticked. Only pure black and white were right.
- **Cause:** Blender stores every `COLOR` property scene-linear and converts to
  sRGB only for display. The linear floats were written straight into hex:
  `#4c8cb8` came out `#12437a`, `#808080` came out `#373737`.
- **Fix:** convert linear → sRGB on the way into the IR, so all three emitters
  inherit it. The add-on's own Stroke/Fill pickers are linear too, so their
  defaults were re-expressed in linear to keep emitting `#121217` / `#c7ccd6`.

### Every material flattened to the same grey
- **Seen:** scripting scenes on Blender 5.1.
- **Cause:** materials are always node-based — `mat.use_nodes = False` reads back
  `True` — so a scene coloured through viewport display colours leaves the
  Principled BSDF at its default `(0.8, 0.8, 0.8)`.
- **Fix:** fall back to `mat.diffuse_color` whenever the shader colour is still
  at that default; set both when building test scenes.

### `setGroupColor` read `hsl()` as `rgb()` (lerpa)
- **Seen:** 2026-08-10.
- **Cause:** it scraped the first three numbers from any CSS string, so
  `hsl(210 70% 60%)` became `rgb(210, 70, 60)` and named colours became black.
  The 8-digit hex path separately shifted one integer past the sign bit.
- **Fix:** a real parser for hex, `rgb[a]()` and `hsl[a]()`; anything else is
  painted to a pixel and read back, which is what makes `oklch()`, `lab()` and
  `color-mix()` work. `lerpa@a62cc50`.

---

## 2. Paint order and occlusion

### Chained lines painted in front of what hides them
- **Seen:** 2026-08-03 — chained lines go in front of objects the original edges
  were behind, even at 0.11 px dissolve.
- **Cause:** a merged chain took `np.min` of its members' depths — its nearest
  point — so a mast leg chained past a dish painted over the dish along its whole
  length.
- **Fix:** cut a chain only where, against a fill it actually overlaps, the depth
  comparison **changes sign** along it (the straddle rule). A first attempt cut
  wherever any fill's depth fell between members: 48 cuts preventing 0
  violations. The straddle rule made 2 cuts, on a fence rail that really does run
  behind and in front.

### Mast legs in front of the antenna dishes
- **Seen:** 2026-08-03, after the fix above.
- **Cause:** two faults. A polyline was sampled a fixed number of times along its
  whole length — a 666 px leg got 8 samples, 83 px apart, against a 41 px dish —
  so 2,204 of 2,595 overlapping pairs got no constraint and fell back to seed
  order. `validate_bake.py` used the same sampler and was blind the same way.
  Then, once the overlaps were visible, flush-mounted dishes (0.002–0.03 units
  from the mast on a ~30-unit scene) produced contradictory constraints, and the
  cycle breaker discarded *every* incoming constraint on a node.
- **Fix:** sample by pixel density, only on segments that reach the fill;
  "coplanar" judged relative to the scene's depth span; break a cycle by giving up
  the weakest constraints in batches. Cycle breaks 149 → 1.

### The cycle breaker threw away 64% of the evidence
- **Seen:** 2026-08-03.
- **Cause:** three faults. "Drop the weakest tenth" was written as a weight
  *threshold*, so every edge tied at the cutoff went too (15,736 of 24,456 in one
  pass). It dropped edges feeding any *blocked* node rather than only edges inside
  a strongly connected component. And edges created without a strength defaulted
  to 0, so disjoint-depth-range constraints — the most certain in the graph — were
  spent first.
- **Fix:** bound by count; only edges inside an SCC; disjoint-range edges weighted.

### Lines thinned, or eaten, by the faces beside them
- **Seen:** 2026-08-03 — lines hidden or thinned by varying degrees by adjacent
  faces, sometimes covered completely.
- **Cause:** a stroke has width and is centred on its edge, so a face painted
  after it covers the inner half. The face won because its depth came from a plane
  fitted to a non-planar n-gon, which misses its own boundary, and because
  boundary samples are a coin toss on an inside test.
- **Fix:** an edge lies on its faces — that is topology, not measurement. The baker
  records `on_faces`, and `order.resolve` asserts "face behind edge", retracting
  any opposing measured edge first so the pair is not a 2-cycle.
- **Guard:** `validate_bake.py`'s `lines_under_own_face` must be 0;
  `test_order_surface.py`.

### Butterfly wing tip drawn over the forewing
- **Seen:** 2026-08-11 — hind tip overlapping fore base mid-animation, both sides,
  with a clear gap between them in Blender.
- **Cause:** fill-vs-fill overlap was sampled on a fixed 4×4 grid over the
  *bounding-box* intersection. The wings overlap by under 1% of their area; the
  grid found one point at best and none on four frames of seventeen, which fell
  back to centroid order — inverted on exactly frames 1, 2, 16 and 17.
- **Fix:** grid scaled to pixels (≤ 576 samples), with an outline-walking fallback
  for slivers. `069d762`.

### City scene fills sorted wrong
- **Seen:** 2026-08-16.
- **Cause:** my advice. I said to turn Resolve Paint Order off because disjoint
  convex boxes sort correctly on centroid depth. Not when side faces span 900 px:
  on ray-cast ground truth centroid order was 114/120 against 120/120, and every
  disagreement went to Resolve Paint Order.
- **Fix:** turn it back on. Keep Cull Hidden on, which is what makes it affordable.

### Culled frames mixing paint ranks and camera distances
- **Seen:** 2026-08-19 — "shafts inside the hull flip into view", things turning
  translucent in a scene with no transparency. Turning Backface Cull off made both
  go away.
- **Cause:** `resolve` ranks only what is drawn in a frame; every culled frame kept
  the raw camera distance. Interpolating from rank 208 to distance ~10 dove hidden
  geometry from the back to the front across one frame, at partial opacity.
- **Fix:** fill culled frames from the nearest ranked frame, in both directions.
  Elements mixing scales 291 → 0. `2687985`.

### A stray line across the golden record
- **Seen:** 2026-08-20, 0.70–1.50 s.
- **Cause:** a real three-element cycle: `edge → disk` (measured, gap 0.046),
  `disk → face` (measured, 0.778), `face → edge` (asserted). With the assertion
  protected, the breaker spends the edge-under-disk constraint.
- **Fix:** none that holds. Spending the assertion first fixed the line
  (`9fbb973`) and was reverted (`38fb353`) because in motion the edge's width
  alternated — 18 edges of 196. The trade is pinned deliberately.
- **Guard:** `test_order_cycle.py` pins the stray line as the known trade.

### A bus edge painted over an edge-on shaft
- **Seen:** 2026-08-20.
- **Cause:** a line against a fill was found by walking the line at 4 px steps.
  `feed-horn/f0` is 3.8 px wide; 32 samples all missed it. 163 of 7,293
  stroke/fill pairs across 14 frames had no constraint at all.
- **Fix:** intersect the segment with the polygon exactly. `68585a4`.

### The closing segment of a closed chain was never ordered
- **Seen:** 2026-08-21 — a bus rim painted over the feed horn at 0.34–0.38 s.
- **Cause:** a chain emitted with `cl:1` is stroked with `closePath()`, one segment
  more than its point list. Every walk used `range(len(pts) - 1)`. The missing
  126 px wrap ran straight across a shaft a unit in front of it.
  `check_occlusion.py` had the same blind spot, so three audits called the frame
  clean.
- **Fix:** walk the wrap in sampling, clipping and `_depths_at`. `46d0446`.

### A merged part ordered by a plane fitted to its rim
- **Seen:** 2026-08-28, found by the ray-cast audit.
- **Cause:** the surface-probe producer was deleted as collateral in `2f3e20a`
  while reverting unrelated work; the consumer stayed as dead code. 259 of 304
  objects were ordered by an affine fit through their silhouette ring — the rim of
  a bowl, asked about its middle. Worse, `dmin`/`dmax` came off the rim, and
  `resolve` settles non-overlapping ranges without measuring.
- **Fix:** restore the probe; its thinning keeps the nearest and furthest vertices
  whatever else it drops. `6274add`.

### Merging silently voided 88% of edge-on-face assertions
- **Seen:** 2026-08-28.
- **Cause:** `edge_face_keys` returns `part/f12`, and a merged object emits no `/f`
  elements. A key that matches nothing reads exactly like an edge bordering
  nothing: 11,319 matched against 81,662 unmatched.
- **Fix:** a merged object asserts against `part/m`. Matched 49,987. Ray-cast wrong
  pixels 4.52% → 2.24%. `6274add`.

### Multi-ring fills sorted against nothing
- **Seen:** 2026-09-12, audit: "element not in this frame's ordering pass" on
  `soyuz_015/m`, `soyuz_001/m`.
- **Cause:** the multi-piece sampler never called `collect.append`. Invisible to any
  check of one element — the shape was right, only its order was not.
- **Fix:** hand points, depths and probe to the ordering pass. Errors 191 → 176.
  `cec7935`.

### Fittings painted under the body they are bolted to
- **Seen:** 2026-09-12, Apollo–Soyuz hero: thrusters `apollo_018`, `apollo_002`,
  `apollo_013`/`014`, `apollo_024` covered by the service module `apollo_003` in some
  frames.
- **Cause:** a merged part's depth at a point was its nearest front-facing *vertex*. On
  a low-poly body that is a corner of a long face. Frame 30, at the centre of what
  `apollo_018` and `apollo_003` share: thruster 34.98, body's true surface 35.24,
  body's nearest vertex **34.25** — so the body ranked 0.73 in front of a part bolted
  on top of it, and the thruster's fill and all 41 edges went under. Frames 0 and 15
  happened to find a corner behind the thruster.
- **Fix:** read depth off the front-facing triangle under the point. Screening audit
  446 → 289 wrong pixels. `20c2f71`. Guard: `scripts/test_surface_depth.py` (the
  bolt-on-body case fails on the old read).
- **Misdiagnosed on the way:** as a cycle-breaking problem (agreement weighting,
  measured worse), and as needing a containment special case. Detail in
  [audits.md](audits.md).

### Welded neighbour ~600 phantom unmatched assertions per bake
- **Cause:** a weld names its neighbour without knowing whether it merged, so it
  offered both `name/m` and `name/f<n>` and the absent one counted as missing.
- **Fix:** `order.resolve` takes whichever spelling exists.

---

## 3. Chaining and thinning

### Chaining stopped at every junction
- **Seen:** 2026-08-01 — reported with the fix: an X of four edges is two
  crossing lines in SVG.
- **Fix:** pair incident edges at a junction by 3D angular continuity, decided once
  so identity holds. 826 → 560 strokes; with Sharp Edges 30°, 920 → 417. Max Turn
  exposed because a longer chain carries one depth.

### A closed rim chained one edge short
- **Seen:** 2026-08-07 — the right tip of a cylinder's top oval stays sharp under
  Blender Shading.
- **Cause:** the chainer arrived back at its start and "left the loop open", so the
  unclaimed edge came back as a straight chord, and both path ends landed at the
  tip where a tangent cannot be measured. A second fault: growing a chain backwards
  prepended to `path` but appended to `members`.
- **Fix:** close the loop; keep the two lists aligned.
- **Guard:** `test_chain_trace.py`.

### Thinning never reached fills
- **Seen:** 2026-08-10 — "did you make chain edges only work for strokes?!"
- **Cause:** `dissolve_px` lived inside the chaining pass, which only iterates
  strokes.
- **Fix:** `thin_fills` runs beside chaining, before ordering, on loose-edge
  outlines only. Reference butterfly 50 elements / 456 points → 11 / 191.
  `402b156`.

---

## 4. Curves and following

### Cylinder side fills spilling on one side and short on the other
- **Seen:** 2026-08-08, reported twice.
- **Cause:** three faults. Follow-split points past a corner were ordered by
  distance outward instead of along the edge, leaving a spur. The silhouette line
  kept an old vertex and hooked back 2.3 px. Then the fill and the line tracked
  different curves — the rim arc's endpoint tangent against a culled bottom cap
  that kept curving, up to 3.22 px apart — and the per-frame curve cache was keyed
  by owner instead of by edge, piling points onto the first edge's curve.
- **Fix:** one curve per rim, with anything that loses anywhere on it demoted to
  follow it; cache keyed by edge. 3.22 px → 0.000 px.
- **Guard:** `test_follow.py`.

### Lines leaping across the model with Curves on
- **Seen:** 2026-08-19 — lines jumping between parts.
- **Cause:** mesh vertex indices restart at zero per object. `follow._movers`
  matched raw indices across the whole scene, handing parts each other's corners:
  x = 1017 pulled to −115.
- **Fix:** scope the match to the object. Strays outside their own box 70 → 0.
  `78a1b8e`.
- **Guard:** `test_silhouette_objects.py` (two identical cylinders far apart).

---

## 5. Merged fills and outlines

Merging went through several designs between 10 and 26 August; the dead ends are
in [experiments.md](experiments.md#merged-outlines). These are the bugs.

### Unstable merged outlines drawn anyway
- **Cause:** `merge.py` set `stable = False` when slot order broke, and nothing read
  it except a stats counter. The docstring and the commit that added it both
  claimed a fallback that did not exist.
- **Fix:** a repeatable sampling pass re-samples unstable objects unmerged.
  `c7c362e`.

### A closed solid vanished with Backface Cull off
- **Cause:** the silhouette test wanted edges with exactly one *drawn* face; with the
  cull off every edge has two, no ring is ever found, and the object silently never
  appeared — the butterfly's transition baked 10 elements instead of 92.
- **Fix:** both failure kinds feed the per-face retry (`8a78377`); then the
  silhouette uses facing, not what is drawn (`937e457`).

### A merged sphere spent 1,120 elements to draw a circle
- **Cause:** slots were bound to mesh vertices, and a quarter turn changes which
  vertices are on the silhouette and in what cyclic order. Five of seven lab
  primitives were rejected on order alone.
- **Fix:** a slot is a position around the loop, kept monotone, with the count set
  by error. Lab scene 2,351 → 399 elements. `e6be3af`, `a47c171`.

### Merged outlines unoutlined at rest
- **Cause:** `emit_svg` did not know fills could carry `w`/`oc`, so resting states
  disagreed with the animation and the line blinked at each end.
- **Fix:** `29dcb07`.

### A stationary circle churning 32% of its points a frame
- **Cause:** `choose` re-decided every frame among near-ties (0.33 px spread).
- **Fix:** carry the previous frame's positions. Sphere keys 24 → 10. `4197fa5`.

### part_019 drawn as a tangled blob
- **Seen:** 2026-08-22 — "moving very weirdly around the mesh".
- **Cause:** 121 of 270 merged outlines self-intersected: the silhouette ring closes
  in 3D and crosses itself once projected. Every test shape was convex.
- **Fix:** refuse (`fe93ab0`); later drawn under nonzero winding, which fills a
  same-direction fold solid (`e2771d1`).

### Endpoints that should meet did not
- **Cause:** keys rounded to 1e-6 where the same corner projected along two paths
  differs by 1.7e-5 — seventeen times more — so corners became two degree-one
  nodes. A comment claimed the opposite.
- **Fix:** weld to a shared representative across neighbouring cells; grow walks in
  both directions. `3b3f127`.

### part_077 lost its outline every fifth frame
- **Cause:** the facing hysteresis band held a face front after it turned away, so
  the silhouette was built from a back-facing polygon. Widening the band made it
  worse (677 → 1,449 gaps), which is what pointed at it.
- **Fix:** exact facing for the silhouette; the band stays on painting decisions.
  `b04f46b`.

### A ring and its hole swapped slots every frame
- **Cause:** matched by centroid distance, and a ring and its hole share a centroid.
- **Fix:** match by kind first. `d963953`.

### Budgets sized by rank, geometry written by region
- **Cause:** the measuring pass sized slots largest-first per frame; the writing
  pass followed the region. part_087 had 14 points crushed into 4.
- **Fix:** decide the assignment once in the measuring pass and replay it.
  `1c5fea4`.

### Parts culled away entirely
- **Cause:** backface culling an open shell leaves nothing — part_310, a single quad,
  lost a 13,468 px² panel for 35 frames. The first fix drew the far side and read
  opacity off the empty near side, so it stayed invisible.
- **Fix:** take the other side when the cull would leave nothing. `c5462e5`.

### A part merged for 11 frames and absent for 49
- **Cause:** the merge rule asked whether *any* frame rang.
- **Fix:** merge only what rings in every frame it is visible. `5fae344`.

### Outlines collapsing to a line between keyframes
- **Cause:** `walk_loop` returned the same ring clockwise on one frame and
  anticlockwise on the next (151 of 217 outlines), so interpolation passed through
  the mirror image. Exact at every keyframe; only visible played slowly.
- **Fix:** wind every outline the same way; `align` considers rotations only.
  `6ff5f67`.

### part_042's edges rotating between keyframes
- **Seen:** 2026-08-23 — "as if the side edge becomes the bottom edge".
- **Cause:** 0.05 px from the truth at every keyframe, 14.26 px between them.
  Placement was re-chosen per frame, and slots divided their budget in a way that
  shifted every index when a vertex joined the silhouette.
- **Fix:** a subframe probe to measure it (`e865e00`); points at equal steps of a
  measure and anchored to mesh vertices (`48bad92`).

### A stick opening to four times its area
- **Seen:** 2026-08-24 — part_113, "a very large area at 0.56 s even though it is
  literally a stick".
- **Cause:** four faults: the corner width solved per loop instead of per span; slots
  re-spread from scratch; matching by vertex identity forcing slots across the
  object; and pinning the cheapest single pair picking the wrong arrangement.
- **Fix:** per-span solve, slots keep their place, monotone matching by arc length
  solved as a shortest path over every cut. 20.04 → 8.79 px. `6c14c0f`, `91a6252`,
  `c9850ef`.

### Ten slots stacked on one vertex
- **Cause:** the point ceiling was three times the ring; part_140 had 24 slots for a
  6-vertex ring.
- **Fix:** never more points than ring vertices plus two. `7377876`.

### Blades that cannot be carried were merged
- **Cause:** a silhouette going from a pentagon to a triangle has no correspondence;
  every placement rule landed at 32–44 px.
- **Fix:** the gap test compares the merged outline against the object's own faces
  between keyframes, with Merge Slack. `fb3fce1`.

### The legs of a V trading places
- **Seen:** 2026-08-25 — part_079.
- **Cause:** the gap test scored an idealised dense correspondence (2.91 px) instead
  of the points actually written (11.53 px).
- **Fix:** judge the points read back out of the slots. `ab6b5e9`.

### A dead-end walk starved later walks
- **Cause:** a walk that dead-ended kept its edges marked used, and `_agree` keyed the
  −1 pinch sentinel as if it were a vertex.
- **Fix:** `f7a03c9`.

### A patch that did everything twice
- **Cause:** a deletion bounded by searching for a loop header that also appeared
  earlier in the file, so `end` came before `start` and 150 lines were duplicated.
  It parsed and ran. The same patch deleted two constants and the tests passed
  anyway, because they call the function directly.
- **Fix:** `66c21ba`.

### Multi-ring fills wound so they cancelled
- **Seen:** 2026-09-12 — "the torus and two intersecting circles don't look right".
- **Cause:** three wrong answers. Nesting cannot decide a pinched spliced ring (one
  subpath in four wrong); the adjacent surface is local while "bounds something" is
  not (a torus at 15° has no visible hole); and `_coverage_side` returns *relative*
  keep/reverse while `wound` wants *absolute*, which gave two overlapping spheres
  opposite windings.
- **Fix:** sample coverage on both sides of a loop; covered on one side is a boundary
  wound with coverage on the left, covered on both is a fold and is dropped.
  `42fa586`. Lab IoU: cube 0.997, boss 0.993, tube 0.991, twins 0.991, torus 0.963.

---

## 6. Visibility, culling and track encoding

### The last frames of a move silently dropped
- **Seen:** 2026-08-16, through the new preview page.
- **Cause:** Step 10 over frames 1–60 sampled 1…51.
- **Fix:** always sample the end frame. `d7edaf0`.

### Flicker and translucency in an opaque city
- **Seen:** 2026-08-16 — flickering, "moving in waves", things translucent for a
  moment.
- **Cause:** two separate faults. Cull Hidden zeroed visibility per frame, and the
  player interpolates visibility, so fills faded in across whole gaps and were
  pruned from ordering on those frames (mixing ranks and metres). Separately, the
  dense track encoding assumed evenly spaced frames, and always sampling the end
  frame broke that (57 then 60): opacity decoded as `1, 1, 0.313, 0`.
- **Fix:** cull whole elements for the whole clip (`e5b0365`); encode sparse unless
  the bake records even spacing, and treat a missing flag as uneven (`786ceb7`).
- **Guard:** `test_occlude.py`, with every assertion spanning two frames;
  `test_encode_grid.py`, which also checks `bake.py` still writes the flag — an
  unrelated revert had once removed it silently.

### A bake crashing on frames with no geometry
- **Cause:** `freeze_hidden` only carried poses forward, and a merged fill writes
  nothing in a frame whose outline is not one ring:
  `TypeError: 'NoneType' object is not iterable`.
- **Fix:** borrow the nearest real pose. `38fb353`.

### The boom flicking 2,900 px across the frame
- **Cause:** a flip-adjacent frame borrowed a pose from long before, and faded in
  across the distance.
- **Fix:** every hidden frame takes the nearest visible pose. `8b5a314`.

### Strokes flying around when objects move
- **Seen:** 2026-09-06 — "strokes are flying around when objects move", in the hero.
- **Cause:** the player interpolates across the frame an element switches off, so
  that frame is drawn. `freeze_hidden` had pinned the hidden end to the last visible
  pose while the object moved on: a ghost one frame behind, fading out. Merged fills
  had no pose of their own to keep there at all.
- **Fix:** a hidden frame touching a visible one keeps its own pose (`3b03a77`); a
  merged fill with none has its opacity reach zero a 2% sliver past the last real
  pose, and a track with off-grid keys is forced sparse (`fe5634e`). Six offenders at
  up to 54 px → 0, worst overrun 1.15 px, for one keyframe and 19 bytes.
- **Guard:** `scripts/check_geometry.py`, exhaustive and gated on opacity.

---

## 7. Welds and seams

### Splitting a part drew its cut
- **Fix:** Weld Seams treats coincident open edges across objects as interior,
  judged by dihedral angle, owned by one side. `bba5c1f`.

### Chevrons on a welded part, misdiagnosed twice
- **Cause:** first attributed to a merged-fill outline (`3abc2c3`) — wrong. Leave-one-out
  over every element put them on real strokes: the cap meets the body at 90.00°
  against a 73.59° threshold, so it is a genuine crease (`87f01f5`).

### `reachable` did not know about welds
- **Cause:** the second face of a welded edge belongs to another object, so the edge
  was judged against half its surface.
- **Fix:** the weld index records the partner's face centre. `4ace104`.

### A cube cut in two did not bake like the cube
- **Seen:** 2026-09-03 — "the split cylinder has to bake the same as the non split one".
- **Cause:** a merged outline is a stroked fill whose silhouette includes the cut, and
  a welded edge could not tell the neighbour's fill which way to sort.
- **Fix:** a per-point seam mask (`sw`) so the stroke skips the cut while the fill
  keeps it, honoured by the player and the static SVG; `under_faces` assertions from
  the weld's recorded far-face normal (`2a4cf17`); the mask requires every frame to
  agree (`b7def3d`). Cube 1,710 → 4 differing pixels of 358,264.

---

## 8. The Lerpa player

### Transparent background never cleared
- **Seen:** 2026-08-01 — the animation trails behind every frame it passes.
- **Cause:** `background="transparent"` is a truthy string, so it took the
  `fillRect` branch, and a transparent fill composites to nothing.
- **Fix:** always `clearRect`, then fill only a real background.

### The wrong end of the clip for one frame
- **Seen:** 2026-08-03 — everything disappears for a moment on click.
- **Cause:** `play()` only scheduled a frame; on a reverse it jumped the clock without
  painting, so the canvas still held frame 0.
- **Fix:** paint immediately.

### Seam hairlines closed nothing when zoomed out (lerpa)
- **Cause:** `seamWidth` was in scene units; the seam is one *device* pixel. At fit
  scale 0.575 the stroke was 0.4 px, and an element correctly behind two abutting
  faces showed through the gap — reading as an ordering bug.
- **Fix:** at least one device pixel, from the context's own transform.
  `lerpa@a252480`, `vecbake@1d9ee37`.

### Clicking the background froze the player (lerpa)
- **Seen:** 2026-08-22 — "it stops when I put the cursor over it".
- **Cause:** `pick()` returns on the first hit, so a miss built and rasterised a path
  for every drawn element: 69 ms.
- **Fix:** reject by bounding box first. 5.1 ms. `lerpa@2279335`.

### Types that did not resolve (lerpa)
- **Cause:** `types` pointed at a missing `index.d.ts`; the README said to install
  `ivg`.
- **Fix:** `lerpa@7e52132`, with `prepublishOnly` importing the package without a DOM.

### Last-bit depth noise flipping order (lerpa)
- **Cause:** found while replacing the comparator sort: depths differing by 9e-13
  against a quantum of 6e-6 ordered by noise.
- **Fix:** pack depth and index into one double and use the native sort; ties go to
  index. `lerpa@29636db`.

---

## 9. Preview, audit and tests

### A player fix that never reached the preview
- **Cause:** `.preview.html` inlines a vendored copy of the player. A 14-minute bake
  embedded the old one because the add-on had not been reinstalled; by 5 September
  the copy was 2,033 diff lines behind and drawing a seam the bake no longer emitted.
- **Fix:** `tools/play.html` loads the player from disk (`d250c57`); synced, with
  `scripts/sync_player.py` checking both currency and inlinability (`b45dcf1`).

### The preview canvas painted over its own controls
- **Cause:** at narrow widths the stage row had no height, so `height: 100%` resolved
  to nothing and the canvas overflowed across the transport bar.
- **Fix:** `dfc8c64`.

### Em dashes as mojibake in embedded previews
- **Fix:** the template is pure ASCII. `e5b0365`.

### Forward + Reverse did work nobody used
- **Cause:** ticked on a lerpa-only export it computed a reversed copy and threw it
  away while the panel promised "3 cameras → 6 file sets".
- **Fix:** `api.CONSUMED_BY` hides options no selected format consumes. `bda49d3`.

### `bpy.ops.vecbake.export()` died on its first progress tick
- **Cause:** its callback took `(done, total)`; the reporter sends `(phase, done, total)`.
- **Fix:** `1d9ee37`.

### The audit never finished, and the validator passed without running
- **Cause:** `audit.py` built one rectangle per *pair* of objects — 46,000 a frame —
  and was killed after 19 minutes. `validate_bake.py` imported `vecbake` (the
  installed add-on is `bl_ext.user_default.vecbake`) and, had it imported, spied on
  `resolve()` in-process while ordering runs in workers, so it would have graded an
  empty list as PASS.
- **Fix:** paint the canvas once per frame; force one worker and raise if the spy
  never fired. `179c836`.

### Tests nobody could run
- **Cause:** `test_chain_trace`, `test_follow`, `test_index_written` said "run inside
  Blender" and imported `vecbake`, which works in neither place — 21 checks.
- **Fix:** `scripts/_addon.py` loads modules standalone. `3ef8aca`.

### Intermittent `PermissionError` in the worker pool
- **Cause:** on Windows the target of `os.replace` can be held briefly after the name
  appears.
- **Fix:** leave it for the next 20 ms poll. `526fd42`. A test asserting arrival order
  was also wrong and now only prints it (`cd21cf7`).

### The audit's diagnosis went silent under the worker pool
- **Cause:** it spies on ordering, and could only see calls in its own process.
- **Fix:** one worker while diagnosing. `1c5fea4`.

### The audit's worst error was outside the canvas
- **Cause:** element boxes deliberately run past the frame edge.
- **Fix:** clip to the canvas. `f16afb5`.

### The audit was wrong three times
- It sampled reduced tracks at `fi/fps`, ignoring frame step: 1.7M phantom flips.
- It graded on-face pairs by the same broken plane fit the baker overrides.
- A between-keyframes scan sampled past `duration` and reported the whole scene ~47 px
  wrong at once — the tell was every part sharing one timestamp.

### A test that could not catch its own bug
- `test_edge_shading.py` set its cylinder to 30°, the same value as the fallback, so
  "the angle is read back" passed on Blender 5.2 with the bug live (see
  [gotchas.md](gotchas.md#blender-52-moved-geometry-node-inputs)). It now checks the
  value came from the modifier. `320f320`.

### A module shadowed by a local
- `from . import boundary` collided with a local mask named `boundary` in
  `_sample_object`. `ad20966`.

---

## 10. Website, hero and demos

### Cloudflare deploy found nothing to deploy (lerpa-website)
- **Cause:** no wrangler config, and the page was an artifact fragment in quirks mode
  named `landing.html`. Later: `npm ci` with no `package.json`.
- **Fix:** `lerpa-website@4135175`.

### Drag along the wrong axis, and backwards (lerpa-website)
- **Cause:** read screen horizontal on a drawing tilted 45°; and time runs down as the
  hand goes right.
- **Fix:** project onto the drawing's own horizon; invert. `@7a48e56`.

### A zoomed part framed as an annulus (lerpa-website)
- **Cause:** a box unioned over the whole clip, centred on the spin axis.
  `groupBounds` measures elements drawn or not (660–1,049 px against 46 px of ink).
- **Fix:** size from the largest apparent extent, centre asked per frame. A drag also
  stuck when pointer capture was lost without `pointerup`. `@04abb62`.

### Zoomed parts jumping as they turn (lerpa-website)
- **Seen:** 2026-09-05/06, reported three times.
- **Cause:** every attempt sampled the model at frame positions and re-interpolated,
  which disagrees with what is drawn between frames — the object origin (all 620 sat
  at the world origin), a harmonic-fitted box, a sidecar of projected points.
- **Fix:** aim at the centroid of elements drawn in every frame, evaluated at the time
  being rendered (`@1e9c68f`); then compute the aim *before* rendering and stop easing
  a moving target, since a first-order filter settles behind it (`@e8b3159`).
  Frame-to-frame movement 14.0 px → 0.

### The zoom tree drifted from the model (lerpa-website)
- **Cause:** the blend gained 18 split pieces after the tree was written; 18 other
  parts existed only in an unsaved session.
- **Fix:** re-cluster from the bake's own groups, label by colour-banded render.
  `@34e52f5`, `@a0130fb`.

### The wobble trailed the pointer (lerpa-website)
- **Seen:** 2026-09-11.
- **Cause:** easing a position target lags by construction. Separately, in a stacked
  layout full deflection used the box's long side, reaching 9 of 20 degrees.
- **Fix:** follow exactly, ease only the return; span from the axis's dominant screen
  direction. `@4d1cc6c`.

### The preview wedged on the easing tail (lerpa-website)
- **Cause:** a geometric ease never arrives, and repainted about fifty times to travel
  a hundredth of a degree.
- **Fix:** snap within an eighth of a frame. `@7a232a4`.

### The model was clipped (lerpa-website)
- **Cause:** `overflow: hidden` trimmed the boom tips, and the panel sat inside the
  1,020 px sheet.
- **Fix:** break out to 1,360 px, visible overflow, pointer read across the whole hero.
  `@58f5cbf`, `@3d14b0f`.

### The site still wore the old theme
- **Seen:** 2026-09-12 — "the yellow theme we have discussed before you regressed to
  the astral theme".
- **Cause:** the rebrand commit (`@ea94d5b`) described moving to the industrial
  identity, but the stylesheet it committed was still the night-sky palette, and no
  later commit changed it.
- **Fix:** re-themed on 13 September — see [decisions.md](decisions.md#visual-identity).

### Butterfly demo: Release left the page unusable
- **Seen:** 2026-08-11.
- **Cause:** removing the focused butterfly cleared the gate but never returned the
  phase to `idle`, which hides both controls. The blue flash was the mobile tap
  highlight on a full-bleed canvas.
- **Fix:** reset phase on removal; clear tap highlight, `touch-action: manipulation`.

### Aegis Pulse test integration
- 2026-08-02: the end frame showed behind the animation — a still restored to hide a
  flash was correct under an opaque `<video>` and wrong under a transparent canvas.
- 2026-08-09: a flash of the end frame on mount, from initialising the still to the
  destination before the canvas painted.
- 2026-08-01: `Failed to resolve component: ivg-player` — Nuxt ignores a `vue.template`
  block; the key is `vue.compilerOptions.isCustomElement`.

---

## 11. Reported and investigated: not bugs

| Date | Reported | What it was |
|---|---|---|
| 2026-08-03 | Mast members in front of dishes | Partly real (§2), partly dishes mounted 0.002–0.03 units from the mast: no painter's order exists. Give parts clearance. |
| 2026-08-10 | Butterfly wings missing | Single-sided planes culled by Backface Cull. Turn the cull off. |
| 2026-08-11 | Forewings passing through each other | They do: the two planes interpenetrate by 0.0277 units on every frame. Offset one. |
| 2026-08-16 | City bake takes forever | 6,144 separate cubes (V−E+F = 6,144), ~60× the reference scene. |
| 2026-08-20 | Stray fill on the golden record | The bus's two 10-vertex caps each span 1.69 units of depth. Triangulating them: 211 → 7 px. |
| 2026-08-22 | Can't see into an uncapped tube with merging on | A merged fill is an opaque silhouette. Use `vecbake_merge_fill = 0` on that object. |
| 2026-08-30 | Decimated Voyager didn't shrink the file | Dissolving coplanar faces removes edges that never creased. `tol_px` and `dissolve_px` are the levers. |
| 2026-09-06 | Specks blinking in a compartment | Real small fittings turning edge-on; `hideSpecksBelow` exists to hide them. |
| 2026-09-11 | `apollo_000` won't merge because it's an open funnel | My diagnosis, and wrong — it is closed (V−E+F = 2). It is concave: a cup. |
| 2026-09-12 | Solar panels paint ~22% of their outline | `soyuz_029`/`030` are frames; `soyuz_033` fills the openings. With everything drawn, at most 0.03% is uncovered. |

---

## 12. Open

- **A dark wedge across the docking module** (`dock_001/m`, seen 2026-09-13 in the
  confirmed preview). The first diagnosis above — a hidden inner ring — was wrong. Two
  causes, found in turn:
  1. **Fixed, 2026-09-14:** subpath slots swapped and an absent slot collapsed onto
     ring 1's far end, so between keyframes a ring swept from one end of the part to the
     other. `_match_pieces` now matches against what each slot last held, and
     `_settle_absent_pieces` parks an absent slot on its own piece in the nearest frame
     that has it. Guard `scripts/test_piece_slots.py` (fails 2 on the old code). On the
     re-bake the third ring waits at x≈1181 instead of x≈1008 in every frame; the dense
     scan finds no ring jumping.
  2. **Fixed, 2026-09-14 — and not a merge bug.** The long wedge that remained was the
     *preview's* player. The installed add-on's `preview/player.js` predated multi-ring
     fills (`42fa586`): every reinstall this session copied only the `.py` files. The
     preview inlines whatever player is installed, so it drew `dock_001/m`'s three rings
     as one 87-point path, and the jumps between ring ends cut across the part.
     Confirmed by recording the canvas calls (one fill subpath of 87 points), then by
     installing the repo's player and re-inlining it into both preview pages: the wedge
     is gone. The "self-crossing outline" reading that briefly replaced (1) was wrong —
     the rings as sampled are simple and wound alike.
     With the correct player the pre-fix bake still shows a dark bar sweeping near the
     flange around 0.70 s, and the fixed bake does not, so (1) was a real artefact too.
     Guard: reinstall the whole add-on folder, not the `.py` files; see gotchas.md.
- **Half the `apollo_018` thrusters are not merged.** 16 identical meshes, identity
  transforms; `.001 .003 .005 .007 .010 .013 .015` fall back as `merged_uncarried`:
  merged they are 11.5–15.7 px off between keyframes against 0.05–0.07 px face by face.
  Probably their outline changes character over the arc at their mounting angles. Their
  tube end caps then show as open rim lines. `dock_004`, `007`, `010`–`015` fall back
  for another reason, not yet read.
- **A light-grey spike at the joint below the docking module** in the same frame; owned
  by one of `dock_004 / 007 / 008 / 010–015` (it disappears with them hidden). Not yet
  isolated.
- **What is left of `apollo_003` over its fittings** after the surface read: 289 wrong
  pixels on the screening audit, 214 with `apollo_003` drawn face by face. Splitting
  still pays; *which* bodies to split has no justified criterion yet (the 50% depth
  share has no knee; the first interleaving selector split 39 bodies and scored 342 —
  [audits.md §7](audits.md#7-interleaving-selector-first-attempt)). See [audits.md](audits.md#5-triangle-read-with-and-without-splitting-apollo_003).
- **Three parts lose one frame each** to a boundary walk that returns nothing
  (part_192, part_170, part_158 — August 23).
- **The butterfly page scrolls sideways** at narrow widths: rotated `.tilt` players
  overflow their cards.
- **Canvas pixels read from a canvas in the document** are not stable across renders in
  Chrome; see [measurement.md](measurement.md#never-score-pixels-off-a-canvas-in-the-page).
