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
