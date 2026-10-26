# Experiment ledger

Every idea that was built or measured to find out whether it worked — including,
especially, the ones that did not. A negative result with numbers is the most
valuable thing in this file: most of these ideas look obviously right, and each one
has been tried at least once.

Verdicts:

| | |
|---|---|
| **KEPT** | shipped and still in the code |
| **REVERTED** | shipped or built, then taken out because a measurement said so |
| **REJECTED** | built or measured and not shipped |
| **NOT BUILT** | measured or analysed, deliberately not implemented |
| **OPEN** | built and not yet decided |

"Where" points at the commit, and at the code comment where the note lives. Many of
these are written into the source beside the code someone would change to try them
again — read those before starting.

Measurements on the Voyager probe and Apollo–Soyuz are only comparable within one
row: the models were re-saved, simplified and split several times.

---

## Contents

1. [Paint order](#1-paint-order)
2. [Merged outlines](#merged-outlines)
3. [Point placement and sizing](#3-point-placement-and-sizing)
4. [Culling, visibility and chaining](#4-culling-visibility-and-chaining)
5. [Time, keyframes and file size](#5-time-keyframes-and-file-size)
6. [Welds](#6-welds)
7. [Player performance](#7-player-performance)
8. [Hero and website](#8-hero-and-website)

---

## 1. Paint order

Four attempts on the spending side and one on graph content have failed. The
direction that survives is better measurement of a merged part's depth, and finer
granularity where one scalar per element genuinely cannot work.

| Experiment | Result | Verdict | Where |
|---|---|---|---|
| Order a pair by a mean over up to 24 samples of the shared region | The sample set changes discretely as overlap moves, so the mean steps: 8,409 order swaps over one orbit | REVERTED | `9b58e0e` |
| Order a pair at the **centre of what they share** (area centroid / length midpoint) | Swaps 8,409 → 6,845, fill-vs-fill 72 → 53, bake 5.0 → 3.8 min | KEPT | `9b58e0e`, `order._overlap_centre` |
| Decide per island of the shared region, area-weighted | Ray cast 538 → 580 wrong (noise), bake +5%. 12.5% of pairs meet in several patches, but the union centroid lands in the dominant one when it matters | REJECTED | `6274add`, `_overlap_centre` docstring. Sampling per patch and voting by area were never separated |
| Spend the edge-on-face **assertion** first when a cycle must break | Fixed the golden-record stray line; in motion 18 of 196 edges alternated width | REVERTED | `9fbb973` → `38fb353` |
| Unprotect only assertions the measured graph contradicts | Halves the width flicker, to 11; nothing reaches zero | REJECTED | `38fb353` |
| Don't record an assertion where sampling already agreed | `lines_under_own_face` 0 → 1,853 for 877 fewer violations elsewhere | REJECTED | `3f9409d`, `order.py` |
| Break each stuck component with one edge, not a tenth of all | Discards 23,724 → 21,540; cycle breaks 179 → 17,491; bake 2.4 → 10.5+ min; clear violations worse | REJECTED | `3f9409d` |
| Make the SCC scan incremental | Does what it promises (breaks 179 → 7, discards to the minimum) and takes a 13-frame bake 146 s → 1,411 s: one SCC holds 5,215 of 6,764 blocked elements | REJECTED | `c0c2e37`, `order.py` |
| Rank fills alone, then place strokes into the intervals left (two-phase) | Deadlock gone, discards 5,746 → 1,931 — and ray cast 2.24% → 4.13%: `fill → stroke → fill` chains carry real depth facts | REJECTED | `d86f892`, `e987c49` (whose conclusion it corrects) |
| Constrain stroke-vs-stroke pairs | On the Voyager recoloured with 237 hues: 1,285 reversals, 39 visible, 217 px² over the whole clip | NOT BUILT | `50b01eb`, `order.py` |
| Merged part depth from a **plane fitted to its rim** | Dish back shell off by up to 1.35 units on a 0.067 shell | REVERTED | `c5462e5`, `6274add` |
| Quadratic fit instead of a plane | Halves the dish error, worsens part_005 | REJECTED | `c5462e5` |
| Depth from the **nearest surface sample** | Exact at samples; a step function between them | KEPT | `c5462e5`, `order._depths_at` |
| Inverse fourth-power weighting over all samples | Continuous; ray cast 2.24% → 2.00%; 60-frame bake 12.5 → 55 min | REVERTED | `72b1c78`, `order._depths_at`. k-nearest is not a cheaper version: it steps when the k-th changes |
| Weight constraints by **sample agreement** instead of gap (`agree·span + |gap|`) | Fixed the synthetic stray-line test; on Apollo–Soyuz errors 176 → 202, `order violated` 75 → 102. Nearly every constraint agrees, so weights compress and the breaker's tenth becomes arbitrary | REVERTED | 2026-09-12, `order.py` beside the gap weighting |
| Measure how much low-agreement ("crossing") constraints hold the cycles together | 1.2% of 1.3M sampled pairs; 0.3% of edges inside stuck components; 98.8% of everything spent is already well supported | Finding | 2026-09-12, [progress report](progress-2026-09-13.md) |
| Draw `apollo_003` face by face instead of merged | 3-frame audit: 446 → 265 wrong px, `apollo_003` errors 43 → 9, +14 elements | Finding | 2026-09-12 |
| `split_span_pct`: auto-split bodies spanning ≥50% of scene depth | Picks 3 bodies on Apollo–Soyuz with all-vertex depth, 2 with near-side depth (58.2 / 51.7 / 42.6%) — no clean knee | OPEN, experiment in `20c2f71` | `bake._deep_bodies` |
| Choose bodies to draw face by face by **evidence of interleaving**: split a merged body when, in some frame, one part is measured in front of it at one place and behind it at another — the case one rank cannot satisfy — decided once for the whole clip | Built 2026-09-13 in `scripts/` only. Lab: correct on stepped body, flush bolt and separate boxes where the 50% rule fails twice. Apollo: 39 bodies, screening audit **342** wrong px (vs 214 hand-split, 289 none), 36 errors, elements 18,324 → 27,809. One cycle break is 261 px; the rest 81. 25 picks contradict on ≤16 px; both sides of a pair get split | REJECTED as built; OPEN ([audits.md §7](audits.md#7-interleaving-selector-first-attempt)) | `scripts/interleave_experiment.py` |
| Select before merge feasibility is settled | 59 bodies, 18 of which fail to merge anyway: their faces are separate ranks, so their "contradictions" are false | REJECTED | `out/interleave/audit-any` |
| Per-object "draw face by face" toggle set by the artist | The predictable alternative to automatic selection; no threshold | NOT BUILT, not chosen | — |
| Draw `apollo_003` face by face **with** the triangle read | Screening audit 289 → 214 wrong px, errors 68 → 41, `measured wrong` 31 → 15, +14 elements, bake +4%. Splitting still pays: what it fixes now is one rank per body, not the depth read. The selector (a 50% share) remains unjustified | Finding | 2026-09-13, [audits.md](audits.md#5-triangle-read-with-and-without-splitting-apollo_003) |
| Read a merged part's depth from the **front triangle** under the point | 446 → 289 wrong px with nothing split; `measured wrong` 53 → 31. `apollo_018` under `apollo_003` in frame 30 before, on top in all three captured frames after. Costs ~1 s of CPU per bake | KEPT | `20c2f71`, `order._surface_read`, `bake._surface_tris`, [audits.md](audits.md) |
| Batch depth reads per item per frame | Ranks identical; `_depths_at` calls 207k → 80k and 41 → 16 s CPU over 10 frames. Its "20–25% faster" came from a synthetic bench; on Apollo within noise | KEPT | `20c2f71` |
| Batch `_overlap_centre`: one inside test per fill for all its pairs, edge crossings once per grid row, a fill's lines clipped together | Ordering 23.4 → 7.5 s per Apollo frame on one thread; 10-frame bake CPU 229.6 → 112.2 s; ranks, stats and a depth/opacity hash identical | KEPT | `20c2f71`, `order._fill_fill_centres`, `_inside_rows`, `_line_spans` |
| Vectorise fill-vs-stroke clipping per *pair* | Exact, and 10.3 → 10.1 s: the Python overhead moved into ~40 numpy calls per pair. Batching per *fill* is what paid (→ 7.5 s) | Superseded | `20c2f71` |
| Resolve Paint Order off for "convex boxes" (city) | Centroid order 114/120 against 120/120 on ray-cast truth | REJECTED | 2026-08-16 |
| Parallel ordering and culling in worker processes | Voyager 66.9 → 8.9 min, byte-identical output | KEPT | `a1fc276` |

---

## Merged outlines

The long arc from 10 to 26 August. What ships: one element per object, a ring found
by chaining silhouette edges, several rings as subpaths since 12 September, slots as
positions around the loop anchored to mesh vertices, and a per-object gap test that
refuses what faces would draw better.

| Experiment | Result | Verdict | Where |
|---|---|---|---|
| One slot per mesh vertex, order fixed by the first ring | Rotation reorders the silhouette; Merge Fills bought 0.3% on the Voyager for 16.7 min | REVERTED | `e6be3af` |
| Slots as positions around the loop, count from error | Lab 2,351 → 399 elements | KEPT | `e6be3af`, `a47c171` |
| Union of projected faces as the outline (`boundary.py`) | Handles folds, holes and splits; 13-shape lab 2,931 → 277 elements — and reached 14 decision points and 17 epsilons that tuned to one model and broke another | REVERTED | `550aa38`, `ad20966` → `2f3e20a`, after "this project has turned into a makeshift code full of bandaids and duct tape" |
| Holes as closed strokes, chosen by containment rather than winding | Winding classified the dish and washer as holes | Superseded by subpaths | `ad20966` |
| Hide a degenerate frame | Flicker from parts hovering near edge-on: 24 flips in 60 frames | REVERTED | `4ca8152` → `3d9ad2b` |
| Hold the last good outline | part_004 frozen for 59 of 60 frames; part_022 caught up 140 px in one step | REVERTED | `3d9ad2b` → `3b3f127`, `1c5fea4` |
| Un-merge parts spanning >5% of scene depth | Ordered the dish right; flicker 3.9% → 15.8% of elements, 96% of it on those parts | REVERTED | `3d9ad2b` → `52e83af` (opt-in) → `2f3e20a` (removed) |
| Widen the facing hysteresis band to 7° | Gaps 677 → 1,449 | REJECTED | `b04f46b` |
| Drop near-zero-area faces from the facing set | 6 fewer parts blinking, 64 more frames self-crossing | REVERTED | `c5462e5` |
| Oriented boundary walk alone | Self-crossings 113 → 6; frames missing 25 → 440 | REJECTED alone; KEPT with the undirected walk as fallback | `6013df6` |
| Orient from the covering probe instead of the owner face | No difference (658 frames either way) | REJECTED | `6013df6` |
| Add folded edges as silhouette candidates | Self-crossing 113 → 38; parts vanishing 25 → 136, frames lost 25 → 301 | REVERTED | see `memory: vecbake-fold-boundary` |
| Resolve silhouette junctions by turn angle / face tracing | Branch failures 987 → 121, **zero** extra merges: every object that branches fails another way elsewhere | REVERTED | `e99cb30` → `e2771d1` |
| Draw self-crossing outlines under nonzero winding instead of refusing | Merged 125 → 223, 18.2 → 16.8 MB, bake 4.0 → 3.4 min | KEPT | `e2771d1` |
| Edge-anchored carrying / keying re-assignment early / scoring rotations on worst displacement | None moved the 146 outlines collapsing between keyframes | REJECTED | `6ff5f67` |
| Local repair instead of re-choosing a carried outline | 8.2 → 39.6 s for one keyframe | REJECTED | `4197fa5` |
| Match slots to vertices by screen distance (median shift, fitted turn and scale) | Whole assignment rotated several places at some frames | REJECTED | `6c14c0f`, `91a6252` |
| Match by arc length round the ring, every cut tried | part_113 20.04 → 8.79 px; rods 12–22 → 1.3–3.2 px | KEPT | `91a6252`, `c9850ef` |
| Rounder stick tips to avoid the rotation artefact | 4, 8, 16, 32-sided rods all showed it; the square one least | Finding | `91a6252` |
| Merge loops that pinch or arrive in pieces, splicing loops that touch | Merged 204 → 234, 18.6 → 17.4 MB, bake 17.5 → 13.1 min | KEPT | `f7a03c9` |
| Bridge a tube's bore to its outside with a zero-area seam | Merged 253 → 259, 16.1 → 15.7 MB; only for two-piece silhouettes | KEPT | `6e69a51` |
| Cover the bridge seam with a second unstroked fill | The seam was not visible at playback | NOT BUILT | `6e69a51` |
| Multiple subpaths per merged fill | Refuses nothing; `apollo_018` 259 face fills → one element | KEPT | `42fa586` |
| Gap test for one-ring outlines only; multi-subpath outlines judged by Merge False Outline alone | Torus lab 30°/60°: 2,246 / 1,654 elements → 1, 0 geometry offenders, no segment over 3 px. Apollo merged 58 → 62, wrong px 196 → 180; Voyager 266 → 269, 162 → 162; nothing newly refused. Not the ring-by-ring test of audits §10, which judged multi-ring outlines *more* | KEPT | audits §22 |
| Park an absent subpath on its own centroid instead of one vertex | Torus birth/death: 4,173 px false line between keyframes against 4,281 now | REJECTED, measured only | audits §23 |
| Park each point on its own surface point in the empty frame | Between keyframes 4,281 → 275 px, but the ring is drawn at the empty keyframe: 3,348.5 px there | REJECTED, measured only | audits §23 |
| Draw a subpath only from the moment it exists (event time by bisection), carried back from its own surface points then | Torus birth/death 4,281 → 276 px, nothing at keyframes; doughnut `Torus.003` 6,217 → 4,590.5 — its events are splits | NOT BUILT | audits §23 |
| Key the whole element at each topology event (two keyframes straddling it), rings by own surface points either side | Doughnut `Torus.003` event gaps 6,217 → **13** px; fits sparse tracks; does not touch the 842–13,962 px per body in steady gaps | NOT BUILT — a decision | audits §23 |
| Size subpaths through the pieces path in every measuring frame | Sizing no longer depends on the last frame; Apollo +1 merge (wrong px 180 → 193), Voyager +9 (162 → 153, segments > 3 px 24 → 38), creature `Joint` refused | KEPT | audits §23 |

---

## 3. Point placement and sizing

The finding that governs this whole area: **per-frame optimality is the wrong target
for an interpolated format.** A placement optimal in every frame, and differently
optimal in each, is worse between keyframes than a mediocre one that stays put. Always
measure at `subframe = 0.5`, never at a keyframe.

| Experiment | Result | Verdict | Where |
|---|---|---|---|
| Place points at equal steps of an arc-length measure, anchored to mesh vertices | part_042 14.26 → 5.25 px between keyframes, against a 1.50 px floor for 24 fps | KEPT | `48bad92` |
| Place points to minimise **area** wrong, per frame | Clearly better per frame (2,478 → 698 px²); chained, part_024 8.45 → 99.63 px | REJECTED | `8a6b337` |
| …which was partly the harness | The 99.63 px came from carrying arc fractions from a moving origin; with shared-vertex alignment, 5.39 px | Correction | `026cae8` |
| Area refinement leashed to one slot, on top of what ships | Better or equal everywhere; part_024 8.45 → 4.62 px | KEPT, gated | `026cae8`, `2ddf47f` |
| Substep the area placement between frames | 2, 4, 10, 20, 40, 80 steps: worst 8.28, 7.85, 3.97, 29.76, 29.69, 5.82 px — a lottery | REJECTED | `5679443`, `189da4f` |
| Solve each point's place exactly, converged | Worst error rises monotonically with convergence: 22.47 → 30.20 px. Stopping early was the regulariser | Finding | `386594a` |
| One leash length for every object | Helps 5 of 30, hurts 4, 21 unchanged | REJECTED; per-object gate kept | `1bd6854`, `2ddf47f` |
| Take the placement sweep out of numpy | 11–32× faster, identical to 8e-13 px | KEPT | `66c21ba` |
| Size a point count by what draws best **between** keyframes | 13% fewer points; better on 14 of 20, worse on none; 2 more merges | KEPT | `9829204` |
| Also search upward for more points | Same file, same ranking; bake 9.5 → 13.9 min | REJECTED | `51c3ab1` |
| Refuse hopeless objects early from the ring cache | Bake 27.4 → 12.5 min, one merge lost | KEPT | `6e69a51` |

---

## 4. Culling, visibility and chaining

| Experiment | Result | Verdict | Where |
|---|---|---|---|
| Chain through junctions by angular continuity | Strokes 826 → 560; with Sharp 30°, 920 → 417 | KEPT | 2026-08-01 |
| Cut chains wherever a fill's depth lies between members | 48 cuts preventing 0 violations | REJECTED | 2026-08-03 |
| Cut only where the depth comparison changes sign (straddle) | 2 cuts | KEPT | 2026-08-03 |
| Remove **facing** from the chain merge key | Merged edges 549 → 826, 1.86 → 1.83 MB; ray cast 2.225% → 2.446% | REJECTED | `ebe60f8` |
| Compare facing only on frames where both edges are drawn | Unlocks 4 pairs of 3,827 | REJECTED | `ebe60f8` |
| Cull Hidden: drop fills never shown against a coverage grid | City: 13,213 → 2,953 elements, 1,184 → 313 KB gzipped; ordering became possible at all | KEPT | `d7edaf0`, `e5b0365` |
| Cull hidden strokes too | 757 of 5,641 strokes culled; short dashes go missing along fill boundaries | KEPT, off by default | `84d147b` |
| Subject boundary edges to `reachable` like every other edge | 7,365 → 6,799 elements; the magnetometer boom collapses from a band to a hairline | REJECTED | `1ffb0ea` |
| Pin a hidden run's frames to the last visible pose | Ghost strokes a frame behind the model | Refined | `3b03a77` |
| Pop a merged fill's opacity 2% past its last real pose | Offenders 6 → 0, cost 1 keyframe and 19 bytes | KEPT | `fe5634e` |
| Occlusion cull on the hero bake | Hides parts that must reappear when their occluder is hidden | Off for the hero | `lerpa-website@ca1794d` |

---

## 5. Time, keyframes and file size

| Experiment | Result | Verdict | Where |
|---|---|---|---|
| Frame step 2 on a 360° orbit | Keyframes 1,236,502 → 704,338; 25.6 → 19.4 MB | Finding | 2026-08-21 |
| How many keys a circle needs | Linear interpolation needs 38–84 keys at 0.35 px for 100–500 px radii; 56% of tracks keyed every frame | Finding | 2026-08-21 |
| **Cubic (Catmull-Rom) time interpolation** | 15.7 → 14.9 MB (−5%) for a bake of 12.5 → 47.8 min. The 40% promise came from comparing against a weaker reducer than the one that ships | REVERTED | `memory: vecbake-open-backlog` §2b. Worth revisiting only for a gentle clip |
| Store every frame instead of interpolating | Geometry 0.98×, whole file ~1.07×; no runtime gain; loses scrubbing and >24 fps | NOT BUILT | 2026-08-25 |
| Decimate the Voyager 93% (dissolve coplanar faces) | Every output number unchanged | Finding | 2026-08-30 |
| Sweep the crease angle | Above 73.59° nothing changes; below it only adds strokes. The first sweep passed degrees to a radians property | Finding | 2026-08-31 |
| Forward + Reverse | Wrote nothing for lerpa-only exports while computing a reversed copy | Fixed | `bda49d3` |

---

## 6. Welds

| Experiment | Result | Verdict | Where |
|---|---|---|---|
| Split a cube and bake it against the whole cube | Weld off 0.53–1.14% differing; on 0.042%; on with chaining off **0 pixels** | Finding | 2026-09-03 |
| Exempt welded edges from `silhouette_edges` | The loop stops closing and the piece loses its fill | REJECTED | `2a4cf17` |
| Union-merge the pieces' outlines | Undoes the point of cutting: the union is the silhouette that would not ring | REJECTED | `2a4cf17` |
| Exempt the neighbour's silhouette from covering | 7.67% → 8.30% | REJECTED | `2a4cf17` |
| Seam mask by any frame / majority / every frame | 7.622% / 7.666% / 7.445% | Every frame KEPT | `b7def3d` |
| Chain across a weld | Would remove the 151 px residual; element ids, caches and matrices are per plan | NOT BUILT | `memory: vecbake-weld-exactness` |

---

## 7. Player performance

| Experiment | Result | Verdict | Where |
|---|---|---|---|
| Stroke width against device pixels | The frame rate collapses ~5× above `stroke_width × scale × dpr = 1.0`; canvas size, width and area were each ruled out | Finding | `memory: lerpa-hairline-cliff` |
| Batch a butterfly's 11 fills into one fill | 120 instances: 12.97 → 2.83 ms | Finding | 2026-08-10 |
| Batch fills by colour on the Voyager | Neutral | REJECTED | 2026-08-31 |
| `Path2D` caching | Butterfly 16.32 → 7.88 ms | NOT BUILT | 2026-08-17 |
| Seam fix cost on the Voyager | 52.0 against 52.3 ms — nothing | Finding | 2026-08-31 |
| Pack depth and index into one double for a native sort | `_solve` 10.15 → 6.63 ms, pixel-identical | KEPT | `lerpa@29636db` |
| Render in a worker on an OffscreenCanvas | No frame cheaper; main-thread wait during a drag 44.5 → 0.2 ms | KEPT | `lerpa@29636db` |
| Frame budget breakdown at 960 px | JS interpolation ~17 ms (33%), path building 6–12, rasterisation 24–29 | Finding | 2026-08-31 |
| A WebGL renderer | Ceiling ~2.5–3× unless interpolation also moves to the GPU; removes "no WebGL required" | NOT BUILT | 2026-08-31 |
| three.js with the same model and camera | 5–6× faster at 960 px, 8–9× at 1080p; Lerpa 11–12 / 6–7 fps; both 1.88 MB over the wire | Finding | 2026-08-31, `out/fps_three.html` |
| Backing store cliff for the Voyager hero | 1200×675 at 116 fps; 1240×698 at 20 fps | Finding | 2026-09-05 |

---

## 8. Hero and website

| Experiment | Result | Verdict | Where |
|---|---|---|---|
| Centre a zoomed part on its object origin | All 620 origins sat at the world origin | REJECTED | `lerpa-website@34e52f5` |
| Harmonic fit of the drawn box, sidecar of projected centres | Sampling artefacts: the jitter lives between frames | REVERTED | `@34e52f5` → `@1e9c68f` |
| Aim at the centroid of elements drawn in every frame, evaluated at render time | Ink centroid wander 74–180 px → 2–18 px | KEPT, then refined | `@1e9c68f` |
| Aim at the box, computed before rendering, no easing once settled | Frame-to-frame movement 14.0 px → 0 | KEPT | `@e8b3159` |
| Cancel distance exactly and damp foreshortening | Size swing: power 2.14× → 1.199×, boom 2.46× → 1.233× | KEPT | `@b244094`, `scripts/fit_orbit_depth.py` |
| Bound the turn to ±20° by pointer position | Apparent-size swing is cumulative over the arc: 1.19× at 15°, 1.94× over a full turn | KEPT | `@7a232a4` |
| Bake Apollo–Soyuz as a 60° arc instead of a full turn | Half the frames; 2.29 MB gzipped against Voyager's 2.73 | KEPT | `@acd56a4` |
| Cluster assemblies by largest gaps between parts | Gave Apollo three parts — both largest gaps are inside Apollo | REJECTED; read from a colour-banded render | `@acd56a4` |
| SHADING edge mode on a Maya GLB | No Smooth-by-Angle in the file: a dark blob with a glowing outline | REJECTED; SHARP at 30° | `@acd56a4` |
| Hide specks below a fraction of the largest part | Nine of 46 parts at 0.01; real geometry, so off by default | KEPT, off | `@f589741` |
| Log elements moving too far between rendered frames | Instrument, off by default | KEPT | `@69779cf` |
