# Audit and measurement record

Every audit and timing run behind the paint-order work of 12–13 September 2026, with
its settings and results, so a number quoted elsewhere can be traced to the run that
produced it. Newest last within each section.

How to read the numbers:

- **Wrong pixels** are ray-cast overlap pixels owned by the wrong element. Compare only
  runs of the same size (see [measurement.md](measurement.md#audit-sizes)).
- **Causes** are `audit.py`'s diagnosis of each error: `measured wrong` (the ordering
  pass read the depths wrong), `order violated` (it read them right and the cycle
  breaker gave the constraint up), `no constraint`, `occluder not drawn there`.
- **Wall-clock times on this machine are unreliable** (see
  [Timing on a loaded machine](#timing-on-a-loaded-machine)). Quote CPU time or
  back-to-back runs.

All runs are on `Documents/Blender 2026/lerpa apollo-soyuz/apollo-soyuz.blend`
(31 frames) at 1920×1080: merge fills, weld seams, SHARP edges at 30°, backface cull,
see-through, cull offscreen, occlusion cull off, accurate order, chain edges,
tol/dissolve 1 px, flat shading, fixed 1.6 px strokes. The settings are applied by
`configure()` in [`scripts/screen_surface_batch.py`](../scripts/screen_surface_batch.py).

---

## 1. Ray-cast audits

### Full audits (frames 0, 7, 15, 23, 30; 30,000 px)

| Run | Date | Errors | Wrong px | Rate | Order violated | Notes |
|---|---|---|---|---|---|---|
| Multi-ring fills skipped by ordering | 09-12 | 191 | — | — | — | "element not in this frame's ordering pass" |
| Multi-ring fills ordered (`cec7935`) | 09-12 | 176 | 1,467 | 4.89% | 75 | Baseline for the agreement experiment |
| Constraints weighted by agreement | 09-12 | 202 | 1,805 | 6.02% | **102** | Reverted; weights compress. 6,630 s total, 1,043 s bake |

### Screening audits (frames 0, 15, 30; 9,000 px)

| Run | Wrong px | Rate | Errors | Measured wrong | Order violated | `apollo_003` errors | Elements | Reported bake |
|---|---|---|---|---|---|---|---|---|
| Nearest-vertex read, nothing split | 446 | 4.96% | 93 | 53 | 37 | 43 (292 px) | 18,324 | 748 s |
| Nearest-vertex read, `apollo_003` split | 265 | 2.94% | 62 | 26 | 30 | 9 (109 px) | 18,338 | 753 s |
| **Triangle read**, nothing split | **289** | **3.21%** | 68 | **31** | 30 | 24 (170 px) | 18,324 | 1,791 s ¹ |
| Triangle read, `apollo_003` split | *pending — §5* | | | | | | | |

¹ Not a real cost of the change; see §3.

An earlier session recorded splitting the next two objects as well at 265 → 246 px for
584 more elements and "occluder not drawn there" 3 → 7. Not re-verified.

### Graph instrumentation (one bake, 31 ordering passes, nearest-vertex read)

| | |
|---|---|
| Sampled pairs | 1,320,490 |
| … with agreement < 0.80 | 16,445 (1.2%) |
| Largest stuck component at first break | 3,714 elements |
| Edges inside stuck components | 173,115, agreement < 0.80: 474 (0.3%) |
| Constraints given up | 1,496,495, agreement ≥ 0.95: 1,478,105 (98.8%) |

There is no weak class of constraint for the cycle breaker to give up first. The fault
had to be in measurement.

---

## 2. `apollo_018` under `apollo_003`, per frame

Captured ordering inputs for frames 0, 15, 30 (`out/surface-batch/capture/`, made by
`screen_surface_batch.py capture`, 169 s). Depth read at the centre of what the two
share; smaller is nearer the camera. Which rank is "in front" was fixed from the
edge-on-face assertions: `apollo_003`'s own edges are always drawn over it.

| Frame | Triangle read: thruster / body | Nearest vertex: thruster / body | Committed code draws `apollo_018` | Triangle read draws it |
|---|---|---|---|---|
| 0 | 31.21 / 31.46 | 31.21 / 32.37 | on top, 42/42 edges | on top, 42/42 |
| 15 | 32.80 / 33.03 | 32.81 / 32.97 | on top, 42/42 | on top, 42/42 |
| 30 | 34.98 / 35.24 | 34.98 / **34.25** | **under: fill and 0/41 edges** | on top, 41/41 |

Keyframes only, and exact geometry rather than the ray cast.

---

## 3. What the triangle read costs

The screening audit reported the bake 748 → 1,791 s, and that figure drove a day of
optimisation work. It was wrong.

### Ordering pass alone, replayed from the captured frames

`screen_surface_batch.py replay`, which fails if ranks or ordering stats differ.

| Run | Condition | Result |
|---|---|---|
| Unbatched vs batched surface read, 2 repeats | Normal load, both builds' frames held in one process | Ranks identical; medians 84.8 / 77.5 s, single runs from **27 s to 107 s** for the same code |
| Committed (nearest vertex), 1 repeat | Same | 22.7–26.6 s |
| `cProfile`, frame 0, `OMP_NUM_THREADS=1` | — | Triangle read 32.0 s, committed 30.9 s; `_overlap_centre` 28 s of both |
| Threads unrestricted, frame 0 | — | 33.5 s — thread count is not it |
| Frame 30, one thread | — | Triangle read 40.6 s, committed 39.0 s |

The triangle read costs the ordering pass about 4%. The 78–85 s medians were memory
pressure (2.3 GB free of 16 GB).

### Whole bakes, wall clock, worker pool

| Build | Run 1 | Run 2 (reverse order) |
|---|---|---|
| Committed | 390 s | 711 s |
| Triangle read | 657 s | 975 s |

Same code varied by 1.8×; stages the change does not touch (chaining 44 → 182 s,
sampling 180 → 428 s) swung as much as ordering did. Inconclusive by design.

### Whole bakes, CPU time, `cProfile`, one worker, frames 0–9

| | Committed | Triangle read |
|---|---|---|
| Total | 1,399 s | **1,166 s** |
| Ordering | 953 s | 866 s |
| `_overlap_centre` | 796 s | 727 s |
| `_depths_at` | 41 s (207k calls) | 16 s (80k calls, batched) |
| `_surface_tris` / `_surface_prep` / `_surface_read` | — | 0.2 / 0.2 / 0.6 s |

**The triangle read costs about 1 s per bake.** Why the audit said 2.4×: `audit.run`
forces `order_workers=1` so its diagnosis can watch the ordering pass, which makes
its reported bake 31 frames ordered serially — a measurement of machine load more
than of the code — and the two audits ran in different sessions, earlier ones in
parallel with each other.

---

## 4. Making ordering faster

Profiling showed `_overlap_centre` at 84% of ordering and 62% of the whole bake,
in code that predates the triangle read. Each change was checked by replaying the
three captured frames against a saved copy of the previous `order.py`; every row
below had **identical ranks and ordering stats**.

| Change | Ordering per frame, one thread (median of frames 0/15/30) |
|---|---|
| Reference (triangle read, batched depth reads) | 23.4 s |
| Fill pairs: one inside test per fill for the grid points of all its pairs (1.2M `_inside` calls → 80k) | 17.2 s |
| … edge crossings computed once per grid row, not per point | 10.3 s |
| Fill-vs-stroke clipping vectorised per pair | 10.1 s — overhead moved, not removed |
| … batched per fill: all segments of all its lines at once | **7.5 s** |

What remains per frame under the profiler: fill-pair inside tests 4.3 s (arithmetic),
line clipping 1.6 s, graph and cycle breaking ~2 s. No hotspot left.

### Confirmed on a bake: CPU time, no profiler, one worker, frames 0–9

| Stage | Before | After |
|---|---|---|
| Sampling | 31.8 s | 30.7 s |
| Sizing | 9.7 s | 9.5 s |
| Chaining edges | 11.7 s | 11.6 s |
| Resolving occlusion | 172.9 s | **57.4 s** |
| Reducing keyframes | 1.4 s | 1.2 s |
| **Total** | **229.6 s** | **112.2 s** |
| Elements | 9,337 | 9,337 |
| Depth + opacity track hash | `bd8ab0bc00d9b07b` | `bd8ab0bc00d9b07b` |

Committed in `20c2f71` with the triangle read.

---

## 5. Triangle read, with and without splitting `apollo_003`

Screening audit, 13 September, `20c2f71`, run one arm at a time with nothing else of
ours on the machine. `split_span_pct = 0` in both; the split arm forces `apollo_003`
out of merging through the harness (`screen_surface_batch.py audit --arm split`).

| | Triangle read, nothing split | Triangle read, `apollo_003` split |
|---|---|---|
| Wrong pixels | 289 | **214** |
| Rate | 3.21% | **2.38%** |
| Errors | 68 | **41** |
| Measured wrong | 31 | **15** |
| Order violated | 30 | **20** |
| Occluder not drawn there | 3 | 3 |
| No constraint | 2 | 1 |
| Coverage edge | 2 | 2 |
| Errors involving `apollo_003` | 24 (170 px) | **7 (109 px)** |
| Elements | 18,324 | 18,338 (+14) |
| Bake (serial ordering) | 406 s | 423 s |
| Whole audit | 928 s | 713 s |

Wrong pixels between `apollo_003` and each part, by object:

| Part | Nothing split | Split |
|---|---|---|
| `apollo_002` | 97 | 80 |
| `apollo_013` | 39 | 19 |
| `apollo_024` | 24 | 6 |
| `apollo_018` (all pieces) | 10 | 4 |

The nothing-split arm reproduces §1 exactly (289 px, 68 errors, 31 / 30), so the audit
is deterministic and the ordering speed-up changed nothing it can see. The whole audit
now takes 12–16 minutes, not 40: the ray cast of 9,000 pixels takes about two seconds,
and the time was the serial bake and the diagnosis.

**Reading.** Splitting still earns its place against the triangle read: a quarter of
the wrong pixels and 40% of the errors gone, for 14 elements and ~4% more bake. What
it fixes is no longer the depth read. A merged body has one rank per frame, and a part
that sits in front of one face of it and behind another — `apollo_002` wraps the
module — cannot be right with one rank whatever depth is read. That is granularity.

**So `split_span_pct` is not removed — but its selector is not justified either.** The
50% threshold has no measured knee (58.2 / 51.7 / 42.6% on this model). Which bodies
to draw face by face needs a criterion that is not a tuned share. Open.

---

## 6. Choosing which bodies to split

Background, because "splitting" is easy to misread:

- **Splitting changes drawing, not the mesh.** A merged object is one filled silhouette
  (`name/m`) with one paint rank per frame. A split object is drawn as one fill per face
  (`name/f<n>`), each with its own rank — how every object was drawn before merging.
- **The triangle read applies only to merged objects.** Their front-facing triangles are
  carried to the ordering pass and never drawn. Face fills don't need it: a plane fitted
  to a flat face is already exact, which is why splitting used to fix the corner-depth
  fault as a side effect (446 → 265 px before the triangle read existed).
- **What is left is granularity.** The triangle read makes every constraint against a
  merged body correct, but they still collapse into one rank. A part in front of the body
  everywhere, or behind it everywhere, is then drawn right. A part that wraps the body —
  in front of some faces, behind others — produces constraints no single rank satisfies;
  they cycle, the breaker gives one up, and pixels come out wrong. Only more ranks fix
  that. §5 measures it: 289 → 214 px.
- **Cost:** 14 more elements for `apollo_003`, ~4% bake. Splitting everything would undo
  what merging saves, so selection matters.

Options considered on 13 September:

| Option | For | Against | Status |
|---|---|---|---|
| `split_span_pct` (≥50% depth share and another object inside that range) | Automatic | A proxy with no knee: 58.2 / 51.7 / 42.6% on Apollo–Soyuz | Committed as experiment, default 50% |
| Per-object toggle set by the artist | Predictable; no threshold; artists already own geometry decisions | Needs the artist to know which bodies interleave | Not chosen |
| Split on evidence of interleaving, decided per clip | Tests the actual failure; no threshold | Must be built and audited; must not flip a body between frames | **Handed to another agent** |

---

## 7. Interleaving selector, first attempt

Built 13 September by another agent from the §6 hand-off, kept entirely in `scripts/`
(the shipping add-on is unchanged): `interleave_experiment.py` (the rule),
`interleave_lab.py` (fixtures and a viewable lab scene), `test_interleave.py`,
`check_interleave_evidence.py` (ray-cast check of saved witnesses), and an
`--arm interleave` in `screen_surface_batch.py` (modes `select`, `bake`, `audit`).

### The rule as built

- During sampling, record every object's front-facing triangles per frame, from
  Blender's own tessellation (so concave n-gons are exact).
- **After** merge feasibility is settled — outline gap tests and fallbacks — so a body
  that will be drawn face by face anyway is several independently ranked faces, not one
  element. (An earlier arm selected before that and picked 59 bodies, 18 of which later
  failed to merge; abandoned, `out/interleave/audit-any`.)
- For every merged body B and every other element P overlapping it in a frame, rasterise
  both front surfaces at **every output pixel centre** (triangle interpolation, inverse
  depth for perspective, nearest surface where front faces overlap). Count pixels where
  P is nearer than B by more than `resolve`'s tolerance (`max(1e-4, span·1e-4)`), and
  pixels where it is further.
- If both counts are non-zero **in the same frame**, select B — and P's object too if P
  is merged. One frame is enough; the choice holds for the whole clip.
- Re-sample the clip with the selected bodies in `no_merge`. `order.resolve` is not
  changed.

### Controlled scene

`test_interleave.py`, 14 checks, passing. `--legacy` runs the same cases through
`_deep_bodies` at 50% and **fails 2**: it misses the stepped body and splits the plate.

| Fixture | Expected | 50% depth share | Interleaving rule |
|---|---|---|---|
| Stepped body (faces at depth 10 and 14) with a bracket at 12 across both | split the body | nothing | **stepped body** |
| Sloping plate with a flush bolt | nothing | **plate** (wrong) | nothing |
| Two separate boxes | nothing | nothing | nothing |

Also pinned: signs from different frames never combine; opposite signs against two
separately ranked faces are not a contradiction; hidden rear faces and touching inside
tolerance produce no evidence; drawing the stepped body's faces separately is
sufficient (`resolve` ranks left < bracket < right).

### Apollo–Soyuz selection

| | |
|---|---|
| Bodies selected | **39** |
| Contradicting (body, part, frame) rows | 6,579, of which 725 had both sides merged (both split) |
| Selection CPU | 63.9 s |
| Largest minority side (pixels) | `apollo_002` / `apollo_003` 5,333; `apollo_000` 1,918; `apollo_013` 821; `dock_001`/`002` 199; `dock_003` 145; `dock_000` 144; `soyuz_049`/`037` 136 |
| Bodies whose minority side never exceeds 16 px | **25**, 10 of them in all 31 frames |

### Screening audit

| | Nothing split | `apollo_003` by hand | **Interleaving rule** |
|---|---|---|---|
| Wrong pixels | 289 | **214** | 342 |
| Rate | 3.21% | 2.38% | 3.80% |
| Errors | 68 | 41 | **36** |
| Measured wrong | 31 | 15 | 15 |
| Order violated | 30 | 20 | 19 |
| No constraint | 2 | 1 | 1 |
| Occluder not drawn there | 3 | 3 | 0 |
| Errors involving `apollo_003` | 24 (170 px) | 7 (109 px) | 10 (305 px) |
| Elements | 18,324 | 18,338 | **27,809 (+52%)** |
| Ordering CPU per frame (serial) | ~7.5 s | — | 11.3–14.2 s |
| Bake CPU (serial ordering) | — | — | 978 s |
| Whole audit | 928 s | 713 s | 3,004 s |

Worst errors:

| `apollo_003` by hand | px | Interleaving rule | px |
|---|---|---|---|
| `apollo_002/m` under `apollo_003` — measured wrong | 71 | **`apollo_003/f1` under `apollo_002`** — order violated | **261** |
| `apollo_002/m` under `apollo_001` — order violated | 28 | `apollo_003/f1` under `apollo_001` — order violated | 13 |
| `apollo_003/f5` under `apollo_013` — order violated | 19 | `apollo_002/f11` under `apollo_003` — measured wrong | 12 |
| `apollo_000/m` under `apollo_002` — measured wrong | 14 | `apollo_002/f16` under `apollo_003` — measured wrong | 6 |

### Reading

- **Worse overall, and 52% more elements.** Verdict: not shippable as built.
- **One error is 261 of the 342 px.** Without it the rule's errors total 81 px against
  214 for the hand split, so more ranks do fix interleaving. What went wrong is a single
  cycle break: with *both* `apollo_003` and `apollo_002` split, one large module face
  (`f1`) sits in a cycle with `apollo_002`'s faces, and the breaker gave up the
  constraint that mattered. Splitting removes cycles and creates new ones; one bad break
  costs more than the rest saves.
- **The selection is too broad for its cost.** 25 of 39 bodies contradict on ≤16 px.
  The likely cause (reasoned, not yet measured): flush-mounted parts whose surface dips
  a hair into the hull along the contact line — geometrically two-sided, but a sliver
  splitting cannot repair.
- **It splits both sides of a pair** where one would give the pair enough ranks.

### Open questions, in order

1. Split one side of a contradicting pair, not both — which one, by a rule.
2. Tell a contact seam from a wrap without a pixel cutoff.
3. Why `apollo_003/f1` still loses to `apollo_002` after both are split: a genuine
   cycle, or a face whose own depth is mis-measured?

---

## 8. Preview bake with the confirmed adjustments

13 September, `scripts/bake_confirmed.py`: the surface read and batched ordering
(`20c2f71`), `apollo_003` drawn face by face (the best audited selection, §5), and
`split_span_pct = 0` because the automatic selection is unaudited. Settings as the
screening audit.

| | |
|---|---|
| Bake, worker pool, wall clock | 260 s |
| Elements | 18,338 — the same count as the audited split arm |
| Drawn per frame | 10,603–11,107 |
| Output | `out/preview-confirmed/apollo-soyuz-confirmed.lerpa_cam.preview.html` (8.7 MB), `.svg` (11.7 MB) |
| Preview render, 1280×900 | 14–15 ms, 68–71 fps, no console errors |

Checked by eye in the preview: the `apollo_018` thruster clusters sit on top of the
service module through the clip, including the last frame, where the nearest-vertex
read drew them underneath.

---

## 9. Merge fold test and absent slots: regression run

14 September. Old = `5d79d72` (slot fix, nine-probe fold test, `SPECK` cut); new = the
working tree (every-segment fold test, no size cut in `walk_pieces`, absent slots
parked on a vertex). Each file baked with its own saved settings, worker pool;
`check_geometry.py` exhaustive at the sampled frames; `check_outline_segments.py
--subframe 0.5`.

Controlled scene first: `test_fold_boundary.py`, dock_001's profile at five angles —
old code leaves 65 px uncovered at one; new covers all five, no spill. On the real
`dock_001`, frames 1–31, the walk's fill against the union of front faces: old up to
1,043 px uncovered; new 0 px uncovered and 0 spill in every frame checked.

| | Apollo old | Apollo new | Voyager old | Voyager new |
|---|---|---|---|---|
| Merged fills | 52 | 52 | 271 | **269** |
| Merge refusals | 39 | 39 | 51 | 53 |
| … uncarried | 17 | 16 (`soyuz_016` merges) | 26 | 28 (`part_054.001`, `part_057.001`) |
| Elements | 18,358 | 18,361 | 8,502 | 8,586 |
| `check_geometry` offenders | 29 (worst 35.0 px, `apollo_018.011/m`) | **0** (worst 1.04) | 0 (worst 1.10) | 0 (worst 1.10) |
| Segments off between keyframes | 53 | **27** | 267 | 267 |

A first new run, with absent slots parked on the nearest frame's screen point, gave
Voyager 50 offenders, all `part_012.004/m`, worst 252.8 px — the parked point left
behind by a moving part. Parking on a vertex fixed it; the table is the second run.

Verdict: kept. The one cost is two Voyager parts that now keep a small real piece,
fail the carry test and draw face by face (+84 elements); nothing draws worse. The
Apollo 35 px offender that predated this work was the same parked-point fault.

Wall-clock times (Apollo 830 → 299 s, Voyager 2,112 → 1,160 s) ran on a loaded
machine at different times and are not evidence either way.

---

## 10. Carry test judged ring by ring — reverted

14–15 September. Why seven of sixteen identical `apollo_018` thrusters are refused
(`merged_uncarried`, 11–15 px against 0.05 px face by face).

**Measured cause.** `judge_carry` compares the drawn outline's midpoint with
`silhouette_of`, which uses `walk_loop` — one ring, bridged, or None past two pieces.
A multi-ring outline is read as one closed loop, ring ends joined. So:

| Thrusters | Frames (of 31 mid-frames) where `walk_loop` gives a ring | Result |
|---|---|---|
| refused: `.001 .003 .005 .007 .008 .010 .015` | 12–15 | judged, against a bridged ring, with the jumps between rings counted |
| merged: `apollo_018 .002 .004 .006 .009 .012 .014` | 0–1 | effectively never judged |
| merged: `.011`, `.013` | 8, 13 | judged in some frames |

On a perfect three-ring outline the old measure scores 30 px
(`test_carry_pieces.py`, saved with the patch).

**Tried.** Judge multi-ring outlines ring by ring against `walk_pieces`: each drawn
ring against the nearest true piece and each true piece against the nearest drawn ring;
a collapsed slot is not a ring. Patch: `out/regress-merge/carry-ring-by-ring.patch`.

| | Apollo `92e2d00` | Apollo ring-by-ring | Voyager `92e2d00` | Voyager ring-by-ring |
|---|---|---|---|---|
| Merged fills | 52 | **44** | 269 | **261** |
| Elements | 18,361 | **20,820** | 8,586 | 8,887 |
| `check_geometry` offenders | 0 | 0 | 0 | 0 |
| Segments off between keyframes | 27 | 28 | 267 | 235 |

Newly refused on Apollo: `apollo_018`, `.002`, `.011`, `.012`, `.013`, `dock_001`,
`dock_006`, `soyuz_009`; on Voyager: `part_005.001`, `part_012.004`, `part_012.008`,
`part_025.003`, `part_026.001`, `part_027.001`, `part_044.001`, `part_078.001`. None of
the seven refused thrusters merged.

**Reading.** The old test was not refusing good outlines by mistake so much as not
judging most multi-ring outlines at all. Judged, they fail. What makes them fail is not
yet measured; the likely candidate (reasoned) is a piece appearing or vanishing between
keyframes, whose ring grows from a parked point, compared against a face-by-face cost
that takes no account of faces appearing. Reverted: it removes merges, including the
part just fixed, without a measured visual gain.

**Open, and a decision.** Either the representation of appearing pieces has to carry
(then this test is right and should come back), or the comparison has to charge face
fills for appearing too. Measure per object what dominates the ring-by-ring cost first.

**Tried again on top of `4b4bf09`** (the one-piece-frame fix, §11), on the reasoning that
the slivers made multi-ring outlines genuinely wrong and so earned their refusals. They
did not explain this test's refusals: the results are the same as the first attempt.

| | Apollo `4b4bf09` | Apollo + ring-by-ring | Voyager `4b4bf09` | Voyager + ring-by-ring |
|---|---|---|---|---|
| Merged fills | 57 | **44** | 269 | **261** |
| Elements | 16,905 | 20,820 | 8,660 | 8,887 |
| `check_geometry` offenders | 0 | 0 | 0 | 0 |
| Segments off between keyframes | 28 | 28 | 256 | 235 |

Newly refused on Apollo: `apollo_001`, `apollo_018`, `.001`, `.002`, `.003`, `.007`,
`.010`, `.011`, `.012`, `.013`, `dock_001`, `dock_006`, `soyuz_009`. Reverted again. The
next step is unchanged: measure, per refused object, which ring and which frame the
ring-by-ring cost comes from, before judging whether the test or the outlines are wrong.

---

## 11. One-piece frames of a multi-ring fill, and the dock joint

15 September.

**Sliver rings on `apollo_018.013`.** Merged as three subpaths (49 / 7 / 9 points), it
drew two small closed slivers ~27 px from its bell in frames 9–15, where only one bell
shows. Traced by hooking the bake's own `_sample_outline_pieces`: it was called only in
frames with two or more pieces. A one-piece frame took the single-ring path, which wrote
one outline across all 65 slots, and the player closed each slot range as a ring of its
own. The slivers lie on real edges, so `check_geometry` and the segment check both
passed them. Fix: once an element carries several subpaths, every frame is written as
subpaths; the measuring pass, which has no subpath sizes yet, is unchanged.

Found on the way: a subpath slot sized to zero points crashed `_sample_outline_pieces`
(`pts[0]` on an empty ring) on a 17-frame range; the absent-slot anchor now falls back
to the piece's first vertex. Full-length bakes had not reached it.

Regression run, `92e2d00` against both fixes, each file's own settings:

| | Apollo `92e2d00` | Apollo fixed | Voyager `92e2d00` | Voyager fixed |
|---|---|---|---|---|
| Merged fills | 52 | **57** | 269 | 269 |
| Newly merged | | `apollo_001`, `apollo_018.001 .003 .007 .010` | | `part_081.001` |
| Newly refused | | — | | `part_026.001` |
| Elements | 18,361 | **16,905** | 8,586 | 8,660 |
| `check_geometry` offenders | 0 | 0 | 0 | 0 |
| Segments off between keyframes | 27 | 28 | 267 | **256** |

`apollo_018.013` ring by ring against `walk_pieces`, frames 0–30 every third: every
drawn ring within 0.9 px of a true piece; frames 9, 12, 15 now draw its two empty slots
as points. Kept.

So four of the seven refused thrusters (§10) were refused for the slivers themselves:
their carry cost was measured on outlines that really were wrong between keyframes.
`.005`, `.008`, `.015` are still refused.

**Grey spike at the dock joint — not a bug.** At 0.83 s, over x 1165–1230, y 585–660,
the ray cast and the bake's fills agree to within a pixel: 13 px where only the bake
fills `dock_008` and 10 px where only the truth has it, all in one 5 × 31 px strip — an
edge a pixel apart, not a shape. The spike is `dock_008`, a thin part at the joint,
drawn face by face; "Mark merged" colours merged parts, so it reads grey among them.

---

## 12. What is left between keyframes

15 September, on `4b4bf09`.

### Where the ring-by-ring carry cost comes from

Every merged multi-ring part on Apollo, at every mid-frame: the drawn outline against
`walk_pieces` at that subframe (`ringcost.py` in the session scratchpad). Two kinds of
cost, cleanly separated by the piece counts at the two keyframes and between them:

| Kind | Examples (worst, mid-frame, pieces before/mid/after, drawn ring) |
|---|---|
| **A piece appears or vanishes across the gap** — the biggest costs | `apollo_001` 78.3 px, 7.5, 1/1/2, 2.6 × 28.8 px · `dock_001` 39.4 px, 0.5, 2/3/3, 13 × 40 px · `apollo_018.001` 27.0 px, 7.5, 2/1/1, 4 × 5 px · `.007` 24.1 px, 1/1/2 · `.010` 23.6 px, 2/1/1 · `soyuz_009` 15.7 px, 1/1/2, < 1 px |
| **The main ring drifts, every piece present** | `apollo_018.012` 8.0 px · `.006` 5.2 px · `.014` 4.4 px · `soyuz_011` 4.2 px |

An appearing piece is a ring growing out of its parked vertex, so halfway it is a
half-grown ring where the truth has nothing. Linear interpolation cannot make it right
at the midpoint. The face-by-face cost it is compared with (`face_cost`) measures vertex
motion only and never charges a face for appearing, though a fading face has the same
half-frame problem — so on appearance events the ring-by-ring test is not comparing like
with like. That, not the slivers, is why it refuses so much (§10).

### The segment count

`check_outline_segments.py --subframe 0.5` graded quarter points that lie off the
canvas: Voyager's worst offenders were `part_001.001` chains, 20 px off on segments
4,700–4,900 px long. Now only on-canvas samples count.

| | Apollo | Voyager (before → on-canvas only) |
|---|---|---|
| Offending (element, mid-frame) | 28 | 256 → **198** |
| > 5 px | 0 | 27 + 15 → **2** |
| 3–5 px | 1 | 27 → 21 |
| 2–3 px | 8 | 58 → 54 |
| 1.25–2 px | 19 | 129 → 121 |

The two over 5 px: `part_081.001/m` 15.8 px on a **zero-length** segment (an empty slot
collapsed to a point, which draws nothing) and `part_226.001/m` 5.1 px. The rest is
merged-outline shape drift of at most ~4.5 px (most often `part_024.001`, 58 mid-frames
at ~4 px; Apollo's worst is `soyuz_013` at 3.05 px) — against 1.6 px strokes, not a stray.

### Verdict

The visible artefact left in merged outlines is the half-grown ring at a piece appearing
or vanishing between keyframes. Everything else measured is ≤ 5 px drift. Which way to
take the appearance question is a decision, recorded in bugs.md's Open list.

---

## 13. An area-based carry decision — measured, not built

15 September. The idea: judge merged against face-by-face with one yardstick, wrongly
covered pixels between keyframes, charging a face that appears or vanishes at the
opacity the player fades it with; merge if merged ≤ faces + outline length × `tol_px`.
The expectation was that `dock_001`, the one part where the half-grown ring is visible
(user, 2026-09-15), would fall back to faces and the thrusters would stay merged.

Measured on the current preview, every merged multi-ring part, every mid-frame
(`area_carry.py`, session scratchpad): truth = its front faces at the subframe; merged =
the preview's interpolated outline; faces = each face at its interpolated position,
alpha 0.5 where it faces the camera at one keyframe only.

| Part | Merged wrong px (clip) | Faces wrong px | Allowance | Decision |
|---|---|---|---|---|
| `dock_001` | 1,724 | 157 | 26,642 | merge |
| `apollo_001` | 2,614 | 1,094 | 13,315 | merge |
| `apollo_018` … `.014` (13 thrusters) | 346–595 | 40–106 | 3,330–4,308 | merge |
| `soyuz_029` / `030` | 11,181 / 10,978 | 3,050 / 3,274 | 54,292 / 58,189 | merge |
| `soyuz_006`, `007`, `009`, `011`, `dock_006` | 115–932 | 9–291 | 822–10,452 | merge |

Merged is 5–15× the faces' error on every part, and `dock_001` (11×) is not an outlier.
With the allowance everything merges; without it everything falls back. Area does not
separate the part where the artefact is seen, because what is seen on `dock_001` is an
outline *stroke* — an extra oval line drawn inside the part for half a frame — and the
area it gets wrong is small. Not built.

What would have to be measured instead: stroke drawn where no edge is, between
keyframes (as `check_outline_segments.py --subframe 0.5` does for points on edges, but
counting length on the canvas that lies on no projected edge).

### False outline length — measured

Same parts, same mid-frames (`false_stroke.py`, session scratchpad): the preview's
interpolated rings sampled every 0.5 px on the canvas; a sample farther than
`tol_px` × 1.25 from the part's true outline at the subframe is false line.

| Part | False line over clip | Worst mid-frame | … on appearing/vanishing rings |
|---|---|---|---|
| `dock_001` | **216.5 px** | 18.5: 90.5 px | 0 |
| `apollo_001` | **204.0 px** | 17.5: 90.0 px | 0 |
| `apollo_018.001` | 136.0 px | 5.5: 56.5 px | 8.0 |
| other thrusters | 5–96 px | 3.5–29 px | 0–6.5 |
| `soyuz_011` | 56.5 px | 8.5: 25 px | 0 |
| `soyuz_006`, `007`, `009`, `029`, `030`, `dock_006` | 0 | — | — |

This separates far better than area, and the two worst frames were cropped from the
player. `dock_001` at 18.5: the body's top edge bends out of shape between the cone and
the body — **visible**, and not the appearing oval (the main ring's slots sliding, not a
piece appearing). `apollo_001` at 17.5: the false line is **behind the service module**
and cannot be seen. So the measure finds the visible fault but also counts line that
nearer parts cover; a decision would have to count only false line that is on top.

### Visible false line, merged against face by face

`false_visible.py` (session scratchpad): a false sample counts only where no fill painted
after it in that bake covers it. Merged = the current preview; face by face = the
`apollo-carry2` bake, where the ring-by-ring test refused these parts, so they are drawn
as faces with their edge strokes (judged against every projected edge of the part).

| Part | Merged: visible (raw) | Worst mid-frame | Faces: visible (raw) |
|---|---|---|---|
| `dock_001` | **214.0** (216.5) | 18.5: 88.5 px | 3.0 (3.0) |
| `apollo_018.001` | 119.0 (136.0) | 5.5: 56.5 px | 0 (0) |
| `apollo_018.003` | 66.5 (96.0) | 5.5: 28.0 px | 0 (0) |
| `soyuz_011` | 56.5 (56.5) | 8.5: 25.0 px | merged in both bakes |
| `apollo_018.012` | 29.0 (29.0) | 27.5: 29.0 px | 0 (5.5) |
| `apollo_018`, `.002`, `.013` | 22.5–26.0 | ≤ 22.5 px | 0 |
| `apollo_018.007` | 6.0 (73.0) | 4.5: 6.0 px | 0 |
| `apollo_001`, `apollo_018.010`, `.011` | **0** (235.0, 92.0, 48.5) | — | 0 |
| `dock_006`, `soyuz_009` | 0 | — | 0 |

Visibility removes `apollo_001` entirely (all 235 px behind the service module) and most
of `.007`, `.010`, `.011`. `dock_001` is the clear worst, at 1.8× the next part — which
the user did not report. Face by face draws essentially no visible false line on any of
them. So a like-for-like comparison has faces at ~0 everywhere, and the decision reduces
to how much visible false line a merged outline may draw: a threshold between 119 and
214 px would split `dock_001` from `apollo_018.001`, and nothing measured here justifies
one number over another.

### The `dock_001` bend at 18.5

From keyframe 18 to 19 the main ring's slots 20–24 move 72–105 px while the ring's median
slot moves 4.2 px: points on the cone's slope ((1021, 523) … (1059, 496)) are re-assigned
to the body's top edge ((1088, 498) … (1164, 501)). Halfway, they cut across the corner
where cone meets body. Smaller slides of the same kind: 16 → 17, 36 px; 20 → 21, 30 px.
This is the slot re-assignment class of §2–3 of experiments.md (arc-length matching kept;
screen-distance matching, edge-anchored carrying, area placement and substeps rejected),
on a new shape. Not attempted here. It is most of `dock_001`'s visible false line; the
half-grown ring the user noticed is the smaller part of it.

---

## 14. Merge False Outline limit

15 September. The user chose option 2 of §13 (relayed by the coordinating session): a
limit on visible false outline, as a bake setting in the Geometry panel next to Merge
Slack, not a per-object property. This is a **chosen threshold**, explicitly approved as
the one exception to the no-tuned-epsilon rule; nothing measured derives the number.

**What the bake measures.** In the pass that already judges carrying (`judge_carry`), for
every merged fill with more than one subpath: the drawn outline halfway between two
keyframes (mean of the two frames' written slots, split by subpath) is sampled every
0.5 px on the canvas; a sample farther than `tol_px` × 1.25 from the true silhouette's
pieces at the subframe (`walk_pieces`) is false. Each false sample takes the depth of the
nearest true-outline vertex and is ray-cast at the subframe (`scene.ray_cast`): hidden if
the first hit is another drawn object nearer than that depth. The part's own surface and
objects the bake does not draw never hide it. Visible length is summed over the clip
(`plan.false_seen`) and reported in the stats as `merged_false_line`. Over
`merge_false_px` the part joins `merged_uncarried` and is drawn face by face in the pass
that already exists for those. Zero switches it off. Single-ring fills are unaffected
(the gap test covers them). Lab guard: `scripts/test_false_outline.py` (10 checks —
half-grown ring, collapsed ring, off-canvas, corner cut, nearer/farther/own/undrawn
occluder).

**The bake's scores against the offline measure.** Ray-cast truth rather than the
player's paint order, so they differ where paint order and 3D disagree:

| Part | In bake (ray cast) | Offline §13 (paint order) |
|---|---|---|
| `dock_001` | 211.0 | 214.0 |
| `apollo_018.001` | 117.0 | 119.0 |
| `apollo_018.003` | 78.5 | 66.5 |
| `apollo_001` | 73.5 | 0 |
| `apollo_018.013` | 66.0 | 24.0 |
| `soyuz_011` | 56.0 | 56.5 |
| `soyuz_010`, `048`, `001`, `apollo_018.015` | 624, 268, 261.5, 116.5 | already refused by Merge Slack |

Voyager, parts still merged at 4b4bf09: `part_043.001` 472.0, `part_005.001` 391.5,
`part_078.001` 204.0, `part_027.001` 77.5, `part_044.001` 27.0, the rest ≤ 24.5
(`part_022.001` 1,234.5, `003.001` 219.5, `080.001` 153 were already refused).

**How the default was chosen.** Across both models the parts that stay merged top out
at 117 px (`apollo_018.001`) below the gap and resume at 204 px (`part_078.001`) and
211 px (`dock_001`) above it. 160 is the middle of that gap (arithmetic 160.5, geometric
154), so it holds `dock_001` out with ~50 px margin on either side of the measured parts.
It is a clip total, so a longer clip accumulates more; the number describes these two
clips (30 and 59 frames), nothing more general.

**Regression, old (4b4bf09 code, unchanged at HEAD) against new at 160.**

| | Apollo old | Apollo new | Voyager old | Voyager new |
|---|---|---|---|---|
| Merged fills | 57 | 56 | 269 | 266 |
| Newly refused | — | `dock_001` | — | `part_005.001`, `043.001`, `078.001` |
| Elements (strokes / fills) | 16,905 (7,328 / 9,577) | 17,462 (7,310 / 10,152) | 8,660 (6,429 / 2,231) | 8,579 (6,356 / 2,223) |
| `check_geometry` offenders | 0 | 0 | 0 | 0 |
| On-canvas segment pairs (`--subframe 0.5`) | 28 | 26 | 198 | 173 |

With the limit set to 1e9 (measuring only, nothing refused) both models bake to the
old element counts, 0 offenders and 28 / 198 segment pairs: the measurement alone
changes nothing. Visible false line on the new Apollo preview (`false_visible.py`):
`dock_001` 3.0 px face by face (was 214), every other part identical to §13.

**Cost.** Profiled with process time on a 15-frame Voyager range: `judge_carry` 13.2 s →
17.4 s cumulative (piece walks 2.6 s, sampling 1.4 s, ray casts 1.1 s, plus one extra
`frame_set` per frame with false line). On Apollo's full clip the piece walks were
16.8 s and sampling 7.6 s. Whole-bake CPU times in this session are load-dominated —
the unchanged old code took 225 s on Apollo on 14 September and 399 s today — so no
whole-bake speed figure is claimed.

What is not addressed: the slot sliding that makes most of `dock_001`'s false line
(§13) still happens on any part under the limit, and a part over it pays face-by-face
element counts instead (`dock_001`: +575 fills).

---

## 15. Drawing order: the blinking thruster and the see-through parts

15–16 September. Both reported by the user on the preview: `apollo_018.003` disappears
for a moment mid-animation, and some parts look see-through.

**Audit setup.** `audit.run(budget=3000, diagnose=True)` on the Apollo blend with its own
saved settings (merge fills on, Merge False Outline 160, `order_workers` forced to 1 by
the audit), all 31 frames, 93,000 sampled overlap pixels. Deterministic: the same code
gives the same count. Replays use the ordering inputs captured from the same bake
(`capall/frame_NNN.pkl`, session scratchpad) and `order.resolve` run standalone.

**The blink.** The preview's depth rank of `apollo_018.003/m` goes 2051 → 4667 → 163
over frames 14–16 while its own edge stays near 2580: one frame under
`apollo_003/f15`. The replay reproduces it exactly. In frame 15 the two depth ranges
miss by 0.0046, so the pair is settled as disjoint and weighted by that gap — the
weakest edge in this cycle, which the breaker spends at break 2:

    apollo_018.003/m  behind apollo_014/m     0.116  measured
    apollo_014/m      behind apollo_014/e35   5.37   asserted (fill under its own edge)
    apollo_014/e35    behind apollo_003/f15   0.025  measured, line against fill
    apollo_003/f15    behind apollo_018.003/m 0.0046 disjoint ranges  <- spent

**The see-through parts.** Baseline audit of 9373c59: 1,623 wrong px, 1,071 of them
`order violated` — the right constraint existed and the breaker spent it. The largest:
`apollo_003/f5` over the mount `apollo_013` (209 px, every frame), `apollo_003/f1` over
`apollo_002` (195, frames 4–5), `apollo_002/f10` over `apollo_003` (183, frames 0–7),
`apollo_002/f22` over `apollo_001` (129). Tracing the cycle that closed each spent edge
(`classify.py`, errors ≥ 3 px) gives the same shape almost every time:

    fill A  behind its own edge e    asserted
    edge e  behind fill B            measured where the line is
    fill B  behind fill A            measured where the fills overlap   <- spent

e.g. frame 29: `apollo_003/f5` < `apollo_013/m` spent at 0.126 against
`apollo_018.015/e327` < `apollo_003/f5` at 0.161. Both measurements are true in different
places (the geometry crosses); the weights compare depth gaps, which say nothing about
cost. Spending the fill pair paints a surface over a part; spending the line pair draws a
line over a surface that hides it.

**Fix: spend the cheapest pair on screen.** In the breaker's sort, each candidate edge is
ranked by what breaking it would paint wrong — the screen the two elements share: the
overlap area for two fills, the length of line inside the fill times the stroke width for
a line against a fill — and only then by weight. Assertions are still drawn from last.
The shared area falls out of `_fill_fill_centres` (the grid cells both cover, scaled to
the box they span) and the shared length out of `_line_spans`, so both come from work the
pass already does. Pairs settled by disjoint depth ranges are never sampled, so theirs is
measured lazily at the first cycle break, for the edges inside a strongly connected
component only (`_share_certain`) — components only split from there, so that answers
every later break. Strokes now carry their `width` into the ordering pass
(`bake.py`, `chain.py`). Lab: `scripts/test_order_spend_cost.py`.

| Ordering | Apollo | Fill | Stroke | Violated | Voyager | Fill | Stroke | Violated |
|---|---|---|---|---|---|---|---|---|
| 9373c59 (baseline) | 1,623 | 1,378 | 245 | 1,071 | 3,018 | 2,106 | 912 | 1,460 |
| Disjoint pairs above every measured edge | 1,978 | 1,780 | 198 | 1,412 | — | | | |
| Disjoint pairs weighed by measured separation | 1,589 | 1,348 | 241 | 1,007 | 2,730 | 1,857 | 873 | 1,073 |
| Lines spent before fills | 1,022 | 714 | 308 | 253 | 3,132 | 1,440 | 1,692 | 1,603 |
| Lines first + measured separation | 1,023 | 715 | 308 | 253 | 3,118 | 1,439 | 1,679 | 1,601 |
| **Cheapest on screen** | **1,012** | **739** | **273** | **284** | **2,105** | **1,101** | **1,004** | **648** |

Wrong pixels of 93,000 sampled on Apollo (31 frames) and 180,000 on Voyager (60 frames).
The chosen rule is the only one that improves both: −38% and −30%.

Apollo, largest changes against the baseline: `apollo_003/f5` over `apollo_013` 209 → 0,
`apollo_002/f10` over `apollo_003` 183 → 2, `apollo_002/f22` over `apollo_001` 129 → 3,
`apollo_003/f1` over `apollo_002` 195 → 76, `apollo_002/f22` over `apollo_000` 61 → 0,
`apollo_003/f5` over `apollo_018.013` 62 → 0, the blink 9 → 0. Worse: `apollo_013/m` over
`apollo_003` 0 → 85 and `apollo_018.013/m` over `apollo_003` 0 → 27 (`measured wrong`:
with the mount now on top, the pixels where the hull really is nearer become the error),
`apollo_000/m` over `apollo_002` 67 → 88. Voyager: `part_033.001/m` over `part_024.001`
153 → 0, `part_021.001/m` over `part_064.001` 121 → 0, `part_024.001/m` over four
neighbours 80/66/63/57 → 0; worse, `part_064.001/m` over `part_021.001` 103 → 136 and
`part_025.003/m` over `part_018.001` 50 → 71.

**Cost.** About a third more CPU in the ordering pass: on Apollo frame 15, 5.0 s → 6.5 s,
and frame 29, 5.0 s → 6.7 s (process time, repeated, standalone replay of the captured
frame). Cheap edges break fewer cycles per pass, so the breaker runs more often and
discards more: frame 15 goes from 31 breaks and 6,961 constraints dropped to 53 and
18,525; frame 29 from 28 and 8,968 to 70 and 29,140. The user accepted this cost on the
strength of the wrong-pixel counts (2026-09-16).

In the full bake `apollo_018.003/m` is above `apollo_003/f15` in every frame: no blink.

**Rejected on the way.**

- *Disjoint pairs weighted at span + gap*, like assertions (a fact that holds everywhere
  outranks a sample). Fixes the blink; Apollo 1,623 → 1,978, `dock_001` faces under
  `dock_002` +275 px. Kept out of the breaker's tenth, certain edges made it spend correct
  measured ones instead, and a spent disjoint pair usually costs nothing because seed
  order restores it.
- *Disjoint pairs weighed by their measured separation* rather than their range gap (the
  gap is the least the two could be apart, not how far apart they are where they overlap).
  Weighed lazily inside the components, bit-identical ranks to measuring all of them, at
  ~15–21k extra pair reads a frame against ~82k sampled. Apollo 1,623 → 1,589, Voyager
  3,018 → 2,730 — an improvement, but smaller than the rule kept, and it adds nothing on
  top of it (1,022 → 1,023). Not kept.
- *Lines spent before fills*, on the reasoning that a line is cheaper than a surface.
  Apollo 1,623 → 1,022, Voyager 3,018 → **3,132**. A chained outline is not cheap:
  `part_043.001/c3_0` drawn over `part_003.001` in all 60 frames cost 335 px where the
  fill pair the old rule spent overlapped by a sliver. This is what turned the rule from
  a kind into a price, and `test_order_spend_cost.py` pins the case.

**What remains.** Apollo 619 px and Voyager 803 px of `measured wrong`: faces spanning 3–5
units of depth that cross parts in front of and behind them — `apollo_003/f1`,
`apollo_002/f22`, `part_024.001/m` — where one depth per element cannot be right whatever
the breaker does. Frame 6's `apollo_002/f10` < `apollo_003/f1` is spent in a fill-only
cycle (`apollo_003/f1` < `apollo_000/m` 0.43 < `apollo_002/f10` 1.54 < `apollo_003/f1`
0.071), which no spending rule can fix. That is the deep-body splitting problem of §5–7,
still open.

---

## 16. Dashed rings: lines measured by their drawing points

16 September. After Merge False Outline drew `dock_001` face by face, the rings round the
docking module came out dashed (marked in `out/preview-a99a6ff/dashed_rings.png`): ring 1
`dock_001/c12_0` at the cone joint, ring 2 `dock_001/c310_0` at the Soyuz end (the line
first taken for `soyuz_005/c72_2` is solid; the dashed one lies 1.5 px beside it), and
ring 3 `soyuz_001/c142_0`, dashed before as well. The user's hypothesis, relayed: a seam
line ordered face by face against the faces around it.

**What the drawing order shows.** Replayed at frame 20 (0.83 s) and read out of the
preview at 0.79, 0.83 and 0.833 s: *no fill is painted over rings 1 and 2*, half a pixel
of ring 3. Ray cast: every sample of all three should show. So not paint order in the
ordinary sense.

**What does it.** Recoloured in the player (`dbg_sheet.png`: `dock_001` lines green,
faces blue): the dashed lines are the part's own lines, and they turn solid with the
player's seam fix off. That fix strokes every fill with its own colour to close the
hairline between abutting faces, half a device pixel past its edge. Each dash gap is a
face of the same part, painted after the line, whose boundary passes within 0.0–0.13 px
of it but which the line does not lie on — it meets the line at one vertex. One gap per
vertex, so the dashes are evenly spaced.

Those faces were painted after the line because the ordering measured the line **behind**
them by 0.007–0.05 (`tol` is ~0.003): real constraints, in the wrong direction. The faces
are triangles and read exactly. The lines are chains, and chains were handed to the
ordering pass with their *thinned* points (`dissolve_px`): between two kept points the
depth is a straight line, and a ring bowing toward the camera sags behind its chord —
ring 1's chain kept 7 of 21 vertices and read 35.29 where its face read the shared
vertex's 35.26. Rings 2 and 3: 11 and 9 kept points, same result.

**Fix, in two parts.**

1. *A chain is ordered along its vertex path, not its drawing points* (`chain.py`,
   `measure_path`). Drawn geometry is unchanged. Stroke points in the ordering pass
   5,906 → 7,671 on frame 20; ordering CPU 6.5 → 6.7 s.
2. *A face of the line's own part that meets it at a vertex and covers none of its length
   puts the line on top*, at the weakest weight (`tol`), as for any pair depth cannot
   separate (`order._touches`). Coordinates compare exactly: both come from the same
   projected vertex, so there is no tolerance. Breaking it costs a line-width square in
   the cycle breaker, not the nothing its shared length says: that is what the face then
   paints over the line.

Lab: `scripts/test_chain_depth.py` — an arc bowing toward the camera with triangles
touching it at single vertices (thinned: 8 of 11 over it; vertex path + touching rule:
0), a face sharing a vertex that really lies across the line in front (still covers it),
another part's vertices (never treated as touching).

**Rings at frame 20** (share of the line under a later fill or its seam stroke, pieces):

| | Ring 1 | Ring 2 | Ring 3 |
|---|---|---|---|
| a99a6ff | 39%, 13 | 16%, 11 | 11%, 9 |
| Vertex path only | 6%, 2 | 2%, 1 | 3%, 3 |
| + touching rule, free to break | 3%, 1 | 0 | 3%, 3 |
| **+ touching rule, priced** | **3%, 1** | **0** | **0** |

The one piece left on ring 1 is `dock_001/f93`, measured 0.08 in front a pixel away from
any shared vertex — a real depth difference.

**Nicks over the whole clip** (`nicks.py`, session scratchpad): length of every line
covered by a later-painted face of its own part that only touches it, all 31 Apollo
frames, same captures, each variant's ordering.

| Variant | Apollo nick length | Line-frames nicked | Voyager nick length | Line-frames |
|---|---|---|---|---|
| a99a6ff (thinned chains) | ≥ 64,941 ¹ | ≥ 22,353 ¹ | — | — |
| Vertex path only | 74,641 | 23,371 | 67,103 | 40,934 |
| + touching rule, free to break | 29,071 | 8,808 | 33,037 | 17,019 |
| **+ touching rule, priced (kept)** | **12,637** | **2,249** | **25,065** | **9,130** |

¹ A lower bound, not comparable: on thinned chains only faces meeting a *kept* point are
recognised as touching, so most of the faces that nicked the rings are not counted.

**Ray-cast audit** (same settings and budget as §15):

| Variant | Apollo | Voyager | Total |
|---|---|---|---|
| a99a6ff | 1,012 | 2,105 | 3,117 |
| Vertex path only | 1,006 | 2,101 | 3,107 |
| + touching rule, free to break | 1,012 | 2,055 | 3,067 |
| **+ touching rule, priced (kept)** | **1,041** | **2,055** | **3,096** |

The audit cannot see a nick — a face of the same part is not an occlusion error — so it is
the side-effect check here. Pricing the touch costs Apollo 29 sampled px (about 1,400 real
px at 2.1% sampling) and removes 16,400 px of nicked line on the same clip; on Voyager it costs no audit pixels and
removes 8,000 px more than the free version. It was kept, and pushed as 10b0517 — and §17
found what the extra 29 Apollo pixels were.
Largest new Apollo errors: `apollo_003/f0` over `apollo_024` 3 → 12, `dock_001/c14_0` over
`apollo_024` 2 → 9. A hypothesis that the vertex path loses shared length the drawn line
has was checked and is wrong: `dock_001/c14_0` meets `apollo_024` for 45.3 px thinned
and 45.8 px along its vertices.

**Regression, a99a6ff against this:** element counts, `check_geometry` offenders and
between-keyframe segments identical on both models (Apollo 17,462 / 0 / 26; Voyager 8,579
/ 0 / 173) — ordering does not touch geometry. The confirmed-settings preview
(`out/preview-rings/`, `rings_before_after.png`) shows rings 2 and 3 solid at 0.77, 0.83
and 1.00 s, the neck outline back, and the module body's own rings, dashed before at
1.00 s, solid too.

**Not fixed — and misread.** Ring 1 is two lines. The right one (`c12_0`) is solid. The
left one, `dock_001/c14_0`, came out broken in two or three places, and this section said
that was so "in every preview since before face by face" and a cross-part seam. Both wrong:
it is hidden behind `apollo_024` along its whole length, and a99a6ff drew only 3 px of it;
the priced touch drew 28 px of it on frame 20. See §17, which corrects it.

---

## 17. The joint line: a touch priced by what it would really show

17 September. Working the item §16 left open: ring 1's left line, `dock_001/c14_0`, broken
in two or three places where the module meets `apollo_024` (the Apollo cone shell,
3.5 × 5.1 × 5.1).

**Truth first.** Ray cast along the whole line at frame 20: at *every* sample the nearest
surface is `apollo_024`, 0.037–0.107 in front of the line (`joint_map.py`). The line sits
inside the shell and should not be seen at all. The gaps were right; the drawn pieces were
the error. §16 read the 0.04 against the audit's 0.05 margin and called it ambiguous — it
is not, it holds along the full length.

**Why it was drawn.** Every shell face over the line had the correct measured constraint,
"line behind shell face" (0.037–0.11), and the breaker spent most of them. Each closed the
same cycle (`cycle5.py`):

    shell face  behind  dock_001 face   measured: the face pokes out through the shell
    dock_001 face behind line           the touching rule of §16 (weight tol)
    line        behind  shell face      measured  <- spent

Three true statements about geometry that crosses. §16 priced breaking a touch at a
line-width square (2.56 px²); each shell face covers ~1.5 px of the line (2.4 px²), so the
shell pair was the cheaper and the hidden line was drawn over the shell.

| Ordering | `c14_0` drawn over the shell, frame 20 (of 36 px) |
|---|---|
| a99a6ff | 3.0 px |
| Vertex path only | 3.5 |
| Touch priced at a line-width square (10b0517) | **28.0** |
| Touch free to break | 3.5 |
| Touch as a sort tie-break only | 3.5 |
| Touch priced by covered contact (kept) | 3.5 |

The 3.5 px left is older: `dock_001/f66`, a face the line *lies on*, pokes out through the
shell too, so the protected assertion holds the line in front of it — the crossing trade
`test_order_cycle.py` pins.

**Two fixes tried and rejected on the way.**

- *Touch as a tie-break, not an edge* (a line sorts no further back than the nearest face
  that only touches it; fills first on ties). No edge, so no cycle; clears the rings and
  the joint. But the breaker cannot protect a tie-break: visible nicks Apollo 11,107 px,
  Voyager 17,227 — six and two times 10b0517's.
- *Touch free when its shared vertex lies under another part the line is behind.* Clears
  the joint and the rings, audit 1,022 / 2,054, but visible nicks on Apollo tripled
  (1,779 → 5,404) — on five `apollo_024` edges. `apollo_024/f953` is a sliver 0.35 px wide
  that shares one vertex with the 285 px edge `e1280` and hugs it the whole way; its vertex
  was buried, so the touch went free, and the sliver's hairline thinned the whole edge.

**Fix: price a touch by the contact it would really show** (`order._price_touches`). The
contact is the stretch of line within half the stroke width of the face — a nick at a
vertex, the whole edge for a hugging sliver; the part of it inside a face of another part
that the line is measured behind is buried and costs nothing; the rest costs its length ×
width. Priced at the first cycle break, only for pairs inside a strongly connected
component (ranks bit-identical to pricing all 7,008 pairs on frame 20).

Lab, `scripts/test_chain_depth.py`, each case failing on at least one rejected version:

| Case | 10b0517 | Vertex-cover | Tie-break | Contact (kept) |
|---|---|---|---|---|
| Rings touched at vertices | pass | pass | pass | pass |
| Line inside a shell, touching faces poke through (0 of 60 shell faces under it) | **fail** (58) | pass | pass | pass |
| Sliver hugging a line, vertex buried, real cycle | **fail** | **fail** | **fail** | pass |

**Measured** (same captures and settings as §16; visible nicks count only nicked line no
other part's face is painted over, in that variant's own order):

| Ordering | Audit Apollo | Voyager | Visible nicks Apollo | Voyager | Rings f20 (pieces) |
|---|---|---|---|---|---|
| a99a6ff | 1,012 | 2,105 | — | — | 13 / 11 / 9 |
| Touch free to break | 1,012 | 2,055 | 10,049 | 13,364 | 1 / 0 / 3 |
| Touch at a line-width square (10b0517) | 1,041 | 2,055 | 1,779 | 9,432 | 1 / 0 / 0 |
| Touch as a tie-break | 1,002 | 2,081 | 11,107 | 17,227 | 1 / 0 / 0 |
| Touch free where its vertex is buried | 1,022 | 2,054 | 5,404 | 9,492 | 1 / 0 / 0 |
| **Touch priced by covered contact** | **1,019** | **2,054** | **1,404** | **8,872** | **1 / 0 / 0** |

The kept version is at or near the best on every column and better than 10b0517 on all of
them. Cost against 10b0517, ordering one frame (process time, repeated): Apollo frame 20
7.6 → 8.3 s, frame 7 7.0 → 7.2 s, Voyager frame 30 3.8 → 4.6 s.

**Regression, 10b0517 against this:** element counts, `check_geometry` offenders and
between-keyframe segments identical on both models (Apollo 17,462 / 0 / 26; Voyager 8,579
/ 0 / 173). The confirmed-settings preview (`out/preview-joint/`,
`joint_before_after.png`) at 0.77, 0.83 and 1.00 s: `dock_001/c14_0` no longer drawn over
the cone, two specks of the 3.5 px crossing residue left; rings 2 and 3 and ring 1's right
line still solid.

---

## 18. The docking joint after the user's Boolean

17 September. The user applied a Boolean in Blender so the docking parts no longer
intersect, and saved `apollo-soyuz.blend`; the previous save is `apollo-soyuz.blend1`.
Measured only, at 5583384, same settings — no code changed. Both files snapshotted to the
session scratchpad first so a later save could not move the inputs.

**What changed in the file** (per-object vertex hashes, `geo_sig.py`): two objects of 92.
`dock_001` 402 → 602 vertices, 800 → 920 faces; `dock_002` 8 → 26 vertices, 12 → 18
faces. No Boolean modifier remains — it was applied, so the evaluated mesh equals the
stored one (identical hashes), and the Blender 5.2 modifier-input change (geometry-node
inputs moved off ID properties, see memory) does not come into it. `apollo_024` and every
other object are bit-identical; the VecBake settings are identical.

**Counts** (scene-settings bake, both files rebaked with the same install):

| | Before | After |
|---|---|---|
| Elements (strokes / fills) | 17,462 (7,310 / 10,152) | 17,574 (7,344 / 10,230): +112 |
| Merged fills / fallbacks | 56 / same list | 56 / same list |
| `dock_001` Merge False Outline score | 211 px (faces) | 1,145.5 px (faces) |
| `check_geometry` offenders | 0 | 0 |
| Between-keyframe segments / offending pairs | 78,580 / 26 | 81,591 / 37 |

All 11 new segment offenders are `dock_002/m`, whose merged outline became a 26-vertex
cut shape: worst 3.30 px at frame 26.5. Every other offender is identical.

**Ray-cast audit** (31 frames, 93,000 sampled px; the old file re-audited reproduces
1,019 exactly): **1,019 → 1,005**. Overlap pixels available 4,481,087 → 4,471,737.
Errors involving `dock_001` or `dock_002`: 80 → 54 — `dock_002/e16` over `dock_001`
14 → 0, `soyuz_005/m` over `dock_001` 18 → 8, `dock_001/c14_0` over `apollo_024` 3 → 0,
`dock_000/m` and `dock_003/m` over `dock_001` −4 and −3. New: `dock_001/c15_0` (the cut
ring) over `apollo_024`, 17 px across 11 frames, measured delta 0.0 — the ring lies on the
cone's surface and takes the coplanar "lines on top" rule. The large faces named as the
worst errors do not move, their geometry being untouched: `apollo_003/f1` over
`apollo_002` 76 → 78, `apollo_002/f11` over `apollo_003` 92 → 94, `apollo_013/m` over
`apollo_003` 85 → 86, `apollo_018.013/m` over `apollo_003` 27 → 26; `apollo_002/f22` over
`apollo_001` 3 → 0. Largest unrelated shift: `apollo_000/m` over `apollo_002` 88 → 110.

**Nicks** (31 frames, 5583384 ordering): visible 1,403.8 → 1,396.5 px; all 19,060.8 →
18,764.0 px.

**The joint at frame 20, ray cast** (every `dock_001` line in the joint, `joint_truth3.py`,
0.05 margin): drawn but hidden 3.0 → 0 px; painted over but visible 28.0 → 1.5 px. The
line that sat 0.04 inside the cone no longer exists; the cut ring is drawn and seen.

Close-ups (`out/bool-compare/closeups.png`): the specks of the hidden line are gone at
0.77, 0.83 and 1.00 s and a clean double ring marks where the module meets the cone; the
module box's top edge, thinned on its right half before, is solid. Frames 1, 4, 5, 16, 22
and 27 around the named errors look the same before and after.

---

## 19. The interleaving selector, measured again against the new breaker

10 October. [Section 7](#7-interleaving-selector-first-attempt) rejected the
interleaving split selector on 13 September: 342 wrong pixels against 289 for
nothing split and 214 for `apollo_003` split by hand. Its own reading named the
reason — **one error was 261 of those 342**, a single cycle break where the
breaker gave up the constraint that mattered.

That breaker was replaced on 16 September by one that spends the pair covering
the least screen ([section 15](#15-drawing-order-the-blinking-thruster-and-the-see-through-parts)).
The selector was never re-run against it. Re-running it is the point of this
section, and the verdict reverses.

**Setup.** `scripts/screen_surface_batch.py audit`, three arms back to back on
one machine, nothing else of ours running. Screening frames 0, 15, 30 at
budget 3000, so 9,000 sampled overlap pixels. Code at `ba4b8dd`. The harness
forces its own settings (crease 30 degrees, merge on, one ordering worker), the
same for every arm.

**These numbers are not comparable with section 7's.** Both the code and the
model have moved: `geometry_signature.py` has `dock_001` at a different vertex
hash and `apollo_018.013`/`.015` in different places since `ba4b8dd` was
measured, and `apollo-soyuz.blend1` is no longer the pre-Boolean save. The three
arms here share a blend and a commit, so they compare with each other and with
nothing else.

| | Nothing split | `apollo_003` by hand | **Interleaving rule** |
|---|---|---|---|
| Wrong pixels | 196 | 157 | **87** |
| Rate | 2.178% | 1.744% | **0.967%** |
| Errors | 48 | 30 | 34 |
| measured wrong | 23 | 14 | 19 |
| order violated | 18 | 10 | 10 |
| no constraint | 3 | 3 | 2 |
| occluder not drawn there | 2 | 2 | 0 |
| Errors involving `apollo_003` | 17 (121 px) | 6 (98 px) | **8 (34 px)** |
| Elements | 17,579 | 17,582 | **27,924 (+59%)** |
| Bake CPU | 1,069 s | 1,646 s | 2,455 s |
| Whole audit | 2,747 s | 2,921 s | 4,798 s |

Wrong pixels by pair, the ones that move:

| Pair | Nothing split | `apollo_003` split | Interleaving |
|---|---|---|---|
| `apollo_002` over `apollo_003` | 77 | **90** | 20 |
| `apollo_013` over `apollo_003` | 28 | 5 | 0 |
| `apollo_000` over `apollo_001` | 18 | 14 | **0** |
| `apollo_000` over `apollo_002` | 18 | 17 | **0** |
| `apollo_024` over `apollo_003` | 6 | 0 | 0 |
| `apollo_002` over `apollo_001` | 0 | 0 | 18 (new) |
| `apollo_003` over `apollo_002` | 0 | 0 | 11 (new) |
| `soyuz_029` over `soyuz_031` | 1 | 1 | 6 (new) |

**Reading.**

- **The rule now wins, by a lot.** 196 to 87 wrong pixels, 56% gone, and better
  than the hand split it lost to last time. The selection itself did not change:
  42 picks of 58 candidates against 39 last time, and its criterion is untouched.
  What changed is what the breaker does with the cycles splitting creates.
- **Section 7's single catastrophic break is gone.** `apollo_003/f1` against
  `apollo_002` cost 261 px there; the same pair costs 11 here. Splitting removes
  cycles and creates new ones, and the old breaker priced a new one by its depth
  gap and spent a constraint worth 261 px. The cheapest-on-screen rule prices that
  pair by the screen the two share, which is large, so it spends something else.
- **Splitting one side of a pair can make it worse.** `apollo_002` over
  `apollo_003` goes 77 to 90 when only `apollo_003` is split, and to 20 when both
  are. That is evidence against section 7's open question 1 — "split one side of a
  contradicting pair, not both" — at least as a general rule. A pair needs enough
  ranks on *both* sides to describe a wrap; giving one side more and leaving the
  other at one rank moved the error rather than removing it.
- **`apollo_000`, the engine bell, is fixed by splitting.** 36 px involved to 2.
  The user reported it on 10 October as looking see-through, and it is the clearest
  case of the whole class: the bell is a cup ([bugs.md](bugs.md)), so its inside is
  far, its rim is near, and `apollo_001` sits in its mouth — in front of part of it
  and behind the rest. One rank cannot say that.
- **The cost is still the objection.** +59% elements and 2.3x the bake CPU, plus
  209 s of CPU in the selector itself. 12,099 faces replace 42 merged fills;
  `soyuz_037` alone brings 1,520. This is what keeps it from being a default, not
  the pixels any more.

**Where that leaves it.** The rule is no longer wrong, it is expensive. The open
question is no longer "does more granularity help" — it does, measurably — but
"how few bodies can be split for most of the 109 pixels". The selector picks 42;
the four worst pairs account for 141 px of the 196, between five bodies
(`apollo_000`, `apollo_001`, `apollo_002`, `apollo_003`, `apollo_013`).

---

## 20. The cross-model scorecard

25 October. Every number above this section comes from Apollo–Soyuz or the Voyager.
This grades the engine on models it was never tuned on, with those two as anchors,
so that "good enough to sell" has numbers behind it.

**Corpus.** Five blends already on disk, picked for being unlike the heroes, plus the
two heroes.

| Name | Blend (under `Documents/`) | What it is | Drawable faces | Camera |
|---|---|---|---|---|
| apollo | `Blender 2026/lerpa apollo-soyuz/apollo-soyuz.blend` | hero, 91 parts | 27,399 | its own 60° arc, 31 frames |
| voyager | `Blender 2026/lerpa voyager/lerpa voyager.blend` | hero, 322 parts | 5,257 | its own arc, 60 frames |
| creature | `blender/Mechanical Creature Kit Free/…Polyfjord_v1.0.blend` | 7 rigid parts on an armature, 10 cm across | 17,069 | none in file: a new one, orbit |
| flashbang | `Blender 2026/flashbang/Untitled1.blend` | hard-surface product: 7 meshes, 6 Bézier curves, a floor | 5,221 | the artist's, orbit |
| celltower | `Blender 2026/celltower/celltower.blend` | the NOC tower, orthographic | 1,154 | its own move, 24 frames at 30 fps |
| discoball | `Blender 2026/AI/claude one prompt disco ball/…` | one 5,664-facet ball over a floor | 5,665 | the artist's, orbit |
| doughnut | `Blender 2026/doughnut tutorial blender 5/…` | eight doughnuts and a 96k-face mug, smooth and organic | 180,608 | `rendering camera`, orbit |

**Setup.** [`scripts/scorecard.py`](../scripts/scorecard.py), one Blender 5.2.2 per
model, two at a time, `--factory-startup` with VecBake enabled by hand
([gotchas](gotchas.md#headless-runs-want---factory-startup)). Every panel setting is
reset to its default, then the hero audits' set is applied (the one at the top of
this page). A scene whose camera moves keeps its move; any other gets Apollo's arc: an
empty at a pivot turning −30° → +30° about world Z over 31 frames at 24 fps, the
camera parented to it. One bake per model, graded three ways: the ray-cast audit at
the first, middle and last frame, 3,000 overlap pixels each (§19's sampling);
`check_geometry.py` at the sampled frames; `check_outline_segments.py` halfway
between them. The audit margin is 0.05 × framed width ÷ Apollo's framed width (25.54),
so Apollo grades at exactly 0.05 — see [measurement.md](measurement.md#five-ways-a-model-the-tools-were-not-built-on-reads-as-clean).
Bake code `0678b45`; `audit.py` with the ray fix below, which changes grading only.
The corpus, with each blend's path and shot camera, is
[`scripts/scorecard_corpus.txt`](../scripts/scorecard_corpus.txt); the table is
`python scripts/scorecard_table.py out/scorecard <names>`.

**The harness reproduces §19.** Apollo comes back at 17,579 elements, **196 wrong
pixels, 2.178%, 48 errors** — 23 measured wrong, 18 order violated, 3 no constraint —
the same as §19's nothing-split arm in every column. Each run also re-grades its logged
gaps at its own margin, and reproduces the audit's own count every time. Apollo ran
before the settings reset was added; rerun with it (and with the fixed audit rays)
it is the identical drawing, colours included, and the identical 196.

**Five measurement faults, found and fixed before any of this could be read.** Each
gave a plausible number. Written up in
[measurement.md](measurement.md#five-ways-a-model-the-tools-were-not-built-on-reads-as-clean);
the one in the add-on is in [bugs.md](bugs.md#the-audit-cast-its-rays-through-the-wrong-pixels).

| Fault | What it read as | Fixed |
|---|---|---|
| Audit rays from the scene's render size, and fanned under an orthographic camera | flashbang 9.4% wrong (now 4.1%); lab clean (now 12.0%); cell tower 23 px (now 40) | `audit._Truth` unprojects through the bake's matrix; `test_audit_rays.py` |
| Each blend's saved panel leaking in | cell tower with ids off: both geometry checkers clean over **zero** point-sets | reset first; the checkers refuse a bake without ids |
| The scene camera is not the shot | doughnut: 258 elements of one texture-baking plane | the corpus names the camera |
| An absolute 0.05 margin | creature: wider than its parts, so nothing could fail | scaled by framed width |
| A profile add-on crashing Blender on load | Voyager: no run at all | `--factory-startup` |

**Results.**

| | apollo | voyager | creature | flashbang | celltower | discoball | doughnut |
|---|---|---|---|---|---|---|---|
| Status | done | done | done | done | done | done | done |
| Camera | own move | own move | orbit, new camera | orbit, artist's camera | own move | orbit, artist's camera | orbit, artist's camera |
| Frames | [1, 31] | [1, 60] | [1, 31] | [1, 31] | [1, 24] | [1, 31] | [1, 31] |
| Audit margin | 0.0500 | 0.0241 | 0.0011 | 0.0090 | 0.0034 | 0.0161 | 0.0031 |
| Elements | 17,579 | 10,592 | 5,155 | 1,414 | 1,394 | 9,749 | 160,287 |
| Bake CPU, s | 996 | 1,960 | 270 | 73 | 66 | 159 | 8,190 |
| File, MB raw / gz | 11.11 / 1.69 | 14.62 / 3.10 | 3.51 / 0.61 | 1.18 / 0.28 | 1.25 / 0.29 | 4.87 / 0.90 | 123.75 / 21.51 |
| Peak memory, MB | 1,711 | 1,507 | 1,480 | 485 | 421 | 659 | 7,444 |
| Overlap px tested | 9,000 | 9,000 | 4,365 | 9,000 | 9,000 | 3,041 | 9,000 |
| Wrong px (own margin) | 196 | 162 | 0 | 369 | 40 | 0 | 0 |
| Wrong rate | 2.178% | 1.800% | 0.000% | 4.100% | 0.444% | 0.000% | 0.000% |
| Wrong px at 0.05 | 196 | 132 | 0 | 266 | 24 | 0 | 0 |
| Distinct errors | 48 | 61 | 0 | 58 | 19 | 0 | 0 |
|   measured wrong | 23 (150 px) | 15 (90 px) | 0 | 22 (68 px) | 5 (9 px) | 0 | 0 |
|   order violated | 18 (36 px) | 31 (36 px) | 0 | 6 (68 px) | 5 (6 px) | 0 | 0 |
|   no constraint | 3 (4 px) | 4 (5 px) | 0 | 0 | 0 | 0 | 0 |
|   occluder not drawn there | 2 (3 px) | 7 (27 px) | 0 | 27 (223 px) | 9 (25 px) | 0 | 0 |
| Geometry offenders | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
|   over 10 px | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
|   worst, px | 1.04 | 1.10 | 1.11 | 1.04 | 1.05 | 1.11 | 1.07 |
| Segments @0.5 offending | 39 | 173 | 14 | 49 | 189 | 0 | 240 |
|   over 3 px | 2 | 22 | 2 | 1 | 4 | 0 | 16 |
|   over 10 px | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
|   worst, px | 3.3 | 15.8 | 6.0 | 3.1 | 4.8 | 0.0 | 3.5 |
| Merged fills | 58 | 266 | 5 | 2 | 9 | 0 | 0 |
|   fell back | 33 | 56 | 2 | 5 | 4 | 2 | 10 |
| Cycle breaks | 1,753 | 2,472 | 3 | 645 | 377 | 771 | 919 |

Bake CPU is the whole process with one ordering worker (the audit needs ordering in
its own process to diagnose), so it is the engine's cost, not the wall time of a panel
bake with the worker pool — and on this machine it is good to about a factor of two:
identical drawings have come back at 577 and 996 s, and at 1,960 and 4,218 s
([measurement.md](measurement.md#machine-noise)). Read it for orders of magnitude. Controls: the test shapes and the lab scene both run end
to end (64 s and 8 s); the shapes never overlap on screen, and the lab's 12.0% is its
crossing pair, which one rank cannot get right by construction.

Bytes per element, raw / gzipped: Apollo 632 / 96, Voyager 1,380 / 292, creature 680 /
119, flashbang 836 / 195, cell tower 898 / 209, disco ball 499 / 92, doughnut 772 / 134. Voyager is twice
the others because its clip is twice as long. The creature and the disco ball overlap
little on screen (4,365 and 3,041 pixels to test in three frames), so their zero wrong
pixels say little about ordering, and the doughnut's (49,808) little more; the cell
tower, flashbang and the anchors each had over 260,000.

**What fails, by how many models it reaches.**

1. **Outline drift between keyframes** — 5 of 6 have a merged outline or chain more
   than 3 px off at subframe 0.5 (none on the disco ball). Small: worst 3.1–6.0 px
   everywhere except one Voyager element. The exception, `part_081.001/m` at 15.8 px,
   is a zero-length segment — two coincident points 16 px off the outline, which reads
   as a parked slot. `part_024.001/m` is 3–4 px off in 58 of 59 frames: a persistent
   drift, not a blip.
2. **Measured wrong** — 4 of 6. One rank per merged body cannot describe a body that
   is in front of a neighbour in one place and behind it in another. The largest class
   on both anchors (Apollo 150 px, Voyager 90, flashbang 68, cell tower 9), and the lab's
   crossing pair is it in pure form. Splitting fixes it ([§19](#19-the-interleaving-selector-measured-again-against-the-new-breaker)),
   at +59% elements on Apollo.
3. **Order violated** — 4 of 6, 6–68 px. Flashbang's 54 px of it is `Cylinder.004`
   against `triangle`: two linked duplicates of one mesh, 2 cm apart. A modelling fault
   more than an ordering one.
4. **Backface cull on open surfaces** — 2 of 6 (cell tower 25 px, Voyager 27 px), new.
   Traced: the nearest surface on the ray is the *back* of a face, which a closed,
   consistently wound mesh never shows first. `tower` has 304 open edges on 308 faces;
   `part_012.009` is 12 loose quads. The cull drops the faces the camera is looking at
   and the part behind shows through. The cell tower's own panel had the cull off; the
   applied set turned it on. Candidate fix, not built: cull a face's back only where its
   connected surface is closed — decided from topology, with no threshold.
5. **Curves and text are not baked** — 1 of 6 measured, structural. `collect_objects`
   takes meshes only, without a word. In flashbang that is 3,072 of 5,221 faces and 199
   of its 369 wrong pixels; without it flashbang would sit near Apollo's rate.
   **Fixed in [§21](#21-curves-text-and-surfaces-baked): flashbang 369 → 134.**
6. **Smooth bodies with holes do not merge** — the doughnut, every one of its nine
   bodies (above). Taken apart in [§22](#22-why-a-torus-will-not-merge): a torus seen
   clearly was refused by a measurement fault (fixed there); the doughnut's, seen low and
   close, by a silhouette that changes topology between keyframes (open). One model, but it decides that model's file size, bake time and
   memory outright, and a body with a hole is no rare shape: rings, handles, pipes,
   gears seen at an angle.
7. **Bake time follows input faces, in shot or not.** The doughnut through its baking
   camera drew one plane and took 1,036 s of CPU, sampling 180k faces it then culled
   off screen, twice (once to size outlines, once to bake), at ~11 s a frame.

Clean everywhere: **every drawn point at every sampled frame is within 1.11 px of its
true projection**, on all seven bakes.

**The doughnut is right and unshippable.** Through its rendering camera every
pixel the audit tested is right and every drawn point is within 1.07 px, over
1,673,427 point-sets. But it is **160,287 elements, 123.7 MB (21.5 MB gzipped)**, from
**8,190 s of CPU** with 7.4 GB of memory at peak — because nothing merged. All nine
bodies fell back to faces: each one refused by the carry test, and each one drawing
1,022–20,687 px of visible false outline over the clip against the 160 px limit
(`Torus.003` merged is 38.0 px off between keyframes, face by face 0.6). A torus's
outline is two rings, and as the camera orbits the hole narrows, splits and closes:
the multi-ring carry problem open since September ([bugs.md §12](bugs.md#12-open),
"a half-grown ring"), here on every object of the most-made model in Blender. Face by
face then makes everything after it expensive: 15.9 million ordering constraints a
clip, ordering frames of 75–480 s on one worker.

---

## 21. Curves, text and surfaces baked

25 October, the first class off §20's list. `collect_objects` took meshes only; it
now takes `bake.DRAWABLE` — mesh, curve, text, surface — and drops, for every type
alike, an object whose evaluated geometry has no vertices. Everything downstream
already read `evaluated_get(dg).to_mesh()`, so nothing else in the baker changed. The
audit's own-surface ray, both geometry checkers and the panel's pickers use the same
rule; `check_geometry` reads the evaluated mesh instead of `.data`, which for a curve
is the Curve. Bugs: [bugs.md §12](bugs.md#12-open).

**Controlled scene first.** `scripts/test_curve_objects.py` builds a bevelled Bézier
circle, an extruded two-letter text, a NURBS patch, a wire curve with no bevel and a
curve with no splines, in front of a wall, under an orbiting camera. It bakes them,
then replaces each with `bpy.data.meshes.new_from_object` of its evaluated self — same
name, same transform channels, same order — and bakes again.

| | Old code | New code |
|---|---|---|
| Elements, as curves / as meshes | 1 / 708 | 706 / 706 |
| Elements that differ, sample for sample | — | **0** |
| Ring / Title / Patch / Wire / Empty | 0 / 0 / 0 / 0 / 0 | 223 / 454 / 27 / 1 / 0 |
| `check_geometry` on the curve bake | 9 point-sets | 4,884, 0 offenders, worst 0.34 px |
| Audit sees the curve's own surface | no | yes |

**Then the scenes.** Same harness and settings as §20.

| | Flashbang before | Flashbang after | Apollo | Voyager |
|---|---|---|---|---|
| Elements | 1,414 | 2,597 | 17,579 → 17,579 | 10,592 → 10,592 |
| Drawing vs before (`compare_bakes.py`) | — | +1,183 curve elements | **identical** | **identical** |
| Wrong px (rate) | 369 (4.10%) | **134 (1.49%)** | 196 → 196 | 162 → 162 |
| `occluder not drawn there` | 27 errors, 223 px | **0** | 2 → 2 | 7 → 7 |
| Measured wrong / order violated | 68 / 68 px | 71 / 53 px | unchanged | unchanged |
| Geometry offenders | 0 | 0 | 0 | 0 |
| Segments @0.5 over 3 px | 1 | 1 (no curve element among them) | 2 → 2 | 22 → 22 |
| File, MB raw / gz | 1.18 / 0.28 | 2.00 / 0.43 | unchanged | unchanged |

**Reading.** The see-through class is gone from flashbang, and with it two thirds of
its wrong pixels; what is left is ordering — 44 px of it the two coincident linked
duplicates — and puts flashbang under Apollo's rate. The anchors hold only meshes, so
any change to them would have been a regression; both are the same drawing, element for
element and field for field, and grade the same. Apollo's rerun also ran the fixed audit rays of §20 and grades the same
196, which settles that the ray fix does not move a hero audit.

---

## 22. Why a torus will not merge

25–26 October. §20's doughnut drew all nine of its bodies face by face — 160,287
elements, 124 MB — each one refused by the gap test (`judge_carry`) *and* by Merge
False Outline. The question was which test refuses what, and why.

**A controlled torus first.** [`scripts/lab_torus.py`](../scripts/lab_torus.py): one
smooth primitive torus (64 × 32), the scorecard's settings and 60° orbit, the camera at
four heights. Pieces are `walk_pieces` loops per keyframe.

| Elevation | Pieces per frame | Merged | Refused by | Gap test, carry / face | False outline |
|---|---|---|---|---|---|
| 5° | 1 every frame | no | sizing's `_gap_cost` (> 4 × slack) | — | — |
| 15° | 1 every frame | no | the same: 227 px | — | — |
| 30° | 2 every frame | no | **gap test** | 10.81 / 0.48 px | 99 px (limit 160) |
| 60° | 2 every frame | no | **gap test** | 13.21 / 0.45 px | none |

No torus merges at any height, and the two kinds of refusal are different things.

*Seen clearly (30°, 60°) the outline is right and the gap test measures it wrong.* At
60° every drawn point at the worst half-frame is within **0.30 px** of the true
silhouette; the 13.21 px is the other direction, a true vertex 13.2 px from the drawn
polyline. `judge_carry` passes `outline.stray` the drawn outline's subpaths
concatenated, as one closed loop, against `walk_loop`'s single ring — which bridges two
pieces with a seam and gives nothing past two. Read as one loop, the drawn outline loses
the rim's own closing segment and gains two chords from rim to hole, and the true rim
vertex on the missing segment is 13.2 px from either chord
(`out/lab-torus/gap-test-ring-joining-60deg.png`). The player closes every subpath on
itself; nothing like that is drawn. §10 measured the same joining on the `apollo_018`
thrusters ("a perfect three-ring outline scores 30 px").

*Edge-on (5°, 15°) the outline really is wrong.* The silhouette is the rim plus the far
inner contour folding over the near tube, and the single ring drawn halfway between
keyframes wanders across the body, 227 px from the truth at its worst
(`edge-on-15deg.png`). Sizing refuses it before the gap test is reached. The fold
problem: [memory and §12](#12-what-is-left-between-keyframes).

**Then the doughnut's own torus.** [`scripts/lab_merge_why.py`](../scripts/lab_merge_why.py)
bakes one object of a scene and logs both tests gap by gap. `Torus.003` alone through
the `rendering camera` reproduces its refusal in the full scene exactly — 38.03 / 0.6 px,
3,982 px of false outline, 658 slots — so it can be studied by itself.

Its silhouette is not rim-and-hole. Through a 20 mm lens, low and close, the pieces are
loops of the torus's contour that cross each other and fold back with cusps, and their
number changes between keyframes: 2, 3 or 4, at keyframes and at half-frames alike.

| Gaps | Visible false outline (as `false_seen` counts it) |
|---|---|
| where the piece count changes (11 of 30) | **3,140 px** |
| where it does not (19 of 30) | **842 px** |
| total, against a 160 px limit | 3,982 px |

The gap test judged three gaps (33.43, 38.03, 4.49 px), all through the same joining.
Drawn against the truth (`doughnut-torus003-gaps-*.png`), the false line in the gaps
that change is the drawn rings cutting straight across the body while the contour
splits or rejoins underneath them; in the steady gaps it is the folded inner contour
drifting. **The doughnut is refused, correctly, by Merge False Outline**: slots fixed
for the clip and interpolated linearly cannot follow a contour that changes topology
between keyframes. That is §12's open decision, now on the most-made model in Blender.

**What was changed.** Only the measurement that is wrong. `judge_carry` now scores
one-ring outlines only; a multi-subpath outline is judged by Merge False Outline, which
measures each ring closed on itself against every true piece. That is what sizing
already did (`_gap_cost` skips multi-subpath outlines) and what `judge_carry`'s own
comment said happened. Nothing about how outlines are drawn changed.

| Lab torus | Before | After |
|---|---|---|
| 30°: elements | 2,246 | **1** (merged), 0 geometry offenders, 1 segment at 1.37 px |
| 60°: elements | 1,654 | **1** (merged), 0 geometry offenders, 0 segments |
| 15°: elements | 2,552 | 2,552 (still refused; folds) |

Guard: `scripts/test_torus_merge.py` — fails on the old `bake.py` (neither merges),
passes on the new.

**The anchors.** Scorecard harness, same settings as §20; "before" is §21's run.

| | Apollo before | Apollo after | Voyager before | Voyager after |
|---|---|---|---|---|
| Merged fills | 58 | **62** | 266 | **269** |
| Newly merged | | `apollo_018.005`, `.008`, `.015`, `soyuz_008` | | `part_013.001`, `part_026.001`, `part_080.001` |
| Newly refused | | none | | none |
| Their visible false outline (limit 160) | | 8, 0, 135.5, 19.5 px | | 44.5, 51.5, 153 px |
| Elements | 17,579 | **16,395** | 10,592 | **10,395** |
| Wrong px (rate) | 196 (2.18%) | **180 (2.00%)** | 162 (1.80%) | 162 (1.80%) |
| Distinct errors | 48 | 46 | 61 | 58 |
| Geometry offenders | 0 | 0 | 0 | 0 |
| Segments @0.5 offending / > 3 px / > 10 px | 39 / 2 / 0 | 39 / 2 / 0 | 173 / 22 / 1 | 176 / 24 / 1 |
| …of which on the new merges | | **0** | | 3 (8.51, 4.13, 2.70 px) |
| File, MB | 11.11 | 10.35 | 14.62 | 14.39 |

The three `apollo_018` thrusters are the ones on the open list since September
("`.005 .008 .015` still refused, 6.8–10.5 px"): refused only by the joining, they merge
and none of their segments is off by 1.25 px halfway between keyframes. On Voyager the
two new offenders over 3 px are degenerate: `part_080.001/m`'s 8.51 px is a zero-length
segment and `part_026.001/m`'s 4.13 px one is 0.2 px long — collapsed slots, two
coincident points that draw nothing, the same thing as `part_081.001/m`'s 15.8 px (§12).

**The rest of the corpus.** Baked again with `scripts/bake_only.py` and compared with
`compare_bakes.py`: the creature kit, the cell tower and the disco ball are the
identical drawing. Flashbang is not, and was graded again in full:

| Flashbang | Before (§21) | After |
|---|---|---|
| Newly merged | | `BézierCircle`, `BézierCircle.004` (the bevelled rings), `Cube`, `Cylinder.002` |
| Their visible false outline | | 2, 20.5, 23.5, 38.5 px |
| Elements | 2,597 | **1,345** |
| File, MB raw / gz | 2.00 / 0.43 | **1.21 / 0.29** |
| Wrong px (rate) | 134 (1.49%) | 130 (1.44%) |
| Geometry offenders | 0 | 0 |
| Segments @0.5 offending / > 3 px | 49 / 1 | 61 / 1 |
| …of which on the new merges | | 12, worst 1.78 px |

A bevelled ring is a torus too: its rim and bore were refused by the same joining.

`bake_only.py` bakes with the worker pool; a pooled bake (11 workers) and a single-worker
bake of the cell tower are the identical drawing, so a pooled rerun answers "did this
change the drawing" without the audit's serial ordering.

**The doughnut**, baked again with four ordering workers (26 min wall, against 2.4 h
on one): still **0 merged fills**, and the merge record — refusals, false outline per
body, slots — identical to §20's; all 160,287 elements it shares with that bake have
the same geometry and opacity. It has three more: `BézierCurve/c0_0`–`c2_0`, a wire curve
on the mug (no bevel, no faces) that §21 now bakes as strokes, the way a mesh's loose
edges are; the paint ranks around them shift, nothing else. The doughnut is refused by
what it draws, not by how it was measured, and stays open (bugs.md §12).

**Where that leaves the torus.** Seen clearly it now merges, and so does everything
built like it — the thrusters, a bevelled ring, a bore. Seen edge-on, or low and close
through a wide lens, it does not, because its silhouette folds and changes topology
between keyframes, which fixed slots interpolated linearly cannot follow. That is the
next thing to decide, not to tune.

---

## 23. A piece that appears: how it could carry, measured before building

26 October. §22 left the doughnut refused by its own silhouette: contour loops that
split, rejoin, appear and vanish between keyframes. The question was how such a piece
could carry, or what charging faces for appearing would change — measured on a
controlled torus before anything is built.

**The controlled case.** `lab_torus.py` with a tilt: the torus leans 15°, the camera
is 10° up through a 20 mm lens, and the 60° orbit sweeps how high the torus is seen
from. Its hole opens at frame 8 and closes at frame 25 of 31: one piece, two, one —
a birth and a death at known gaps. (A primitive torus never splits into three or four;
the doughnut's lumpy dough does.) `out/lab-torus/torus_birth.blend`.

**A sizing fault first.** The full clip was sized as one ring and refused by sizing's
gap check; cut to frames 1–20 it was two subpaths, refused by Merge False Outline at
855 px; cut to 8–31, one ring again. The measuring pass sent one-piece frames down the
single-ring path, which set `piece_needs` to one number and dropped what two-piece
frames had measured for the hole — so whether a part was sized as several subpaths
depended on its **last frame**. Every measuring frame now goes through the pieces path,
matched by vertices; a part that is one ring throughout measures as before. All three
clips now size `[78, 90]` and are judged the same way (855, 1,315 and 2,176.5 px).
Guard: `scripts/test_piece_sizing.py`.

**What the sizing fix does to the corpus.** Scorecard harness for the anchors and the
creature kit, `bake_only.py` + `compare_bakes.py` for the rest; "before" is §22.

| | Before | After |
|---|---|---|
| Apollo merged / wrong px / segments > 3 px | 62 / 180 / 2 | 63 (`dock_008`) / **193** / 2 |
| Voyager merged / wrong px / segments > 3 px | 269 / 162 / 24 | **278** / **153** / **38** |
| Creature kit elements / segments > 3 px (worst) | 5,155 / 2 (6.04 px) | **6,176** / **0 (1.65 px)** |
| Flashbang, cell tower, disco ball | | identical drawings |
| Doughnut | 0 merged | 0 merged, same refusals; the mug re-sized (1,577 → 1,147 slots, false line 20,687 → 20,232.5 px) |
| Newly refused | | creature `Joint` only |

Fourteen Apollo outlines change size, which is the fix doing its job, and the audit moves
13 px the wrong way, ten of them on `apollo_000` over `apollo_002` — the "measured wrong"
class that shifts whenever the ordering inputs change (§19). Voyager's nine new merges
draw 0.5–149.5 px of visible false outline, under the limit, and 14 of their segments are
over 3 px (worst `part_092.001/m`, 9.55 px on a 28 px segment for four half-frames). The
creature's `Joint` was a one-ring merge only because its last frame had one piece; sized
for its others it draws 1,032 px of false outline and goes face by face — its 6 px drift
gone, 1,021 elements more.

That makes a standing inconsistency visible. A **one-ring** outline is refused if any gap
is 6 px worse than faces (Merge Slack); a **multi-ring** outline only if its visible false
line over the whole clip passes 160 px, so a part can drift 9 px for a few half-frames
and pass. Both fixes in §22–23 move parts from the strict test to the lenient one,
correctly by the design, and the bars do not match. Which should — one bar for every
merged outline, or a per-gap peak alongside the clip total — is a decision, not a fix.

**Where the false line is.** The birth gap alone is 829 of frames 1–20's 855 px. The
true hole halfway through it is nearly its full length already and only thinner — it
opens along a line, 0.122 of the way into the gap — while the drawn one grows out of the
one vertex its slot was parked on, at the hole's far end, so halfway it is a half-size
copy beside the truth (`out/lab-torus/birth-gaps-6-7-8.png`).

**Options, measured** (`scripts/lab_appear_options.py`). The object is baked once with
the limit off (measuring, not deciding, as §14 did at 1e9), then every gap where a slot
is parked at one end is re-scored under each rule with the bake's own false-outline
measure, at a quarter, half and three quarters of the gap, and at the empty keyframe —
because a parked ring is drawn there.

| Parking rule | Birth 7→8 | Death 24→25 | Total in between | At the empty keyframes |
|---|---|---|---|---|
| now: the whole ring on one vertex | 2,052 | 2,229 | 4,281.5 px | 0 |
| on its own centroid | 2,066 | 2,107 | 4,173 px | 0 |
| each point on its own surface point | 137 | 138 | 275 px | **3,348.5 px** |
| **from the event**: the ring drawn only from the moment it exists, starting from its own surface points then | **138** | **138** | **276 px** | **0** |

Each point on its own surface point is right in between and wrong at the keyframe,
where the ring of those vertices is a long false loop; one fill path and one opacity
per element cannot leave a subpath out at a keyframe and in between. Drawn from the
event — the moment found by bisecting the piece count, the ring carried back along the
line through its full shape and never shown before it — keeps the good half of both:
**94% less, nothing at the keyframes**.

**On the doughnut's own torus it is not enough.** Same rules on `Torus.003` through the
`rendering camera`:

| | now | own centroid | own surface points | from the event | **keyed at the event** |
|---|---|---|---|---|---|
| False line in event gaps (¼, ½, ¾) | 6,217 | 7,338 | 4,942 (+197 at keys) | 4,590.5 | **13** |

Its events are mostly splits — one contour loop pinching into two — and the slot that
continues has to morph from the whole loop to its half; re-parking only the slot that
appears leaves that. The bound in the last column keys the whole element at the event:
up to it every ring follows its own surface points from the keyframe before, after it
every ring of the keyframe after, switched at the instant. That draws **13 px** of false
line where the bake draws 6,217. In format terms it is two keyframes straddling each
topology change, which sparse tracks already hold (`{"t": […]}`); the baker, the IR and
keyframe reduction would have to sample there.

**Charging faces for appearing** changes the comparison, not the drawing. §13 measured
that faces draw essentially no visible false line even where they appear — they fade
where they are — so under any like-for-like rule a merged outline loses at a topology
event unless the outline itself carries the event. It cannot rescue the doughnut alone.

**And events are not the doughnut's whole problem.** `lab_merge_why.py` on two more of
its bodies, splitting each one's visible false outline (as the bake counts it, halfway)
by whether the piece count changes across the gap:

| Body | False outline | In gaps with an event | In steady gaps |
|---|---|---|---|
| `Torus.003` | 3,982 px | 3,140 | **842** |
| `Torus` | 6,082.5 px | 4,722.5 | **1,360** |
| `mug` | 20,232.5 px | 6,270.5 | **13,962** |

Even a representation that carried every event perfectly would leave each body 5–90×
over the 160 px limit, from the folded contour drifting in gaps where nothing appears or
vanishes — the slot-sliding class of §13 and experiments §2–3, on a contour with cusps.
**Keying events would not make the doughnut merge.** It would pay where births and deaths
are most of the false line, as on the lab torus (98% of it).

**Decision, not built.** Keying a merged outline at its topology events is the one option
measured here that carries an appearing or splitting piece; it fits the existing format;
it is also two more keyframes per event, which the working principles call patching when
they are added to hide an artefact. Whether recording the instant a silhouette changes
topology is that, and whether it is worth building while the doughnut's steady drift
would still refuse it, is the user's call.

---

## Timing on a loaded machine

This machine runs Chrome, Voicemeeter and the Blender GUI; CPU sat at 53–56% before
bakes started and free memory at 2–4 GB. Under that load the same bake timed 390 and
711 s, and the same ordering replay 27 and 107 s. Rules that came out of it:

- Compare **CPU time** (`time.process_time()`, one worker so the ordering pass runs
  in-process), or back-to-back runs in alternating order.
- An audit's "bake seconds" is serial ordering; never read it as bake cost.
- Run audits one at a time.
- A replay that holds two copies of every frame in one process measures memory.
