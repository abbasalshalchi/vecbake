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

## Timing on a loaded machine

This machine runs Chrome, Voicemeeter and the Blender GUI; CPU sat at 53–56% before
bakes started and free memory at 2–4 GB. Under that load the same bake timed 390 and
711 s, and the same ordering replay 27 and 107 s. Rules that came out of it:

- Compare **CPU time** (`time.process_time()`, one worker so the ordering pass runs
  in-process), or back-to-back runs in alternating order.
- An audit's "bake seconds" is serial ordering; never read it as bake cost.
- Run audits one at a time.
- A replay that holds two copies of every frame in one process measures memory.
